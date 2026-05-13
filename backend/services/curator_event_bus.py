"""
Curator Event Bus — async pub/sub for cross-agent observability.

Curator Phase 1 (2026-05-12). Skeleton implementation:
- Every @-prefix agent emits events after its action completes (collector,
  curator, research, future @mcp). Emits are post-action and async, so no
  latency is added to the user's hot path.
- A single subscriber — the CuratorBrain consumer loop — drains the queue
  and persists events via curator_brain.record_event for replay/telemetry.
- For Phase 1, the consumer just logs + persists. Real event handlers
  (mental model updates, engagement-driven boosts, ambient devil's
  advocate triggers) come in Phase 2.

Architectural notes:
- The bus is process-local (asyncio.Queue). LocalBook is single-process,
  so this is sufficient. If we ever multi-process, swap to Redis or
  similar without touching emit/subscribe call sites.
- Failure mode: emit never raises. If the queue is full or persistence
  fails, the event is dropped with a warning. Agents don't care.
- Bounded queue (default 10k events) protects against memory blowup if
  the consumer falls behind during a burst.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


Actor = Literal["@collector", "@research", "@curator", "@mcp", "user", "system"]


class CuratorEvent(BaseModel):
    """A single observable event in the agent system.

    `actor` is who emitted it. `action` is the verb (source_added,
    intent_dispatched, curator_query, etc.). `intent` is the intent_classifier
    intent id when applicable (e.g. "collect_now", "morning_brief"). `payload`
    is a free-form dict — keep it small and JSON-serializable. `outcome`
    records pass/fail/deferred for actions where that matters.
    """

    ts: datetime = Field(default_factory=datetime.utcnow)
    notebook_id: Optional[str] = None
    actor: Actor
    action: str
    intent: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    outcome: Optional[Literal["success", "failed", "deferred"]] = None


EventHandler = Callable[[CuratorEvent], Awaitable[None]]


class CuratorEventBus:
    """In-memory async pub/sub with optional persistence to curator_brain.

    Single-process. Bounded queue. Skeleton implementation — Phase 2 adds
    real handlers and 30-day rolling-window rotation.
    """

    DEFAULT_MAX_QUEUE = 10_000

    def __init__(self, max_queue: int = DEFAULT_MAX_QUEUE):
        self._queue: asyncio.Queue[CuratorEvent] = asyncio.Queue(maxsize=max_queue)
        self._handlers: List[EventHandler] = []
        self._consumer_task: Optional[asyncio.Task] = None
        self._running = False
        # Phase 1 telemetry: emit-side dropped events when queue is full.
        self._dropped = 0
        # Phase 2a telemetry: per-action consumer dispatch counts. Lets
        # us spot dead actions (events emitted but no handler running)
        # or runaway actions (handler firing far too often).
        self._dispatch_counts: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def emit(self, event: CuratorEvent) -> None:
        """Fire-and-forget emit. Never raises. Returns immediately.

        Drops the event with a warning if the queue is full (consumer
        is lagging). Calling code does NOT need to await anything.
        """
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % 100 == 1:
                logger.warning(
                    f"[event_bus] queue full — dropped event "
                    f"#{self._dropped} ({event.actor} {event.action}). "
                    f"Consumer may be lagging."
                )
        except Exception as e:
            logger.warning(f"[event_bus] emit failed: {e}")

    # Convenience overload for the common path — agents can either
    # build a CuratorEvent or pass kwargs directly.
    def emit_now(
        self,
        actor: Actor,
        action: str,
        notebook_id: Optional[str] = None,
        intent: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        outcome: Optional[Literal["success", "failed", "deferred"]] = None,
    ) -> None:
        self.emit(
            CuratorEvent(
                actor=actor,
                action=action,
                notebook_id=notebook_id,
                intent=intent,
                payload=payload or {},
                outcome=outcome,
            )
        )

    # ------------------------------------------------------------------
    # Subscribe
    # ------------------------------------------------------------------

    def subscribe(self, handler: EventHandler) -> None:
        """Register an async handler called for every event.

        Handlers run sequentially per event (not parallel) so a slow
        handler can hold up the queue. Keep handlers fast or push their
        work into background tasks.
        """
        self._handlers.append(handler)

    def unsubscribe(self, handler: EventHandler) -> bool:
        """Remove a previously-subscribed handler. Returns True if found.

        Used by the SSE endpoint to clean up per-connection handlers when
        clients disconnect — otherwise handlers accumulate forever.
        Curator Phase 2b (2026-05-13).
        """
        try:
            self._handlers.remove(handler)
            return True
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the consumer loop. Idempotent.

        Called from main.py lifespan on backend boot.
        """
        if self._consumer_task is not None and not self._consumer_task.done():
            return

        # ── Default handler chain (Curator Phase 2a) ──────────────────
        # Every event flows through TWO handlers in order:
        #   1. action-specific dispatch (may take real side effects on
        #      the brain — mark dirty, record engagement, etc.)
        #   2. persist + log (always runs, even if action handler fails)
        #
        # Action handlers are intentionally fault-tolerant: any error is
        # logged and the persist+log default still fires. This keeps the
        # observability layer truthful even if the consumer logic regresses.

        async def _action_dispatch(event: CuratorEvent) -> None:
            try:
                from services.curator_brain import curator_brain
                action = event.action
                nb = event.notebook_id

                # New source landed: dirty the notebook digest so the next
                # consolidation cycle rebuilds the curator's understanding.
                if action in ("source_added", "source_ingested") and nb:
                    name = (event.payload or {}).get("filename") or ""
                    try:
                        curator_brain.mark_notebook_dirty(nb, name=name)
                        self._dispatch_counts[action] = self._dispatch_counts.get(action, 0) + 1
                    except Exception as e:
                        logger.debug(f"[event_bus] mark_notebook_dirty: {e}")

                    # Curator Phase 3a: tiered inference trigger. When
                    # the notebook's source count crosses 5/10/25/50/+25,
                    # fire mental-model inference asynchronously. Brain
                    # has its own 30s debounce so this is safe to call
                    # repeatedly during bursty source additions.
                    try:
                        from storage.source_store import source_store
                        sources = await source_store.list(nb)
                        count = len(sources)
                        if curator_brain.should_trigger_inference(count):
                            logger.info(
                                f"[event_bus] notebook {nb[:8]} source_count={count} "
                                f"crossed mental-model threshold; triggering inference"
                            )
                            asyncio.create_task(
                                curator_brain.infer_mental_model(nb),
                                name=f"mental-model-infer-{nb[:8]}",
                            )
                    except Exception as e:
                        logger.debug(f"[event_bus] mental-model trigger check: {e}")

                    # Curator Phase 3b: score the new source's stance
                    # against the notebook thesis (if a thesis exists).
                    # Each source gets one stance row per notebook;
                    # scorer is debounced via scored_thesis_hash so this
                    # is safe to call repeatedly.
                    try:
                        source_id = (event.payload or {}).get("source_id")
                        if source_id:
                            asyncio.create_task(
                                curator_brain.score_source_stance(nb, source_id),
                                name=f"stance-score-{nb[:8]}-{str(source_id)[:8]}",
                            )
                    except Exception as e:
                        logger.debug(f"[event_bus] stance scoring trigger: {e}")

                    # Curator Phase 6a: anticipatory draft trigger. Fires
                    # on each source-add but has thick gating (≥15
                    # sources + stable thesis 3d + no existing draft +
                    # not in 14-day cool-off + nag budget). The method
                    # itself does all the checks; we just kick it off.
                    try:
                        from agents.curator import curator
                        asyncio.create_task(
                            curator.maybe_fire_anticipatory_draft(nb),
                            name=f"anticipatory-draft-{nb[:8]}",
                        )
                    except Exception as e:
                        logger.debug(f"[event_bus] anticipatory_draft trigger: {e}")

                # Curator Phase 3c: when a stance is scored as
                # high-confidence contradicts, fire the dissent overwatch
                # check. maybe_fire_dissent_overwatch handles all gating
                # (nag budget, supporting-count threshold, confidence)
                # and queues a pending aside that the next chat reply
                # picks up via generate_overwatch_aside.
                if action == "stance_scored" and nb:
                    payload = event.payload or {}
                    if (
                        payload.get("stance") == "contradicts"
                        and (payload.get("confidence") or 0) > 0.6
                    ):
                        try:
                            from agents.curator import curator
                            asyncio.create_task(
                                curator.maybe_fire_dissent_overwatch(
                                    nb, payload.get("source_id"),
                                ),
                                name=f"dissent-overwatch-{nb[:8]}",
                            )
                            self._dispatch_counts[action] = self._dispatch_counts.get(action, 0) + 1
                        except Exception as e:
                            logger.debug(f"[event_bus] dissent overwatch trigger: {e}")

                # Curator Phase 5: event-driven overwatch triggers.
                # These replace the probabilistic firing of the existing
                # generate_overwatch_aside as the primary surfacing path.
                # Each gated by can_fire_nag with appropriate priority.

                # New high-confidence connection between two notebooks.
                if action == "connection_discovered":
                    payload = event.payload or {}
                    strength = payload.get("strength") or 0
                    if strength > 0.7:
                        try:
                            nb_a = payload.get("notebook_a")
                            nb_b = payload.get("notebook_b")
                            description = (payload.get("description") or "")[:200]
                            # Choose one of the two notebooks as the "host" for
                            # the aside — by convention, notebook_a (the brain's
                            # detect_connections orders pairs deterministically).
                            host_nb = nb_a or nb_b
                            if host_nb and curator_brain.can_fire_nag(
                                "connection_overwatch", host_nb, priority="low"
                            ):
                                other_label = "another notebook" if nb_a == host_nb else "your other notebook"
                                if nb_a and nb_b:
                                    other_label = nb_b if host_nb == nb_a else nb_a
                                    other_label = f"notebook {other_label[:8]}..."
                                aside = f"Cross-notebook link: {description} (shared with {other_label})"
                                nag_id = curator_brain.record_nag(
                                    "connection_overwatch",
                                    notebook_id=host_nb,
                                    subject_id=str(payload.get("connection_id") or ""),
                                )
                                curator_brain.queue_pending_aside(
                                    notebook_id=host_nb,
                                    kind="connection",
                                    aside_text=aside,
                                    nag_id=nag_id,
                                )
                                self._dispatch_counts[action] = self._dispatch_counts.get(action, 0) + 1
                        except Exception as e:
                            logger.debug(f"[event_bus] connection_discovered handler: {e}")

                # Stagnation severity escalation (mild→moderate→plateau).
                if action == "stagnation_escalated" and nb:
                    payload = event.payload or {}
                    severity = payload.get("severity", "")
                    days = payload.get("days_since_growth", 0)
                    try:
                        if curator_brain.can_fire_nag(
                            "stagnation_overwatch", nb, priority="medium"
                        ):
                            if severity == "plateau":
                                aside = (
                                    f"Heads up — this notebook's collection has PLATEAUED "
                                    f"({days} days without new content). The topic space may "
                                    f"be saturated. Consider expanding scope."
                                )
                            elif severity == "moderate":
                                aside = (
                                    f"This notebook has been quiet for {days} days. "
                                    f"Search criteria have been auto-expanded — keep an eye on "
                                    f"the approval queue."
                                )
                            else:  # mild
                                aside = (
                                    f"Collection slowing down ({days} days). Curator is "
                                    f"trying wider exploratory queries."
                                )
                            nag_id = curator_brain.record_nag(
                                "stagnation_overwatch",
                                notebook_id=nb,
                                subject_id=severity,
                            )
                            curator_brain.queue_pending_aside(
                                notebook_id=nb,
                                kind="stagnation",
                                aside_text=aside,
                                nag_id=nag_id,
                            )
                            self._dispatch_counts[action] = self._dispatch_counts.get(action, 0) + 1
                    except Exception as e:
                        logger.debug(f"[event_bus] stagnation_escalated handler: {e}")

                # User-visible plan completed — low-priority aside.
                # We only fire when there was meaningful output (steps that
                # produced anything user-visible). Avoids "plan completed"
                # spam for every collect-now run that finds nothing.
                if action == "plan_completed" and nb:
                    payload = event.payload or {}
                    plan_id = payload.get("plan_id")
                    try:
                        # Only fire for user_visible plans (avoid system noise)
                        # and only if the brain has the plan recorded as having
                        # actually produced work — we check by reading the row.
                        if plan_id and curator_brain.can_fire_nag(
                            "plan_completed_overwatch", nb, priority="low"
                        ):
                            plan = curator_brain.get_plan(plan_id)
                            if plan and plan.get("user_visible"):
                                summary = plan.get("summary") or "Plan completed"
                                aside = f"✓ {summary[:160]} — done."
                                nag_id = curator_brain.record_nag(
                                    "plan_completed_overwatch",
                                    notebook_id=nb,
                                    subject_id=plan_id,
                                )
                                curator_brain.queue_pending_aside(
                                    notebook_id=nb,
                                    kind="plan_completed",
                                    aside_text=aside,
                                    nag_id=nag_id,
                                )
                                self._dispatch_counts[action] = self._dispatch_counts.get(action, 0) + 1
                    except Exception as e:
                        logger.debug(f"[event_bus] plan_completed handler: {e}")

                # User-engagement signals → engagement_events table.
                # These power Phase 5 brief learning + Phase 4 calibration.
                elif action == "rag_query":
                    payload = event.payload or {}
                    curator_brain.record_engagement(
                        kind="query",
                        signal="asked",
                        notebook_id=nb,
                        payload={
                            "question_chars": payload.get("question_chars"),
                            "source_count": payload.get("source_count"),
                            "streaming": payload.get("streaming"),
                        },
                    )
                    self._dispatch_counts[action] = self._dispatch_counts.get(action, 0) + 1

                elif action == "source_rejected":
                    payload = event.payload or {}
                    curator_brain.record_engagement(
                        kind="source",
                        signal="rejected",
                        subject_type="source",
                        subject_id=payload.get("item_id"),
                        notebook_id=nb,
                        payload={
                            "by": payload.get("by"),
                            "reason": payload.get("reason"),
                            "feedback_type": payload.get("feedback_type"),
                        },
                    )
                    self._dispatch_counts[action] = self._dispatch_counts.get(action, 0) + 1

                elif action == "curator_intent_dispatched" and event.intent:
                    curator_brain.record_engagement(
                        kind="curator_feature",
                        signal="invoked",
                        subject_type="intent",
                        subject_id=event.intent,
                        notebook_id=nb,
                        payload=event.payload or {},
                    )
                    self._dispatch_counts[action] = self._dispatch_counts.get(action, 0) + 1

                elif action == "conversational_reply":
                    curator_brain.record_engagement(
                        kind="curator_feature",
                        signal="invoked",
                        subject_type="intent",
                        subject_id="conversational_reply",
                        notebook_id=nb,
                        payload=event.payload or {},
                    )
                    self._dispatch_counts[action] = self._dispatch_counts.get(action, 0) + 1
            except Exception as e:
                logger.warning(f"[event_bus] action dispatch failed for {event.action}: {e}")

        async def _default_persist_and_log(event: CuratorEvent) -> None:
            try:
                from services.curator_brain import curator_brain
                curator_brain.record_event(
                    ts=event.ts.isoformat(),
                    actor=event.actor,
                    action=event.action,
                    notebook_id=event.notebook_id,
                    intent=event.intent,
                    payload=event.payload,
                    outcome=event.outcome,
                )
            except Exception as e:
                logger.warning(f"[event_bus] persist failed: {e}")
            logger.info(
                f"[event_bus] {event.actor} {event.action}"
                + (f" intent={event.intent}" if event.intent else "")
                + (f" outcome={event.outcome}" if event.outcome else "")
                + (f" nb={event.notebook_id[:8]}" if event.notebook_id else "")
            )

        if not self._handlers:
            # Order matters: action dispatch runs first so brain side
            # effects happen before the persist/log row is written. If
            # action dispatch fails the persist still fires (handlers
            # are isolated by the consumer loop's try/except).
            self._handlers.append(_action_dispatch)
            self._handlers.append(_default_persist_and_log)

        self._running = True
        self._consumer_task = asyncio.create_task(self._consumer_loop(), name="curator-event-bus")
        logger.info("[event_bus] consumer started with %d handler(s)", len(self._handlers))

    async def stop(self) -> None:
        """Stop the consumer loop. Drains any pending events first."""
        self._running = False
        if self._consumer_task and not self._consumer_task.done():
            # Wake the loop so it can see _running=False.
            try:
                self._queue.put_nowait(CuratorEvent(actor="system", action="shutdown"))
            except asyncio.QueueFull:
                pass
            try:
                await asyncio.wait_for(self._consumer_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("[event_bus] consumer didn't stop in 5s, cancelling")
                self._consumer_task.cancel()
            self._consumer_task = None
        logger.info(
            f"[event_bus] consumer stopped (dropped {self._dropped} events total)"
        )

    async def _consumer_loop(self) -> None:
        """Drain the queue, dispatch to handlers, loop forever."""
        while self._running:
            try:
                event = await self._queue.get()
            except asyncio.CancelledError:
                break
            # Sentinel shutdown event — exit cleanly.
            if event.actor == "system" and event.action == "shutdown" and not self._running:
                break
            for handler in self._handlers:
                try:
                    await handler(event)
                except Exception as e:
                    logger.warning(f"[event_bus] handler error ({handler}): {e}")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {
            "queue_size": self._queue.qsize(),
            "queue_maxsize": self._queue.maxsize,
            "dropped": self._dropped,
            "handlers": len(self._handlers),
            "running": self._running,
            "dispatch_counts": dict(self._dispatch_counts),
        }


# Singleton instance
event_bus = CuratorEventBus()
