"""System observability endpoints (NS-B1).

Read-only view of the background enrichment worker + presence state, so the
frontend living-view can show "synthesizing N/M", current tier, and memory
pressure. Wires existing primitives — no new state machinery.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/version")
async def get_version():
    """App/build version + platform facts. Feeds Quality-Signals Phase 2 incident
    context (`build_incident_context`); read-only, never raises."""
    try:
        from services.app_version import get_app_version_info
        return get_app_version_info()
    except Exception as e:
        logger.debug(f"[system] version info failed: {e}")
        return {"app_version": "unknown"}


@router.get("/tray-status")
async def get_tray_status():
    """Compact one-shot status for the macOS menu-bar tray poller (build: tray v1).

    Everything the tray shows in a single cheap call: active models, token totals +
    throughput + avg response time, and the background-worker queue depth. Never
    raises — always returns a usable (possibly partial) payload."""
    from config import settings
    out = {
        "ok": True,
        "models": {"main": "", "fast": "", "vision": ""},
        # Wave 9.6 — which engine is actually serving: "mlx" (all roles), "mixed"
        # (some MLX, some Ollama), or "ollama". Lets the menu-bar show ⚡ MLX so the
        # user can tell at a glance which stack is live (persisted knowledge, #5).
        "engine": "ollama",
        # Aligned 1:1 with the Health Portal's counters (same rag_metrics source)
        # so the two never disagree: Tokens In/Out + AVG Tokens/Sec + Avg Latency.
        "metrics": {"tokens_in": 0, "tokens_out": 0, "tokens_per_sec": 0.0, "avg_latency_ms": 0},
        "enrichment": {"queue_depth": 0},
    }
    try:
        from evaluator.model_registry import model_registry
        # Engine-aware: when a role's engine == "mlx", the live model is the mlx_* one,
        # not the Ollama default — report what's actually resident so the tray is truthful.
        def _eng(attr):
            return getattr(settings, attr, "ollama") or "ollama"
        main_eng, fast_eng, vision_eng = _eng("main_engine"), _eng("fast_engine"), _eng("vision_engine")
        ollama_main = getattr(settings, "ollama_model", "") or ""
        main = getattr(settings, "mlx_main_model", "") if main_eng == "mlx" else ollama_main
        fast = getattr(settings, "mlx_fast_model", "") if fast_eng == "mlx" else (getattr(settings, "ollama_fast_model", "") or "")
        vision = (getattr(settings, "mlx_vision_model", "") if vision_eng == "mlx"
                  else model_registry.resolve_vision_model(ollama_main, getattr(settings, "vision_model", "") or ""))
        # Friendly names in the menu bar too (user #4) — same short names as the Evaluator.
        from utils.model_display import friendly_model_name
        out["models"] = {"main": friendly_model_name(main),
                         "fast": friendly_model_name(fast),
                         "vision": friendly_model_name(vision)}
        engines = [main_eng, fast_eng, vision_eng]
        out["engine"] = ("mlx" if all(e == "mlx" for e in engines)
                         else "mixed" if any(e == "mlx" for e in engines) else "ollama")
    except Exception as e:
        logger.debug(f"[system.tray] models snapshot failed: {e}")
    try:
        from services.rag_metrics import rag_metrics
        ts = rag_metrics.get_token_stats() or {}
        hs = rag_metrics.get_health_summary() or {}
        out["metrics"] = {
            "tokens_in": int(ts.get("prompt_tokens", 0) or 0),
            "tokens_out": int(ts.get("completion_tokens", 0) or 0),
            "tokens_per_sec": float(ts.get("avg_tokens_per_sec", 0) or 0),
            "avg_latency_ms": int(round(hs.get("avg_latency_ms", 0) or 0)),
        }
    except Exception as e:
        logger.debug(f"[system.tray] metrics snapshot failed: {e}")
    try:
        from services.enrichment_worker import enrichment_worker
        out["enrichment"] = {"queue_depth": int(enrichment_worker.queue_depth() or 0)}
    except Exception as e:
        logger.debug(f"[system.tray] enrichment snapshot failed: {e}")
    return out


@router.get("/schedule")
async def get_schedule():
    """Live background-worker schedule/observability snapshot.

    Returns queue depth, the current job key, the presence tier, the memory-pressure
    flag, and the last-completed-job timestamp+label. Never raises — returns a
    partial/error payload so a UI poll can't 500.
    """
    from services.enrichment_worker import enrichment_worker
    from services import presence

    out = {
        "queue_depth": None,
        "current_job": None,
        "tier": None,
        "memory_pressure": None,
        "last_run": None,
        "last_run_label": None,
    }
    try:
        out["queue_depth"] = enrichment_worker.queue_depth()
        out["current_job"] = getattr(enrichment_worker, "_current_key", None)
        out["last_run"] = getattr(enrichment_worker, "_last_run", None)
        out["last_run_label"] = getattr(enrichment_worker, "_last_run_label", None)
    except Exception as e:
        logger.debug(f"[system.schedule] worker snapshot failed: {e}")
    try:
        out["tier"] = presence.current_tier().name
        out["memory_pressure"] = presence.memory_pressure()
    except Exception as e:
        logger.debug(f"[system.schedule] presence snapshot failed: {e}")
    return out


# ==========================================================================
# Unified Schedule Viewer (2.2.0) — GET /system/schedules (merged read) +
# PUT /system/schedules/{id} (Collector-delegated write). See
# services/schedule_store.py and READFIRST/planning/schedule-viewer.md.
# ==========================================================================

# Collector frequency enum → the Schedule Viewer reuses this so the dropdown
# matches the Collector UI 1:1. Mirrors collection_scheduler.INTERVALS keys.
_COLLECTOR_FREQUENCIES = [
    "hourly", "every_2_hours", "every_4_hours", "every_8_hours",
    "twice_daily", "daily", "every_3_days", "weekly", "manual",
]


# --------------------------------------------------------------------------
# Step 7 — Run-now dispatch.
#
# Each entry maps a registry schedule id → a zero-arg factory returning the
# actor's EXISTING public run-once coroutine. We only ever call already-public
# methods (poll_all / run_compact / … / check_and_recover / warmup_cycle) — no
# actor refactors, no private `_tick` calls. Actors NOT in this map don't get a
# live Run-now button; `_run_now_meta` explains why (see below).
#
# Gated actors (weekly-journal, digest-composer) are deliberately absent: their
# only run-once entrypoint is a private `_tick` that self-gates to a day/7-day
# cadence, so a forced run would either no-op or require refactoring the actor
# (out of this file lane). Reactive/infra actors have no timer to trigger.
# --------------------------------------------------------------------------
def _rn_correspondent_poll() -> Awaitable[Any]:
    from agents.correspondent import correspondent_agent
    return correspondent_agent.poll_all()


def _rn_memory_compact() -> Awaitable[Any]:
    from services.memory_manager import memory_manager
    return memory_manager.run_compact()


def _rn_memory_pattern() -> Awaitable[Any]:
    from services.memory_manager import memory_manager
    return memory_manager.run_pattern_analysis()


def _rn_memory_consolidation() -> Awaitable[Any]:
    from services.memory_manager import memory_manager
    return memory_manager.run_consolidation()


def _rn_memory_daily_summary() -> Awaitable[Any]:
    from services.memory_manager import memory_manager
    return memory_manager.run_daily_summary()


def _rn_stuck_source_recovery() -> Awaitable[Any]:
    from services.stuck_source_recovery import stuck_source_recovery
    return stuck_source_recovery.check_and_recover()


def _rn_model_warmup() -> Awaitable[Any]:
    from services import model_warmup
    return model_warmup.warmup_cycle(force_all=True)


_RUN_NOW: Dict[str, Callable[[], Awaitable[Any]]] = {
    "correspondent-poll": _rn_correspondent_poll,
    "memory-compact": _rn_memory_compact,
    "memory-pattern": _rn_memory_pattern,
    "memory-consolidation": _rn_memory_consolidation,
    "memory-daily-summary": _rn_memory_daily_summary,
    "stuck-source-recovery": _rn_stuck_source_recovery,
    "model-warmup": _rn_model_warmup,
}


def _run_now_meta(d) -> tuple:
    """(can_run_now, reason_if_disabled) for a registry definition."""
    if d.id in _RUN_NOW:
        return True, None
    if d.cadence_kind == "reactive":
        return False, "Event-driven — runs when its trigger fires, not on a timer."
    if d.cadence_kind == "gated":
        return False, ("Day/interval-gated — only acts when due; no safe "
                       "on-demand trigger.")
    if d.cadence_kind == "env":
        return False, "Configured via environment; not runnable on demand."
    return False, "No safe on-demand trigger for this actor."


def _registry_row(d, ov: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Registry definition ⊕ stored override → one API row."""
    from services.schedule_store import definition_as_dict
    row = definition_as_dict(d)
    default_seconds = d.default_seconds
    override_interval = None
    enabled = True
    if ov:
        override_interval = ov.get("interval_seconds")
        if ov.get("enabled") is False:
            enabled = False
    effective = override_interval if override_interval is not None else default_seconds
    can_run_now, run_now_reason = _run_now_meta(d)
    row.update({
        "interval_seconds": effective,
        "overridden": bool(override_interval is not None),
        "enabled": enabled,
        # Interval editing for code-defined rows is not exposed in the UI yet;
        # only the on/off toggle (Step 7) is live for `can_disable` rows.
        "editable": False,
        # Step 7 metadata.
        "can_disable": bool(getattr(d, "can_disable", False)),
        "can_run_now": can_run_now,
        "run_now_reason": run_now_reason,
    })
    return row


async def _collector_rows() -> List[Dict[str, Any]]:
    """One live-editable row per notebook with a configured Collector schedule.

    Cadence lives in collector.yaml (read live by collection_scheduler) — the
    only truly bidirectional schedule today. last_run/next_due come from the
    scheduler's persisted state.
    """
    rows: List[Dict[str, Any]] = []
    try:
        from storage.notebook_store import notebook_store
        from agents.collector import get_collector
        from services.collection_scheduler import collection_scheduler

        status = {}
        try:
            status = collection_scheduler.get_status().get("schedule_details", {}) or {}
        except Exception as e:
            logger.debug(f"[system.schedules] scheduler status failed: {e}")

        notebooks = await notebook_store.list()
        for nb in notebooks:
            nb_id = nb.get("id")
            if not nb_id:
                continue
            try:
                config = get_collector(nb_id).get_config()
            except Exception as e:
                logger.debug(f"[system.schedules] collector cfg {nb_id}: {e}")
                continue
            intent = (getattr(config, "intent", "") or "").strip()
            if not intent:
                continue  # unconfigured collector — nothing scheduled
            sched = config.schedule if isinstance(config.schedule, dict) else {}
            freq = sched.get("frequency", "daily")
            nb_name = nb.get("name") or nb.get("title") or nb_id[:8]
            det = status.get(nb_id, {}) if isinstance(status, dict) else {}
            rows.append({
                "id": f"collector:{nb_id}",
                "name": f"Collector — {nb_name}",
                "agent": "collector",
                "category": "agents",
                "cadence_kind": "per_notebook",
                "editable": True,
                "enabled": freq != "manual",
                "frequency": freq,
                "max_items_per_run": sched.get("max_items_per_run"),
                "frequency_options": _COLLECTOR_FREQUENCIES,
                "notebook_id": nb_id,
                "last_run": det.get("last_run"),
                "next_due": det.get("next_due"),
                "overdue": det.get("overdue"),
                "note": "Per-notebook collection cadence — edited here, read live "
                        "by the collection scheduler.",
                "managed_in": None,
                "advanced": False,
                "overridden": False,
                "module_const": "collector.yaml:schedule.frequency",
                "min_seconds": None,
                "max_seconds": None,
                "default_seconds": None,
                "tier": "DEEP",
                "env_var": None,
                "rung_c_candidate": False,
                # Collector rows manage on/off via the frequency dropdown
                # ("Manual Only") + collect from the notebook's Collector panel.
                "can_disable": False,
                "can_run_now": False,
                "run_now_reason": "Collect from the notebook's Collector panel.",
            })
    except Exception as e:
        logger.debug(f"[system.schedules] collector rows failed: {e}")
    return rows


@router.get("/schedules")
async def get_schedules():
    """Comprehensive read of everything scheduled/timed across the app.

    Merges: registry defaults ⊕ stored overrides ⊕ per-notebook Collector
    schedules ⊕ the live enrichment-worker snapshot. Never raises — returns a
    partial payload so a settings poll can't 500.
    """
    from services.schedule_store import schedule_store, SCHEDULE_REGISTRY

    schedules: List[Dict[str, Any]] = []
    try:
        overrides = schedule_store.all_overrides()
        for d in SCHEDULE_REGISTRY:
            schedules.append(_registry_row(d, overrides.get(d.id)))
    except Exception as e:
        logger.debug(f"[system.schedules] registry merge failed: {e}")

    schedules.extend(await _collector_rows())

    live = await get_schedule()

    return {
        "schedules": schedules,
        "live": live,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


class ScheduleUpdate(BaseModel):
    interval_seconds: Optional[float] = None
    enabled: Optional[bool] = None
    frequency: Optional[str] = None            # Collector rows
    max_items_per_run: Optional[int] = None    # Collector rows


@router.put("/schedules/{schedule_id:path}")
async def update_schedule(schedule_id: str, request: ScheduleUpdate):
    """Edit a schedule.

    Rung A/B scope: only Collector per-notebook schedules are live-editable —
    they DELEGATE to `collector.update_config` (one writer per store, per the
    Centralization Rule). Hardcoded/infra schedules are read-only until their
    loop reads `schedule_store.get_interval` (Rung C, deferred); editing one
    now would not be honored, so we reject it rather than lie.
    """
    if schedule_id.startswith("collector:"):
        notebook_id = schedule_id.split(":", 1)[1]
        if not notebook_id:
            raise HTTPException(status_code=400, detail="missing notebook id")

        freq = request.frequency
        if request.enabled is False:
            freq = "manual"
        if freq is not None and freq not in _COLLECTOR_FREQUENCIES:
            raise HTTPException(
                status_code=400,
                detail=f"invalid frequency '{freq}'; expected one of "
                       f"{_COLLECTOR_FREQUENCIES}",
            )
        if request.max_items_per_run is not None and not (
            1 <= request.max_items_per_run <= 100
        ):
            raise HTTPException(
                status_code=400,
                detail="max_items_per_run must be between 1 and 100",
            )

        try:
            from agents.collector import get_collector
            collector = get_collector(notebook_id)
            current = collector.get_config()
            sched = dict(current.schedule) if isinstance(current.schedule, dict) else {}
            if freq is not None:
                sched["frequency"] = freq
            if request.max_items_per_run is not None:
                sched["max_items_per_run"] = request.max_items_per_run
            updated = collector.update_config({"schedule": sched})
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"[system.schedules] collector update {notebook_id}: {e}")
            raise HTTPException(status_code=500, detail=f"update failed: {e}")

        new_sched = updated.schedule if isinstance(updated.schedule, dict) else {}
        return {
            "success": True,
            "id": schedule_id,
            "frequency": new_sched.get("frequency"),
            "max_items_per_run": new_sched.get("max_items_per_run"),
            "enabled": new_sched.get("frequency") != "manual",
        }

    # Registry (code-defined) schedule.
    from services.schedule_store import get_definition, schedule_store
    d = get_definition(schedule_id)
    if d is None:
        raise HTTPException(status_code=404, detail=f"unknown schedule: {schedule_id}")

    # Step 7: per-actor enable/disable is live for actors whose loop gates on
    # `schedule_store.is_enabled(id)` — the store write is picked up on the next
    # loop iteration (no restart, no second cadence source).
    if request.enabled is not None:
        if not getattr(d, "can_disable", False):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{d.name}' can't be turned off — its background loop "
                    "always runs (it doesn't gate on the enabled flag)."
                ),
            )
        try:
            schedule_store.set_enabled(schedule_id, bool(request.enabled))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {
            "success": True,
            "id": schedule_id,
            "enabled": schedule_store.is_enabled(schedule_id),
        }

    # Interval editing for code-defined rows is not exposed yet (their loops read
    # get_interval, but the UI only ships the on/off toggle in this build).
    raise HTTPException(
        status_code=409,
        detail=(
            f"'{d.name}' interval editing isn't available in this build — only "
            "its on/off toggle is."
        ),
    )


@router.post("/schedules/{schedule_id:path}/run-now")
async def run_schedule_now(schedule_id: str):
    """Trigger a schedule's work ONCE, right now (Step 7).

    Only actors with a safe, existing public run-once entrypoint are dispatchable
    (see `_RUN_NOW`). Reactive/env/gated actors return 400 with a reason. The work
    is launched as a background task so a slow actor (memory consolidation, IMAP
    poll) can't hold the HTTP request open — the endpoint returns as soon as the
    run is scheduled. Never triggers the presence-gated worker path; this is a
    deliberate user-initiated immediate run.
    """
    from services.schedule_store import get_definition

    if schedule_id.startswith("collector:"):
        raise HTTPException(
            status_code=400,
            detail="Run a notebook's Collector from its Collector panel.",
        )

    d = get_definition(schedule_id)
    if d is None:
        raise HTTPException(status_code=404, detail=f"unknown schedule: {schedule_id}")

    factory = _RUN_NOW.get(schedule_id)
    if factory is None:
        _, reason = _run_now_meta(d)
        raise HTTPException(
            status_code=400,
            detail=f"'{d.name}' can't be run on demand — "
                   f"{reason or 'no safe trigger.'}",
        )

    async def _runner():
        try:
            await factory()
            logger.info(f"[system.run-now] '{schedule_id}' completed")
        except Exception as e:
            logger.warning(f"[system.run-now] '{schedule_id}' failed: {e}")

    try:
        asyncio.create_task(_runner())
    except Exception as e:
        logger.warning(f"[system.run-now] '{schedule_id}' dispatch failed: {e}")
        raise HTTPException(status_code=500, detail=f"could not start run: {e}")

    return {"success": True, "id": schedule_id, "triggered": True}


@router.get("/schedule/{notebook_id}")
async def get_notebook_schedule(notebook_id: str):
    """Per-notebook "synthesizing N/M" snapshot for the living-view Constellation.

    Same shape the enrichment worker pushes over `synthesis_progress`, so the UI
    can seed the indicator on mount before the first WS event. Never raises."""
    from services.enrichment_worker import enrichment_worker

    out = {
        "notebook_id": notebook_id,
        "synthesized": None,
        "total": None,
        "pending_jobs": None,
        "running_jobs": None,
        "last_label": None,
        "communities_built": None,
        "communities_total": None,
    }
    try:
        prog = enrichment_worker.notebook_progress(notebook_id)
        out["pending_jobs"] = prog["pending"]
        out["running_jobs"] = prog["running"]
        out["last_label"] = prog["last_label"]
    except Exception as e:
        logger.debug(f"[system.schedule/nb] worker snapshot failed: {e}")
        prog = {"source_ids_pending": []}
    try:
        from storage.source_store import source_store
        sources = await source_store.list(notebook_id)
        total = sum(1 for s in sources if s.get("status") == "completed")
        out["total"] = total
        out["synthesized"] = max(0, total - len(prog.get("source_ids_pending", [])))
    except Exception as e:
        logger.debug(f"[system.schedule/nb] source snapshot failed: {e}")
    try:
        from services.community_detection import community_detector
        c_total = len(community_detector.get_all_communities(notebook_id))
        out["communities_total"] = c_total
        out["communities_built"] = max(
            0, c_total - community_detector.count_missing_summaries(notebook_id)
        )
    except Exception as e:
        logger.debug(f"[system.schedule/nb] community snapshot failed: {e}")
    return out
