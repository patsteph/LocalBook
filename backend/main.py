"""FastAPI main application"""
import multiprocessing
import os
import sys

# PyInstaller multiprocessing freeze support - must be at very top
if getattr(sys, 'frozen', False):
    multiprocessing.freeze_support()

# ── Fix SSL certificates for fresh macOS Python installs ──
# Python 3.12+ from Homebrew may lack CA bundle; certifi provides it.
# Must run before any HTTPS downloads (HuggingFace, FlashRank, etc.)
try:
    import certifi
    _ca = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", _ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _ca)
    os.environ.setdefault("CURL_CA_BUNDLE", _ca)
except ImportError:
    if os.path.exists("/etc/ssl/cert.pem"):
        os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/cert.pem")

# ── Rich logging: colored output + better tracebacks ──
from utils.logging_config import setup_logging
setup_logging()

# ── Quick-exit CLI flags (must run before any heavy imports) ──
if "--verify-kokoro" in sys.argv or "--verify-tts" in sys.argv:
    failed = []
    for mod in ["kokoro_mlx", "mlx", "misaki", "phonemizer", "segments", "csvw",
                "language_tags", "rdflib", "soundfile", "loguru",
                "num2words", "dlinfo", "spacy", "thinc", "blis",
                "cymem", "murmurhash", "preshed", "srsly", "catalogue",
                "isodate"]:
        try:
            __import__(mod)
        except Exception as e:
            failed.append(f"{mod}: {e}")
    if failed:
        print("TTS BUNDLE VERIFICATION FAILED:")
        for f in failed:
            print(f"  ✗ {f}")
        sys.exit(1)
    else:
        print("✓ mlx-audio TTS bundle verified — all imports OK")
        sys.exit(0)

import asyncio
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# ── Safe Startup: purge LLM Locker .env overrides ────────────────────────────
# The LLM Locker writes model swaps to .env so they take effect in-process.
# On restart we ALWAYS revert to known-good defaults (OLMo + Phi4) so users
# never get stuck with an OOM-inducing config they can't recover from.
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    print("[SafeStart] Removing .env overrides — reverting to known-good model config")
    _env_path.unlink()

from config import settings

# ── Apply user-chosen permanent defaults ──────────────────────────────────────
# If the user saved a preferred combo via the Locker UI, apply it now.
# This runs AFTER .env purge + config load, so we start from known-good
# defaults and then overlay the user's validated choice.
import json as _json
_prefs_path = settings.data_dir / "user_preferences.json"
if _prefs_path.exists():
    try:
        _prefs = _json.loads(_prefs_path.read_text())
        _default_combo = _prefs.get("default_combo", {})
        if _default_combo.get("main_model"):
            settings.ollama_model = _default_combo["main_model"]
        if _default_combo.get("fast_model"):
            settings.ollama_fast_model = _default_combo["fast_model"]
        if _default_combo.get("vision_model"):
            settings.vision_model = _default_combo["vision_model"]
        print(f"[SafeStart] Applied user default combo: {settings.ollama_model} + {settings.ollama_fast_model}")
    except Exception as e:
        print(f"[SafeStart] Failed to load user preferences, using built-in defaults: {e}")

from utils.tasks import safe_create_task
from utils.diagnostics import install_signal_handlers, start_heartbeat, stop_heartbeat, record_endpoint

# Layer 1: Install crash signal handlers before anything else
install_signal_handlers()

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
from api import notebooks, sources, chat, skills, audio, source_viewer, web, settings as settings_api, embeddings, timeline, export, reindex, memory, graph, constellation_ws, updates, content, exploration, quiz, visual, writing, voice, site_search, contradictions, credentials, browser, browser_transform, audio_llm, rag_health, health_portal, jobs, agent_browser, rlm, findings, curator, collector, source_discovery, people, video, evaluator, flashcards, canvas_notes as canvas_notes_api, scan as scan_api
from api.capture import capture_router
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

    # ── Step 2b: Activity-ledger backfill (one-shot per install) ──────────
    # Phase B (2026-05-22) introduced the activity_ledger; notebooks created
    # before that date have empty ledger state and the new views (stagnation,
    # source_reputation, voice scoreboard, etc.) return "no data" for them.
    # This step synthesizes back-dated events from source_store +
    # collection_history.json so old notebooks immediately show real history
    # in the new UI. Guarded by a sentinel file — runs once per install,
    # never blocks startup on failure.
    async def _maybe_backfill():
        sentinel = settings.data_dir / ".activity_ledger_backfilled"
        if sentinel.exists():
            return
        try:
            # Import is lazy because the script lives in backend/scripts/
            # which is not on the module path during normal operation.
            # PyInstaller bundles it via --add-data (see build_backend.sh).
            run_backfill = None
            try:
                from scripts.backfill_activity_ledger import run_backfill as _rb
                run_backfill = _rb
            except ImportError:
                # PyInstaller / frozen bundle layout — load by path.
                import importlib.util
                candidate = Path(__file__).resolve().parent / "scripts" / "backfill_activity_ledger.py"
                if not candidate.exists():
                    print("[startup] backfill script not bundled — skipping")
                    sentinel.write_text(datetime.utcnow().isoformat())
                    return
                spec = importlib.util.spec_from_file_location("backfill_activity_ledger", candidate)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                run_backfill = module.run_backfill

            def _backfill_status(_status, message, progress):
                # Map per-notebook progress (0-100) into our 12-15% slice
                # so the splash bar moves visibly but we don't lie about
                # where in startup we are.
                scaled = 12 + int(progress * 0.03)
                set_startup_status("migrating", message, scaled)

            set_startup_status(
                "migrating",
                "Migrating notebook activity history... a few minutes max.",
                12,
            )
            grand = await run_backfill(status_callback=_backfill_status)
            print(
                f"[startup] activity ledger backfill: {grand['notebooks_processed']} notebooks, "
                f"+{grand['sources_added']} sources, +{grand['runs_added']} runs, "
                f"+{grand['approvals_added']} approvals"
            )
            sentinel.write_text(datetime.utcnow().isoformat())
        except Exception as e:
            # Non-fatal: backfill is a convenience, not a correctness gate.
            # Notebooks still work; they just won't have historical ledger
            # context until enough new activity accrues.
            print(f"[startup] activity ledger backfill failed (non-fatal): {e}")

    await _step("migrating", "Checking notebook history...", 12, _maybe_backfill())

    # ── Step 3: Verify data directory ─────────────────────────────────────
    await _step("checking", "Verifying data directory...", 15)

    # ── Step 4: Check AI models ───────────────────────────────────────────
    await _step("checking", "Checking AI models...", 30,
                run_all_startup_checks(status_callback=set_startup_status))

    # ── Step 5: Checking embeddings ───────────────────────────────────────
    await _step("checking", "Checking embedding compatibility...", 55)

    # ── Step 5b: Warm the macOS Keychain ──────────────────────────────────
    # Proactively read API keys so macOS prompts for the login password NOW
    # (while the user is watching startup) instead of later when the
    # background scheduler tries to use Brave Search with a locked keychain.
    async def _warm_keychain():
        try:
            import asyncio as _asyncio
            from services.keychain_manager import get_api_key_async
            keys_found = []
            for key_name in ("brave_api_key", "youtube_api_key"):
                val = await get_api_key_async(key_name)
                if val:
                    keys_found.append(key_name)
            if keys_found:
                print(f"🔑 Keychain unlocked — {len(keys_found)} API key(s) ready for background collection")
            else:
                print("🔑 Keychain checked — no API keys configured (collection will use RSS/news feeds only)")
        except Exception as e:
            print(f"🔑 Keychain warm-up skipped: {e}")

    await _step("checking", "Checking API keys...", 60, _warm_keychain())

    # ── Step 6: Starting background services ──────────────────────────────
    async def _start_services():
        from services.stuck_source_recovery import stuck_source_recovery
        stuck_source_recovery.start_background_task()
        from services.memory_manager import memory_manager
        safe_create_task(memory_manager.start_scheduler(), name="memory-scheduler")
        print("📝 Memory consolidation manager started")
        from services.collection_scheduler import collection_scheduler
        safe_create_task(collection_scheduler.start(), name="collection-scheduler")
        print("📅 Collection scheduler started (first check in 2 min)")
        from services.coaching_insights import check_stale_insights_on_startup
        safe_create_task(check_stale_insights_on_startup(), name="coaching-insights-check")
        print("🧠 Coaching insights staleness check queued")
        # Note: shallow scrape remediation flags (remediated_shallow_scrape) are
        # intentionally preserved across restarts. Sources that were attempted and
        # failed to improve stay marked so the Health Portal doesn't re-report them.
        # Users can manually retry via the "Fix Shallow Sources" button which
        # clears flags before re-attempting.

        # One-time migration: re-mark shallow collector sources whose flags were
        # previously cleared by the old startup code (removed in v1.6.1).
        async def _migrate_shallow_flags():
            try:
                from config import settings as _s
                sentinel = _s.data_dir / ".shallow_flag_migration_done"
                if sentinel.exists():
                    return
                from storage.database import get_db
                conn = get_db().get_connection()
                cursor = conn.execute(
                    "UPDATE sources SET metadata_json = "
                    "json_set(metadata_json, '$.remediated_shallow_scrape', true) "
                    "WHERE json_extract(metadata_json, '$.collected_by') = 'collector' "
                    "AND LENGTH(content) < 900 "
                    "AND url IS NOT NULL "
                    "AND (json_extract(metadata_json, '$.remediated_shallow_scrape') IS NULL "
                    "     OR json_extract(metadata_json, '$.remediated_shallow_scrape') = false)"
                )
                if cursor.rowcount > 0:
                    print(f"🔧 Migration: marked {cursor.rowcount} previously-attempted shallow sources as remediated")
                sentinel.write_text("done")
            except Exception as e:
                print(f"⚠️ Shallow flag migration failed (non-fatal): {e}")

        safe_create_task(_migrate_shallow_flags(), name="migrate-shallow-flags")

    await _step("starting", "Starting background services...", 75, _start_services())

    # ── Step 7: Preparing workspace ───────────────────────────────────────
    await _step("starting", "Preparing workspace...", 90)

    # ── Mark startup complete — UI appears ────────────────────────────────
    mark_startup_complete()
    print(f"✅ LocalBook v{CURRENT_VERSION} ready!")

    # ── Deferred: warm models in background (first query may be ~3s slower) ─
    async def _deferred_warmup():
        from api.updates import mark_models_ready
        try:
            print("🔥 Warming AI models in background...")
            await initial_warmup()
            mark_models_ready()
            await start_warmup_task()
            print("🔥 All models warm and ready")
        except Exception as e:
            print(f"⚠️ Background warmup error: {e}")
            mark_models_ready()  # Mark ready even on error so features aren't gated forever
            await start_warmup_task()

    # Warm models in background — Ollama (external) + embed/reranker (memory-gated).
    # Whisper and Kokoro TTS models lazy-download on first use via their services
    # (mlx_whisper.transcribe and audio_llm._load_model respectively).
    # Pre-downloading them here caused concurrent memory spikes + SSL stalls.
    safe_create_task(_deferred_warmup(), name="model-warmup")


# Background task reference for cleanup
_startup_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for the application.
    
    IMPORTANT: We start the HTTP server FIRST, then run startup tasks in background.
    This allows the frontend to poll /updates/startup-status for progress updates.
    """
    global _startup_task

    # P0.1a (2026-05-15): generate per-launch app token. Not enforced yet
    # (warn-only middleware lands in P0.1f). Initialized before any startup
    # task so later code paths can read it.
    try:
        from utils.token import initialize_app_token
        _tok = initialize_app_token(settings.data_dir)
        logger.info(f"[main] app token generated ({len(_tok)} chars), written to .app_token (0o600)")
    except Exception as _e:
        logger.error(f"[main] failed to initialize app token (non-fatal until P0.1f): {_e}")

    # Start startup tasks in background - HTTP server will be ready immediately
    _startup_task = safe_create_task(_run_startup_tasks(), name="startup-tasks")
    
    # Layer 2: Start heartbeat logger (30s interval)
    start_heartbeat()

    # v1.8.0: Auto-start llama-server sidecar when the active combo uses one
    # (or when user_preferences.json → sidecar.auto_start is truthy). Runs
    # in a background task so a slow Metal init never blocks FastAPI boot.
    try:
        from services.sidecar_manager import maybe_auto_start_on_boot
        safe_create_task(maybe_auto_start_on_boot(), name="sidecar-autostart")
    except Exception as _e:
        logger.debug(f"[main] sidecar auto-start skipped: {_e}")

    # Curator Phase 1: start the event bus consumer loop. Agents emit
    # observability events post-action; brain consumer persists + logs.
    try:
        from services.curator_event_bus import event_bus
        await event_bus.start()
    except Exception as _e:
        logger.warning(f"[main] curator event bus start failed (non-fatal): {_e}")

    yield
    
    # Wait for startup task to complete if still running
    if _startup_task and not _startup_task.done():
        _startup_task.cancel()
        try:
            await _startup_task
        except asyncio.CancelledError as _e:
            logger.debug(f"[main] {type(_e).__name__}: {_e}")
    
    # ── Graceful shutdown: flush stores, cancel tasks, close connections ──
    print("👋 LocalBook API shutting down — flushing stores...")
    
    # v1.8.0: Stop sidecar cleanly so we don't leak llama-server across restarts
    try:
        from services.sidecar_manager import sidecar_manager
        await sidecar_manager.stop(grace_seconds=5.0)
    except Exception as _e:
        logger.debug(f"[main] sidecar stop error: {_e}")

    # Curator Phase 1: stop the event bus consumer loop cleanly.
    try:
        from services.curator_event_bus import event_bus
        await event_bus.stop()
    except Exception as _e:
        logger.debug(f"[main] curator event bus stop: {_e}")

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
    
    # Flush SQLite WAL to prevent corruption from SIGTERM/SIGKILL
    if settings.use_sqlite:
        try:
            from storage.database import get_db
            db = get_db()
            conn = db.get_connection()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            print("💾 SQLite WAL flushed")
        except Exception as e:
            print(f"⚠️ SQLite flush failed: {e}")
    
    # Stop diagnostics heartbeat
    stop_heartbeat()
    
    print("👋 LocalBook API shutdown complete")

app = FastAPI(
    title="LocalBook API",
    description="Backend API for LocalBook - Your local NotebookLM alternative",
    version=CURRENT_VERSION,
    lifespan=lifespan
)

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import logging
logger = logging.getLogger(__name__)

class DiagnosticsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        record_endpoint(f"{request.method} {request.url.path}")
        return await call_next(request)

# Middleware ordering note (P0.1f, 2026-05-21):
#   Starlette runs middleware in REVERSE order of add_middleware() calls.
#   The LAST added is the OUTERMOST wrapper (runs first on request, last
#   on response). For the auth middleware's 401 response to include CORS
#   headers — so the browser lets JS see the 401 and our refresh-retry
#   logic can run — CORS must be the OUTERMOST. So: add Diagnostics,
#   then Auth, then CORS last.
app.add_middleware(DiagnosticsMiddleware)

# P0.1f Stage 2 RE-ENABLED 2026-05-21 with the actual root cause fixed:
# middleware ordering. Previously CORS was innermost — auth middleware's
# 401 response bypassed it, so the browser blocked the entire response
# (no Access-Control-Allow-Origin header). JS never saw the 401, retry
# never fired, app hung. Now CORS is OUTERMOST (added last below) so 401s
# pass through it and the browser allows JS to read them.
from utils.auth_middleware import AppTokenAuthMiddleware
app.add_middleware(AppTokenAuthMiddleware, enforce=True)

# CORS middleware — added LAST so it's the OUTERMOST wrapper. This is
# critical: it ensures error responses (401 from auth, etc.) include
# CORS headers, so browsers don't block the response and our retry logic
# can actually see the status code.
#
# P0.1g (2026-05-21): narrowed origins from "*" to specific allowlist.
# Combined with P0.1f token enforcement, this means random browser tabs
# (not in this list) can't even READ responses from the backend, AND
# can't make credentialed requests. Defense in depth.
#
# Allowed origins:
#   - tauri://localhost              → main app webview
#   - http://localhost:1420          → Vite dev server (if used)
#   - http://localhost:8000          → loopback (Tauri Rust → backend)
#   - chrome-extension://<id>        → pinned LocalBook Companion extension
#     The ID is deterministic via the manifest "key" field (P0.1d) — same
#     across all installs of OUR signed extension.
from config import settings as _cfg_for_cors
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "tauri://localhost",
        "http://localhost:1420",
        "http://localhost:8000",
        f"chrome-extension://{_cfg_for_cors.extension_id}",
    ],
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
app.include_router(canvas_notes_api.router, tags=["canvas-notes"])
app.include_router(collector.router, tags=["collector"])
app.include_router(source_discovery.router, tags=["source-discovery"])
app.include_router(people.router, tags=["people"])
app.include_router(evaluator.router, tags=["evaluator"])
app.include_router(flashcards.router, tags=["flashcards"])
app.include_router(scan_api.router, prefix="/scan", tags=["scan"])
app.include_router(capture_router, prefix="/capture", tags=["capture"])

# P0.1e (2026-05-15): extension token-bootstrap endpoint (Origin-checked).
from api import auth as auth_api
app.include_router(auth_api.router)

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
