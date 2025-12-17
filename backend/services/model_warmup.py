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
            # Import here to avoid circular imports
            from services.rag_engine import rag_service
            
            # This will load the model if not already loaded
            if rag_service.embedding_model is None:
                rag_service._load_embedding_model()
            
            # Encode a short text to keep it warm
            if rag_service.embedding_model:
                _ = rag_service.embedding_model.encode("warmup", show_progress_bar=False)
            return True
    except Exception as e:
        print(f"⚠️ Embedding warmup failed: {e}")
        return False


async def warm_reranker_model() -> bool:
    """Warm up the reranker model by loading it"""
    try:
        if not getattr(settings, 'use_reranker', True):
            return True  # Reranker disabled, skip
        
        from services.rag_engine import rag_service
        rag_service._load_reranker()
        return True
    except Exception as e:
        print(f"⚠️ Reranker warmup failed: {e}")
        return False


async def warmup_cycle(force_all: bool = False):
    """Run one warmup cycle for models that have been recently used"""
    now = time.time()
    main_ok = fast_ok = embed_ok = rerank_ok = True
    
    # Only warm main model if recently used (or forced on startup)
    if force_all or (now - _last_main_model_use < MODEL_IDLE_TIMEOUT):
        main_ok = await warm_ollama_model(settings.ollama_model)
    
    # Only warm fast model if recently used (or forced on startup)
    if force_all or (now - _last_fast_model_use < MODEL_IDLE_TIMEOUT):
        fast_ok = await warm_ollama_model(settings.ollama_fast_model)
    
    # Only warm embedding model if recently used (or forced on startup)
    if force_all or (now - _last_embedding_use < MODEL_IDLE_TIMEOUT):
        embed_ok = await warm_embedding_model()
    
    # Only warm reranker if recently used (or forced on startup)
    if force_all or (now - _last_reranker_use < MODEL_IDLE_TIMEOUT):
        rerank_ok = await warm_reranker_model()
    
    return main_ok, fast_ok, embed_ok, rerank_ok


async def warmup_loop():
    """Background loop that keeps models warm"""
    global _should_run, _last_main_model_use, _last_fast_model_use, _last_embedding_use, _last_reranker_use
    
    print("🔥 Starting model warmup service...")
    
    # Mark all models as "used" at startup so initial warmup happens
    now = time.time()
    _last_main_model_use = now
    _last_fast_model_use = now
    _last_embedding_use = now
    _last_reranker_use = now
    
    # Initial warmup on startup (force all)
    main_ok, fast_ok, embed_ok, rerank_ok = await warmup_cycle(force_all=True)
    print(f"🔥 Initial warmup complete - Main: {'✓' if main_ok else '✗'}, Fast: {'✓' if fast_ok else '✗'}, Embed: {'✓' if embed_ok else '✗'}, Rerank: {'✓' if rerank_ok else '✗'}")
    
    # Periodic warmup - only warms recently-used models
    while _should_run:
        await asyncio.sleep(WARMUP_INTERVAL)
        
        if not _should_run:
            break
            
        # Only warm models that have been used recently
        await warmup_cycle(force_all=False)


async def start_warmup_task():
    """Start the background warmup task"""
    global _warmup_task, _should_run
    _should_run = True
    _warmup_task = asyncio.create_task(warmup_loop())


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
