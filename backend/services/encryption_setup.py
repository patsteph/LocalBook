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


def _start(kind: str, fn, **kwargs) -> bool:
    """Run one long LB-11 operation on a background thread. False if one is
    already running — they all touch the same volume, so only one at a time.

    A background job rather than a long HTTP request: holding a connection for
    minutes makes WebKit's network process give up, and the user would read a
    690 MB copy that is still running as a crash.
    """
    global _job
    with _job_lock:
        if _job and _job.get("running"):
            return False
        report = em.MigrationReport(stage="queued")
        _job = {"running": True, "kind": kind, "report": report,
                "started_at": datetime.now(timezone.utc).isoformat()}

    def _run():
        global _job
        try:
            fn(report=report, **kwargs)
        except Exception as exc:
            logger.exception("[encrypt] %s crashed", kind)
            report.errors.append(f"unexpected error: {exc}")
            report.ok = False
        finally:
            with _job_lock:
                _job = dict(_job or {}, running=False,
                            finished_at=datetime.now(timezone.utc).isoformat())

    threading.Thread(target=_run, name=f"lb11-{kind}", daemon=True).start()
    return True


def start_prepare_job(*, skip_backup: bool = False) -> bool:
    return _start("encrypt", lambda **kw: em.prepare(skip_backup=skip_backup, **kw))


def start_decrypt_job() -> bool:
    from services import encryption_rollback

    return _start("decrypt", lambda **kw: encryption_rollback.prepare(**kw))


def start_export_job(destination_dir: Path) -> bool:
    from services import encryption_rollback

    return _start("export", lambda **kw: encryption_rollback.export(destination_dir, **kw))


def job_status() -> Optional[Dict[str, object]]:
    with _job_lock:
        if not _job:
            return None
        out = {k: v for k, v in _job.items() if k != "report"}
        out["report"] = _job["report"].as_dict()
        return out


# ── the startup prompt ──────────────────────────────────────────────────────
#
# How every user — the developer first — reaches the setup screen. Never an
# automatic migration: the recovery phrase and the backup destination need a
# person, and D11 enables encryption one machine at a time by choice.
#
# State lives BESIDE the data dir, like the encryption flag: per machine, and out
# of reach of LB-12's sync, which would otherwise let "don't ask again" on one Mac
# silence the offer on another that has never seen it.

PROMPT_NAME = ".encryption-prompt.json"
SNOOZE_DAYS = 7


def _prompt_path() -> Path:
    d = em._data_dir()
    return d.parent / f"{d.name}{PROMPT_NAME}"


def _prompt_state() -> Dict[str, object]:
    try:
        import json

        return json.loads(_prompt_path().read_text())
    except Exception:
        return {}


def _save_prompt_state(state: Dict[str, object]) -> None:
    import json

    _prompt_path().write_text(json.dumps(state, indent=2))


def prompt() -> Dict[str, object]:
    """What, if anything, to show at startup. At most one thing, most urgent first."""
    from services import volume_gate, volume_service

    state = _prompt_state()
    now = datetime.now(timezone.utc)
    encrypted = volume_gate.encryption_enabled() and volume_service.is_mounted()

    # 1. A switch the user asked for did not happen. Shown until acknowledged.
    last = em.last_apply()
    if last and not last.get("applied") and not encrypted \
            and last.get("at") != state.get("failure_acknowledged"):
        return {"show": True, "kind": "failed", "error": last.get("error"),
                "at": last.get("at")}

    # 2. Encrypted, but the old plaintext is still on disk — the protection is
    #    not real until it goes. Not dismissible: it is the migration's last step.
    if encrypted:
        copies = em.plaintext_copies()
        if copies:
            return {"show": True, "kind": "finish",
                    "bytes": sum(int(c.get("bytes") or 0) for c in copies)}
        return {"show": False}

    # 3. The offer. Quiet while a migration is running or staged.
    job = job_status()
    if em.pending() or (job and job.get("running")):
        return {"show": False}
    if state.get("dismissed"):
        return {"show": False}
    snoozed = state.get("snoozed_until")
    if snoozed:
        try:
            if datetime.fromisoformat(str(snoozed)) > now:
                return {"show": False}
        except ValueError:
            pass
    return {"show": True, "kind": "offer"}


def respond_to_prompt(action: str) -> Dict[str, object]:
    from datetime import timedelta

    state = _prompt_state()
    now = datetime.now(timezone.utc)
    if action == "snooze":
        state["snoozed_until"] = (now + timedelta(days=SNOOZE_DAYS)).isoformat()
    elif action == "dismiss":
        state["dismissed"] = now.isoformat()
    elif action == "acknowledge_failure":
        last = em.last_apply() or {}
        state["failure_acknowledged"] = last.get("at")
    else:
        raise ValueError(f"unknown action {action!r}")
    _save_prompt_state(state)
    return prompt()
