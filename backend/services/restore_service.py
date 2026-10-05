"""Getting the data back, and proving it would work before you need it.

LB-10 items 4 and 5. A backup nobody has restored is a hypothesis, not a backup.

**Restoring into a running app is refused** (plan §5 LB-10 item 4), and that is
not a policy so much as a physical fact: the backend has the four SQLite
databases open with WALs live, LanceDB holds file handles, and the enrichment
worker is mid-write. Swapping the directory underneath all of that produces
exactly the torn state a restore exists to escape.

So a live restore is two moves:

  1. **Verify and stage.** The archive is opened, every file checked against its
     recorded hash, every database `integrity_check`ed and its row counts
     compared to the manifest — all in a temp directory. Only if that is clean
     does the verified tree move to `LocalBook.restore-pending` beside the data
     dir, with a marker naming it.
  2. **Swap at startup**, before anything opens a database. `apply_pending()`
     runs first thing in the lifespan: it moves the current data dir aside to
     `LocalBook.pre-restore-<stamp>` — never deletes it — and moves the staged
     tree into place.

The old directory is kept. A restore that turns out to be the wrong archive is a
bad day; a restore that destroyed the only copy of what it replaced is
unrecoverable, and the difference is one `shutil.move` we chose not to make.

**`lancedb/` is not in the archive (D19)**, so a restored install has no vector
index until `/reindex/all?drop_tables=true` rebuilds it from `sources.content`.
That is slow — a full re-embed, and slow on the mini. It is reported, not hidden.

**The drill** (item 5) is the same verification with no staging: nightly, against
the newest archive, recording whether it would have worked. The plan's gate is
seven consecutive green nights before LB-11 migrates anything, so the drill has
to be able to FAIL loudly rather than skip quietly.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PENDING_DIR_NAME = "LocalBook.restore-pending"
MARKER_NAME = ".restore-pending.json"
PRE_RESTORE_PREFIX = "LocalBook.pre-restore-"

DRILL_LOG_NAME = "restore_drills.json"
DRILL_HISTORY = 30


@dataclass
class RestoreReport:
    ok: bool
    archive: str
    checked_files: int = 0
    missing_files: List[str] = field(default_factory=list)
    mismatched_files: List[str] = field(default_factory=list)
    databases: Dict[str, Dict[str, object]] = field(default_factory=dict)
    row_count_drift: Dict[str, Dict[str, object]] = field(default_factory=dict)
    staged_to: Optional[str] = None
    restart_required: bool = False
    needs_reindex: bool = False
    errors: List[str] = field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "archive": self.archive,
            "checked_files": self.checked_files,
            "missing_files": self.missing_files,
            "mismatched_files": self.mismatched_files,
            "databases": self.databases,
            "row_count_drift": self.row_count_drift,
            "staged_to": self.staged_to,
            "restart_required": self.restart_required,
            "needs_reindex": self.needs_reindex,
            "errors": self.errors,
            "seconds": round(self.seconds, 2),
        }


# ── verification ────────────────────────────────────────────────────────────


def _check_database(path: Path, expected_counts: Dict[str, int]) -> Dict[str, object]:
    """`integrity_check` plus a row-count comparison.

    Both, not either. `integrity_check` proves the file is a well-formed
    database; it says nothing about whether it is the RIGHT database. A restore
    that produces a pristine, empty `localbook.db` passes the integrity check
    and has lost everything.
    """
    out: Dict[str, object] = {"integrity": None, "tables": {}, "ok": False}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        out["integrity"] = f"could not open: {exc}"
        return out
    try:
        out["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        actual: Dict[str, int] = {}
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall():
            try:
                actual[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except sqlite3.Error:
                actual[name] = -1
        out["tables"] = actual
        out["ok"] = out["integrity"] == "ok"
    except sqlite3.Error as exc:
        out["integrity"] = f"failed: {exc}"
    finally:
        conn.close()
    return out


def _compare_counts(expected: Dict[str, int], actual: Dict[str, int]) -> Dict[str, object]:
    drift = {}
    for table, count in expected.items():
        got = actual.get(table)
        if got is None:
            drift[table] = {"expected": count, "actual": "table missing"}
        elif got != count:
            drift[table] = {"expected": count, "actual": got}
    return drift


def _extract(payload: bytes, into: Path) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as fh:
        fh.write(payload)
        tar_path = Path(fh.name)
    try:
        with tarfile.open(tar_path, "r") as tar:
            tar.extractall(into, filter="data")
    finally:
        tar_path.unlink(missing_ok=True)
    return into / "data"


def verify(archive: Path, *, phrase: Optional[str] = None) -> RestoreReport:
    """Everything a restore would do, except the part that changes anything.

    This IS the drill. It opens the archive, checks every hash, opens every
    database and compares every row count — in a temp directory that is deleted
    afterwards. A green report means the archive would restore; a red one means
    it would not, and says which part.
    """
    from services import backup_service

    started = time.perf_counter()
    archive = Path(archive)
    report = RestoreReport(ok=False, archive=str(archive))

    try:
        opened = backup_service.open_archive(archive, phrase=phrase)
    except Exception as exc:
        report.errors.append(f"could not open the archive: {exc}")
        report.seconds = time.perf_counter() - started
        return report

    manifest = opened["manifest"]
    expected_files = manifest.get("files", {})
    expected_rows = manifest.get("row_counts", {})

    with tempfile.TemporaryDirectory(prefix="lb-restore-check-") as tmp:
        try:
            root = _extract(opened["payload"], Path(tmp))
        except Exception as exc:
            report.errors.append(f"could not unpack the archive: {exc}")
            report.seconds = time.perf_counter() - started
            return report

        for rel, meta in expected_files.items():
            path = root / rel
            if not path.is_file():
                report.missing_files.append(rel)
                continue
            report.checked_files += 1
            if backup_service._sha256(path) != meta.get("sha256"):
                report.mismatched_files.append(rel)

        for rel, counts in expected_rows.items():
            db_path = root / rel
            if not db_path.is_file():
                report.databases[rel] = {"integrity": "missing", "ok": False}
                continue
            result = _check_database(db_path, counts)
            report.databases[rel] = result
            drift = _compare_counts(counts, result.get("tables", {}))
            if drift:
                report.row_count_drift[rel] = drift

    report.needs_reindex = True     # lancedb is never in the archive (D19)
    report.ok = (
        not report.missing_files
        and not report.mismatched_files
        and not report.row_count_drift
        and not report.errors
        and all(db.get("ok") for db in report.databases.values())
    )
    report.seconds = time.perf_counter() - started
    return report


# ── staging a real restore ──────────────────────────────────────────────────


def _pending_paths(data_dir: Path):
    parent = Path(data_dir).parent
    return parent / PENDING_DIR_NAME, Path(data_dir) / MARKER_NAME


def stage_restore(
    archive: Path,
    *,
    phrase: Optional[str] = None,
    data_dir: Optional[Path] = None,
    force: bool = False,
) -> RestoreReport:
    """Verify an archive and stage it for the next launch.

    Refuses to stage anything that does not verify — `force` exists for the case
    where the user genuinely wants a damaged archive back rather than nothing,
    and it still records exactly what was wrong.
    """
    if data_dir is None:
        from config import settings

        data_dir = Path(settings.data_dir)
    data_dir = Path(data_dir)

    report = verify(archive, phrase=phrase)
    if not report.ok and not force:
        report.errors.append(
            "archive did not verify; nothing was staged. Pass force to restore it anyway."
        )
        return report

    from services import backup_service

    pending_dir, marker = _pending_paths(data_dir)
    if pending_dir.exists():
        shutil.rmtree(pending_dir)

    opened = backup_service.open_archive(archive, phrase=phrase)
    with tempfile.TemporaryDirectory(prefix="lb-restore-stage-") as tmp:
        root = _extract(opened["payload"], Path(tmp))
        shutil.move(str(root), str(pending_dir))

    data_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "archive": str(archive),
        "staged_at": datetime.now(timezone.utc).isoformat(),
        "pending_dir": str(pending_dir),
        "verified": report.ok,
        "forced": bool(force and not report.ok),
    }, indent=2))

    report.staged_to = str(pending_dir)
    report.restart_required = True
    logger.warning(
        "[restore] staged %s — the swap happens on the next launch, before any "
        "database is opened", archive.name,
    )
    return report


def pending_restore(data_dir: Optional[Path] = None) -> Optional[Dict[str, object]]:
    if data_dir is None:
        from config import settings

        data_dir = Path(settings.data_dir)
    _, marker = _pending_paths(Path(data_dir))
    if not marker.is_file():
        return None
    try:
        return json.loads(marker.read_text())
    except Exception as exc:
        logger.error("[restore] pending marker is unreadable: %s", exc)
        return None


def cancel_pending(data_dir: Optional[Path] = None) -> bool:
    if data_dir is None:
        from config import settings

        data_dir = Path(settings.data_dir)
    pending_dir, marker = _pending_paths(Path(data_dir))
    removed = marker.exists()
    marker.unlink(missing_ok=True)
    if pending_dir.exists():
        shutil.rmtree(pending_dir)
    return removed


def apply_pending(data_dir: Optional[Path] = None) -> Optional[Dict[str, object]]:
    """Swap a staged restore into place. Called at startup, before any DB opens.

    The current data directory is MOVED ASIDE, never deleted. A restore that
    turns out to be the wrong archive is a bad day; one that destroyed what it
    replaced is unrecoverable, and the difference is a `shutil.move` we chose
    not to skip.
    """
    if data_dir is None:
        from config import settings

        data_dir = Path(settings.data_dir)
    data_dir = Path(data_dir)

    marker_info = pending_restore(data_dir)
    if not marker_info:
        return None

    pending_dir = Path(marker_info.get("pending_dir") or _pending_paths(data_dir)[0])
    if not pending_dir.is_dir():
        logger.error("[restore] marker points at %s, which is not there", pending_dir)
        _pending_paths(data_dir)[1].unlink(missing_ok=True)
        return {"applied": False, "error": f"staged data missing at {pending_dir}"}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    aside = data_dir.parent / f"{PRE_RESTORE_PREFIX}{stamp}"

    try:
        if data_dir.exists():
            shutil.move(str(data_dir), str(aside))
        shutil.move(str(pending_dir), str(data_dir))
    except Exception as exc:
        logger.error("[restore] swap failed: %s", exc)
        # Put it back rather than leaving the app with no data directory.
        if not data_dir.exists() and aside.exists():
            shutil.move(str(aside), str(data_dir))
        return {"applied": False, "error": str(exc)}

    (data_dir / MARKER_NAME).unlink(missing_ok=True)
    try:
        from services import keyvault
        keyvault.adopt_restored_keys(data_dir)
    except Exception as exc:                     # never fail the swap over this
        logger.error("[restore] wrapped keys not adopted: %s", exc)
    logger.warning(
        "[restore] applied %s; previous data kept at %s",
        marker_info.get("archive"), aside,
    )
    return {
        "applied": True,
        "archive": marker_info.get("archive"),
        "previous_data_kept_at": str(aside),
        # lancedb was never in the archive (D19) — chat will find nothing until
        # this runs, so it must be surfaced rather than discovered.
        "needs_reindex": True,
    }


# ── the nightly drill ───────────────────────────────────────────────────────


def _drill_log_path(data_dir: Path) -> Path:
    return Path(data_dir) / DRILL_LOG_NAME


def record_drill(report: RestoreReport, data_dir: Optional[Path] = None) -> Dict[str, object]:
    """Append one drill result. Keeps the last 30, which covers the 7-night gate."""
    if data_dir is None:
        from config import settings

        data_dir = Path(settings.data_dir)
    path = _drill_log_path(Path(data_dir))

    history: List[Dict[str, object]] = []
    if path.exists():
        try:
            history = json.loads(path.read_text())
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []

    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "archive": report.archive,
        "ok": report.ok,
        "checked_files": report.checked_files,
        "seconds": round(report.seconds, 2),
        "problems": (
            report.errors
            + [f"missing {f}" for f in report.missing_files[:5]]
            + [f"corrupt {f}" for f in report.mismatched_files[:5]]
            + [f"row drift in {db}" for db in report.row_count_drift]
        ),
    }
    history.append(entry)
    history = history[-DRILL_HISTORY:]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2))
    return entry


def drill_status(data_dir: Optional[Path] = None) -> Dict[str, object]:
    """For Data Health. The plan's gate is SEVEN consecutive green nights.

    `consecutive_green` counts backwards from the newest, so one red night
    resets it — which is the point. A drill that has been green 20 times and red
    last night has not earned the gate.
    """
    if data_dir is None:
        from config import settings

        data_dir = Path(settings.data_dir)
    path = _drill_log_path(Path(data_dir))
    if not path.exists():
        return {"runs": 0, "last": None, "consecutive_green": 0, "gate_met": False}

    try:
        history = json.loads(path.read_text())
    except Exception as exc:
        return {"runs": 0, "last": None, "consecutive_green": 0,
                "gate_met": False, "error": str(exc)}

    streak = 0
    for entry in reversed(history):
        if entry.get("ok"):
            streak += 1
        else:
            break

    return {
        "runs": len(history),
        "last": history[-1] if history else None,
        "consecutive_green": streak,
        "gate_met": streak >= 7,
        "history": history[-7:],
    }


def run_drill(
    backups_dir: Path,
    *,
    phrase: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> Dict[str, object]:
    """Verify the newest archive and record the outcome.

    Records a FAILURE when there is no archive at all. "No backup exists" is the
    worst possible state, and a drill that skips quietly in that case reports
    green forever on a machine with nothing to restore.
    """
    from services import backup_service

    backups_dir = Path(backups_dir)
    archives = sorted(
        backups_dir.glob(f"*{backup_service.ARCHIVE_SUFFIX}"), reverse=True
    ) if backups_dir.is_dir() else []

    if not archives:
        report = RestoreReport(ok=False, archive=str(backups_dir))
        report.errors.append("no backup archive found to drill against")
        return record_drill(report, data_dir)

    report = verify(archives[0], phrase=phrase)
    entry = record_drill(report, data_dir)
    if report.ok:
        logger.info("[restore-drill] %s verified in %.1fs", archives[0].name, report.seconds)
    else:
        logger.error("[restore-drill] %s FAILED: %s", archives[0].name, entry["problems"])
    return entry
