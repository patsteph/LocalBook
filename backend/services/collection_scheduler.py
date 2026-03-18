"""
Collection Scheduler - Background job runner for Collectors

Manages periodic collection runs for all active notebooks.
Uses asyncio for non-blocking background execution.
Persists last-run timestamps to disk so schedules survive app restarts.
"""
import asyncio
import json
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List

from agents.collector import get_collector
from storage.notebook_store import notebook_store
from config import settings

logger = logging.getLogger(__name__)

# How long to wait after app launch before the first check (seconds).
# Gives models time to warm up and avoids contention on startup.
STARTUP_DELAY_SECONDS = 120  # 2 minutes

# How often the scheduler wakes up to check for due collections (seconds).
CHECK_INTERVAL_SECONDS = 600  # 10 minutes

# Cooldown between consecutive collection runs in the same cycle (seconds).
# Prevents Ollama from being hammered by back-to-back LLM-heavy pipelines.
STAGGER_DELAY_SECONDS = 180  # 3 minutes between runs

# Maximum number of collections to run in a single check cycle.
# Remaining overdue notebooks will be picked up in the next cycle.
MAX_COLLECTIONS_PER_CYCLE = 3


class CollectionScheduler:
    """
    Background scheduler for running Collector jobs.
    Each notebook's Collector runs according to its configured schedule.
    Last-run timestamps are persisted to disk so the schedule is
    maintained across app restarts.
    """

    def __init__(self):
        self._running = False
        self._last_runs: Dict[str, datetime] = {}
        self._task: Optional[asyncio.Task] = None
        self._state_path = Path(settings.data_dir) / "collection_scheduler_state.json"
        self._load_state()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Load persisted last-run timestamps from disk."""
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
            for notebook_id, iso_str in data.get("last_runs", {}).items():
                self._last_runs[notebook_id] = datetime.fromisoformat(iso_str)
            logger.info(f"Loaded scheduler state: {len(self._last_runs)} notebooks tracked")
        except Exception as e:
            logger.warning(f"Could not load scheduler state (will start fresh): {e}")

    def _save_state(self) -> None:
        """Persist last-run timestamps to disk."""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "last_runs": {
                    k: v.isoformat() for k, v in self._last_runs.items()
                }
            }
            self._state_path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning(f"Could not save scheduler state: {e}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background scheduler."""
        if self._running:
            return

        self._running = True
        from utils.tasks import safe_create_task
        self._task = safe_create_task(self._run_loop(), name="collection-scheduler-loop")
        logger.info("Collection scheduler started")

    def stop(self) -> None:
        """Stop the background scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
        self._save_state()
        logger.info("Collection scheduler stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        """Main scheduler loop — runs until stopped."""
        # Wait for models to warm up before first check
        logger.info(f"Scheduler waiting {STARTUP_DELAY_SECONDS}s before first check...")
        await asyncio.sleep(STARTUP_DELAY_SECONDS)

        while self._running:
            try:
                await self._check_and_run_collections()
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Collection scheduler error: {e}")
                await asyncio.sleep(60)

    async def _check_and_run_collections(self) -> None:
        """Check all notebooks and run collections that are due.

        Staggering strategy:
        1. Fresh notebooks (never run) get a synthetic last_run so only one
           fires per cycle — avoids the "all-at-once" stampede on first boot.
        2. Already-due notebooks are sorted by staleness (most overdue first)
           and capped at MAX_COLLECTIONS_PER_CYCLE per wake-up.
        3. A STAGGER_DELAY_SECONDS pause is inserted between consecutive runs
           so Ollama can breathe.
        """
        notebooks = await notebook_store.list()

        # ── Phase 1: identify eligible notebooks ──
        due_list: List[dict] = []    # [{notebook_id, nb_name, freq, staleness}]
        fresh_count = 0              # notebooks that have never been collected

        for notebook in notebooks:
            notebook_id = notebook["id"]
            try:
                collector = get_collector(notebook_id)
                config = collector.get_config()

                # Skip unconfigured collectors
                if not config.intent or not config.intent.strip():
                    continue

                # Skip manual-only collection mode
                mode_val = config.collection_mode.value if hasattr(config.collection_mode, 'value') else str(config.collection_mode)
                if mode_val == "manual":
                    continue

                # Skip manual schedule frequency
                if config.schedule.get("frequency") == "manual":
                    continue

                freq = config.schedule.get("frequency", "daily")
                nb_name = notebook.get("name", notebook.get("title", notebook_id[:8]))

                # ── Fresh notebook: assign a staggered synthetic last_run ──
                if notebook_id not in self._last_runs:
                    # Stagger by giving each fresh notebook a synthetic last_run
                    # offset so only one becomes "due" per check cycle.
                    interval = self.INTERVALS.get(freq, timedelta(days=1))
                    offset = timedelta(seconds=CHECK_INTERVAL_SECONDS * fresh_count)
                    synthetic = datetime.utcnow() - interval + offset
                    self._last_runs[notebook_id] = synthetic
                    self._save_state()
                    fresh_count += 1
                    logger.info(
                        f"Fresh notebook '{nb_name}' — assigned staggered schedule "
                        f"(will be due in ~{offset.total_seconds():.0f}s)"
                    )
                    # Re-check: is this one actually due NOW after staggering?
                    if not self._is_collection_due(notebook_id, config):
                        continue

                # Check if collection is due
                if self._is_collection_due(notebook_id, config):
                    interval = self.INTERVALS.get(freq, timedelta(days=1))
                    staleness = (datetime.utcnow() - self._last_runs[notebook_id]) - interval
                    due_list.append({
                        "notebook_id": notebook_id,
                        "nb_name": nb_name,
                        "freq": freq,
                        "staleness": staleness.total_seconds(),
                    })
            except Exception as e:
                logger.error(f"Error checking notebook {notebook_id}: {e}")

        if not due_list:
            return

        # ── Phase 2: sort by staleness (most overdue first), cap per cycle ──
        due_list.sort(key=lambda x: x["staleness"], reverse=True)
        to_run = due_list[:MAX_COLLECTIONS_PER_CYCLE]
        skipped = len(due_list) - len(to_run)

        if skipped > 0:
            logger.info(f"Scheduler: {len(due_list)} due, running {len(to_run)} this cycle ({skipped} deferred to next cycle)")
        else:
            logger.info(f"Scheduler: {len(to_run)} collection(s) due this cycle")

        # ── Phase 3: run with stagger delay ──
        for idx, entry in enumerate(to_run):
            if not self._running:
                break

            notebook_id = entry["notebook_id"]
            nb_name = entry["nb_name"]
            freq = entry["freq"]

            # Stagger: pause before 2nd, 3rd, etc. runs
            if idx > 0:
                logger.info(f"Stagger pause: waiting {STAGGER_DELAY_SECONDS}s before next collection...")
                print(f"[SCHEDULER] Cooling down {STAGGER_DELAY_SECONDS}s before next run...")
                await asyncio.sleep(STAGGER_DELAY_SECONDS)

            logger.info(f"Scheduled collection due for '{nb_name}' ({notebook_id[:8]}) — frequency: {freq}")
            print(f"[SCHEDULER] Running scheduled collection for '{nb_name}' (freq={freq})")

            try:
                result = await self._run_collection(notebook_id)
                self._last_runs[notebook_id] = datetime.utcnow()
                self._save_state()

                approved = result.get("items_approved", 0)
                pending = result.get("items_pending", 0)
                collected = result.get("items_collected", 0)
                print(f"[SCHEDULER] '{nb_name}' done: {collected} found, {approved} approved, {pending} pending")
            except Exception as e:
                logger.error(f"Collection failed for '{nb_name}': {e}")

    # ------------------------------------------------------------------
    # Frequency logic
    # ------------------------------------------------------------------

    INTERVALS = {
        "hourly": timedelta(hours=1),
        "every_2_hours": timedelta(hours=2),
        "every_4_hours": timedelta(hours=4),
        "every_8_hours": timedelta(hours=8),
        "twice_daily": timedelta(hours=12),
        "daily": timedelta(days=1),
        "every_3_days": timedelta(days=3),
        "weekly": timedelta(weeks=1),
    }

    def _is_collection_due(self, notebook_id: str, config) -> bool:
        """Check if a notebook's collection is due based on its frequency.
        
        If stagnation has reached 'plateau' severity (15+ days with no growth),
        the effective interval is doubled to conserve resources on saturated topics.
        """
        last_run = self._last_runs.get(notebook_id)

        if last_run is None:
            return True

        frequency = config.schedule.get("frequency", "daily")
        interval = self.INTERVALS.get(frequency, timedelta(days=1))
        
        # Plateau auto-frequency reduction: stretch interval 2x when saturated
        try:
            from services.collection_history import detect_stagnation
            stag = detect_stagnation(notebook_id)
            if stag.get("severity") == "plateau" and getattr(config, 'auto_expand', True):
                interval = interval * 2
                logger.info(f"[Scheduler] Plateau detected for {notebook_id} — stretching interval to {interval}")
        except Exception:
            pass
        
        return datetime.utcnow() - last_run >= interval

    # ------------------------------------------------------------------
    # Run collection
    # ------------------------------------------------------------------

    async def _run_collection(self, notebook_id: str) -> Dict[str, Any]:
        """Run a scheduled collection via the full Curator pipeline.

        Uses the same intelligent path as 'Collect Now' but WITHOUT the
        2-minute deadline, giving background runs more time for thorough
        contextualization and judgment.
        """
        try:
            from agents.curator import curator
            result = await curator.assign_immediate_collection(
                notebook_id=notebook_id,
                deadline_seconds=None,  # No deadline for scheduled runs
                trigger="scheduled",
            )
            logger.info(
                f"Scheduled collection for {notebook_id}: "
                f"{result.get('items_approved', 0)} approved, "
                f"{result.get('items_pending', 0)} pending"
            )
            return result
        except Exception as e:
            logger.error(f"Scheduled collection failed for {notebook_id}: {e}")
            return {
                "notebook_id": notebook_id,
                "items_collected": 0,
                "error": str(e),
            }

    async def run_immediate(self, notebook_id: str) -> Dict[str, Any]:
        """Run collection immediately for a specific notebook."""
        result = await self._run_collection(notebook_id)
        self._last_runs[notebook_id] = datetime.utcnow()
        self._save_state()
        return result

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Get scheduler status including next-due info."""
        next_due = {}
        for nb_id, last in self._last_runs.items():
            try:
                collector = get_collector(nb_id)
                config = collector.get_config()
                freq = config.schedule.get("frequency", "daily")
                interval = self.INTERVALS.get(freq, timedelta(days=1))
                due_at = last + interval
                next_due[nb_id] = {
                    "frequency": freq,
                    "last_run": last.isoformat(),
                    "next_due": due_at.isoformat(),
                    "overdue": datetime.utcnow() > due_at,
                }
            except Exception:
                next_due[nb_id] = {"last_run": last.isoformat()}

        return {
            "running": self._running,
            "notebooks_tracked": len(self._last_runs),
            "last_runs": {
                k: v.isoformat() for k, v in self._last_runs.items()
            },
            "schedule_details": next_due,
        }


# Singleton instance
collection_scheduler = CollectionScheduler()
