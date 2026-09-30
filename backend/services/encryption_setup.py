"""The encryption setup screen's backend: pre-flight checks and a background job.

LB-11. `encryption_migration` does the work; this is what the UI polls while it
happens. Split out so the migration module stays about the migration.
"""

from __future__ import annotations

import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from services import encryption_migration as em

logger = logging.getLogger(__name__)


# Room the copy needs beyond the data itself: bands, the image's own overhead,
# and enough left over that the Mac is not pushed to a full disk mid-copy.
FREE_SPACE_HEADROOM_BYTES = 2 * 1024 ** 3


def preflight() -> Dict[str, object]:
    """Everything that must be true before the migration is offered.

    Reported as named checks, not a single yes/no, so the screen can say which
    step is missing and link to it.
    """
    from services import keyvault, volume_service

    source = em._data_dir()
    checks: Dict[str, Dict[str, object]] = {}

    already = volume_service.is_mounted(source)
    checks["not_already_encrypted"] = {"ok": not already}

    staged = em.pending()
    checks["nothing_staged"] = {"ok": staged is None, "pending": staged}

    checks["recovery_phrase"] = {"ok": keyvault.has_recovery_key()}

    dest = None
    try:
        from services import backup_scheduler

        dest = backup_scheduler.configured_destination()
    except Exception:
        pass
    checks["backup_destination"] = {
        "ok": dest is not None and Path(dest).is_dir(),
        "path": str(dest) if dest else None,
    }

    data_bytes = em._tree_bytes(source) if source.is_dir() else 0
    try:
        free = shutil.disk_usage(source.parent).free
    except OSError:
        free = 0
    needed = data_bytes + FREE_SPACE_HEADROOM_BYTES
    # A backup onto the same disk costs the data size again.
    try:
        if dest is not None and Path(dest).stat().st_dev == source.parent.stat().st_dev:
            needed += data_bytes
    except OSError:
        pass
    checks["free_space"] = {"ok": free >= needed, "free_bytes": free,
                            "needed_bytes": needed}

    job = job_status()
    checks["not_running"] = {"ok": not (job and job.get("running"))}

    return {
        "ready": all(c["ok"] for c in checks.values()),
        "checks": checks,
        "data_bytes": data_bytes,
        "data_dir": str(source),
    }


_job_lock = threading.Lock()
_job: Optional[Dict[str, object]] = None


def start_prepare_job(*, skip_backup: bool = False) -> bool:
    """Run `prepare` on a background thread. False if one is already running.

    A background job rather than a long HTTP request: holding a connection for
    minutes makes WebKit's network process give up, and the user would read a
    690 MB copy that is still running as a crash.
    """
    global _job
    with _job_lock:
        if _job and _job.get("running"):
            return False
        report = em.MigrationReport(stage="queued")
        _job = {"running": True, "report": report,
                "started_at": datetime.now(timezone.utc).isoformat()}

    def _run():
        global _job
        try:
            em.prepare(skip_backup=skip_backup, report=report)
        except Exception as exc:
            logger.exception("[encrypt] prepare crashed")
            report.errors.append(f"unexpected error: {exc}")
            report.ok = False
        finally:
            with _job_lock:
                _job = dict(_job or {}, running=False,
                            finished_at=datetime.now(timezone.utc).isoformat())

    threading.Thread(target=_run, name="encrypt-prepare", daemon=True).start()
    return True


def job_status() -> Optional[Dict[str, object]]:
    with _job_lock:
        if not _job:
            return None
        out = {k: v for k, v in _job.items() if k != "report"}
        out["report"] = _job["report"].as_dict()
        return out
