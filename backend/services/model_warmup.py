"""
Model Warmup Service

Keeps LLM and embedding models warm in memory to eliminate cold start latency.
Runs a background task that periodically pings the models with minimal requests.
"""
import asyncio
import httpx
from typing import Optional
from config import settings

# Background task reference
_warmup_task: Optional[asyncio.Task] = None
_should_run = True

# Warmup interval in seconds (ping every 45s to keep models in memory)
WARMUP_INTERVAL = 45


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
        # Import here to avoid circular imports
        from services.rag_engine import rag_service
        
        # This will load the model if not already loaded
        if rag_service.embedding_model is None:
            rag_service._load_embedding_model()
        
        # Encode a short text to keep it warm
        _ = rag_service.embedding_model.encode("warmup", show_progress_bar=False)
        return True
    except Exception as e:
        print(f"⚠️ Embedding warmup failed: {e}")
        return False


async def warmup_cycle():
    """Run one warmup cycle for all models"""
    # Warm up main LLM model
    main_ok = await warm_ollama_model(settings.ollama_model)
    
    # Warm up fast model
    fast_ok = await warm_ollama_model(settings.ollama_fast_model)
    
    # Warm up embedding model
    embed_ok = await warm_embedding_model()
    
    return main_ok, fast_ok, embed_ok


async def warmup_loop():
    """Background loop that keeps models warm"""
    global _should_run
    
    print("🔥 Starting model warmup service...")
    
    # Initial warmup on startup
    main_ok, fast_ok, embed_ok = await warmup_cycle()
    print(f"🔥 Initial warmup complete - Main: {'✓' if main_ok else '✗'}, Fast: {'✓' if fast_ok else '✗'}, Embed: {'✓' if embed_ok else '✗'}")
    
    # Periodic warmup
    while _should_run:
        await asyncio.sleep(WARMUP_INTERVAL)
        
        if not _should_run:
            break
            
        # Silent warmup (no logging unless there's an error)
        await warmup_cycle()


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
