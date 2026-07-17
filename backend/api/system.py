"""System observability endpoints (NS-B1).

Read-only view of the background enrichment worker + presence state, so the
frontend living-view can show "synthesizing N/M", current tier, and memory
pressure. Wires existing primitives — no new state machinery.
"""
import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()


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
        out["models"] = {"main": main, "fast": fast, "vision": vision}
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
