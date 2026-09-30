"""Moving the corpus onto the encrypted volume.

LB-11 measure 5, and the most dangerous operation in the release: it relocates
~690 MB that exists nowhere else. Everything here is shaped by one rule —

    **nothing is deleted, ever, by this module.**

The plaintext directory is moved aside and kept. Removing it is a separate,
explicit act the user takes after they have seen their own notebooks come back
(`discard_plaintext`). A migration that destroys what it replaced is not a
migration, it is a gamble with extra steps.

**Why it is two phases.** The mount point IS the data directory. The volume
cannot be mounted where the plaintext still lives, and the backend is running
with live WALs on `localbook.db` and `brain.db` while all this is being decided.
So:

  Phase 1, app running — `prepare()`
      LB-10 backup → create the image → mount it somewhere TEMPORARY → copy
      everything in → verify → detach → write a marker. Touches nothing the app
      is using. Abandonable at any point at zero cost.

  Phase 2, at startup before anything opens — `apply_pending()`
      Move the plaintext aside, mount the volume at the data dir, confirm the
      sentinel. Runs in `main.py` at import, ahead of the volume gate, for the
      same reason LB-10's restore does: by lifespan time the databases are
      already open.

**Databases are copied with the `sqlite3` backup API, never `cp`.** Both live
databases have a `-wal` beside them right now. A file copy of that is a torn
page plus whatever the WAL happened to hold — it opens without complaint and is
wrong. This is the same lesson LB-10's backup learned, and it matters more here,
because here the copy becomes the only copy.

**Verification is row counts, not file counts.** A file that copied to the right
size and hashes correctly can still be a database with no rows in it. Every
table in every database is counted on both sides and compared before anything is
staged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MARKER_NAME = ".encryption-pending.json"
PLAINTEXT_PREFIX = "LocalBook.plaintext-"
STAGING_MOUNT = ".localbook-migrate-mnt"

# Copied with the sqlite3 backup API. Everything else is a plain file copy.
SQLITE_DBS = (
    "localbook.db",
    "tabular.db",
    "memory/recall_memory.db",
    "curator_brain/brain.db",
)

# Never copied into the volume. Transient, reproducible, or actively harmful to
# carry across — a stale `.app_token` would be read by the next launch, and the
# WAL/SHM files belong to a snapshot that no longer exists once the DB is copied.
SKIP_NAMES = {".app_token", ".DS_Store", ".volume_id", ".clean_shutdown"}
SKIP_SUFFIXES = ("-wal", "-shm", ".keyvault-tmp", ".json.tmp")


@dataclass
class MigrationReport:
    ok: bool = False
    stage: str = "not started"
    backup_path: Optional[str] = None
    files_copied: int = 0
    bytes_copied: int = 0
    databases: Dict[str, Dict[str, object]] = field(default_factory=dict)
    row_count_drift: Dict[str, Dict[str, object]] = field(default_factory=dict)
    mismatched_files: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    staged: bool = False
    restart_required: bool = False
    errors: List[str] = field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "backup_path": self.backup_path,
            "files_copied": self.files_copied,
            "bytes_copied": self.bytes_copied,
            "databases": self.databases,
            "row_count_drift": self.row_count_drift,
            "mismatched_files": self.mismatched_files,
            "skipped": self.skipped,
            "staged": self.staged,
            "restart_required": self.restart_required,
            "errors": self.errors,
            "seconds": round(self.seconds, 2),
        }


# ── helpers ─────────────────────────────────────────────────────────────────


def _data_dir() -> Path:
    from config import settings

    return Path(settings.data_dir)


def _marker_path() -> Path:
    # BESIDE the data dir. Inside would be swallowed by the swap it describes.
    d = _data_dir()
    return d.parent / f"{d.name}{MARKER_NAME}"


def _should_skip(rel: Path) -> bool:
    name = rel.name
    if name in SKIP_NAMES:
        return True
    if any(name.endswith(s) for s in SKIP_SUFFIXES):
        return True
    if str(rel) in SQLITE_DBS:
        return True          # handled by the backup API, not copied
    return False


def _row_counts(db: Path) -> Dict[str, int]:
    out: Dict[str, int] = {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall():
            try:
                out[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except sqlite3.Error:
                out[name] = -1
    finally:
        conn.close()
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ── phase 1: prepare ────────────────────────────────────────────────────────


def prepare(*, backup_destination: Optional[Path] = None,
            skip_backup: bool = False) -> MigrationReport:
    """Build a fully populated, verified encrypted volume. Changes nothing live.

    Abandonable at any point at zero cost: the plaintext directory is only ever
    read, and a failure leaves no marker, so the next launch does nothing.
    """
    started = time.perf_counter()
    report = MigrationReport(stage="starting")

    from services import backup_service, volume_service

    source = _data_dir()
    if not source.is_dir():
        report.errors.append(f"{source} does not exist")
        return report

    if volume_service.is_mounted(source):
        report.errors.append("the data directory is already an encrypted volume")
        return report

    if _marker_path().exists():
        report.errors.append(
            "a migration is already staged and waiting for a restart"
        )
        return report

    # ── the backup, first and non-negotiable ────────────────────────────────
    if not skip_backup:
        report.stage = "backing up"
        try:
            from services import backup_scheduler

            dest = Path(backup_destination) if backup_destination else \
                backup_scheduler.configured_destination()
            if dest is None or not dest.is_dir():
                report.errors.append(
                    "no backup destination is set. A backup is taken before the "
                    "migration, not after — set one in Settings → Data Health."
                )
                return report
            result = backup_service.create_backup(dest, data_dir=source)
            report.backup_path = str(result.path)
            logger.warning("[encrypt] pre-migration backup at %s", result.path)
        except Exception as exc:
            report.errors.append(f"the pre-migration backup failed: {exc}")
            return report

    # ── the volume ──────────────────────────────────────────────────────────
    report.stage = "creating the volume"
    staging_mount = source.parent / STAGING_MOUNT
    try:
        if not volume_service.image_path().exists():
            volume_service.create()
    except Exception as exc:
        report.errors.append(f"could not create the encrypted volume: {exc}")
        return report

    # Mounted at a TEMPORARY point, never at the data dir — that still holds the
    # plaintext, and mounting over it would hide it.
    try:
        staging_mount.mkdir(parents=True, exist_ok=True)
        _attach_at(staging_mount)
    except Exception as exc:
        report.errors.append(f"could not mount the new volume: {exc}")
        return report

    try:
        report.stage = "copying"
        _copy_into(source, staging_mount, report)

        report.stage = "verifying"
        _verify(source, staging_mount, report)

        if report.errors or report.row_count_drift or report.mismatched_files:
            report.ok = False
            logger.error("[encrypt] verification FAILED — nothing staged")
            return report

        # The sentinel goes in last: it is what makes this a LocalBook volume,
        # and writing it before the contents verify would mark a half-copied
        # volume as ready.
        volume_service._write_sentinel(staging_mount, encrypted=True)

    finally:
        report.stage = "detaching"
        try:
            _detach(staging_mount)
        except Exception as exc:
            report.errors.append(f"could not detach the staging mount: {exc}")
        try:
            staging_mount.rmdir()
        except OSError:
            pass

    if report.errors:
        report.ok = False
        return report

    _marker_path().write_text(json.dumps({
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "image": str(volume_service.image_path()),
        "backup_path": report.backup_path,
        "files_copied": report.files_copied,
        "bytes_copied": report.bytes_copied,
    }, indent=2))

    report.ok = True
    report.staged = True
    report.restart_required = True
    report.stage = "staged"
    report.seconds = time.perf_counter() - started
    logger.warning(
        "[encrypt] volume prepared and verified — the swap happens on the next launch"
    )
    return report


def _attach_at(mount: Path) -> None:
    from services import volume_service

    proc = volume_service._run(
        ["hdiutil", "attach", "-stdinpass", "-nobrowse", "-mountpoint",
         str(mount), str(volume_service.image_path())],
        stdin=volume_service.passphrase(),
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip()[:300])


def _detach(mount: Path) -> None:
    from services import volume_service

    for attempt in range(5):
        proc = volume_service._run(["hdiutil", "detach", str(mount)])
        if proc.returncode == 0:
            return
        time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"could not detach {mount}")


def _copy_into(source: Path, target: Path, report: MigrationReport) -> None:
    """Databases through the backup API, everything else as files."""
    for rel in SQLITE_DBS:
        src = source / rel
        if not src.is_file():
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # The live WAL is why this cannot be `cp`. Both `localbook.db` and
        # `brain.db` have one open right now.
        conn_src = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            conn_dst = sqlite3.connect(str(dst))
            try:
                conn_src.backup(conn_dst)
            finally:
                conn_dst.close()
        finally:
            conn_src.close()
        report.files_copied += 1
        report.bytes_copied += dst.stat().st_size

    for entry in source.rglob("*"):
        if not entry.is_file():
            continue
        rel = entry.relative_to(source)
        if _should_skip(rel):
            continue
        dst = target / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, dst)
        except FileNotFoundError:
            # Rotated or evicted while we walked. Recorded, not swallowed — the
            # same lesson LB-10's hot backup learned.
            report.skipped.append(str(rel))
            continue
        except OSError as exc:
            report.errors.append(f"could not copy {rel}: {exc}")
            continue
        report.files_copied += 1
        report.bytes_copied += dst.stat().st_size


def _verify(source: Path, target: Path, report: MigrationReport) -> None:
    """Row counts AND hashes. A file that copied to the right size and hashes
    correctly can still be a database with no rows in it, and a database that
    counts correctly can still sit beside a corrupted audio file."""
    for rel in SQLITE_DBS:
        src, dst = source / rel, target / rel
        if not src.is_file():
            continue
        if not dst.is_file():
            report.errors.append(f"{rel} did not copy at all")
            continue

        conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()

        expected, actual = _row_counts(src), _row_counts(dst)
        drift = {
            table: {"expected": n, "actual": actual.get(table, "table missing")}
            for table, n in expected.items()
            if actual.get(table) != n
        }
        report.databases[rel] = {"integrity": integrity, "tables": len(actual),
                                 "ok": integrity == "ok" and not drift}
        if integrity != "ok":
            report.errors.append(f"{rel} failed its integrity check: {integrity}")
        if drift:
            report.row_count_drift[rel] = drift

    for entry in source.rglob("*"):
        if not entry.is_file():
            continue
        rel = entry.relative_to(source)
        if _should_skip(rel) or str(rel) in report.skipped:
            continue
        dst = target / rel
        if not dst.is_file():
            report.mismatched_files.append(f"{rel} (missing)")
            continue
        try:
            if _sha256(entry) != _sha256(dst):
                report.mismatched_files.append(str(rel))
        except OSError:
            report.skipped.append(str(rel))


# ── phase 2: the swap ───────────────────────────────────────────────────────


def pending() -> Optional[Dict[str, object]]:
    marker = _marker_path()
    if not marker.is_file():
        return None
    try:
        return json.loads(marker.read_text())
    except Exception as exc:
        logger.error("[encrypt] the pending marker is unreadable: %s", exc)
        return None


def cancel_pending() -> bool:
    """Abandon a staged migration. The volume is left in place, unused."""
    marker = _marker_path()
    existed = marker.is_file()
    marker.unlink(missing_ok=True)
    return existed


def apply_pending() -> Optional[Dict[str, object]]:
    """Swap the volume in. Runs at startup, before anything opens a database.

    The plaintext directory is MOVED ASIDE and kept. If any step fails, it is
    moved back — the app must never be left with no data directory at all.
    """
    info = pending()
    if not info:
        return None

    from services import volume_service

    source = _data_dir()
    image = volume_service.image_path()
    if not image.exists():
        _marker_path().unlink(missing_ok=True)
        return {"applied": False, "error": f"the prepared volume is gone: {image}"}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    aside = source.parent / f"{PLAINTEXT_PREFIX}{stamp}"

    try:
        if source.exists():
            shutil.move(str(source), str(aside))
        source.mkdir(parents=True, exist_ok=True)
        _attach_at(source)
    except Exception as exc:
        logger.error("[encrypt] swap failed: %s", exc)
        # Put it back. An app with no data directory is worse than an
        # unencrypted one.
        try:
            if not source.exists() or not any(source.iterdir()):
                if source.exists():
                    source.rmdir()
                if aside.exists():
                    shutil.move(str(aside), str(source))
        except Exception as inner:
            logger.critical(
                "[encrypt] COULD NOT RESTORE THE PLAINTEXT DIRECTORY: %s. "
                "Your data is at %s", inner, aside,
            )
            return {"applied": False, "error": str(exc),
                    "plaintext_at": str(aside), "needs_manual_recovery": True}
        return {"applied": False, "error": str(exc)}

    if not volume_service.is_mounted(source):
        logger.error("[encrypt] volume mounted but carries no sentinel — rolling back")
        try:
            _detach(source)
            source.rmdir()
            shutil.move(str(aside), str(source))
        except Exception as inner:
            logger.critical("[encrypt] rollback failed: %s — data is at %s", inner, aside)
        return {"applied": False, "error": "the volume carries no sentinel"}

    _marker_path().unlink(missing_ok=True)
    _enable_encryption_flag()

    logger.warning(
        "[encrypt] the data directory is now encrypted. The plaintext copy is "
        "kept at %s until you confirm.", aside,
    )
    return {
        "applied": True,
        "plaintext_kept_at": str(aside),
        "image": str(image),
        # Deliberately surfaced: the migration is not finished until the user
        # has seen their own notebooks and said so.
        "confirm_required": True,
    }


def _enable_encryption_flag() -> None:
    """Turn `encryption_enabled` on, per-machine, in the data-dir `.env`.

    Written only AFTER a successful swap. Setting it earlier would lock the app
    out of a data directory that is still plaintext.
    """
    try:
        from config import get_data_directory, settings

        env_path = Path(get_data_directory()) / ".env"
        key = "LOCALBOOK_ENCRYPTION_ENABLED"
        lines = env_path.read_text().splitlines() if env_path.exists() else []
        out, replaced = [], False
        for line in lines:
            if line.strip().startswith(f"{key}="):
                out.append(f"{key}=true")
                replaced = True
            else:
                out.append(line)
        if not replaced:
            out.append(f"{key}=true")
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("\n".join(out) + "\n")
        settings.encryption_enabled = True
    except Exception as exc:
        logger.error("[encrypt] could not persist encryption_enabled: %s", exc)


# ── the last step, and the only one that deletes ────────────────────────────


def plaintext_copies() -> List[Dict[str, object]]:
    """Plaintext directories left behind by past migrations."""
    parent = _data_dir().parent
    out = []
    for entry in sorted(parent.glob(f"{PLAINTEXT_PREFIX}*")):
        if not entry.is_dir():
            continue
        size = 0
        try:
            size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        except OSError:
            pass
        out.append({"path": str(entry), "name": entry.name, "bytes": size})
    return out


def discard_plaintext(path: str) -> Dict[str, object]:
    """Delete one kept plaintext copy. The ONLY destructive call in this module.

    Refuses unless the encrypted volume is currently mounted and carries a
    sentinel: deleting the plaintext while the volume is unavailable would turn
    a recoverable situation into a total loss, and that is exactly the moment a
    frustrated user is most likely to click it.
    """
    from services import volume_service

    target = Path(path)
    if not target.is_dir() or not target.name.startswith(PLAINTEXT_PREFIX):
        return {"deleted": False, "error": "that is not a kept plaintext copy"}

    if not volume_service.is_mounted():
        return {
            "deleted": False,
            "error": "the encrypted volume is not open. Refusing to delete the "
                     "plaintext copy while it is the only readable one.",
        }

    try:
        size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
        shutil.rmtree(target)
    except OSError as exc:
        return {"deleted": False, "error": str(exc)}

    logger.warning("[encrypt] plaintext copy %s deleted by request", target.name)
    return {"deleted": True, "freed_bytes": size, "path": str(target)}
