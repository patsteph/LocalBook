"""Background Enrichment Worker — the "Night Shift".

One presence-aware, cancellable, dosed worker that replaces fire-and-forget
sprawl. All deferred second-brain work (entity extraction, graph relationships,
community summaries, curator inference, stance scoring) is ENQUEUED here instead
of spawned independently. The worker:

  • runs ONE job at a time (single chokepoint → no uncoordinated pile-up),
  • only starts a job when presence permits (tier gate) and the ingest flood has
    drained (system-quiet gate),
  • CANCELS the in-flight job the instant a foreground op starts — yielding the
    GPU mid-call — and re-queues it for later (the fix for the un-preemptable
    contention that no amount of priority queueing could solve),
  • trickles via an inter-job dose gap so a backlog drains across idle windows
    instead of bursting.

Patterns borrowed: Lucene's instant-segment / background-merge split, Salsa /
rust-analyzer's cancel-on-input + re-run, Postgres autovacuum's dose budget,
WorkManager's constraint-gated scheduling.

See READFIRST/planning/enrichment-worker-night-shift.md.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional

from services import presence
from services.enrichment_jobs import EnrichmentJob, JobTier, min_presence_for

logger = logging.getLogger(__name__)

_POLL = 0.5            # cancellation-watch granularity (s) — sub-perceptual
_MAX_ATTEMPTS = 5      # drop a job after this many foreground cancellations
_OLLAMA_QUIET = 8.0    # require Ollama quiet this long before starting a job
_IDLE_RECHECK = 5.0    # re-evaluate presence at least this often when parked
                       # (bounds how long after the user goes idle before
                       # enrichment resumes — new enqueues wake it immediately)

# Phase 2 — adaptive dose budget. The worker drains at a pace SCALED TO HOW AWAY
# the user is (autovacuum-style cost budget). Each entry is (burst, cooldown_s):
# run up to `burst` jobs back-to-back, then rest `cooldown` (re-evaluating
# presence) before the next burst. This is what makes "daydream" (gentle trickle
# while the user might return any second) genuinely different from "deep sleep"
# (drain the backlog overnight). ACTIVE never runs jobs (gated upstream).
from services.presence import Tier as _Tier  # noqa: E402

_DOSE = {
    _Tier.SHORT_IDLE: (1, 15.0),     # might return any second → one then rest
    _Tier.LONG_IDLE:  (3, 8.0),      # sustained idle → moderate
    _Tier.AWAY:       (10_000, 1.5), # away / overnight → drain hard
}


class EnrichmentWorker:
    def __init__(self) -> None:
        # dict keyed by job.key == coalescing: a second enqueue of the same key
        # while one is pending is a no-op. _order preserves FIFO fairness.
        self._jobs: Dict[str, EnrichmentJob] = {}
        self._order: List[str] = []
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._current_key: Optional[str] = None

    # ── public API ──────────────────────────────────────────────────────
    def enqueue(self, job: EnrichmentJob) -> None:
        """Add a job. Duplicate keys coalesce (already-pending wins)."""
        if job.key in self._jobs:
            return
        self._jobs[job.key] = job
        self._order.append(job.key)
        self._wake.set()
        logger.debug(
            f"[enrichment-worker] enqueued {job.label} ({job.key}) "
            f"tier={job.tier.name} depth={len(self._jobs)}"
        )

    def queue_depth(self) -> int:
        return len(self._jobs)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="enrichment-worker")
        logger.info("[enrichment-worker] started")

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass

    # ── internals ───────────────────────────────────────────────────────
    def _peek_runnable_key(self, tier_now) -> Optional[str]:
        """First queued job whose tier is permitted by `tier_now`."""
        for key in self._order:
            job = self._jobs.get(key)
            if job is not None and tier_now >= min_presence_for(job.tier):
                return key
        return None

    async def _loop(self) -> None:
        burst_done = 0
        burst_tier = None
        while self._running:
            tier_now = presence.current_tier()
            # A change in presence (e.g. user went from LONG_IDLE → AWAY, or
            # returned) resets the dose so the new tier's pace applies at once.
            if burst_tier != tier_now:
                # Visibility for the overnight soak: log transitions into the
                # idle tiers that unlock background work (esp. AWAY, which gates
                # the NIGHT-tier consolidation/journal jobs). Kept low-noise —
                # ACTIVE↔SHORT flapping during use isn't logged.
                if tier_now >= _Tier.LONG_IDLE:
                    logger.info(
                        f"[enrichment-worker] presence → {tier_now.name} "
                        f"(queue depth={len(self._jobs)})"
                    )
                burst_tier = tier_now
                burst_done = 0

            key = self._peek_runnable_key(tier_now)
            if key is None:
                # Nothing runnable now (queue empty, or presence too "active").
                # Wait for new work or re-evaluate presence on a timer.
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=_IDLE_RECHECK)
                except asyncio.TimeoutError:
                    pass
                continue
            # Don't pile onto a still-draining ingest flood.
            if presence.system_busy(_OLLAMA_QUIET):
                await asyncio.sleep(_POLL)
                continue

            job = self._jobs.pop(key, None)
            try:
                self._order.remove(key)
            except ValueError:
                pass
            if job is None:
                continue
            await self._run_one(job)

            # Adaptive dose: run a burst of jobs, then rest. The pace scales with
            # how "away" the user is (gentle trickle when they might return, hard
            # drain overnight).
            burst, cooldown = _DOSE.get(tier_now, (1, _IDLE_RECHECK))
            burst_done += 1
            if burst_done >= burst:
                burst_done = 0
                await asyncio.sleep(cooldown)
            else:
                await asyncio.sleep(_POLL)  # brief yield, keep draining the burst

    async def _run_one(self, job: EnrichmentJob) -> None:
        self._current_key = job.key
        t0 = time.time()
        # Surface the heavier tiers (DEEP / NIGHT) when they actually start —
        # this is how the overnight soak confirms the night-pass jobs fired.
        if job.tier >= JobTier.DEEP:
            logger.info(
                f"[enrichment-worker] running {job.label} "
                f"(tier={job.tier.name}, attempt={job.attempts})"
            )
        task = asyncio.create_task(job.factory(), name=f"enrich:{job.label}")
        cancelled = False
        try:
            while not task.done():
                # Foreground op started → yield the GPU immediately.
                if presence.is_active():
                    task.cancel()
                    cancelled = True
                    break
                await asyncio.wait({task}, timeout=_POLL)

            if cancelled:
                try:
                    await task
                except BaseException:
                    pass
                job.attempts += 1
                if job.attempts < _MAX_ATTEMPTS:
                    self.enqueue(job)  # job was popped → re-adds for later
                    logger.info(
                        f"[enrichment-worker] cancelled {job.label} ({job.key}) "
                        f"for foreground; re-queued (attempt {job.attempts})"
                    )
                else:
                    logger.warning(
                        f"[enrichment-worker] dropping {job.label} ({job.key}) "
                        f"after {job.attempts} foreground cancellations"
                    )
                return

            exc = None if task.cancelled() else task.exception()
            if exc is not None:
                logger.warning(
                    f"[enrichment-worker] {job.label} ({job.key}) failed: {exc}"
                )
            else:
                logger.debug(
                    f"[enrichment-worker] {job.label} ({job.key}) "
                    f"done in {time.time() - t0:.1f}s"
                )
        finally:
            self._current_key = None


# Module-level singleton — import and `enqueue` from anywhere.
enrichment_worker = EnrichmentWorker()
