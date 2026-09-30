"""Turning encryption off, and exporting a decrypted copy — LB-11's escape hatch.

The plan's rollback. An encrypted volume nobody can get back out of is a trap,
and the test matrix requires the result to open in 2.4.0 — i.e. to be an
ordinary data directory, byte-for-byte the shape the app has always used.

It mirrors the migration, in reverse, and for the same reason: the data dir is
the mount point, and the backend holds live WALs inside it.

  Phase 1, app running — `prepare()`
      Copy the mounted volume out to a plaintext directory BESIDE the data dir,
      verify it (row counts + hashes), write a marker. Nothing live changes.

  Phase 2, at startup — `apply_pending()`
      Catch up whatever changed since phase 1, detach the volume, put the
      plaintext directory where the mount point was, drop the flag.

**The encrypted image is kept.** Deleting it is a separate, confirmed act
(`discard_image`), for the same reason the migration keeps the plaintext: the
user should see their notebooks come back before the other copy goes.

`export()` is the one-phase variant: the same verified copy to a folder the user
chooses, and nothing else changes — encryption stays on.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from services import encryption_migration as em

logger = logging.getLogger(__name__)

MARKER_NAME = ".decryption-pending.json"
LAST_APPLY_NAME = ".decryption-last-apply.json"
STAGED_PREFIX = "LocalBook.decrypted-"


def _marker_path() -> Path:
    d = em._data_dir()
    return d.parent / f"{d.name}{MARKER_NAME}"


def _last_apply_path() -> Path:
    d = em._data_dir()
    return d.parent / f"{d.name}{LAST_APPLY_NAME}"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _copy_out(source: Path, dest: Path, report: em.MigrationReport) -> bool:
    """Verified copy of the volume's contents to `dest`. Removes a failed copy —
    it is a partial duplicate, and the volume still holds everything."""
    started = time.perf_counter()
    report.bytes_total = em._tree_bytes(source)
    report.stage = "copying"
    em._sync_into(source, dest, report)
    report.stage = "verifying"
    em._verify(source, dest, report)
    report.seconds = time.perf_counter() - started
    if report.errors or report.row_count_drift or report.mismatched_files:
        shutil.rmtree(dest, ignore_errors=True)
        return False
    return True


# ── export only ─────────────────────────────────────────────────────────────


def export(destination_dir: Path, *,
           report: Optional[em.MigrationReport] = None) -> em.MigrationReport:
    """A decrypted, verified copy in a folder of the user's choosing.

    Encryption stays on. The copy is an ordinary data directory: pointing
    LOCALBOOK_DATA_DIR at it — or putting it in place of the data dir — opens it.
    """
    from services import volume_service

    report = report or em.MigrationReport()
    report.stage = "starting"
    source = em._data_dir()
    destination_dir = Path(destination_dir).expanduser()

    if not volume_service.is_mounted(source):
        report.errors.append("the encrypted volume is not open, so there is nothing to export")
        return report
    if not destination_dir.is_dir():
        report.errors.append(f"{destination_dir} is not a folder")
        return report
    try:
        if destination_dir.resolve().is_relative_to(source.resolve()):
            report.errors.append("the export cannot go inside the encrypted volume itself")
            return report
    except OSError:
        pass

    dest = destination_dir / f"LocalBook-export-{_stamp()}"
    if _copy_out(source, dest, report):
        report.ok = True
        report.stage = "exported"
        report.backup_path = str(dest)      # where it went, for the screen
    return report


# ── turning encryption off ──────────────────────────────────────────────────


def prepare(*, report: Optional[em.MigrationReport] = None) -> em.MigrationReport:
    """Stage a verified plaintext copy beside the data dir. Changes nothing live."""
    from services import volume_service

    report = report or em.MigrationReport()
    report.stage = "starting"
    source = em._data_dir()

    if not volume_service.is_mounted(source):
        report.errors.append("the data directory is not an open encrypted volume")
        return report
    if _marker_path().exists() or em._marker_path().exists():
        report.errors.append("a switch is already staged and waiting for a restart")
        return report

    staged = source.parent / f"{STAGED_PREFIX}{_stamp()}"
    if not _copy_out(source, staged, report):
        return report

    _marker_path().write_text(json.dumps({
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "staged": str(staged),
        "image": str(volume_service.image_path()),
    }, indent=2))
    report.ok = True
    report.staged = True
    report.restart_required = True
    report.stage = "staged"
    logger.warning("[decrypt] plaintext copy staged at %s — the switch happens on restart", staged)
    return report


def pending() -> Optional[Dict[str, object]]:
    try:
        return json.loads(_marker_path().read_text())
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.error("[decrypt] the pending marker is unreadable: %s", exc)
        return None


def cancel_pending() -> bool:
    """Abandon a staged switch. The staged copy is removed — it duplicates what
    the volume still holds."""
    info = pending()
    _marker_path().unlink(missing_ok=True)
    if info and info.get("staged"):
        shutil.rmtree(str(info["staged"]), ignore_errors=True)
    return info is not None


def apply_pending() -> Optional[Dict[str, object]]:
    """Swap the plaintext copy in. At startup, before anything opens a database.

    Every failure leaves the app ENCRYPTED and working — the volume is the copy
    that is known to be complete until the very last step.
    """
    info = pending()
    if not info:
        return None

    from services import volume_service

    source = em._data_dir()
    staged = Path(str(info.get("staged", "")))

    def fail(msg: str, keep_marker: bool = False) -> Dict[str, object]:
        if not keep_marker:
            _marker_path().unlink(missing_ok=True)
        logger.error("[decrypt] not applied: %s", msg)
        return _record({"applied": False, "error": msg})

    if not staged.is_dir():
        return fail(f"the staged copy is gone: {staged}")
    if not volume_service.is_mounted(source):
        # The writes since `prepare` are inside the volume, and it is not open.
        # Switching now would silently drop them. Try again next launch.
        return fail("the encrypted volume is not open, so recent changes could "
                    "not be carried over. Nothing was changed.", keep_marker=True)

    # Catch up: the app kept running after `prepare`.
    rep = em.MigrationReport(stage="catching up")
    changed: set = set()
    try:
        em._sync_into(source, staged, rep, changed)
        em._verify(source, staged, rep, only=changed)
    except Exception as exc:
        rep.errors.append(str(exc))
    if rep.errors or rep.row_count_drift or rep.mismatched_files:
        return fail("could not bring the plaintext copy up to date: "
                    + "; ".join(rep.errors or ["verification failed"]))

    try:
        em._detach(source)
    except Exception as exc:
        return fail(f"could not close the encrypted volume: {exc}")

    try:
        if source.exists():
            leftovers = [p for p in source.iterdir()]
            if leftovers:
                # Never expected on a clean mount point; never deleted either.
                source.rename(source.parent / f"{source.name}.mountpoint-leftovers-{_stamp()}")
            else:
                source.rmdir()
        shutil.move(str(staged), str(source))
    except Exception as exc:
        logger.critical("[decrypt] swap failed after detach: %s — re-attaching", exc)
        try:
            source.mkdir(parents=True, exist_ok=True)
            em._attach_at(source)
        except Exception as inner:
            logger.critical("[decrypt] COULD NOT RE-ATTACH: %s. The volume is intact at "
                            "%s and the plaintext copy at %s", inner,
                            volume_service.image_path(), staged)
            return _record({"applied": False, "error": str(exc),
                            "needs_manual_recovery": True, "staged": str(staged)})
        return fail(f"could not move the plaintext copy into place: {exc}")

    _disable_encryption_flag(source)
    _marker_path().unlink(missing_ok=True)
    logger.warning("[decrypt] encryption is OFF. The encrypted image is kept at %s",
                   volume_service.image_path())
    return _record({"applied": True, "image_kept_at": str(volume_service.image_path())})


def _disable_encryption_flag(data_dir: Path) -> None:
    from config import encryption_flag_path, settings

    encryption_flag_path(data_dir).unlink(missing_ok=True)
    settings.encryption_enabled = False
    # A pre-fix build wrote the flag into the data dir's `.env`, which is now
    # plaintext and read at every launch. Left there it would turn encryption
    # back on for a directory with no volume, and lock the app.
    env = data_dir / ".env"
    try:
        if env.is_file():
            lines = [l for l in env.read_text().splitlines()
                     if not l.strip().startswith("LOCALBOOK_ENCRYPTION_ENABLED=")]
            env.write_text("\n".join(lines) + ("\n" if lines else ""))
    except OSError as exc:
        logger.error("[decrypt] could not clean %s: %s", env, exc)


def _record(result: Dict[str, object]) -> Dict[str, object]:
    try:
        out = dict(result, at=datetime.now(timezone.utc).isoformat())
        _last_apply_path().write_text(json.dumps(out, indent=2))
    except Exception as exc:
        logger.error("[decrypt] could not record the outcome: %s", exc)
    return result


def last_apply() -> Optional[Dict[str, object]]:
    try:
        return json.loads(_last_apply_path().read_text())
    except Exception:
        return None


# ── the image left behind ───────────────────────────────────────────────────


def leftover_image() -> Optional[Dict[str, object]]:
    """The encrypted image, once encryption is off and it is no longer in use."""
    from services import volume_gate, volume_service

    image = volume_service.image_path()
    if not image.exists() or volume_gate.encryption_enabled() or volume_service.is_mounted():
        return None
    size = 0
    try:
        size = sum(f.stat().st_size for f in image.rglob("*") if f.is_file())
    except OSError:
        pass
    return {"path": str(image), "bytes": size}


def discard_image() -> Dict[str, object]:
    """Delete the encrypted image after turning encryption off.

    Refused unless encryption is off, the volume is not mounted, and the data
    dir holds a real database — i.e. the plaintext copy is demonstrably in use.
    """
    from services import volume_gate, volume_service

    image = volume_service.image_path()
    source = em._data_dir()
    if not image.exists():
        return {"deleted": False, "error": "there is no encrypted image"}
    if volume_gate.encryption_enabled() or volume_service.is_mounted():
        return {"deleted": False, "error": "encryption is still on — refusing to delete the volume in use"}
    if not (source / "localbook.db").is_file():
        return {"deleted": False,
                "error": "the data directory has no database, so the image may be the only copy"}
    size = leftover_image()["bytes"] if leftover_image() else 0
    try:
        shutil.rmtree(image)
    except OSError as exc:
        return {"deleted": False, "error": str(exc)}
    logger.warning("[decrypt] encrypted image deleted by request")
    return {"deleted": True, "freed_bytes": size}
