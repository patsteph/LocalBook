"""FastAPI main application"""
import multiprocessing
import sys

# PyInstaller multiprocessing freeze support - must be at very top
if getattr(sys, 'frozen', False):
    multiprocessing.freeze_support()

import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from api import notebooks, sources, chat, skills, audio, source_viewer, web, settings as settings_api, embeddings, timeline, export, reindex, memory, graph, constellation_ws, updates, content, exploration, quiz, visual, writing, voice, site_search, contradictions, credentials, agent, browser, audio_llm, rag_health, health_portal
from api.updates import check_if_upgrade, set_startup_status, mark_startup_complete, CURRENT_VERSION
from config import settings
from services.model_warmup import initial_warmup, start_warmup_task, stop_warmup_task
from services.startup_checks import run_all_startup_checks
from services.migration_manager import check_and_migrate_on_startup

async def _run_startup_tasks():
    """Run all startup tasks in background after HTTP server is ready.
    
    This allows the frontend to poll /updates/startup-status while we work.
    """
    print(f"🚀 LocalBook API starting on {settings.api_host}:{settings.api_port}")
    print(f"📁 Data directory: {settings.data_dir}")
    print(f"🤖 LLM Provider: {settings.llm_provider}")
    print(f"🔥 Models: {settings.ollama_model} (think), {settings.ollama_fast_model} (fast)")
    
    # Check if this is an upgrade
    is_upgrade, previous_version = check_if_upgrade()
    if is_upgrade:
        print(f"⬆️ Upgrading from v{previous_version} to v{CURRENT_VERSION}")
        set_startup_status("upgrading", f"Upgrading from v{previous_version}...", 5)
    else:
        set_startup_status("starting", "Starting LocalBook...", 5)
    
    # v0.60: Check for data migration needs and run migration with progress updates
    migration_status = await check_and_migrate_on_startup()
    if migration_status.get("needs_migration"):
        migration_type = migration_status.get('migration_type')
        print(f"📦 Migration needed: {migration_type}")
        
        # Import migration manager and run with progress updates
        from services.migration_manager import migration_manager
        async for update in migration_manager.migrate():
            progress = update.get("progress", 0)
            status_msg = update.get("status", "Migrating...")
            # Scale migration progress to 10-40% of startup
            scaled_progress = 10 + int(progress * 0.3)
            set_startup_status("migrating", status_msg, scaled_progress)
            print(f"[Migration] {status_msg} ({progress}%)")
            
            # Check for errors
            if update.get("error"):
                print(f"[Migration] ERROR: {update.get('error')}")
            if update.get("warning"):
                print(f"[Migration] WARNING: {update.get('warning')}")
    
    # Run comprehensive startup checks (data migration, models, embeddings)
    await run_all_startup_checks(status_callback=set_startup_status)
    
    # Warm up models (blocks until ready)
    set_startup_status("starting", "Warming up AI models...", 85)
    await initial_warmup()
    
    # Start background task to keep models warm (periodic keep-alive)
    set_startup_status("starting", "Starting background services...", 95)
    await start_warmup_task()
    
    # Mark startup complete
    mark_startup_complete()
    print(f"✅ LocalBook v{CURRENT_VERSION} ready!")


# Background task reference for cleanup
_startup_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for the application.
    
    IMPORTANT: We start the HTTP server FIRST, then run startup tasks in background.
    This allows the frontend to poll /updates/startup-status for progress updates.
    """
    global _startup_task
    
    # Start startup tasks in background - HTTP server will be ready immediately
    _startup_task = asyncio.create_task(_run_startup_tasks())
    
    yield
    
    # Wait for startup task to complete if still running
    if _startup_task and not _startup_task.done():
        _startup_task.cancel()
        try:
            await _startup_task
        except asyncio.CancelledError:
            pass
    
    # Stop warmup task on shutdown
    await stop_warmup_task()
    
    # Save RAG metrics on shutdown
    from services.rag_metrics import rag_metrics
    rag_metrics.force_save()
    
    print("👋 LocalBook API shutting down")

app = FastAPI(
    title="LocalBook API",
    description="Backend API for LocalBook - Your local NotebookLM alternative",
    version=CURRENT_VERSION,
    lifespan=lifespan
)

# CORS middleware
# Allow all origins since backend binds to localhost only (not network-exposed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(notebooks.router, prefix="/notebooks", tags=["notebooks"])
app.include_router(sources.router, prefix="/sources", tags=["sources"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(skills.router, prefix="/skills", tags=["skills"])
app.include_router(audio.router, prefix="/audio", tags=["audio"])
app.include_router(source_viewer.router, prefix="/source-viewer", tags=["source-viewer"])
app.include_router(web.router, prefix="/web", tags=["web"])
app.include_router(settings_api.router, prefix="/settings", tags=["settings"])
app.include_router(embeddings.router, prefix="/embeddings", tags=["embeddings"])
app.include_router(timeline.router, prefix="/timeline", tags=["timeline"])
app.include_router(export.router, prefix="/export", tags=["export"])
app.include_router(reindex.router, prefix="/reindex", tags=["reindex"])
app.include_router(memory.router, tags=["memory"])
app.include_router(graph.router, tags=["knowledge-graph"])
app.include_router(constellation_ws.router, tags=["constellation"])
app.include_router(updates.router, tags=["updates"])
app.include_router(content.router, prefix="/content", tags=["content"])
app.include_router(exploration.router, tags=["exploration"])
app.include_router(quiz.router, tags=["quiz"])
app.include_router(visual.router, tags=["visual"])
app.include_router(writing.router, tags=["writing"])
app.include_router(voice.router, tags=["voice"])
app.include_router(site_search.router, tags=["site-search"])
app.include_router(contradictions.router, tags=["contradictions"])
app.include_router(credentials.router, tags=["credentials"])
app.include_router(agent.router, tags=["agent"])
app.include_router(browser.router, tags=["browser"])
app.include_router(audio_llm.router, tags=["audio-llm"])
app.include_router(rag_health.router, tags=["rag-health"])
app.include_router(health_portal.router, tags=["health-portal"])

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "LocalBook API",
        "version": CURRENT_VERSION,
        "docs": "/docs"
    }

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    
    # Use uvicorn.run() directly - more reliable in PyInstaller bundles
    # than creating Server instance manually
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="warning",
        loop="asyncio"  # Explicitly use asyncio loop for PyInstaller compatibility
    )
