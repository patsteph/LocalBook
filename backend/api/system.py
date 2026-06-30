"""System observability endpoints (NS-B1).

Read-only view of the background enrichment worker + presence state, so the
frontend living-view can show "synthesizing N/M", current tier, and memory
pressure. Wires existing primitives — no new state machinery.
"""
import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()


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
