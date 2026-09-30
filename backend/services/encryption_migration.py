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
LAST_APPLY_NAME = ".encryption-last-apply.json"
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
#
# `.clean_shutdown` is deliberately NOT here. The catch-up runs after the old
# backend has exited, so the source's marker is the true answer to "did the last
# run shut down cleanly" — skipping it made every first launch after a migration
# report an unclean shutdown that never happened (seen on the mini, 2026-09-30).
SKIP_NAMES = {".app_token", ".DS_Store", ".volume_id"}
SKIP_SUFFIXES = ("-wal", "-shm", ".keyvault-tmp", ".json.tmp")


@dataclass
class MigrationReport:
    ok: bool = False
    stage: str = "not started"
    bytes_total: int = 0
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
            "bytes_total": self.bytes_total,
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


def _last_apply_path() -> Path:
    d = _data_dir()
    return d.parent / f"{d.name}{LAST_APPLY_NAME}"


def _should_skip(rel: Path) -> bool:
    # The sidecar's TMPDIR once the volume is mounted (lib.rs). Scratch only.
    if rel.parts and rel.parts[0] == "tmp":
        return True
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
            skip_backup: bool = False,
            report: Optional[MigrationReport] = None) -> MigrationReport:
    """Build a fully populated, verified encrypted volume. Changes nothing live.

    Abandonable at any point at zero cost: the plaintext directory is only ever
    read, and a failure leaves no marker, so the next launch does nothing.

    `report` may be passed in so a caller on another thread can watch it fill —
    that is how the setup screen shows progress.
    """
    started = time.perf_counter()
    report = report or MigrationReport()
    report.stage = "starting"

    from services import backup_service, keyvault, volume_service

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

    # The volume key is random and lives only in the Keychain. Without a recovery
    # phrase there is no wrapped copy of it, and a wiped Keychain would cost the
    # whole corpus — strictly worse than not encrypting at all.
    if not keyvault.has_recovery_key():
        report.errors.append(
            "set up a recovery phrase first (Settings → Recovery). Without one, "
            "losing this Mac's Keychain would lose the encrypted data with it."
        )
        return report

    report.bytes_total = _tree_bytes(source)

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
        _release_stale_staging(staging_mount)
        staging_mount.mkdir(parents=True, exist_ok=True)
        _attach_at(staging_mount)
    except Exception as exc:
        report.errors.append(f"could not mount the new volume: {exc}")
        return report

    try:
        report.stage = "copying"
        _sync_into(source, staging_mount, report)

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

    # The volume key must have its recovery copy before anything depends on it.
    # `get_or_create` wraps on creation when a recovery key exists; this closes
    # the case where the key predates the phrase.
    try:
        if "volume" in keyvault.unprotected_purposes():
            keyvault.wrap_for_recovery("volume")
    except Exception as exc:
        _marker_path().unlink(missing_ok=True)
        report.errors.append(f"could not store a recovery copy of the volume key: {exc}")
        report.ok = False
        return report

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


def _tree_bytes(root: Path) -> int:
    total = 0
    for entry in root.rglob("*"):
        try:
            if entry.is_file() and not _should_skip(entry.relative_to(root)):
                total += entry.stat().st_size
        except OSError:
            pass
    # A database's live WAL is folded into the copy by the backup API, so it
    # counts toward the total or the progress bar reaches 100% early.
    for rel in SQLITE_DBS:
        for suffix in ("", "-wal"):
            try:
                total += (root / f"{rel}{suffix}").stat().st_size
            except OSError:
                pass
    return total


def _release_stale_staging(mount: Path) -> None:
    """A quit mid-prepare leaves the volume attached at the staging point, and a
    second `hdiutil attach` there fails. Detach it before starting over."""
    if os.path.ismount(mount):
        logger.warning("[encrypt] detaching a staging mount left by an interrupted attempt")
        _detach(mount)


def _same_file(src: Path, dst: Path) -> bool:
    """Size and whole-second mtime. `copy2` preserves the mtime, so an unchanged
    file compares equal; seconds rather than ns so a filesystem that rounds
    differently cannot make every file look changed."""
    try:
        a, b = src.stat(), dst.stat()
    except OSError:
        return False
    return a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime)


def _sync_into(source: Path, target: Path, report: MigrationReport,
               changed: Optional[set] = None) -> None:
    """Make `target` match `source`. Databases through the backup API, the rest
    as files — only those that differ, so a second pass costs only the delta.

    `changed`, when given, collects the relative paths this pass wrote, so the
    caller can verify exactly those.

    Files present in the volume but gone from the source are removed FROM THE
    VOLUME COPY. That is not a deletion of user data — the plaintext is the
    source and is never touched — it is the copy following a file the user (or
    an eviction) removed after the first pass.
    """
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
        if changed is not None:
            changed.add(rel)

    seen = set()
    for entry in source.rglob("*"):
        if not entry.is_file():
            continue
        rel = entry.relative_to(source)
        if _should_skip(rel):
            continue
        seen.add(str(rel))
        dst = target / rel
        if dst.is_file() and _same_file(entry, dst):
            continue
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
        if changed is not None:
            changed.add(str(rel))

    for entry in list(target.rglob("*")):
        if not entry.is_file():
            continue
        rel = entry.relative_to(target)
        if _should_skip(rel) or str(rel) in seen:
            continue
        try:
            entry.unlink()
        except OSError as exc:
            report.errors.append(f"could not remove stale {rel} from the volume: {exc}")


def _verify(source: Path, target: Path, report: MigrationReport,
            only: Optional[set] = None) -> None:
    """Row counts AND hashes. A file that copied to the right size and hashes
    correctly can still be a database with no rows in it, and a database that
    counts correctly can still sit beside a corrupted audio file.

    `only` limits the file hashing to the paths a catch-up pass wrote; the
    databases are always checked in full."""
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
        if only is not None and str(rel) not in only:
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

    **Re-entrant.** A `kill -9`, a crash or a power cut can land between any two
    steps, and the next launch runs this again. So the aside path is written to
    the marker BEFORE the move (write-ahead), and a re-run resumes from what is
    on disk instead of starting over. Starting over was the bug: after the move,
    the data dir is empty, and a fresh catch-up from an empty dir would have
    "synced" the volume down to nothing.
    """
    info = pending()
    if not info:
        return None

    from services import volume_service

    source = _data_dir()
    image = volume_service.image_path()
    aside = Path(str(info["aside"])) if info.get("aside") else None

    # Interrupted after the attach: only the bookkeeping is missing.
    if volume_service.is_mounted(source):
        logger.warning("[encrypt] resuming an interrupted swap — the volume is already in place")
        return _finish_swap(image, aside)

    if not image.exists():
        if aside and aside.is_dir():
            _restore_plaintext(source, aside)
        _marker_path().unlink(missing_ok=True)
        return _record_apply({"applied": False, "error": f"the prepared volume is gone: {image}"})

    # Where the live plaintext is right now: still in the data dir, or already
    # moved aside by an interrupted attempt.
    resumed = bool(aside and aside.is_dir())
    live = aside if resumed else source

    # The app kept running between `prepare` and this restart — Collector runs,
    # chats happen. Without a catch-up pass those writes would stay behind in the
    # plaintext copy while the user carried on in a volume that never saw them.
    catch_up = _catch_up(live)
    if not catch_up.get("ok"):
        if resumed:
            _restore_plaintext(source, aside)
        _marker_path().unlink(missing_ok=True)
        return _record_apply({
            "applied": False,
            "error": "could not bring the prepared volume up to date: "
                     + "; ".join(catch_up.get("errors") or ["unknown error"]),
            "catch_up": catch_up,
        })

    if not resumed:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        aside = source.parent / f"{PLAINTEXT_PREFIX}{stamp}"
        _marker_path().write_text(json.dumps(dict(info, aside=str(aside)), indent=2))

    try:
        if source.exists() and not resumed:
            shutil.move(str(source), str(aside))
        source.mkdir(parents=True, exist_ok=True)
        if any(source.iterdir()):
            # Something wrote into the data dir after the move. Never mount over
            # it (that would hide it) and never delete it.
            raise RuntimeError(f"{source} is not empty after the plaintext was moved aside")
        _attach_at(source)
    except Exception as exc:
        logger.error("[encrypt] swap failed: %s", exc)
        # Put it back. An app with no data directory is worse than an
        # unencrypted one.
        if not _restore_plaintext(source, aside):
            return _record_apply({"applied": False, "error": str(exc),
                                  "plaintext_at": str(aside),
                                  "needs_manual_recovery": True})
        _marker_path().unlink(missing_ok=True)
        return _record_apply({"applied": False, "error": str(exc)})

    if not volume_service.is_mounted(source):
        logger.error("[encrypt] volume mounted but carries no sentinel — rolling back")
        try:
            _detach(source)
        except Exception as inner:
            logger.critical("[encrypt] rollback detach failed: %s — data is at %s", inner, aside)
        _restore_plaintext(source, aside)
        _marker_path().unlink(missing_ok=True)
        return _record_apply({"applied": False, "error": "the volume carries no sentinel"})

    return _finish_swap(image, aside)


def _restore_plaintext(source: Path, aside: Optional[Path]) -> bool:
    """Move the kept plaintext back into the data dir. True on success.

    Only ever into an absent or EMPTY data dir — anything else is left exactly
    where it is and logged, never overwritten.
    """
    if not aside or not aside.is_dir():
        return source.is_dir()
    try:
        if source.exists():
            if any(source.iterdir()):
                logger.critical("[encrypt] %s is not empty — NOT restoring over it. "
                                "Your data is at %s", source, aside)
                return False
            source.rmdir()
        shutil.move(str(aside), str(source))
        return True
    except Exception as exc:
        logger.critical("[encrypt] COULD NOT RESTORE THE PLAINTEXT DIRECTORY: %s. "
                        "Your data is at %s", exc, aside)
        return False


def _finish_swap(image: Path, aside: Optional[Path]) -> Dict[str, object]:
    _marker_path().unlink(missing_ok=True)
    _enable_encryption_flag()
    logger.warning(
        "[encrypt] the data directory is now encrypted. The plaintext copy is "
        "kept at %s until you confirm.", aside,
    )
    return _record_apply({
        "applied": True,
        "plaintext_kept_at": str(aside) if aside else None,
        "image": str(image),
        # Deliberately surfaced: the migration is not finished until the user
        # has seen their own notebooks and said so.
        "confirm_required": True,
    })


def _catch_up(source: Path) -> Dict[str, object]:
    """Copy whatever changed since `prepare` into the volume, and verify it.

    Runs with no backend holding the databases (this is import time), so the
    snapshot it takes is the final one.
    """
    report = MigrationReport(stage="catching up")
    staging = source.parent / STAGING_MOUNT
    changed: set = set()
    try:
        _release_stale_staging(staging)
        staging.mkdir(parents=True, exist_ok=True)
        _attach_at(staging)
    except Exception as exc:
        return {"ok": False, "errors": [f"could not mount the prepared volume: {exc}"]}
    try:
        # Never sync FROM a directory that has lost its database while the
        # volume still has one: that is an interrupted swap or a wrong path, and
        # following it would delete the corpus out of the volume.
        if (staging / "localbook.db").is_file() and not (source / "localbook.db").is_file():
            raise RuntimeError(f"{source} has no localbook.db but the volume does — "
                               "refusing to sync from it")
        _sync_into(source, staging, report, changed)
        _verify(source, staging, report, only=changed)
    except Exception as exc:
        report.errors.append(str(exc))
    finally:
        try:
            _detach(staging)
        except Exception as exc:
            report.errors.append(f"could not detach: {exc}")
        try:
            staging.rmdir()
        except OSError:
            pass
    problems = list(report.errors)
    if report.row_count_drift:
        problems.append(f"row counts differ in {sorted(report.row_count_drift)}")
    if report.mismatched_files:
        problems.append(f"{len(report.mismatched_files)} file(s) did not verify")
    # Minus the databases, which are re-copied every time by design.
    files = len([c for c in changed if c not in SQLITE_DBS])
    return {"ok": not problems, "errors": problems, "files": files}


def _record_apply(result: Dict[str, object]) -> Dict[str, object]:
    """Keep the outcome where the setup screen can read it after the restart.

    Beside the data dir, like the marker: a failed swap must be reportable when
    there is no volume to write into.
    """
    try:
        out = dict(result)
        out["at"] = datetime.now(timezone.utc).isoformat()
        _last_apply_path().write_text(json.dumps(out, indent=2))
    except Exception as exc:
        logger.error("[encrypt] could not record the swap outcome: %s", exc)
    return result


def last_apply() -> Optional[Dict[str, object]]:
    try:
        return json.loads(_last_apply_path().read_text())
    except Exception:
        return None


def _enable_encryption_flag() -> None:
    """Turn `encryption_enabled` on for this machine (D11: never synced).

    Written only AFTER a successful swap — earlier would lock the app out of a
    data directory that is still plaintext. And written BESIDE the data dir, not
    into its `.env`: that file is now inside the volume, so a failed mount would
    hide the flag and let the app open empty (see `config.encryption_flag_path`).
    """
    try:
        from config import encryption_flag_path, settings

        flag = encryption_flag_path(_data_dir())
        flag.write_text(datetime.now(timezone.utc).isoformat() + "\n")
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

    from services.encryption_verify import verified_for

    if (verified_for(str(target)) or {}).get("ok") is False:
        return {"deleted": False,
                "error": "the check after the switch found differences between the "
                         "encrypted volume and this copy — it is kept."}

    try:
        size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
        shutil.rmtree(target)
    except OSError as exc:
        return {"deleted": False, "error": str(exc)}

    logger.warning("[encrypt] plaintext copy %s deleted by request", target.name)
    return {"deleted": True, "freed_bytes": size, "path": str(target)}

