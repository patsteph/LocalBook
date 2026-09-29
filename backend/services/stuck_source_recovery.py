"""Stuck Source Recovery Service

Automatically detects and recovers sources stuck in "processing" status.
Runs on startup and periodically to prevent orphaned sources.

v1.1.0: Added 10-minute threshold for auto-recovery
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
import lancedb

from config import settings
from storage.source_store import source_store


# Configuration
STUCK_THRESHOLD_MINUTES = 10  # Sources stuck longer than this get recovered
CHECK_INTERVAL_MINUTES = 5    # How often to check for stuck sources


class StuckSourceRecovery:
    """Service to detect and recover stuck sources."""
    
    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._data_dir = settings.data_dir
    
    async def check_and_recover(self) -> Dict:
        """Check for stuck sources and attempt recovery.
        
        Returns summary of actions taken.
        """
        result = {
            "checked_at": datetime.now().isoformat(),
            "stuck_found": 0,
            "recovered": 0,
            "failed": 0,
            "details": []
        }
        
        try:
            # Read through the store's PUBLIC api, not `_load_data()`. That helper is the
            # JSON backend, and `settings.use_sqlite` has defaulted to True for a long
            # time — so this swept a `sources.json` that the app had stopped writing
            # (January, on the dev machine) and found nothing, ever. The net under an
            # interrupted capture was not weak, it was disconnected. Found 2026-09-25,
            # when /browser/capture started reporting success before the ingest ran and
            # made this sweep the thing that guarantees the capture still lands.
            grouped = await source_store.list_all()
            # Timezone-aware, because the stored timestamps are UTC (see below).
            threshold = datetime.now(timezone.utc) - timedelta(minutes=STUCK_THRESHOLD_MINUTES)

            all_sources = [s for sources in grouped.values() for s in sources]
            for source in all_sources:
                if source.get("status") != "processing":
                    continue
                source_id = source.get("id")
                if not source_id:
                    continue
                
                # Check if stuck (created more than threshold ago)
                created_at = source.get("created_at")
                if not created_at:
                    # No timestamp, assume stuck
                    is_stuck = True
                else:
                    try:
                        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        if created_dt.tzinfo is None:
                            # `source_store.create` writes `datetime.utcnow().isoformat()`
                            # — naive UTC — and overwrites whatever the caller passed. The
                            # old code stripped tzinfo and compared against a naive LOCAL
                            # `now()`, so on a UTC-5 machine every source looked five hours
                            # in the FUTURE and NOTHING was ever old enough to be stuck.
                            # Third of three independent faults that each, alone, disabled
                            # this sweep entirely.
                            created_dt = created_dt.replace(tzinfo=timezone.utc)
                        is_stuck = created_dt < threshold
                    except Exception:
                        is_stuck = True
                
                if not is_stuck:
                    continue
                
                result["stuck_found"] += 1
                title = source.get("title") or source.get("filename", "Unknown")
                
                # Attempt recovery
                recovery_result = await self._recover_source(source_id, source)
                
                if recovery_result["success"]:
                    result["recovered"] += 1
                    result["details"].append({
                        "source_id": source_id,
                        "title": title,
                        "action": recovery_result["action"],
                        "chunks": recovery_result.get("chunks", 0)
                    })
                    print(f"[StuckRecovery] Recovered: {title} ({recovery_result['action']})")
                else:
                    result["failed"] += 1
                    result["details"].append({
                        "source_id": source_id,
                        "title": title,
                        "action": "failed",
                        "error": recovery_result.get("error", "Unknown")
                    })
                    print(f"[StuckRecovery] Failed: {title} - {recovery_result.get('error')}")
            
            if result["stuck_found"] > 0:
                print(f"[StuckRecovery] Found {result['stuck_found']} stuck, recovered {result['recovered']}, failed {result['failed']}")
            
        except Exception as e:
            print(f"[StuckRecovery] Error during check: {e}")
            result["error"] = str(e)
        
        return result
    
    @staticmethod
    async def _notify(notebook_id: str, source_id: str, status: str, title: str,
                      chunks: int = 0, error: Optional[str] = None) -> None:
        """Push the outcome so the UI learns about it without a reload.

        A recovery that no surface hears about is half a recovery: the source becomes
        searchable, but the notebook's source-count badge and its source list keep showing
        the pre-recovery state until the window is reloaded. Never raises — a socket
        problem must not turn a successful recovery into a failed one.
        """
        try:
            from api.constellation_ws import notify_source_updated
            payload = {
                "notebook_id": notebook_id, "source_id": source_id,
                "status": status, "title": title, "chunks": chunks,
            }
            if error:
                payload["error"] = error[:100]
            await notify_source_updated(payload)
        except Exception as e:
            print(f"[StuckRecovery] Could not notify clients: {type(e).__name__}: {e}")

    async def _recover_source(self, source_id: str, source: Dict) -> Dict:
        """Attempt to recover a single stuck source.

        Strategy:
        1. If has content and 0 chunks -> re-ingest
        2. If has content and chunks exist in LanceDB -> mark completed
        3. If no content -> mark as failed
        """
        try:
            content = source.get("content", "")
            notebook_id = source.get("notebook_id")
            title = source.get("title") or source.get("filename", "Unknown")

            if not notebook_id:
                return {"success": False, "error": "No notebook_id"}

            # Check if chunks already exist in LanceDB
            chunks_in_db = await self._count_chunks_in_db(notebook_id, source_id)

            if chunks_in_db > 0:
                # Chunks exist, just update status
                await source_store.update(notebook_id, source_id, {
                    "status": "completed",
                    "chunks": chunks_in_db
                })
                await self._notify(notebook_id, source_id, "completed", title, chunks_in_db)
                return {"success": True, "action": "marked_completed", "chunks": chunks_in_db}

            if not content:
                # No content to ingest, mark as failed
                await source_store.update(notebook_id, source_id, {
                    "status": "failed",
                    "error": "No content available for ingestion"
                })
                await self._notify(notebook_id, source_id, "failed", title,
                                   error="No content available for ingestion")
                return {"success": True, "action": "marked_failed_no_content"}

            # Has content but no chunks - re-ingest
            chunks_created = await self._ingest_content(notebook_id, source_id, content, title, source)

            if chunks_created > 0:
                await source_store.update(notebook_id, source_id, {
                    "status": "completed",
                    "chunks": chunks_created
                })
                await self._notify(notebook_id, source_id, "completed", title, chunks_created)
                return {"success": True, "action": "re_ingested", "chunks": chunks_created}
            else:
                await source_store.update(notebook_id, source_id, {
                    "status": "failed",
                    "error": "Ingestion produced 0 chunks"
                })
                await self._notify(notebook_id, source_id, "failed", title,
                                   error="Ingestion produced 0 chunks")
                return {"success": True, "action": "marked_failed_no_chunks"}

        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _count_chunks_in_db(self, notebook_id: str, source_id: str) -> int:
        """Count existing chunks for a source in LanceDB."""
        try:
            db = lancedb.connect(str(self._data_dir / "lancedb"))
            table_name = f"notebook_{notebook_id}"
            
            if table_name not in db.table_names():
                return 0
            
            table = db.open_table(table_name)
            # Use filtered search instead of loading entire table into pandas
            try:
                results = table.search().where(f"source_id = '{source_id}'", prefilter=True).select(["source_id"]).limit(1000).to_list()
                return len(results)
            except Exception:
                # Fallback: count via schema scan (still cheaper than to_pandas)
                return 0
        except Exception as e:
            print(f"[StuckRecovery] Error counting chunks: {e}")
            return 0
    
    async def _ingest_content(
        self,
        notebook_id: str,
        source_id: str,
        content: str,
        title: str,
        source: Dict
    ) -> int:
        """Re-ingest through the CANONICAL path.

        This used to hand-roll the whole thing: `_chunk_text_smart`, `encode_async`, then
        `table.add()` with rows of `{vector, text, source_id, chunk_index, filename}`. Two
        problems with that. It skipped everything the real ingest does — `parent_text` for
        parent-context expansion above all, which v0.60 added and which a row built here
        simply did not have — so a recovered source was indexed WORSE than a normally
        ingested one, silently and permanently. And it bailed out when the notebook had no
        table yet, which is precisely the case where a first capture was interrupted.

        One implementation, one place to fix (the centralization rule). Any partial chunks
        are cleared first so a retry cannot double-index.
        """
        try:
            from services.rag_engine import rag_engine

            source_type = source.get("format") or source.get("type") or "web"
            if source_type in ["pdf", "docx", "pptx"]:
                source_type = "document"

            # A crash can leave a handful of chunks behind. Clearing first makes the
            # re-ingest idempotent rather than additive.
            try:
                await rag_engine.delete_source(notebook_id, source_id)
            except Exception as _e:
                print(f"[StuckRecovery] Could not clear partial chunks: {_e}")

            result = await rag_engine.ingest_document(
                notebook_id=notebook_id,
                source_id=source_id,
                text=content,
                filename=title,
                source_type=source_type,
            )
            return int((result or {}).get("chunks", 0))

        except Exception as e:
            print(f"[StuckRecovery] Ingestion error: {e}")
            return 0
    
    def start_background_task(self):
        """Start periodic background checking."""
        if self._running:
            return
        
        self._running = True
        from utils.tasks import safe_create_task
        self._task = safe_create_task(self._background_loop(), name="stuck-source-recovery")
        print(f"[StuckRecovery] Started background task (check every {CHECK_INTERVAL_MINUTES} min, threshold {STUCK_THRESHOLD_MINUTES} min)")
    
    def stop_background_task(self):
        """Stop the background task."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        print("[StuckRecovery] Stopped background task")
    
    async def _run_check(self):
        try:
            await self.check_and_recover()
        except Exception as e:
            print(f"[StuckRecovery] Background check error: {e}")

    def _enqueue_check(self, tier) -> None:
        """Enqueue the sweep on the worker — never run it inline (S1/C7)."""
        from services.enrichment_worker import enrichment_worker
        from services.enrichment_jobs import EnrichmentJob

        enrichment_worker.enqueue(EnrichmentJob(
            key="stuck-source-recovery",
            tier=tier,
            factory=self._run_check,
            label="stuck-source-recovery",
        ))

    async def _background_loop(self):
        """Cadence poll — enqueues the actual check on the enrichment worker.

        S1/C7 (2026-07-03): this was the ONE background timer loop the Night-Shift
        fold missed (absent from BACKGROUND_SCHEDULE.md). Same fold template as
        memory_manager: the loop keeps the cheap cadence check but execution routes
        through the presence-gated worker (tier=DEEP, coalesced by key), so recovery
        work can never collide with foreground use. Completes the one-traffic-cop goal.
        """
        from services.enrichment_jobs import JobTier

        await asyncio.sleep(30)  # Wait 30s after startup

        # One pass shortly after startup, at DAYDREAM rather than DEEP. A source still
        # marked `processing` when this process starts CANNOT have a task running for it —
        # whatever owned it died with the previous process — so startup is the least
        # ambiguous recovery signal there is. It is also the exact case a user hits when
        # the watchdog restarts the backend mid-capture, and since 2026-09-25
        # /browser/capture reports success BEFORE the ingest runs, that user has already
        # been told the page was captured. DEEP needs ~120s of continuous idle, which
        # someone actively browsing and capturing may not hand over for a long time;
        # DAYDREAM needs ~20s. The periodic sweep below stays DEEP.
        self._enqueue_check(JobTier.DAYDREAM)

        while self._running:
            # Rung C (Schedule Viewer): re-read the sweep cadence + enabled flag
            # from the schedule store each iteration so a user's edit lands on the
            # next cycle without a restart. Never raises → falls back to the const.
            from services.schedule_store import schedule_store
            if schedule_store.is_enabled("stuck-source-recovery"):
                self._enqueue_check(JobTier.DEEP)
            # Wait for next check (worker coalesces by key, so a slow drain
            # can't stack duplicate jobs).
            await asyncio.sleep(
                schedule_store.get_interval("stuck-source-recovery", CHECK_INTERVAL_MINUTES * 60))


# Singleton instance
stuck_source_recovery = StuckSourceRecovery()
