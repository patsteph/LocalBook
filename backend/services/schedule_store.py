"""Schedule Store — canonical registry of every scheduled/timed background actor
plus a JSON-persisted override layer.

Part of the Unified Schedule Viewer (2.2.0). This is the "one store, one writer"
seam the plan calls for:

  * `SCHEDULE_REGISTRY` is the single source of truth for *what* is scheduled —
    id, display name, agent, category, default interval, editability, safety
    bounds, and the module constant each row documents. Built from the 21-actor
    inventory in `READFIRST/planning/schedule-viewer.md` +
    `READFIRST/ArchitectureDocs/BACKGROUND_SCHEDULE.md`.

  * `schedule_store` layers a `schedule_overrides.json` (in `settings.data_dir`)
    on top of the registry, exposing `get_interval(id, default)` /
    `set_override(...)` — mirroring the `collection_scheduler` JSON-persist
    pattern. `get_interval` is the retrofit seam each hardcoded loop will call
    at the top of its iteration in **Rung C** (deferred — not wired yet).

Design rules (match the codebase):
  * **Never raise.** Store failures log and fall back to registry defaults —
    losing an override must never break a background loop (mirrors
    `activity_ledger`).
  * **One writer per store.** The Collector's per-notebook schedules are NOT
    duplicated here — they live in `collector.yaml` and are written only via
    `collector.update_config`. This store owns the *code-defined* schedules.

Scope note: Rung A/B ship the registry + override plumbing + the read path.
`set_override` is validated and persisted, but the hardcoded actor loops do not
yet read it (Rung C). Only the Collector per-notebook schedules are live-editable
today, and those route through `collector.update_config`, not this store.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Categories / cadence kinds (kept as plain strings for JSON friendliness)
# --------------------------------------------------------------------------
CAT_AGENTS = "agents"
CAT_EMAIL = "email"
CAT_MEMORY = "memory"
CAT_INFRA = "infrastructure"

# How a row's cadence is expressed — drives how the UI renders it.
KIND_INTERVAL = "interval"        # fixed period; interval_seconds meaningful
KIND_PER_NOTEBOOK = "per_notebook"  # one row per notebook, cadence in collector.yaml
KIND_GATED = "gated"              # wakes on an interval but only acts on a gate (day/7d)
KIND_REACTIVE = "reactive"        # event-driven, no interval
KIND_ENV = "env"                  # module const read once at import; env-overridable


@dataclass
class ScheduleDef:
    """A canonical schedule definition — one background actor's timing."""
    id: str
    name: str
    agent: str                      # curator | collector | correspondent | research | memory | system
    category: str                   # CAT_*
    cadence_kind: str = KIND_INTERVAL
    default_seconds: Optional[int] = None   # None for reactive/per_notebook
    min_seconds: Optional[int] = None       # safety floor (None = not bounded / not editable)
    max_seconds: Optional[int] = None       # safety ceiling
    # Whether the Schedule Viewer lets the user edit this row **today** and have
    # the edit honored live. Only per-notebook Collector rows qualify in Rung
    # A/B; everything hardcoded is read-only until Rung C wires get_interval into
    # its loop. (`rung_c_candidate` marks the ones that become editable then.)
    editable: bool = False
    rung_c_candidate: bool = False  # override-able once its loop reads get_interval
    module_const: str = ""          # the code constant this row documents
    note: str = ""                  # human cadence description (esp. reactive/gated)
    tier: Optional[str] = None      # presence tier the work runs under (DEEP/NIGHT/…)
    env_var: Optional[str] = None   # env override, if any (read-only "via env")
    managed_in: Optional[str] = None  # where it's actually edited today (UI pointer)
    advanced: bool = False          # hide behind the "advanced" disclosure in the UI
    # Step 7 (per-actor enable/disable): True only when this actor's background
    # loop actually gates its work on `schedule_store.is_enabled(id)`. Toggling a
    # row where this is False would be a lie (the loop never checks), so the
    # Schedule Viewer only offers the on/off switch for these — one cadence
    # source, honestly surfaced.
    can_disable: bool = False


# --------------------------------------------------------------------------
# THE REGISTRY — the 21-actor inventory, code-verified 2026-07-28.
# Constants cross-checked against:
#   memory_manager.py, correspondent.py, weekly_journal_agent.py,
#   digest_composer.py, stuck_source_recovery.py, model_warmup.py,
#   collection_scheduler.py, loop_monitor.py, presence.py.
# --------------------------------------------------------------------------
_H = 3600
_D = 86400

SCHEDULE_REGISTRY: List[ScheduleDef] = [
    # ── Agents ──────────────────────────────────────────────────────────
    ScheduleDef(
        id="collector-scheduler-wake",
        name="Collector scheduler wake",
        agent="collector",
        category=CAT_AGENTS,
        cadence_kind=KIND_INTERVAL,
        default_seconds=600,
        module_const="collection_scheduler.CHECK_INTERVAL_SECONDS",
        note="How often the scheduler wakes to check which notebooks are due. "
             "Per-notebook cadence is set on each notebook's Collector below.",
        tier="DEEP",
        advanced=True,
    ),
    ScheduleDef(
        id="curator-brief",
        name="Curator morning brief",
        agent="curator",
        category=CAT_AGENTS,
        cadence_kind=KIND_REACTIVE,
        note="On-demand only — the Curator has no periodic timer. Runs when you "
             "ask for a morning brief / weekly wrap-up.",
    ),
    ScheduleDef(
        id="curator-inferences",
        name="Curator event-bus inferences",
        agent="curator",
        category=CAT_AGENTS,
        cadence_kind=KIND_REACTIVE,
        note="Reactive — mental-model / stance scoring fires per @-agent event, "
             "routed through the enrichment worker (DEEP lane).",
        tier="DEEP",
        advanced=True,
    ),
    ScheduleDef(
        id="research-agent",
        name="Research agent",
        agent="research",
        category=CAT_AGENTS,
        cadence_kind=KIND_REACTIVE,
        note="Fully interactive — @research runs only when invoked. Nothing "
             "scheduled.",
    ),

    # ── Email (Correspondent) ───────────────────────────────────────────
    ScheduleDef(
        id="correspondent-poll",
        name="Correspondent IMAP poll",
        agent="correspondent",
        category=CAT_EMAIL,
        cadence_kind=KIND_INTERVAL,
        default_seconds=8 * _H,
        min_seconds=1 * _H,     # floor: never a 10s IMAP poll
        max_seconds=2 * _D,
        rung_c_candidate=True,
        module_const="correspondent.DEFAULT_POLL_INTERVAL_MINUTES",
        note="Fetch + classify + route new mail across all enabled IMAP accounts.",
        tier="DEEP",
        can_disable=True,
    ),
    ScheduleDef(
        id="weekly-journal",
        name="Weekly auto-journal",
        agent="correspondent",
        category=CAT_EMAIL,
        cadence_kind=KIND_GATED,
        default_seconds=6 * _H,
        min_seconds=1 * _H,
        max_seconds=1 * _D,
        rung_c_candidate=True,
        module_const="weekly_journal_agent.CHECK_INTERVAL_SECONDS",
        note="Wakes every 6h; composes + SMTPs the journal once per enabled "
             "account, gated to a 7-day cadence. Per-account on/off lives in "
             "Correspondent settings.",
        tier="NIGHT",
        managed_in="Correspondent settings",
        can_disable=True,
    ),
    ScheduleDef(
        id="digest-composer",
        name="Per-sender digest",
        agent="correspondent",
        category=CAT_EMAIL,
        cadence_kind=KIND_GATED,
        default_seconds=6 * _H,
        min_seconds=1 * _H,
        max_seconds=1 * _D,
        rung_c_candidate=True,
        module_const="digest_composer.WORKER_INTERVAL_SECONDS",
        note="Wakes every 6h; ships each digest-mode sender's pending items on "
             "their configured weekday. Per-sender day/mode lives in "
             "Correspondent settings.",
        tier="NIGHT",
        managed_in="Correspondent settings",
        can_disable=True,
    ),

    # ── Memory ──────────────────────────────────────────────────────────
    ScheduleDef(
        id="memory-compact",
        name="Memory: compact",
        agent="memory",
        category=CAT_MEMORY,
        cadence_kind=KIND_INTERVAL,
        default_seconds=1 * _H,
        min_seconds=15 * 60,
        max_seconds=1 * _D,
        rung_c_candidate=True,
        module_const="memory_manager.COMPACT_INTERVAL_HOURS",
        note="Event dedup / merge (non-LLM).",
        tier="DEEP",
        can_disable=True,
    ),
    ScheduleDef(
        id="memory-pattern",
        name="Memory: pattern analysis",
        agent="memory",
        category=CAT_MEMORY,
        cadence_kind=KIND_INTERVAL,
        default_seconds=3 * _H,
        min_seconds=1 * _H,
        max_seconds=2 * _D,
        rung_c_candidate=True,
        module_const="memory_manager.PATTERN_INTERVAL_HOURS",
        note="phi4 pattern analysis — emerging preferences.",
        tier="DEEP",
        can_disable=True,
    ),
    ScheduleDef(
        id="memory-consolidation",
        name="Memory: deep consolidation",
        agent="memory",
        category=CAT_MEMORY,
        cadence_kind=KIND_INTERVAL,
        default_seconds=6 * _H,
        min_seconds=1 * _H,
        max_seconds=2 * _D,
        rung_c_candidate=True,
        module_const="memory_manager.DEEP_CONSOLIDATION_HOURS",
        note="Tier-3 consolidation — updates memory embeddings. NIGHT-only.",
        tier="NIGHT",
        can_disable=True,
    ),
    ScheduleDef(
        id="memory-daily-summary",
        name="Memory: daily summary",
        agent="memory",
        category=CAT_MEMORY,
        cadence_kind=KIND_INTERVAL,
        default_seconds=24 * _H,
        min_seconds=6 * _H,
        max_seconds=7 * _D,
        rung_c_candidate=True,
        module_const="memory_manager.DAILY_SUMMARY_HOURS",
        note="Tier-4 daily summary + brief pre-compute. NIGHT-only.",
        tier="NIGHT",
        can_disable=True,
    ),

    # ── Infrastructure (read-only, advanced disclosure) ─────────────────
    ScheduleDef(
        id="enrichment-worker",
        name="Enrichment worker",
        agent="system",
        category=CAT_INFRA,
        cadence_kind=KIND_REACTIVE,
        note="The traffic cop — continuously drains the background job queue, "
             "gated on presence + system-busy + memory pressure.",
        advanced=True,
    ),
    ScheduleDef(
        id="stuck-source-recovery",
        name="Stuck-source recovery",
        agent="system",
        category=CAT_INFRA,
        cadence_kind=KIND_INTERVAL,
        default_seconds=5 * 60,
        min_seconds=60,
        max_seconds=1 * _H,
        rung_c_candidate=True,
        module_const="stuck_source_recovery.CHECK_INTERVAL_MINUTES",
        note="Resets sources stuck in 'processing'.",
        tier="DEEP",
        advanced=True,
        can_disable=True,
    ),
    ScheduleDef(
        id="model-warmup",
        name="Model warmup",
        agent="system",
        category=CAT_INFRA,
        cadence_kind=KIND_INTERVAL,
        default_seconds=240,
        module_const="model_warmup.WARMUP_INTERVAL",
        note="Pings resident models so Ollama doesn't unload them (idle-gated).",
        advanced=True,
    ),
    ScheduleDef(
        id="loop-monitor",
        name="Event-loop monitor",
        agent="system",
        category=CAT_INFRA,
        cadence_kind=KIND_ENV,
        default_seconds=1,   # 0.5s — displayed via note; sub-second not editable
        module_const="loop_monitor._INTERVAL",
        note="0.5s tick — logs when the event loop stalls. Configured via env.",
        env_var="LOCALBOOK_LOOPMON_INTERVAL_S",
        advanced=True,
    ),
    ScheduleDef(
        id="loop-watchdog",
        name="Event-loop watchdog",
        agent="system",
        category=CAT_INFRA,
        cadence_kind=KIND_REACTIVE,
        note="Re-arms on a ~12.5s timer; faulthandler dump on a fatal freeze.",
        advanced=True,
    ),
    ScheduleDef(
        id="post-ingest-enrichment",
        name="Post-ingest enrichment",
        agent="system",
        category=CAT_INFRA,
        cadence_kind=KIND_REACTIVE,
        note="Per source — entities (DAYDREAM) → relationships + communities "
             "(DEEP), routed through the worker.",
        advanced=True,
    ),
    ScheduleDef(
        id="community-summaries",
        name="Community summaries",
        agent="system",
        category=CAT_INFRA,
        cadence_kind=KIND_REACTIVE,
        note="Finishes capped community-summary batches when work remains "
             "(self-re-enqueues on the worker).",
        advanced=True,
    ),
    ScheduleDef(
        id="event-bus-consumer",
        name="Event-bus consumer",
        agent="system",
        category=CAT_INFRA,
        cadence_kind=KIND_REACTIVE,
        note="Reactive — persists @-agent events to the curator brain.",
        advanced=True,
    ),
    ScheduleDef(
        id="presence-tiers",
        name="Presence tiers",
        agent="system",
        category=CAT_INFRA,
        cadence_kind=KIND_ENV,
        note="Idle thresholds gating ALL background work — SHORT_IDLE 20s / "
             "LONG_IDLE 120s / AWAY 1800s + night window. Configured via env.",
        env_var="LOCALBOOK_PRESENCE_*",
        advanced=True,
    ),
    ScheduleDef(
        id="canvas-idle-research",
        name="Canvas idle-research",
        agent="curator",
        category=CAT_AGENTS,
        cadence_kind=KIND_INTERVAL,
        default_seconds=1800,   # a full idle pass at most every 30 min
        min_seconds=5 * 60,
        max_seconds=6 * _H,
        rung_c_candidate=True,  # enrichment_worker reads get_interval(id) live
        module_const="canvas_idle_research.DEFAULT_INTERVAL_S",
        note="While idle: draws high-confidence suggested (dashed) connections between "
             "canvas nodes, and researches one top gap per pass (AWAY tier, 3h cooldown).",
        tier="DEEP",
        can_disable=True,       # enrichment_worker gates on is_enabled(id)
    ),
]

# id → def, for O(1) lookup.
_REGISTRY_BY_ID: Dict[str, ScheduleDef] = {d.id: d for d in SCHEDULE_REGISTRY}


def get_definition(schedule_id: str) -> Optional[ScheduleDef]:
    return _REGISTRY_BY_ID.get(schedule_id)


def definition_as_dict(d: ScheduleDef) -> Dict[str, Any]:
    return asdict(d)


class ScheduleStore:
    """JSON-persisted override layer over `SCHEDULE_REGISTRY`.

    Override file shape:
        { "<id>": { "interval_seconds": int?, "enabled": bool?, "config": {...}? } }

    Never raises. All public methods degrade to registry defaults on error.
    """

    def __init__(self) -> None:
        try:
            from config import settings
            self._path = Path(settings.data_dir) / "schedule_overrides.json"
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"[schedule_store] could not resolve data_dir: {e}")
            self._path = Path("schedule_overrides.json")
        self._overrides: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ---------------------------------------------------------------- IO
    def _load(self) -> None:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text())
                if isinstance(data, dict):
                    self._overrides = {
                        k: v for k, v in data.items() if isinstance(v, dict)
                    }
                    logger.info(
                        f"[schedule_store] loaded {len(self._overrides)} override(s)"
                    )
        except Exception as e:
            logger.warning(f"[schedule_store] load failed (starting empty): {e}")
            self._overrides = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._overrides, indent=2))
        except Exception as e:
            logger.warning(f"[schedule_store] save failed: {e}")

    # ------------------------------------------------------------ reads
    def get_override(self, schedule_id: str) -> Optional[Dict[str, Any]]:
        return self._overrides.get(schedule_id)

    def all_overrides(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._overrides)

    def is_enabled(self, schedule_id: str) -> bool:
        """A schedule is enabled unless an override explicitly disables it."""
        ov = self._overrides.get(schedule_id)
        if ov is not None and ov.get("enabled") is False:
            return False
        return True

    def get_interval(self, schedule_id: str, default: float) -> float:
        """The retrofit seam. A background loop calls this at the top of every
        iteration with its hardcoded default; an admin override wins.

        NOTE (Rung A/B): only Collector loops read their cadence live today.
        Hardcoded loops still use their module constant — wiring them to call
        this is Rung C. Returns `default` for any unknown id / on any error.
        """
        try:
            ov = self._overrides.get(schedule_id)
            if not ov:
                return default
            if ov.get("enabled") is False:
                return default
            val = ov.get("interval_seconds")
            if val is None:
                return default
            return float(val)
        except Exception:
            return default

    # ----------------------------------------------------------- writes
    def set_override(
        self,
        schedule_id: str,
        *,
        interval_seconds: Optional[float] = None,
        enabled: Optional[bool] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist an override, validated against the registry bounds.

        Raises ValueError on an unknown id or an out-of-bounds interval — the
        caller (the API) turns that into a 400/404. Persistence itself never
        raises (logs + drops on disk error).
        """
        d = _REGISTRY_BY_ID.get(schedule_id)
        if d is None:
            raise ValueError(f"unknown schedule id: {schedule_id}")

        ov = dict(self._overrides.get(schedule_id, {}))

        if interval_seconds is not None:
            iv = float(interval_seconds)
            if d.min_seconds is not None and iv < d.min_seconds:
                raise ValueError(
                    f"{schedule_id}: interval {iv:.0f}s below floor "
                    f"{d.min_seconds}s"
                )
            if d.max_seconds is not None and iv > d.max_seconds:
                raise ValueError(
                    f"{schedule_id}: interval {iv:.0f}s above ceiling "
                    f"{d.max_seconds}s"
                )
            ov["interval_seconds"] = iv

        if enabled is not None:
            ov["enabled"] = bool(enabled)

        if config is not None:
            merged = dict(ov.get("config", {}))
            merged.update(config)
            ov["config"] = merged

        self._overrides[schedule_id] = ov
        self._save()
        return dict(ov)

    def set_enabled(self, schedule_id: str, enabled: bool) -> Dict[str, Any]:
        """Enable/disable a schedule (Step 7). Thin wrapper over `set_override`
        so the on/off toggle has a first-class name. Raises ValueError on an
        unknown id (the API maps that to 404); persistence itself never raises.
        The loops that gate on `is_enabled(id)` pick this up on their next
        iteration — no restart, no second cadence source.
        """
        return self.set_override(schedule_id, enabled=bool(enabled))

    def clear_override(self, schedule_id: str) -> None:
        if schedule_id in self._overrides:
            self._overrides.pop(schedule_id, None)
            self._save()


# Singleton — mirrors collection_scheduler / activity_ledger module singletons.
schedule_store = ScheduleStore()
