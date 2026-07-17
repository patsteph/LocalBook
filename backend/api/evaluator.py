"""Evaluator API endpoints — LLM benchmarking and model performance testing.

Provides endpoints to run evaluations, check progress, and view results.
"""

import asyncio
import logging
import traceback
from fastapi import APIRouter, HTTPException, BackgroundTasks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluator", tags=["evaluator"])


@router.get("/hardware")
async def get_hardware():
    """Get hardware profile for this machine."""
    from evaluator.hardware_profiler import get_hardware_profile
    from evaluator.models import ModelCombo
    from config import settings
    hw = get_hardware_profile()
    combo = ModelCombo.from_config(settings)
    return {
        "hardware": hw.to_dict(),
        "combo": combo.to_dict()
    }


@router.get("/models")
async def get_models():
    """List installed models with registry metadata."""
    from evaluator.model_registry import model_registry
    installed = model_registry.get_installed_models()
    return {"models": installed, "count": len(installed)}


@router.get("/models/registry")
async def get_registry():
    """Get the full model registry (including not-installed models)."""
    from evaluator.model_registry import model_registry
    model_registry.refresh_installed_status()
    all_models = model_registry.list_all()
    return {"models": [m.to_dict() for m in all_models], "count": len(all_models)}


@router.post("/run")
async def run_evaluation(background_tasks: BackgroundTasks):
    """Run a full LLM evaluation suite.
    
    Runs in background — poll /evaluator/status for progress.
    Returns immediately with run start confirmation.
    """
    from evaluator.evaluator_service import get_progress

    progress = get_progress()
    
    # Debug logging to understand the state
    import time as _time
    real_elapsed = (_time.time() - progress.run_start_time) if progress.run_start_time > 0 else 0
    logger.info(f"[EVALUATOR] /run called - running={progress.running}, phase={progress.phase}, real_elapsed={real_elapsed:.0f}s")
    
    if progress.running:
        # Additional safety: if we've been running for an unreasonable time (>30 min),
        # assume it's stuck and force reset
        if real_elapsed > 1800:  # 30 minutes
            logger.warning("[EVALUATOR] Detected stuck run (>30min), forcing reset")
            progress.running = False
            progress.error = ""
        else:
            logger.warning(f"[EVALUATOR] Rejecting run request - already running (phase={progress.phase}, real_elapsed={real_elapsed:.0f}s)")
            raise HTTPException(
                status_code=409,
                detail="An evaluation is already running. Check /evaluator/status for progress."
            )

    # Reset progress state cleanly before starting new run
    logger.info("[EVALUATOR] Resetting progress state before starting new evaluation")
    progress.running = True  # Set TRUE immediately so status polls see it as running
    progress.error = ""
    progress.results_so_far = {}
    progress.elapsed_seconds = 0.0
    progress.run_start_time = _time.time()  # For live elapsed computation in to_dict()
    progress.phase = 0
    progress.phase_name = "Starting..."
    progress.current_test = "Initializing evaluation"

    # Run in background
    background_tasks.add_task(_run_evaluation_background)

    return {
        "message": "Evaluation started. Poll /evaluator/status for progress.",
        "status": "started",
    }


async def _run_evaluation_background():
    """Background task to run the full evaluation."""
    from evaluator.evaluator_service import run_full_evaluation, get_progress

    logger.info("[EVALUATOR] Background task started")
    try:
        summary = await run_full_evaluation()
        logger.info(f"[EVALUATOR] Evaluation complete: {summary.overall_grade} ({summary.overall_score:.1f})")
    except Exception as e:
        logger.error(f"[EVALUATOR] Evaluation failed: {e}")
        traceback.print_exc()
        # Defensive: ensure error and running flag are always set
        progress = get_progress()
        if not progress.error:
            progress.error = str(e)
        if progress.running:
            logger.warning("[EVALUATOR] Forcing reset of stuck running flag after exception")
            progress.running = False


@router.get("/status")
async def get_status():
    """Get current evaluation progress."""
    from evaluator.evaluator_service import get_progress
    
    progress = get_progress()
    
    # Recovery: detect inconsistent states and auto-reset
    if progress.running:
        import time as _time
        # Compute real elapsed from start time (elapsed_seconds field only updates at end)
        real_elapsed = (_time.time() - progress.run_start_time) if progress.run_start_time > 0 else 0
        # If we've been stuck at phase 0 for more than 2 min, the background task likely crashed
        if progress.phase == 0 and progress.phase_name == "Starting..." and real_elapsed > 120:
            logger.warning(f"[EVALUATOR] Detected stuck state (phase=0, real_elapsed={real_elapsed:.0f}s), auto-resetting")
            progress.running = False
            progress.error = "Evaluation failed to start — background task did not begin. Check server logs."
        # If we've been running for an unreasonable time (>30 min), assume stuck
        elif real_elapsed > 1800:
            logger.warning(f"[EVALUATOR] Detected long-running state (real_elapsed={real_elapsed:.0f}s), auto-resetting")
            progress.running = False
            progress.error = "Evaluation timed out after 30 minutes."
    
    return progress.to_dict()




@router.get("/results")
async def list_results():
    """List all historical evaluation runs.
    
    Filters out legacy runs missing model names and limits to top 8 by score.
    """
    from evaluator.evaluator_service import get_results_list
    runs = get_results_list()
    
    # Backfill main_model/fast_model from combo string for older runs
    for r in runs:
        if not r.get("main_model") and r.get("combo") and " + " in r.get("combo", ""):
            parts = r["combo"].split(" + ", 1)
            r["main_model"] = parts[0].strip()
            r["fast_model"] = parts[1].strip() if len(parts) > 1 else ""
    
    # Filter: only include runs that have explicit main_model and fast_model
    runs = [r for r in runs if r.get("main_model") and r.get("fast_model")]
    
    # Sort by score descending, limit to top 8
    runs.sort(key=lambda r: r.get("overall_score", 0), reverse=True)
    runs = runs[:8]
    
    return {"runs": runs, "count": len(runs)}


@router.get("/results/latest")
async def get_latest():
    """Get the most recent evaluation run."""
    from evaluator.evaluator_service import get_latest_result
    result = get_latest_result()
    if not result:
        return {"message": "No evaluation runs found", "result": None}
    return {"result": result}


@router.get("/results/{run_id}")
async def get_result(run_id: str):
    """Get a specific evaluation run by ID."""
    from evaluator.evaluator_service import get_result_by_id
    result = get_result_by_id(run_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return {"result": result}


@router.post("/results/compare")
async def compare_results(run_a: str, run_b: str):
    """Compare two evaluation runs side by side."""
    from evaluator.evaluator_service import get_result_by_id

    result_a = get_result_by_id(run_a)
    result_b = get_result_by_id(run_b)

    if not result_a:
        raise HTTPException(status_code=404, detail=f"Run '{run_a}' not found")
    if not result_b:
        raise HTTPException(status_code=404, detail=f"Run '{run_b}' not found")

    # Build comparison
    comparison = {
        "run_a": {
            "run_id": run_a,
            "combo": result_a.get("combo", {}),
            "hardware": result_a.get("hardware", {}),
            "overall_score": result_a.get("overall_score", 0),
            "overall_grade": result_a.get("overall_grade", ""),
            "category_scores": result_a.get("category_scores", {}),
            "timestamp": result_a.get("timestamp", ""),
        },
        "run_b": {
            "run_id": run_b,
            "combo": result_b.get("combo", {}),
            "hardware": result_b.get("hardware", {}),
            "overall_score": result_b.get("overall_score", 0),
            "overall_grade": result_b.get("overall_grade", ""),
            "category_scores": result_b.get("category_scores", {}),
            "timestamp": result_b.get("timestamp", ""),
        },
        "differences": {},
    }

    # Per-category differences
    for cat in set(list(result_a.get("category_scores", {}).keys()) + 
                   list(result_b.get("category_scores", {}).keys())):
        score_a = result_a.get("category_scores", {}).get(cat, 0)
        score_b = result_b.get("category_scores", {}).get(cat, 0)
        comparison["differences"][cat] = {
            "score_a": score_a,
            "score_b": score_b,
            "delta": round(score_b - score_a, 1),
        }

    return comparison


@router.post("/cleanup")
async def cleanup():
    """Delete any stale test notebook from a failed/interrupted run."""
    from evaluator.evaluator_service import cleanup_stale_notebook
    await cleanup_stale_notebook()
    return {"message": "Cleanup complete"}


@router.get("/providers")
async def get_providers():
    """Report health status for every known LLM provider (Ollama, llama-server).

    Used by the UI to show sidecar availability badges and by pre-flight checks
    before allowing swaps to llama-server-backed models.
    """
    from services.llm_provider import providers_status
    providers = await providers_status()
    return {"providers": providers}


# ── v1.8.0 (Phase 2): llama-server sidecar lifecycle control ──────────────────

@router.get("/sidecar/status")
async def get_sidecar_status():
    """Return runtime state of the llama-server sidecar (running, healthy, pid)."""
    from services.sidecar_manager import sidecar_manager
    return await sidecar_manager.status()


@router.post("/sidecar/start")
async def start_sidecar():
    """Start the sidecar if it isn't already healthy. Blocks up to ~45s."""
    from services.sidecar_manager import sidecar_manager
    from services.llm_provider import invalidate_health_cache
    ok = await sidecar_manager.ensure_started(timeout=45.0)
    invalidate_health_cache()
    if not ok:
        raise HTTPException(status_code=503, detail=sidecar_manager.last_error or "Failed to start sidecar")
    return {"status": "success", "message": "Sidecar started and healthy", **(await sidecar_manager.status())}


@router.post("/sidecar/stop")
async def stop_sidecar():
    """Stop the sidecar child process. Idempotent."""
    from services.sidecar_manager import sidecar_manager
    from services.llm_provider import invalidate_health_cache
    await sidecar_manager.stop(grace_seconds=5.0)
    invalidate_health_cache()
    return {"status": "success", "message": "Sidecar stopped"}


@router.post("/swap")
async def swap_model(payload: dict):
    """Swap the active model for a specific role (main_model or fast_model)."""
    from services.llm_locker import locker, ModelSwapError

    target_model = payload.get("target_model")
    role = payload.get("role")

    if not target_model or not role:
        raise HTTPException(status_code=400, detail="Missing target_model or role")

    try:
        # Sanitize role strings from UI to what the locker expects
        normalized_role = role
        if role == "main": normalized_role = "main_model"
        if role == "fast": normalized_role = "fast_model"
        if role == "embeddings": normalized_role = "embedding_model"
        if role == "vision": normalized_role = "vision_model"

        # v1.8.0 (Phase 2): auto-spawn the sidecar when the target is a
        # llama_server-provider model. This is what makes "click Use → run
        # evaluator" actually work without the user launching anything.
        try:
            from evaluator.model_registry import model_registry
            _info = model_registry.get_model(target_model)
            if _info and getattr(_info, "provider", "ollama") == "llama_server":
                from services.sidecar_manager import sidecar_manager
                from services.llm_provider import invalidate_health_cache
                ok = await sidecar_manager.ensure_started(timeout=45.0)
                invalidate_health_cache()
                if not ok:
                    raise HTTPException(
                        status_code=503,
                        detail=f"Sidecar failed to start: {sidecar_manager.last_error}",
                    )
        except HTTPException:
            raise
        except Exception as _e:
            logger.warning(f"[evaluator] sidecar pre-spawn skipped: {_e}")

        message = locker.execute_swap(target_model, normalized_role)
        return {"status": "success", "message": message}
    except ModelSwapError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Swap failed: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error during model swap: {str(e)}")


@router.post("/save-default")
async def save_default_combo(payload: dict):
    """Save the current model combo as the permanent startup default.
    
    Persists to user_preferences.json in the data directory. On next startup,
    these models will be loaded instead of the built-in OLMo/Phi4 defaults.
    """
    import json
    from config import settings
    from evaluator.model_registry import model_registry
    
    main_model = payload.get("main_model") or settings.ollama_model
    fast_model = payload.get("fast_model") or settings.ollama_fast_model
    vision_model = payload.get("vision_model") or settings.vision_model
    
    # Validate models are installed (registry match preferred, live fallback for community models).
    # Wave 9.6 — MLX models are HuggingFace ids (org/repo), NOT Ollama models: the Ollama /api/show
    # check would always 404 ("not installed in Ollama"), blocking Save-as-default for an MLX combo.
    # Skip the Ollama check for a role whose engine is mlx (or whose name is an HF path) — those are
    # validated at adopt/download time, and the combo being saved is the one currently running.
    import httpx as _httpx
    from config import settings as _s
    for name, role, engine in [(main_model, "main", settings.main_engine),
                               (fast_model, "fast", settings.fast_engine)]:
        if engine == "mlx" or "/" in (name or ""):
            continue
        info = model_registry.get_model(name)
        if not info:
            try:
                r = _httpx.post(f"{_s.ollama_base_url}/api/show", json={"name": name}, timeout=5.0)
                if r.status_code != 200:
                    raise HTTPException(status_code=400, detail=f"{role} model '{name}' is not installed in Ollama.")
            except _httpx.RequestError:
                raise HTTPException(status_code=503, detail="Ollama is not reachable — cannot validate models.")
    
    prefs_path = settings.data_dir / "user_preferences.json"
    
    # Load existing preferences or create new
    try:
        existing = json.loads(prefs_path.read_text()) if prefs_path.exists() else {}
    except Exception:
        existing = {}
    
    existing["default_combo"] = {
        "main_model": main_model,
        "fast_model": fast_model,
        "vision_model": vision_model,
        # Wave 9 — persist the per-role engine flags + MLX model ids from the LIVE settings
        # (which reflect the user's Locker swaps) so an adopted MLX config survives the restart
        # .env purge. main.py SafeStart restores these. Old prefs files without them default to
        # "ollama" via config, so this is backward-compatible.
        "main_engine": settings.main_engine,
        "fast_engine": settings.fast_engine,
        "vision_engine": settings.vision_engine,
        "image_engine": settings.image_engine,
        "mlx_main_model": settings.mlx_main_model,
        "mlx_fast_model": settings.mlx_fast_model,
        "mlx_vision_model": settings.mlx_vision_model,
        "mlx_image_model": settings.mlx_image_model,
    }

    prefs_path.write_text(json.dumps(existing, indent=2))
    logger.info(f"Saved default combo: {main_model} + {fast_model} "
                f"(engines: main={settings.main_engine} fast={settings.fast_engine} vision={settings.vision_engine})")
    
    return {
        "status": "success",
        "message": f"Default combo saved: {main_model} + {fast_model}. Will be used on next startup.",
        "combo": existing["default_combo"],
    }


@router.delete("/save-default")
async def clear_default_combo():
    """Clear the saved default combo, reverting to built-in OLMo/Phi4 defaults."""
    import json
    from config import settings
    
    prefs_path = settings.data_dir / "user_preferences.json"
    
    if prefs_path.exists():
        try:
            existing = json.loads(prefs_path.read_text())
            existing.pop("default_combo", None)
            if existing:
                prefs_path.write_text(json.dumps(existing, indent=2))
            else:
                prefs_path.unlink()
        except Exception:
            if prefs_path.exists():
                prefs_path.unlink()
    
    return {
        "status": "success",
        "message": "Default combo cleared. Will use OLMo + Phi4 on next startup.",
    }


@router.get("/save-default")
async def get_default_combo():
    """Get the currently saved default combo, if any."""
    import json
    from config import settings
    
    prefs_path = settings.data_dir / "user_preferences.json"
    
    if prefs_path.exists():
        try:
            existing = json.loads(prefs_path.read_text())
            combo = existing.get("default_combo")
            if combo:
                return {"has_custom_default": True, "combo": combo}
        except Exception as _e:
            logger.debug(f"[evaluator] {type(_e).__name__}: {_e}")
    
    # No saved custom default → report the CURRENT engine-aware config defaults (was a
    # stale hardcoded olmo/granite combo). Keeps the "Built-in default" line truthful.
    from evaluator.models import ModelCombo
    c = ModelCombo.from_config(settings)
    return {
        "has_custom_default": False,
        "combo": {
            "main_model": c.main_model,
            "fast_model": c.fast_model,
            "vision_model": c.vision_model,
        },
    }
