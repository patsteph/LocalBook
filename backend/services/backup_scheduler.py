"""The nightly backup and its restore drill.

LB-10 items 3 and 5. The mechanics of backup, restore and verification live in
`backup_service` and `restore_service`; this is only the thing that runs them
without being asked, because the plan's gate is **seven consecutive green
nights** and a drill nobody schedules never turns green at all.

Order matters, and it is: **back up → prune → drill.**

  * Prune AFTER the backup succeeds, never before. Pruning first means a failed
    backup costs an old archive too, at the exact moment you can least afford
    to lose one.
  * Drill LAST, against whatever the newest archive now is — so the thing being
    verified is the thing just written, not last night's.

**Blobs are off by default for the nightly run.** A real data directory is
~600 MB with generated audio and ~80 MB without. Seven daily plus four weekly
archives is the difference between ~6 GB and ~900 MB, and the audio is
regenerable from the notebooks it came from. A manual backup still defaults to
including them.

**Nothing runs until a destination is set.** There is no sensible default: the
plan requires a folder OUTSIDE the data directory — iCloud Drive, an external
disk, a NAS — and guessing one would either fail silently or write the backup
inside the thing it is backing up.

Cadence comes from `schedule_store` on every iteration (`nightly-backup`), so
an edit takes effect next cycle without a restart, and the loop never raises.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

SCHEDULE_ID = "nightly-backup"

# The DEFAULT, not the live cadence — `schedule_store` is the authority and is
# re-read at the top of every iteration.
DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60

# Never wake more often than this, whatever the override says. A backup is
# minutes of disk work; an over-eager cadence would be indistinguishable from a
# runaway loop.
MIN_INTERVAL_SECONDS = 60 * 60


def configured_destination() -> Optional[Path]:
    """The user's chosen backup folder, or None.

    Returns None rather than guessing. A wrong default here writes the archive
    inside the data directory it is meant to survive.
    """
    try:
        from config import settings

        raw = (getattr(settings, "backup_destination", "") or "").strip()
    except Exception:
        return None
    return Path(raw).expanduser() if raw else None


async def run_once(destination: Optional[Path] = None) -> Dict[str, object]:
    """One cycle: back up, prune, drill. Safe to call by hand."""
    from services import backup_service, restore_service

    destination = destination or configured_destination()
    if destination is None:
        return {"skipped": "no backup destination configured"}
    if not destination.is_dir():
        # Not created for the user: a mistyped path would silently start
        # filling a directory nobody meant to exist, on a disk that may not be
        # the one they wanted.
        return {"skipped": f"{destination} is not a folder"}

    out: Dict[str, object] = {"destination": str(destination)}

    try:
        from config import settings

        include_blobs = bool(getattr(settings, "backup_include_blobs_nightly", False))
    except Exception:
        include_blobs = False

    try:
        result = await asyncio.to_thread(
            backup_service.create_backup, destination, include_blobs=include_blobs
        )
        out["backup"] = {
            "path": str(result.path),
            "bytes": result.bytes_written,
            "seconds": round(result.seconds, 2),
            "recoverable_with_phrase": bool(result.manifest.get("recovery_public_key")),
        }
    except Exception as exc:
        # The drill still runs: last night's archive is the one that matters if
        # tonight's failed, and the record should say whether it is still good.
        logger.error("[nightly-backup] backup failed: %s", exc)
        out["backup"] = {"error": str(exc)}

    if "error" not in out.get("backup", {}):
        try:
            out["pruned"] = await asyncio.to_thread(backup_service.prune, destination)
        except Exception as exc:
            logger.warning("[nightly-backup] prune failed: %s", exc)
            out["pruned"] = {"error": str(exc)}

    try:
        out["drill"] = await asyncio.to_thread(restore_service.run_drill, destination)
    except Exception as exc:
        logger.error("[nightly-backup] drill failed: %s", exc)
        out["drill"] = {"error": str(exc)}

    return out


class NightlyBackup:
    """Schedules `run_once` through the enrichment worker at NIGHT tier."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._task is not None:
            return
        self._running = True
        from utils.tasks import safe_create_task

        self._task = safe_create_task(self._loop(), name="nightly-backup")
        logger.info("[nightly-backup] scheduler started")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        from services.enrichment_jobs import EnrichmentJob, JobTier
        from services.enrichment_worker import enrichment_worker

        while self._running:
            interval = DEFAULT_INTERVAL_SECONDS
            try:
                from services.schedule_store import schedule_store

                # Re-read every iteration so an edit lands on the next cycle
                # without a restart. Never raises — falls back to the default.
                if schedule_store.is_enabled(SCHEDULE_ID) and configured_destination():
                    enrichment_worker.enqueue(EnrichmentJob(
                        key=SCHEDULE_ID,
                        # NIGHT: it reads every database and writes hundreds of
                        # megabytes. It must never compete with someone using
                        # the app.
                        tier=JobTier.NIGHT,
                        factory=run_once,
                        label="nightly-backup",
                    ))
                interval = schedule_store.get_interval(SCHEDULE_ID, DEFAULT_INTERVAL_SECONDS)
            except Exception as exc:
                logger.warning("[nightly-backup] loop error (continuing): %s", exc)
            try:
                await asyncio.sleep(max(MIN_INTERVAL_SECONDS, float(interval)))
            except asyncio.CancelledError:
                break


nightly_backup = NightlyBackup()
