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

# Background task reference
_warmup_task: Optional[asyncio.Task] = None
_should_run = True

# Warmup interval in seconds (ping every 2 minutes to keep models in memory)
# Reduced from 45s to save resources - models stay warm for ~5min anyway
WARMUP_INTERVAL = 120

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


async def warm_ollama_model(model: str) -> bool:
    """Send a minimal request to keep an Ollama model loaded in memory"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": "Hi",
                    "stream": False,
                    "keep_alive": "30m",  # Keep model in VRAM for 30 minutes
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
        # Check if using Ollama embeddings
        if getattr(settings, 'use_ollama_embeddings', False):
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
            
            # This will load the model if not already loaded
            load_embedding_model()
            
            # Encode a short text to keep it warm
            encode("warmup")
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
        rag_search._get_reranker()
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
    
    # ── 1. Ollama models (safe — loaded in Ollama's process, not ours) ──
    if force_all or (now - _last_main_model_use < MODEL_IDLE_TIMEOUT):
        try:
            result_map["main"] = await warm_ollama_model(settings.ollama_model)
        except Exception:
            result_map["main"] = False
    
    if force_all or (now - _last_fast_model_use < MODEL_IDLE_TIMEOUT):
        if settings.ollama_fast_model != settings.ollama_model or "main" not in result_map:
            try:
                result_map["fast"] = await warm_ollama_model(settings.ollama_fast_model)
            except Exception:
                result_map["fast"] = False
    
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
        await asyncio.sleep(WARMUP_INTERVAL)
        
        if not _should_run:
            break
            
        # Only warm models that have been used recently
        await warmup_cycle(force_all=False)


async def initial_warmup():
    """Run initial warmup synchronously at startup - blocks until models are ready"""
    global _last_main_model_use, _last_fast_model_use, _last_embedding_use, _last_reranker_use
    
    print("🔥 Warming up AI models (this ensures fast first query)...")
    
    # Mark all models as "used" so they get warmed
    now = time.time()
    _last_main_model_use = now
    _last_fast_model_use = now
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
        except asyncio.CancelledError:
            pass
        _warmup_task = None
    
    print("🔥 Model warmup service stopped")
