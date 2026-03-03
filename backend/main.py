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

from config import settings

# ── SQLite migration: MUST run before store singletons are created ──────────
# Stores read settings.use_sqlite at import time and cache it. If we delay
# migration to a background task, the frontend sees empty SQLite tables.
# Running it here (synchronously, before API imports) guarantees:
#   1. Database schema is created and populated from JSON files
#   2. If migration fails, use_sqlite is reverted BEFORE stores read it
if settings.use_sqlite:
    try:
        from storage.migrate_json_to_sqlite import run_migration
        run_migration()
        print("💾 SQLite storage backend active")
    except Exception as e:
        print(f"⚠️ SQLite migration failed, falling back to JSON: {e}")
        settings.use_sqlite = False

# Initialize findings store before API imports (uses deferred init pattern)
from storage.findings_store import init_findings_store
init_findings_store(settings.data_dir)

# NOW import API modules — stores will read the (possibly corrected) use_sqlite flag
from api import notebooks, sources, chat, skills, audio, source_viewer, web, settings as settings_api, embeddings, timeline, export, reindex, memory, graph, constellation_ws, updates, content, exploration, quiz, visual, writing, voice, site_search, contradictions, credentials, agent, browser, browser_transform, audio_llm, rag_health, health_portal, jobs, agent_browser, rlm, findings, curator, collector, source_discovery, people, video
from api.updates import check_if_upgrade, set_startup_status, mark_startup_complete, CURRENT_VERSION
from services.model_warmup import initial_warmup, start_warmup_task, stop_warmup_task
from services.startup_checks import run_all_startup_checks
from services.migration_manager import check_and_migrate_on_startup

async def _run_startup_tasks():
    """Run all startup tasks in background after HTTP server is ready.
    
    This allows the frontend to poll /updates/startup-status while we work.
    Each visual step has a minimum display duration (MIN_STEP_MS) so the
    frontend's 1-second polling interval reliably catches every message.
    Real work runs concurrently with the timer via asyncio.gather, so no
    artificial delay is added when the work itself takes longer.
    """
    MIN_STEP_MS = 1.2  # seconds — guarantees each step is visible to 1s poller

    async def _step(status: str, message: str, progress: int, work=None):
        """Show a status step, do optional work, guarantee minimum visibility."""
        set_startup_status(status, message, progress)
        print(f"[Startup] {message}")
        if work is not None:
            # Run real work and minimum timer in parallel
            await asyncio.gather(work, asyncio.sleep(MIN_STEP_MS))
        else:
            await asyncio.sleep(MIN_STEP_MS)

    # ── Banner ────────────────────────────────────────────────────────────
    print(f"🚀 LocalBook API starting on {settings.api_host}:{settings.api_port}")
    print(f"📁 Data directory: {settings.data_dir}")
    print(f"🤖 LLM Provider: {settings.llm_provider}")
    print(f"🔥 Models: {settings.ollama_model} (think), {settings.ollama_fast_model} (fast)")
    print(f"💾 Storage: {'SQLite' if settings.use_sqlite else 'JSON files'}")
    
    # ── Step 1: Upgrade check ─────────────────────────────────────────────
    is_upgrade, previous_version = check_if_upgrade()
    if is_upgrade:
        print(f"⬆️ Upgrading from v{previous_version} to v{CURRENT_VERSION}")
        await _step("upgrading", f"Upgrading from v{previous_version}...", 5)
    else:
        await _step("starting", "Starting LocalBook...", 5)

    # ── Step 2: Data migration ────────────────────────────────────────────
    migration_status = await check_and_migrate_on_startup()
    if migration_status.get("needs_migration"):
        migration_type = migration_status.get('migration_type')
        print(f"📦 Migration needed: {migration_type}")
        from services.migration_manager import migration_manager
        async for update in migration_manager.migrate():
            progress = update.get("progress", 0)
            status_msg = update.get("status", "Migrating...")
            scaled_progress = 10 + int(progress * 0.3)
            set_startup_status("migrating", status_msg, scaled_progress)
            print(f"[Migration] {status_msg} ({progress}%)")
            if update.get("error"):
                print(f"[Migration] ERROR: {update.get('error')}")
            if update.get("warning"):
                print(f"[Migration] WARNING: {update.get('warning')}")

    # ── Step 3: Verify data directory ─────────────────────────────────────
    await _step("checking", "Verifying data directory...", 15)

    # ── Step 4: Check AI models ───────────────────────────────────────────
    await _step("checking", "Checking AI models...", 30,
                run_all_startup_checks(status_callback=set_startup_status))

    # ── Step 5: Checking embeddings ───────────────────────────────────────
    await _step("checking", "Checking embedding compatibility...", 55)

    # ── Step 6: Starting background services ──────────────────────────────
    async def _start_services():
        from services.stuck_source_recovery import stuck_source_recovery
        stuck_source_recovery.start_background_task()
        from services.memory_manager import memory_manager
        asyncio.create_task(memory_manager.start_scheduler())
        print("📝 Memory consolidation manager started")
        from services.collection_scheduler import collection_scheduler
        asyncio.create_task(collection_scheduler.start())
        print("📅 Collection scheduler started (first check in 2 min)")
        from services.coaching_insights import check_stale_insights_on_startup
        asyncio.create_task(check_stale_insights_on_startup())
        print("🧠 Coaching insights staleness check queued")

    await _step("starting", "Starting background services...", 75, _start_services())

    # ── Step 7: Preparing workspace ───────────────────────────────────────
    await _step("starting", "Preparing workspace...", 90)

    # ── Mark startup complete — UI appears ────────────────────────────────
    mark_startup_complete()
    print(f"✅ LocalBook v{CURRENT_VERSION} ready!")

    # ── Deferred: warm models in background (first query may be ~3s slower) ─
    async def _deferred_warmup():
        try:
            print("🔥 Warming AI models in background...")
            await initial_warmup()
            await start_warmup_task()
            print("🔥 All models warm and ready")
        except Exception as e:
            print(f"⚠️ Background warmup error: {e}")
            await start_warmup_task()
    asyncio.create_task(_deferred_warmup())

    # Pre-download MLX Whisper model in background (no-op if already cached)
    async def _predownload_whisper():
        try:
            from huggingface_hub import snapshot_download
            import os
            repo_id = "mlx-community/whisper-base-mlx"
            cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
            repo_dir = os.path.join(cache_dir, "models--mlx-community--whisper-base-mlx")
            if os.path.exists(repo_dir):
                print(f"🎤 Whisper model already cached")
                return
            print(f"🎤 Pre-downloading Whisper model ({repo_id})...")
            await asyncio.to_thread(snapshot_download, repo_id=repo_id)
            print(f"🎤 Whisper model ready")
        except Exception as e:
            print(f"🎤 Whisper pre-download skipped: {e}")
    asyncio.create_task(_predownload_whisper())

    # Pre-download LFM2.5-Audio model in background (no-op if already cached)
    async def _predownload_audio_llm():
        try:
            from huggingface_hub import snapshot_download
            import os
            repo_id = "LiquidAI/LFM2.5-Audio-1.5B"
            cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
            repo_dir = os.path.join(cache_dir, "models--LiquidAI--LFM2.5-Audio-1.5B")
            if os.path.exists(repo_dir):
                print(f"🔊 LFM2.5-Audio model already cached")
                return
            print(f"🔊 Pre-downloading LFM2.5-Audio model ({repo_id})...")
            await asyncio.to_thread(snapshot_download, repo_id=repo_id)
            print(f"🔊 LFM2.5-Audio model ready")
        except Exception as e:
            print(f"🔊 LFM2.5-Audio pre-download skipped: {e}")
    asyncio.create_task(_predownload_audio_llm())


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
    
    # Stop memory manager on shutdown
    from services.memory_manager import memory_manager
    memory_manager.stop_scheduler()
    
    # Stop collection scheduler on shutdown
    from services.collection_scheduler import collection_scheduler
    collection_scheduler.stop()
    
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
app.include_router(video.router, prefix="/video", tags=["video"])
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
app.include_router(browser_transform.router, tags=["browser-transform"])
app.include_router(audio_llm.router, tags=["audio-llm"])
if settings.debug_mode:
    app.include_router(rag_health.router, tags=["rag-health"])
app.include_router(health_portal.router, tags=["health-portal"])
app.include_router(jobs.router, tags=["jobs"])
app.include_router(agent_browser.router, tags=["agent-browser"])
app.include_router(rlm.router, tags=["rlm"])
app.include_router(findings.router, tags=["findings"])
app.include_router(curator.router, tags=["curator"])
app.include_router(collector.router, tags=["collector"])
app.include_router(source_discovery.router, tags=["source-discovery"])
app.include_router(people.router, tags=["people"])

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
