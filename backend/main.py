"""FastAPI main application"""
import multiprocessing
import os
import sys

# PyInstaller multiprocessing freeze support - must be at very top
if getattr(sys, 'frozen', False):
    multiprocessing.freeze_support()

# ── Fix SSL certificates for the bundled (PyInstaller) app + fresh macOS Python ──
# The frozen app's Python has no usable default CA bundle, so HTTPS (HuggingFace model
# downloads, FlashRank, etc.) fails with CERTIFICATE_VERIFY_FAILED. Point ssl/requests/httpx at a
# CA bundle that actually EXISTS on disk. The previous version used certifi.where() unconditionally,
# but in the frozen app certifi imports while its cacert.pem is NOT bundled → that path doesn't
# exist, and setdefault pinned a missing file. Verify existence, fall back, and OVERRIDE a broken
# pre-set value. Runs before any HTTPS.
def _pick_ca_bundle():
    candidates = []
    try:
        import certifi
        candidates.append(certifi.where())
    except Exception:
        pass
    candidates += ["/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"]
    return next((c for c in candidates if c and os.path.exists(c)), None)

_ca = _pick_ca_bundle()
if _ca:
    for _var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        _cur = os.environ.get(_var)
        if not _cur or not os.path.exists(_cur):   # override a missing/broken pre-set value
            os.environ[_var] = _ca

# ── LB-11: apply a prepared encryption migration, before ANYTHING else ──────
# Ahead of the volume gate, because a successful swap is what makes the gate
# find a mounted volume. Ahead of every `from api import ...` below, because
# those reach storage.database — by lifespan time the databases are open and
# the data directory cannot be moved.
#
# The plaintext copy is MOVED ASIDE and kept. Nothing here deletes anything.
try:
    from services.encryption_migration import apply_pending as _apply_encryption
    _enc = _apply_encryption()
    if _enc and _enc.get("applied"):
        print("=" * 72)
        print("🔐 YOUR DATA DIRECTORY IS NOW ENCRYPTED")
        print(f"    plaintext copy kept at: {_enc.get('plaintext_kept_at')}")
        print("    Check your notebooks, then remove it from Settings → Data Health.")
        print("=" * 72)
    elif _enc and _enc.get("needs_manual_recovery"):
        print("=" * 72)
        print("⚠️  ENCRYPTION SWAP FAILED AND COULD NOT BE ROLLED BACK")
        print(f"    Your data is intact at: {_enc.get('plaintext_at')}")
        print("    Move it back to the data directory to continue.")
        print("=" * 72)
    elif _enc:
        print(f"⚠️  encryption migration not applied: {_enc.get('error')}")
except Exception as _e:
    print(f"⚠️  encryption migration skipped: {_e}")

# The databases half of the post-swap check (LB-11 simplified flow). HERE, before
# anything opens a database, because this is the only moment the row counts
# cannot have drifted. The files half runs after startup (`_run_startup_tasks`).
try:
    from services import encryption_verify as _ev
    if _ev.needs_databases():
        _ev.verify_databases()
except Exception as _e:
    print(f"⚠️  post-swap database check skipped: {_e}")

# ── LB-11: apply a staged DEcryption (the escape hatch), same constraints ───
try:
    from services.encryption_rollback import apply_pending as _apply_decryption
    _dec = _apply_decryption()
    if _dec and _dec.get("applied"):
        print("=" * 72)
        print("🔓 ENCRYPTION IS OFF — the data directory is plaintext again")
        print(f"    encrypted image kept at: {_dec.get('image_kept_at')}")
        print("=" * 72)
    elif _dec:
        print(f"⚠️  decryption not applied: {_dec.get('error')}")
except Exception as _e:
    print(f"⚠️  decryption skipped: {_e}")

# ── LB-11: decide whether we may serve at all, before ANY store opens ───────
# Ordered before the restore pre-flight and before every `from api import ...`
# below, because importing those reaches `storage.database`. If the encrypted
# volume is not mounted, nothing may open, create or migrate anything — the app
# comes up in a locked state serving only a recovery screen.
try:
    from services.volume_gate import evaluate as _evaluate_volume_gate
    _gate = _evaluate_volume_gate()
    if _gate.locked:
        print("=" * 72)
        print("🔒 LOCALBOOK IS LOCKED — the encrypted volume is not open")
        print(f"    {_gate.reason}")
        print(f"    {_gate.detail}")
        print("    Your notebooks have NOT been touched.")
        print("=" * 72)
except Exception as _e:
    print(f"⚠️  volume gate check failed: {_e}")

# ── LB-10: apply a staged restore BEFORE anything opens a database ──────────
# This has to be the first real thing that happens. `storage.database.Database`
# opens the SQLite connection on first use, and importing the API modules below
# reaches it — so by the time the lifespan runs, the directory is already in
# use. Swapping it then would be the torn state a restore exists to escape.
#
# Deliberately quiet and non-fatal when there is nothing staged: the common case
# is every launch, forever.
try:
    from services.volume_gate import current as _gate_now
    if _gate_now().locked:
        # Unpacking a restore into an unmounted mount point would put a whole
        # data directory where the volume belongs, and the next successful
        # attach would then refuse because the mount point is not empty.
        raise RuntimeError("locked — a staged restore cannot be applied yet")
    from services.restore_service import apply_pending as _apply_pending_restore
    _restore_result = _apply_pending_restore()
    if _restore_result:
        if _restore_result.get("applied"):
            print("=" * 72)
            print("♻️  RESTORED FROM BACKUP")
            print(f"    archive:  {_restore_result.get('archive')}")
            print(f"    previous data kept at: {_restore_result.get('previous_data_kept_at')}")
            print("    Run a full re-index — the vector store is not in a backup (D19).")
            print("=" * 72)
        else:
            print(f"⚠️  staged restore could not be applied: {_restore_result.get('error')}")
except Exception as _e:
    print(f"⚠️  restore pre-flight skipped: {_e}")

# ── Rich logging: colored output + better tracebacks ──
from utils.logging_config import setup_logging
setup_logging()

# ── TLS trust: the keychain, not just certifi ────────────────────────────────
# The CA bundle picked above carries PUBLIC roots only. On a network that inspects HTTPS,
# every connection is re-signed by a private root that macOS trusts and certifi has never
# heard of, so curl works and Python does not. Route verification through the platform
# verifier instead. Must run before any service import constructs an HTTPS client, and
# before the first request either way — injection swaps `ssl.SSLContext` globally.
from services.hf_transport import install_system_trust
install_system_trust()

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
# The LLM Locker writes model/engine swaps to .env so they take effect in-process.
# On restart we ALWAYS purge that .env so users never get stuck with an OOM-inducing
# session config they can't recover from. The user's EXPLICITLY-SAVED default combo
# (user_preferences.json) is re-applied below — including the Wave-9 engine flags.
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    print("[SafeStart] Removing .env session overrides — reverting to the saved/default config")
    _env_path.unlink()

from config import settings

# ── Apply user-chosen permanent defaults ──────────────────────────────────────
# If the user saved a preferred combo via the Locker UI, apply it now.
# This runs AFTER .env purge + config load, so we start from known-good
# defaults and then overlay the user's validated choice.
import json as _json
_prefs_path = settings.data_dir / "user_preferences.json"

# Prefs schema v3 — must run HERE, before the restore loop below reads default_combo. It
# rewrites saved "ollama" role engines to "mlx" where the MLX weights are on disk; running it
# after the restore (where it used to live) applied the promotion a launch late, so the first
# launch on a new build still came up all-Ollama. Backs up first, never deletes a key,
# idempotent, never raises.
try:
    from storage.migrate_prefs_v2 import run as _migrate_prefs
    _migrate_prefs()
except Exception as e:
    print(f"⚠️ prefs migration skipped: {e}")

if _prefs_path.exists():
    try:
        _prefs = _json.loads(_prefs_path.read_text())
        _default_combo = _prefs.get("default_combo", {})
        # One attribute per role, each holding a checkpoint id. Before the v2.3.0 collapse a
        # role was a PAIR (an Ollama name + an mlx_* id) with an engine flag choosing between
        # them, so this loop had to skip the Ollama name whenever the role was on MLX. With
        # one engine that reduces to: restore what the user saved.
        #
        # The migration (run above, BEFORE this) is what guarantees the keys are already in
        # the new shape — a v3-or-older file still stores mlx_main_model etc.
        for _k in ("main_model", "fast_model", "vision_model", "image_model", "embedding_model"):
            if _default_combo.get(_k):
                setattr(settings, _k, _default_combo[_k])
        print(f"[SafeStart] Applied user default combo: "
              f"main={settings.main_model} fast={settings.fast_model}")
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

# One-shot: purge Cursor Style residue (feature removed in v2.3.0). Must run AFTER the SQLite
# migration (it reads those tables) and BEFORE the stores cache anything. Marker-guarded and
# never-raises — see the module docstring for why the catalog rows are actively harmful.
if settings.use_sqlite:
    try:
        from storage.migrate_purge_cursor import run as _purge_cursor
        _purge_cursor()
    except Exception as e:
        print(f"⚠️ cursor purge skipped: {e}")

# Initialize findings store before API imports (uses deferred init pattern)
from storage.findings_store import init_findings_store
init_findings_store(settings.data_dir)

# NOW import API modules — stores will read the (possibly corrected) use_sqlite flag
from api import notebooks, sources, chat, skills, audio, source_viewer, web, settings as settings_api, embeddings, timeline, export, reindex, memory, graph, constellation_ws, updates, content, exploration, quiz, visual, writing, voice, site_search, contradictions, credentials, browser, browser_transform, audio_llm, rag_health, health_portal, jobs, agent_browser, rlm, curator, collector, source_discovery, people, video, evaluator, flashcards, canvas_notes as canvas_notes_api, scan as scan_api, comparison, correspondent as correspondent_api, synthesis as synthesis_api, articles as articles_api, system as system_api, signals as signals_api, incidents as incidents_api, canvas as canvas_api, folders as folders_api, companions as companions_api, openai_compat, openai_audio, memory_bridge as memory_bridge_api, sync as sync_api
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

    # ── Post-swap file check (LB-11) ──────────────────────────────────────
    # Fire and forget, off the loop: hashing a large corpus must not hold up
    # startup (the wizard polls for the result).
    try:
        from services import encryption_verify
        if encryption_verify.needs_files():
            safe_create_task(asyncio.to_thread(encryption_verify.verify_files),
                             name="lb11-verify-files")
    except Exception as _e:
        logger.warning(f"[main] post-swap file check skipped: {_e}")

    # ── Data health (LB-10 item 6) ────────────────────────────────────────
    # Before the schema ledger, before anything writes. If the last run did not
    # exit cleanly, every database gets an integrity_check first. Non-fatal on
    # purpose: a corrupt database should stop the USER, not the process — if the
    # app refuses to boot they cannot reach the restore screen, which is the one
    # thing that would help.
    try:
        from services import data_health
        _integrity = await asyncio.to_thread(data_health.startup_check)
        if _integrity and not _integrity.get("ok"):
            print("=" * 72)
            print("⚠️  DATABASE INTEGRITY PROBLEMS after an unclean shutdown")
            for _db, _why in (_integrity.get("problems") or {}).items():
                print(f"    {_db}: {_why}")
            print("    Restore from a backup — Settings → Data Health.")
            print("=" * 72)
    except Exception as _e:
        logger.warning(f"[main] data-health startup check skipped: {_e}")

    # ── Data schema (LB-10) ───────────────────────────────────────────────
    # Before anything reads or writes a format. A failure here does NOT advance
    # the ledger and does not stop the app: the honest state is "running at the
    # version the data actually reached", which /health and Data Health report,
    # rather than a half-migrated store the app pretends is current.
    try:
        from services import migration_ledger
        _mig = await asyncio.to_thread(migration_ledger.run_pending)
        if _mig.get("ran"):
            print(f"[Startup] applied migrations: {', '.join(_mig['ran'])}")
        if _mig.get("failed"):
            print(f"[Startup] ⚠️  migration {_mig['failed']} FAILED: {_mig['error']}")
            logger.error("[main] migration %s failed: %s", _mig["failed"], _mig["error"])
    except Exception as _e:
        logger.error(f"[main] migration ledger could not run (non-fatal): {_e}")

    # ── Key custody (K-1) ─────────────────────────────────────────────────
    # Run the credential migration and the legacy-backup cleanup HERE rather
    # than lazily on first locker use. Both were originally triggered by
    # `credential_locker._ensure_initialized()`, which only fires when
    # something actually reads a credential — so on a machine with no IMAP
    # account and no saved site login, neither ever ran. The 2026-09-29 build
    # proved it: every cleanup gate passed and the `.pre-keyvault` file was
    # still sitting there two launches later.
    #
    # That matters because those backups are encrypted with the OLD key —
    # PBKDF2(hostname + username, a literal) — every input of which is public.
    # Leaving them undoes K-1 for exactly the data K-1 protects, and waiting
    # for the user to happen to open Settings is not a policy.
    #
    # Never fatal: a locker that cannot initialize must not stop the app.
    try:
        from services.credential_locker import credential_locker
        await asyncio.to_thread(credential_locker._ensure_initialized)
    except Exception as _e:
        print(f"[Startup] credential key custody deferred: {_e}")

    # Same shape, same reason: the legacy shared companion key is a PLAINTEXT
    # secret on disk, and its migration used to run only inside
    # `companion_keys.verify()` — i.e. only once a companion happened to call
    # in. On this machine that fired by luck; on one with no companion
    # connected it would have sat there indefinitely.
    try:
        from services import companion_keys
        await asyncio.to_thread(companion_keys.migrate_legacy_key)
    except Exception as _e:
        print(f"[Startup] companion key migration deferred: {_e}")

    # ── Banner ────────────────────────────────────────────────────────────
    # LB-10 item 7: an unfrozen process pointed at the real data dir has to say
    # so, every time. The hazard was never doing it deliberately — it was doing
    # it by accident and not noticing until something had been overwritten.
    try:
        import config as _config
        if getattr(_config, "DEV_USING_PRODUCTION_DATA", False):
            print("=" * 72)
            print("⚠️  DEV BUILD IS USING THE PRODUCTION DATA DIRECTORY")
            print(f"    {settings.data_dir}")
            print("    Writes here affect your real notebooks, credentials and keys.")
            print("    Unset LOCALBOOK_USE_PRODUCTION_DATA to use the dev sandbox.")
            print("=" * 72)
            logger.warning("[main] DEV BUILD USING PRODUCTION DATA DIR: %s", settings.data_dir)
    except Exception:
        pass

    print(f"🚀 LocalBook API starting on {settings.api_host}:{settings.api_port}")
    print(f"📁 Data directory: {settings.data_dir}")
    print(f"🔥 Models: {settings.main_model} (main), {settings.fast_model} (fast)")
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
        # Event-loop lag monitor — names the culprit when a sync call blocks the
        # loop, before a fatal stall silently trips the Tauri watchdog. Start it
        # first so it covers the rest of service startup too.
        from services.loop_monitor import loop_monitor
        await loop_monitor.start()
        # Fatal-freeze watchdog — faulthandler-based; dumps the blocking stack if
        # the loop freezes past the threshold (the silent-kill case loop_monitor
        # can't log live). Diagnostics only; does not prevent the freeze.
        from services.loop_watchdog import loop_watchdog
        await loop_watchdog.start()
        from services.stuck_source_recovery import stuck_source_recovery
        stuck_source_recovery.start_background_task()
        # Background Enrichment Worker — the single presence-aware, cancellable
        # queue that all deferred second-brain work runs through. Must start
        # before the schedulers/poller that enqueue onto it.
        from services.enrichment_worker import enrichment_worker
        await enrichment_worker.start()
        print("🌙 Enrichment worker started (presence-aware night shift)")

        # Linked Folders — polls watched directories and ingests what's new.
        # Enqueues onto the enrichment worker above, so it must start after it.
        from services.folder_watcher import folder_watcher
        folder_watcher.start_background_task()

        # Companion update checks — pinned installers must not mean frozen ones.
        from services.companion_updates import companion_update_checker
        companion_update_checker.start_background_task()

        # LB-10: nightly backup + restore drill. Does nothing until a
        # destination is set in Settings — there is no safe default.
        from services.backup_scheduler import nightly_backup
        nightly_backup.start()
        # Research jobs a companion started must survive a restart (Jocasta contract).
        try:
            from services import research_jobs
            _resumed = await asyncio.to_thread(research_jobs.resume_interrupted)
            for _job_id in _resumed:
                research_jobs.launch(_job_id)          # on the loop, not the worker thread
            if _resumed:
                logger.info(f"[main] resumed {len(_resumed)} research job(s)")
        except Exception as _e:
            logger.warning(f"[main] research job resume skipped: {_e}")
        # LB-11: lock if the encrypted volume vanishes mid-session.
        from services.volume_watch import volume_watch
        volume_watch.start()
        # LB-12: resume sync if this Mac had it on (listener + loop).
        try:
            from services.sync import service as _sync_service
            await _sync_service.startup()
        except Exception as _e:
            logger.warning(f"[main] sync startup skipped: {_e}")
        from services.memory_manager import memory_manager
        safe_create_task(memory_manager.start_scheduler(), name="memory-scheduler")
        print("📝 Memory consolidation manager started")
        from services.collection_scheduler import collection_scheduler
        safe_create_task(collection_scheduler.start(), name="collection-scheduler")
        print("📅 Collection scheduler started (first check in 2 min)")
        # Phase 6 — Correspondent IMAP poller. Singleton; reads enabled
        # accounts from credential_locker each cycle.
        from agents.correspondent import correspondent_agent
        safe_create_task(correspondent_agent.start(), name="correspondent-poller")
        print("📬 Correspondent IMAP poller started (5 min cadence)")
        # Phase 13 — weekly auto-journal (K). Singleton; wakes every 6h
        # and gates per-account via last_journal_at + 7-day cadence.
        from agents.weekly_journal_agent import weekly_journal_agent
        safe_create_task(weekly_journal_agent.start(), name="weekly-journal-scheduler")
        print("📅 Weekly journal scheduler started (6h cadence)")
        # Phase 4 Tier 2 / G (2026-06-10) — weekly per-sender digest
        # scheduler. Wakes every 6h and ships digests for senders in
        # weekly_digest mode when their digest_day matches today.
        from services.digest_composer import digest_scheduler
        safe_create_task(digest_scheduler.start(), name="digest-scheduler")
        print("📨 Digest scheduler started (6h cadence)")
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

        # Reconcile derived knowledge stores against the live notebook list —
        # drop entities/graph/communities left behind by notebooks deleted before
        # the delete-cascade existed (the "loaded N notebooks, only M live" drift).
        async def _reconcile_derived_stores():
            try:
                from storage.notebook_store import notebook_store
                live = {nb["id"] for nb in await notebook_store.list() if nb.get("id")}
                if not live:
                    return  # never reconcile against an empty/unloaded list
                from services.entity_extractor import entity_extractor
                from services.entity_graph import entity_graph
                from services.community_detection import community_detector
                dropped = (
                    entity_extractor.reconcile_notebooks(live)
                    + entity_graph.reconcile_notebooks(live)
                    + community_detector.reconcile_notebooks(live)
                )
                if dropped:
                    print(f"🧹 Reconciled derived stores — dropped {dropped} orphaned notebook entr(ies)")
            except Exception as e:
                print(f"⚠️ Derived-store reconcile failed (non-fatal): {e}")

        safe_create_task(_reconcile_derived_stores(), name="reconcile-derived-stores")

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


    # Curator Phase 1: start the event bus consumer loop. Agents emit
    # observability events post-action; brain consumer persists + logs.
    try:
        from services.curator_event_bus import event_bus
        await event_bus.start()
    except Exception as _e:
        logger.warning(f"[main] curator event bus start failed (non-fatal): {_e}")

    # LB-2: the MCP app mounted at /mcp carries its OWN lifespan, and that is
    # what starts its session manager. Mounting without running it gives a
    # server that accepts a connection and then fails at runtime. Entered here
    # and exited on the way out, around the same yield as everything else.
    #
    # NOTE this is fastmcp 2.x, where `http_app()` returns a Starlette app with
    # a lifespan. The older low-level SDK's `session_manager.run()` does not
    # exist on this version.
    _mcp_lifespan = None
    try:
        from services.mcp_server import lifespan_context as _mcp_lifespan_context
        _mcp_lifespan = _mcp_lifespan_context(app)
        await _mcp_lifespan.__aenter__()
        logger.info("[main] MCP server ready at /mcp")
    except Exception as _e:
        # Non-fatal: LocalBook itself must still start. A companion that cannot
        # reach /mcp gets a clear failure; the user's app does not.
        _mcp_lifespan = None
        logger.error(f"[main] MCP server failed to start (non-fatal): {_e}")

    yield

    if _mcp_lifespan is not None:
        try:
            await _mcp_lifespan.__aexit__(None, None, None)
        except Exception as _e:
            logger.warning(f"[main] MCP shutdown: {_e}")
    
    # Wait for startup task to complete if still running
    if _startup_task and not _startup_task.done():
        _startup_task.cancel()
        try:
            await _startup_task
        except asyncio.CancelledError as _e:
            logger.debug(f"[main] {type(_e).__name__}: {_e}")
    
    # ── Graceful shutdown: flush stores, cancel tasks, close connections ──
    print("👋 LocalBook API shutting down — flushing stores...")
    

    # Curator Phase 1: stop the event bus consumer loop cleanly.
    try:
        from services.curator_event_bus import event_bus
        await event_bus.stop()
    except Exception as _e:
        logger.debug(f"[main] curator event bus stop: {_e}")

    # Stop warmup task on shutdown
    await stop_warmup_task()

    # Stop the enrichment worker on shutdown
    from services.enrichment_worker import enrichment_worker
    await enrichment_worker.stop()

    # Close the shared headless chromium (svg/mermaid/slide renderers — S3/C4)
    try:
        from services.playwright_utils import shutdown_shared_browser
        await shutdown_shared_browser()
    except Exception as _e:
        logger.debug(f"[main] shared browser shutdown: {_e}")

    # Stop the event-loop lag monitor + fatal-freeze watchdog
    from services.loop_monitor import loop_monitor
    await loop_monitor.stop()
    from services.loop_watchdog import loop_watchdog
    await loop_watchdog.stop()

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
    
    # LB-10 item 6: written LAST, after the WAL flush. Its ABSENCE on the next
    # launch is what says the previous run did not get this far.
    try:
        from services import data_health
        data_health.mark_clean_shutdown()
    except Exception as _e:
        print(f"⚠️ could not record a clean shutdown: {_e}")

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
        # User-activity signal for idle-gating autonomous schedulers. Non-GET
        # requests are user actions that consume resources (chat, upload,
        # generate, config); GET polls + passive navigation don't count, so the
        # app can actually reach "idle" while open. Foreground generations also
        # mark activity directly (some stream via GET) — see foreground_guard.
        if request.method != "GET":
            try:
                from services.memory_steward import mark_user_activity
                mark_user_activity()
            except Exception:
                pass
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
# enforce defaults to True (production); flip via settings.auth_enforce
# (set AUTH_ENFORCE=false in ~/Library/Application Support/LocalBook/.env)
# while diagnosing 401 regressions, then flip back when fixed.
app.add_middleware(AppTokenAuthMiddleware, enforce=settings.auth_enforce)
# LB-11: added LAST so Starlette makes it OUTERMOST — it must not be bypassable
# by any other middleware, and it costs nothing once the gate is open.
from services.volume_gate import LockedGateMiddleware as _LockedGateMiddleware
app.add_middleware(_LockedGateMiddleware)
logger.info(f"[main] auth middleware enforce={settings.auth_enforce}")

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
app.include_router(signals_api.router, tags=["signals"])
app.include_router(incidents_api.router, tags=["incidents"])
app.include_router(comparison.router, prefix="/comparison", tags=["comparison"])
app.include_router(correspondent_api.router, prefix="/correspondent", tags=["correspondent"])
app.include_router(synthesis_api.router, prefix="/synthesis", tags=["synthesis"])
app.include_router(articles_api.router, prefix="/articles", tags=["articles"])
app.include_router(notebooks.router, prefix="/notebooks", tags=["notebooks"])
app.include_router(sources.router, prefix="/sources", tags=["sources"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(system_api.router, prefix="/system", tags=["system"])
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
app.include_router(folders_api.router, prefix="/folders", tags=["linked-folders"])
# Any error a route did not handle: written to backend.log WITH its traceback, and
# returned as a JSON `detail` the UI can show. Before this, FastAPI answered a bare
# "Internal Server Error" and the traceback went only to the console — on
# 2026-10-01 the Encrypt banner failed on the MBP with "Could not generate a
# recovery phrase" and backend.log said nothing at all (a missing package).
from fastapi import Request as _Request
from fastapi.responses import JSONResponse as _JSONResponse


@app.exception_handler(Exception)
async def _unhandled_error(request: _Request, exc: Exception):
    # exc_info=exc, not logger.exception(): a handler is not inside the `except`,
    # so .exception() would log the message and silently drop the traceback.
    logger.error(f"[main] unhandled error on {request.method} {request.url.path}: {exc!r}",
                 exc_info=exc)
    return _JSONResponse(status_code=500,
                         content={"detail": f"{type(exc).__name__}: {exc}"[:500]})


# LB-4 before companions + memory: Starlette matches in definition order, and a
# later wildcard on either router must not swallow these (cf. /companions/keys).
app.include_router(memory_bridge_api.router, tags=["memory-bridge"])
app.include_router(sync_api.router)
app.include_router(companions_api.router, tags=["companions"])
# K-1: recovery-phrase setup and key recovery. Without this the move off
# the old machine-derived key is a DOWNGRADE in durability — see api/keyvault.py.
from api import keyvault as keyvault_api
app.include_router(keyvault_api.router, tags=["keyvault"])
# LB-10: backup + verify. The destination is always outside the data dir.
from api import backup as backup_api
app.include_router(backup_api.router, tags=["backup"])
# LB-11: the encrypted volume and the recovery surface. These stay reachable
# while the app is locked — they are how the user gets back in.
from api import volume as volume_api
app.include_router(volume_api.router, tags=["volume"])
# OpenAI-compatible surface so companion tools can use LocalBook's engine
# instead of loading a second copy of the same model. Auth is the companion
# key, checked inside the router (see utils/auth_middleware EXEMPT_PREFIXES).
app.include_router(openai_compat.router, prefix="/v1", tags=["openai-compat"])
app.include_router(openai_audio.router, prefix="/v1", tags=["openai-compat"])
# LB-2: MCP for agent companions (Jocasta). Loopback-only, and gated on a
# companion key carrying scope `mcp` — both enforced in the ASGI middleware
# inside services/mcp_server.py, not here. Its lifespan is entered above.
try:
    from services.mcp_server import ExactMountPath, get_app as _mcp_app
    app.mount("/mcp", _mcp_app())
    # Exactly `/mcp` (no slash) must not 307 — Hermes posts there.
    app.add_middleware(ExactMountPath, path="/mcp")
except Exception as _e:
    logger.error(f"[main] could not mount /mcp (non-fatal): {_e}")
app.include_router(memory.router, tags=["memory"])
app.include_router(graph.router, tags=["knowledge-graph"])
app.include_router(constellation_ws.router, tags=["constellation"])
app.include_router(updates.router, tags=["updates"])
app.include_router(content.router, prefix="/content", tags=["content"])
app.include_router(exploration.router, tags=["exploration"])
app.include_router(canvas_api.router, tags=["canvas"])
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
    """Health check endpoint.

    Stays cheap and never raises: the Tauri shell polls this for readiness, and
    it is exempt from the app token, so anything slow or throwing here shows up
    as "LocalBook won't start".
    """
    out = {"status": "healthy"}
    try:
        from services.model_sizing import (
            RESIDENT_RESERVE_GB, budget_gb, external_reserve_gb, working_set_gb,
        )
        # LB-1: what LocalBook thinks it may use, and why. Three numbers rather
        # than one, because "budget 6.7 GB" on a 48 GB Mac is alarming until you
        # can see that 26 of it was deliberately handed to something else.
        out["memory"] = {
            "working_set_gb": round(working_set_gb(), 2),
            "resident_reserve_gb": RESIDENT_RESERVE_GB,
            "external_reserve_gb": external_reserve_gb(),
            "budget_gb": budget_gb(),
        }
        # The Jocasta contract reads these three at the TOP level: brainctl's
        # memory governor sizes Ornith from `resident_gb` (and assumed 9.5 GB
        # when it was missing).
        out["budget_gb"] = out["memory"]["budget_gb"]
        out["reserve_gb"] = out["memory"]["external_reserve_gb"]
    except Exception as exc:
        out["memory"] = {"error": str(exc)}
    try:
        import sys
        # What LocalBook holds on the GPU right now: MLX active memory plus its
        # buffer cache — both are unavailable to anything else. Only if MLX is
        # already loaded: /health must stay cheap, and 0 is the truth before then.
        if "mlx.core" in sys.modules:
            mx = sys.modules["mlx.core"]
            active = mx.get_active_memory()
            try:
                active += mx.get_cache_memory()
            except Exception:
                pass
            out["resident_gb"] = round(active / 1024 ** 3, 2)
        else:
            out["resident_gb"] = 0.0
    except Exception as exc:
        out["resident_gb"] = None
        out["resident_error"] = str(exc)
    try:
        from services import migration_ledger
        # The head is what two LB-12 peers compare before syncing: the one that
        # is behind pauses rather than applying records it cannot interpret.
        out["schema"] = {
            "version": migration_ledger.schema_version(),
            "ledger_head": migration_ledger.head(),
        }
    except Exception as exc:
        out["schema"] = {"error": str(exc)}
    try:
        from services.audio_codec import codec_ok
        # LB-3: speech needs no Homebrew. A False here on a built app means the
        # PyAV wheel did not make it into the bundle.
        out["codec_ok"] = codec_ok()
    except Exception as exc:
        out["codec_ok"] = False
        out["codec_error"] = str(exc)
    return out

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
