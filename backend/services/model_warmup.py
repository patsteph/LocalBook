"""
Model Warmup Service

Keeps LLM and embedding models warm in memory to eliminate cold start latency.
Runs a background task that periodically pings the models with minimal requests.

Resource optimization: Only warms models that have been recently used.
"""
import asyncio
import httpx
import time
from typing import Optional
from config import settings
import logging
logger = logging.getLogger(__name__)

# Background task reference
_warmup_task: Optional[asyncio.Task] = None
_should_run = True

# Warmup interval in seconds — must be shorter than keep_alive so models
# don't expire between pings, but long enough to avoid constant churn.
WARMUP_INTERVAL = 240  # 4 minutes

# Track last usage time for each model type (only warm if used in last 10 min)
_last_main_model_use: float = 0
_last_fast_model_use: float = 0
_last_embedding_use: float = 0
_last_reranker_use: float = 0
MODEL_IDLE_TIMEOUT = 600  # 10 minutes - don't warm if idle longer than this


def mark_reranker_used():
    """Call this when reranker model is used"""
    global _last_reranker_use
    _last_reranker_use = time.time()


def mark_main_model_used():
    """Call this when main LLM model is used"""
    global _last_main_model_use
    _last_main_model_use = time.time()


def mark_fast_model_used():
    """Call this when fast LLM model is used"""
    global _last_fast_model_use
    _last_fast_model_use = time.time()


def mark_embedding_used():
    """Call this when embedding model is used"""
    global _last_embedding_use
    _last_embedding_use = time.time()


def _mlx_available() -> bool:
    """True iff the in-process MLX engine is usable (mlx/mlx_lm/mlx_vlm importable).
    Stable from startup — does not depend on whether a model is currently resident."""
    try:
        from services.mlx_engine import mlx_engine
        return mlx_engine.available()
    except Exception:
        return False


def _role_on_mlx(role: str) -> bool:
    """True when `role` ('main'|'fast') is actually served in-process by MLX right now
    (its `{role}_engine` flag == 'mlx' AND MLX available). When True the Ollama twin
    of that role must NOT be warmed — MLX holds/loads it in-process, so warming Ollama
    would leave a redundant model resident (the 2.1.0 memory-pressure bug). This mirrors
    the llm_service runtime decision (`mlx_model_for_role(...) and mlx_engine.available()`)
    so warmup and generation always agree on which backend serves the role: if MLX is
    configured but unavailable, calls fall back to Ollama and its twin still gets warmed."""
    try:
        return getattr(settings, f"{role}_engine", "ollama") == "mlx" and _mlx_available()
    except Exception:
        return False


async def warm_ollama_model(model: str, keep_alive = "30m") -> bool:
    """Send a minimal request to keep an Ollama model loaded in memory"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": "Hi",
                    "stream": False,
                    "keep_alive": keep_alive,
                    "options": {
                        "num_predict": 1  # Generate just 1 token
                    }
                }
            )
            return response.status_code == 200
    except Exception as e:
        print(f"⚠️ Warmup failed for {model}: {e}")
        return False


async def warm_embedding_model() -> bool:
    """Warm up the embedding model by encoding a short text"""
    try:
        # Warm Ollama's embedding model ONLY when embeddings are actually served by the
        # Ollama daemon. When embed_engine == "mlx" (and MLX is available) embeddings run
        # in-process (MLX arctic, same 1024-dim → no re-index) and encode() routes there —
        # so warming Ollama would load a redundant ~1GB model. NB: the legacy
        # `use_ollama_embeddings` flag is independent of `embed_engine`; this reconciles
        # them (2.1.1 fix). If embed_engine=mlx but MLX is unavailable, _embed_on_mlx is
        # False and we correctly warm the Ollama model that real embeds then fall back to.
        _embed_on_mlx = getattr(settings, "embed_engine", "ollama") == "mlx" and _mlx_available()
        # Check if using Ollama embeddings
        if getattr(settings, 'use_ollama_embeddings', False) and not _embed_on_mlx:
            # Warm Ollama embedding model via API
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/embeddings",
                    json={
                        "model": settings.embedding_model,
                        "prompt": "warmup"
                    }
                )
                return response.status_code == 200
        else:
            # Use rag_embeddings module directly (model lives there, not on rag_engine)
            from services.rag_embeddings import load_embedding_model, encode

            # OFF THE EVENT LOOP. `load_embedding_model()` and `encode()` are both SYNCHRONOUS, and
            # on a machine without the MLX arctic model cached the load is a ~1.1 GB HuggingFace
            # DOWNLOAD — which, called directly from this `async def`, froze the entire backend
            # until it finished. Fresh installs hide it because install.sh pre-caches the model;
            # the UPGRADE path has no such block, so upgrading users hit it for real.
            # (Same class as the 2026-06-25 soak finding: blocking-on-loop is a CLASS of bug, not
            # one site — see COLLABORATION_NOTES "Background scheduling + event-loop safety".)
            def _warm_sync() -> None:
                load_embedding_model()
                encode("warmup")

            await asyncio.to_thread(_warm_sync)
            return True
    except Exception as e:
        print(f"⚠️ Embedding warmup failed: {e}")
        return False


async def warm_reranker_model() -> bool:
    """Warm up the reranker model by loading it"""
    try:
        if not getattr(settings, 'use_reranker', True):
            return True  # Reranker disabled, skip
        
        from services import rag_search

        # Off the loop for the same reason as the embedding warmup above: `_get_reranker()` loads
        # the FlashRank cross-encoder synchronously (and downloads it on first use).
        await asyncio.to_thread(rag_search._get_reranker)
        return True
    except Exception as e:
        print(f"⚠️ Reranker warmup failed: {e}")
        return False


# Minimum available RAM (bytes) to attempt in-process model loading
# Embedding + reranker load sentence_transformers INTO our process (~400MB spike).
# Only skip at catastrophic levels — let macOS handle normal memory pressure.
_MIN_RAM_FOR_HEAVY_MODELS = 500 * 1024 * 1024  # 500 MB — catastrophic only


def _get_available_memory() -> int:
    """Return available system memory in bytes. Returns MAX_INT if psutil unavailable."""
    try:
        import psutil
        return psutil.virtual_memory().available
    except Exception:
        return 2**63  # Assume plenty if we can't check


async def warmup_cycle(force_all: bool = False):
    """Run one warmup cycle for models that have been recently used.
    
    When force_all=True (startup), models are warmed SEQUENTIALLY to avoid
    memory spikes that trigger macOS OOM kills. Ollama models are safe (loaded
    in Ollama's process) but embedding/reranker load into OUR process.
    """
    now = time.time()
    result_map = {}
    
    # v1.7.0: Skip Ollama warmup for models served by llama-server (always resident).
    def _is_sidecar(name: str) -> bool:
        try:
            from services.llm_provider import resolve as _resolve_provider
            return _resolve_provider(name).api_style != "ollama"
        except Exception:
            return False

    # ── 1. Ollama models (safe — loaded in Ollama's process, not ours) ──
    # keep_alive="5m" — short TTL so Ollama reclaims RAM quickly.
    # The warmup loop (every 4 min) re-pings active models before expiry.
    if force_all or (now - _last_main_model_use < MODEL_IDLE_TIMEOUT):
        if _is_sidecar(settings.ollama_model) or _role_on_mlx("main"):
            # Served by llama-server (sidecar, always resident) or in-process MLX — the
            # Ollama twin must NOT be warmed or it sits redundantly resident (2.1.1 fix).
            result_map["main"] = True
        else:
            try:
                result_map["main"] = await warm_ollama_model(settings.ollama_model, keep_alive="5m")
            except Exception:
                result_map["main"] = False

    # Fast model: only warm if recently used (NOT at startup).
    # Lazy-loads on first ingestion/auto-tag — saves ~4GB RAM at boot.
    if not force_all and (now - _last_fast_model_use < MODEL_IDLE_TIMEOUT):
        if settings.ollama_fast_model != settings.ollama_model or "main" not in result_map:
            if _is_sidecar(settings.ollama_fast_model) or _role_on_mlx("fast"):
                # Sidecar (llama-server) or in-process MLX — skip the Ollama twin (2.1.1 fix).
                result_map["fast"] = True
            else:
                try:
                    result_map["fast"] = await warm_ollama_model(settings.ollama_fast_model, keep_alive="5m")
                except Exception:
                    result_map["fast"] = False
    
    # Vision model: NOT warmed here — loads lazily on first vision task to save memory
    
    # ── 2. In-process models (heavy — check memory first) ──
    avail = _get_available_memory()
    if avail < _MIN_RAM_FOR_HEAVY_MODELS:
        print(f"⚠️ Low memory ({avail / 1024**3:.1f} GB free) — deferring embedding/reranker warmup")
        result_map["embed"] = False
        result_map["rerank"] = False
    else:
        # Load sequentially to avoid concurrent memory spike
        if force_all or (now - _last_embedding_use < MODEL_IDLE_TIMEOUT):
            try:
                result_map["embed"] = await warm_embedding_model()
            except Exception:
                result_map["embed"] = False
        
        if force_all or (now - _last_reranker_use < MODEL_IDLE_TIMEOUT):
            try:
                result_map["rerank"] = await warm_reranker_model()
            except Exception:
                result_map["rerank"] = False
    
    main_ok = result_map.get("main", True) is True
    fast_ok = result_map.get("fast", main_ok if settings.ollama_fast_model == settings.ollama_model else True) is True
    embed_ok = result_map.get("embed", True) is True
    rerank_ok = result_map.get("rerank", True) is True
    
    return main_ok, fast_ok, embed_ok, rerank_ok


async def _warmup_loop_periodic():
    """Background loop that keeps models warm (periodic keep-alive only)"""
    print("🔥 Starting periodic model keep-alive service...")
    
    # Periodic warmup - only warms recently-used models
    while _should_run:
        # Rung C (Schedule Viewer): re-read the warmup interval from the schedule
        # store each iteration so an edit lands on the next cycle without a
        # restart. Never raises → falls back to WARMUP_INTERVAL.
        from services.schedule_store import schedule_store
        await asyncio.sleep(schedule_store.get_interval("model-warmup", WARMUP_INTERVAL))

        if not _should_run:
            break
            
        # Only warm models that have been used recently
        await warmup_cycle(force_all=False)

        # Stage 3.10 — IDLE EVICTION. Warming keeps hot models resident; nothing ever released
        # cold ones, so an MLX machine ended every session holding ~7.6 GB of weights (measured
        # 2026-08-19: an eval run's end state was exactly gemma+phi+arctic). Load-time budgeting
        # (3.2) cannot fix that — it only acts when something new is loading, and a session ends
        # after work, not before it.
        await _evict_idle_mlx()


async def _evict_idle_mlx() -> None:
    """Unload MLX models idle longer than MODEL_IDLE_TIMEOUT. Never raises.

    Three guards, each protecting against a way this could hurt more than it helps:
      · NEVER while the user is in the foreground — a reload costs ~20 s for gemma, and paying
        that because a sweep fired mid-session is worse than holding the memory.
      · NEVER a model used within the timeout, tracked by the SAME `mark_*_used` stamps the
        warmup loop reads, so warming and evicting cannot disagree about what is hot.
      · `unload()` itself skips a busy model, so a generation in flight is safe regardless.
    """
    try:
        from config import settings
        if not any(getattr(settings, f"{r}_engine", "ollama") == "mlx"
                   for r in ("main", "fast", "vision", "embed")):
            return

        from services.presence import system_busy
        if system_busy():
            return

        from services.mlx_engine import mlx_engine
        held = mlx_engine.resident()
        loaded = list(held.get("text", [])) + list(held.get("embed", []))
        if not loaded:
            return

        now = time.time()
        # Map each MLX model back to the role stamp that tracks its use.
        stamps = {
            getattr(settings, "mlx_main_model", None): _last_main_model_use,
            getattr(settings, "mlx_fast_model", None): _last_fast_model_use,
            getattr(settings, "mlx_vision_model", None): _last_main_model_use,
            getattr(settings, "mlx_embedding_model", None): _last_embedding_use,
        }
        idle = [m for m in loaded
                if (now - (stamps.get(m) or 0)) > MODEL_IDLE_TIMEOUT]
        if not idle:
            return

        before = held.get("active_gb")
        freed = []
        for mid in idle:
            if await mlx_engine.unload(mid, wait=1.0):
                freed.append(mid)
        if freed:
            after = mlx_engine.resident().get("active_gb")
            print(f"[model-warmup] idle-evicted {len(freed)} MLX model(s) after "
                  f"{MODEL_IDLE_TIMEOUT}s idle — active {before} → {after} GB")
    except Exception as e:
        logger.debug(f"[model-warmup] idle eviction skipped: {e}")


async def initial_warmup():
    """Run initial warmup synchronously at startup - blocks until models are ready"""
    global _last_main_model_use, _last_fast_model_use, _last_embedding_use, _last_reranker_use
    
    print("🔥 Warming up AI models (this ensures fast first query)...")
    
    # Only mark models we actually warm at startup as "used".
    # Fast model is NOT warmed at startup — it lazy-loads on first
    # ingestion/auto-tag call, saving ~4GB RAM at boot.
    now = time.time()
    _last_main_model_use = now
    # _last_fast_model_use intentionally NOT set — lazy-loads on demand
    _last_embedding_use = now
    _last_reranker_use = now
    
    # Run warmup and WAIT for it to complete
    main_ok, fast_ok, embed_ok, rerank_ok = await warmup_cycle(force_all=True)
    print(f"🔥 Models ready - Main: {'✓' if main_ok else '✗'}, Fast: {'✓' if fast_ok else '✗'}, Embed: {'✓' if embed_ok else '✗'}, Rerank: {'✓' if rerank_ok else '✗'}")
    
    return main_ok and embed_ok  # Main model and embeddings are critical


async def start_warmup_task():
    """Start the background warmup task (for periodic keep-alive, not initial warmup)"""
    global _warmup_task, _should_run
    _should_run = True
    from utils.tasks import safe_create_task
    _warmup_task = safe_create_task(_warmup_loop_periodic(), name="model-warmup-loop")


async def stop_warmup_task():
    """Stop the background warmup task"""
    global _warmup_task, _should_run
    _should_run = False
    
    if _warmup_task:
        _warmup_task.cancel()
        try:
            await _warmup_task
        except asyncio.CancelledError as _e:
            logger.debug(f"[model-warmup] {type(_e).__name__}: {_e}")
        _warmup_task = None
    
    print("🔥 Model warmup service stopped")
