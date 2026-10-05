"""Is the data actually safe right now, and how would you know?

LB-10 items 6 and 8. Everything else in LB-10 produces a fact — an archive
exists, a drill passed, a migration ran. This is the one place those facts are
gathered into an answer, because a backup nobody looks at is only marginally
better than no backup.

Two jobs:

**The shutdown sentinel (item 6).** LocalBook writes a marker on a clean exit
and removes it on launch. If a launch finds the marker missing, the previous run
did not shut down cleanly — a crash, a `kill -9`, a power cut — and every SQLite
database gets an `integrity_check` BEFORE the app serves traffic. SQLite's WAL
makes this survivable almost always; "almost" is why the check exists, and
discovering corruption at startup is enormously better than discovering it three
weeks later when the backup that also captured it has already rotated out.

**The health summary (item 8).** Last backup, last drill, the drill streak, the
schema head, the memory budget and the codec. Deliberately assembled from the
*real* sources — it reads the drill log and the archive headers rather than
keeping its own tally, because a status cache is one more thing that can be
wrong in the direction of reassuring.

Everything here degrades to "unknown" rather than raising. A health panel that
500s when one probe fails tells the user nothing about the other five.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

CLEAN_MARKER = ".clean_shutdown"

# Checked after an unclean exit. Same four the backup snapshots.
CHECKED_DBS = (
    "localbook.db",
    "tabular.db",
    "memory/recall_memory.db",
    "curator_brain/brain.db",
)

# Directories and files that accumulate and are safe to remove (item 9).
# Each is derived, superseded, or a one-off from a migration that has long
# since finished.
DEAD_WEIGHT_PREFIXES = ("lancedb_backup_",)
DEAD_WEIGHT_SUFFIXES = (".pre-keyvault", ".keyvault-tmp", ".json.tmp")


def _data_dir(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    from config import settings

    return Path(settings.data_dir)


# ── the shutdown sentinel (item 6) ──────────────────────────────────────────


def mark_clean_shutdown(data_dir: Optional[Path] = None) -> None:
    """Called on the way out. Its ABSENCE at startup is the signal."""
    path = _data_dir(data_dir) / CLEAN_MARKER
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now(timezone.utc).isoformat())
    except OSError as exc:
        logger.warning("[data-health] could not write the clean-shutdown marker: %s", exc)


def was_unclean(data_dir: Optional[Path] = None) -> bool:
    """True when the previous run did not exit cleanly.

    A brand-new data directory has no marker either, which would read as
    "unclean" — handled by the caller, which treats a directory with no
    databases as a first run rather than a crash.
    """
    return not (_data_dir(data_dir) / CLEAN_MARKER).exists()


def clear_clean_marker(data_dir: Optional[Path] = None) -> None:
    """Called at startup, after the check. From here until a clean exit, the
    marker is absent — which is exactly what makes a crash detectable."""
    try:
        (_data_dir(data_dir) / CLEAN_MARKER).unlink(missing_ok=True)
    except OSError:
        pass


def check_integrity(data_dir: Optional[Path] = None) -> Dict[str, object]:
    """`integrity_check` every database. Read-only; never repairs.

    Repairing automatically is the wrong instinct: SQLite's own guidance is that
    a corrupt database should be recovered from a backup, and a well-meant
    in-place rebuild can turn a partially readable file into a confidently wrong
    one. This reports; LB-10's restore path is the fix.
    """
    root = _data_dir(data_dir)
    results: Dict[str, str] = {}
    for rel in CHECKED_DBS:
        path = root / rel
        if not path.is_file():
            continue
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                results[rel] = conn.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.Error as exc:
            results[rel] = f"could not open: {exc}"

    bad = {k: v for k, v in results.items() if v != "ok"}
    return {
        "checked": results,
        "ok": not bad,
        "problems": bad,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def startup_check(data_dir: Optional[Path] = None) -> Optional[Dict[str, object]]:
    """Run at launch, before serving. Returns None on a clean previous exit.

    Non-fatal by design: a corrupt database should stop the user, not the
    process. If the app refuses to boot they cannot reach the restore screen,
    which is the one thing that would help.
    """
    root = _data_dir(data_dir)
    has_data = any((root / rel).is_file() for rel in CHECKED_DBS)

    if not was_unclean(root):
        clear_clean_marker(root)
        return None
    if not has_data:
        return None                      # first run, not a crash

    logger.warning("[data-health] previous shutdown was not clean — checking databases")
    report = check_integrity(root)
    clear_clean_marker(root)

    if not report["ok"]:
        logger.error("[data-health] INTEGRITY PROBLEMS: %s", report["problems"])
    else:
        logger.info("[data-health] all databases check out after the unclean shutdown")
    return report


# ── dead weight (item 9) ────────────────────────────────────────────────────


def find_dead_weight(data_dir: Optional[Path] = None) -> List[Dict[str, object]]:
    """Things that accumulate and are safe to remove. Reports; does not delete."""
    root = _data_dir(data_dir)
    found: List[Dict[str, object]] = []
    if not root.is_dir():
        return found

    for entry in sorted(root.iterdir()):
        name = entry.name
        matched = None
        if entry.is_dir() and any(name.startswith(p) for p in DEAD_WEIGHT_PREFIXES):
            matched = "an orphaned one-off LanceDB copy from a finished migration"
        elif entry.is_file() and any(name.endswith(s) for s in DEAD_WEIGHT_SUFFIXES):
            matched = "a leftover from a completed migration"
        if not matched:
            continue
        try:
            size = (
                sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                if entry.is_dir() else entry.stat().st_size
            )
        except OSError:
            size = 0
        found.append({"path": str(entry), "name": name, "bytes": size, "why": matched})
    return found


def remove_dead_weight(data_dir: Optional[Path] = None) -> Dict[str, object]:
    """Delete what `find_dead_weight` found. Only ever called explicitly.

    Never automatic. Everything here is *probably* junk, and "probably" is not a
    standard at which to delete a user's files without being asked.
    """
    removed, failed = [], {}
    for item in find_dead_weight(data_dir):
        path = Path(item["path"])
        try:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            removed.append(item["name"])
        except OSError as exc:
            failed[item["name"]] = str(exc)
    return {"removed": removed, "failed": failed}


# ── the summary (item 8) ────────────────────────────────────────────────────


def _last_backup(data_dir: Optional[Path] = None) -> Dict[str, object]:
    from services import backup_service, backup_scheduler

    destination = backup_scheduler.configured_destination()
    if destination is None:
        return {"configured": False, "reason": "no backup destination set"}
    if not destination.is_dir():
        return {"configured": True, "destination": str(destination),
                "reason": "the destination folder is not there"}

    archives = sorted(
        destination.glob(f"*{backup_service.ARCHIVE_SUFFIX}"), reverse=True
    )
    if not archives:
        return {"configured": True, "destination": str(destination),
                "count": 0, "reason": "no archives yet"}

    newest = archives[0]
    out: Dict[str, object] = {
        "configured": True,
        "destination": str(destination),
        "count": len(archives),
        "newest": str(newest),
        "bytes": newest.stat().st_size,
        "total_bytes": sum(a.stat().st_size for a in archives),
    }
    try:
        header = backup_service.read_header(newest)
        out["created_at"] = header.get("created_at")
        # Surfaced because an archive only this Mac can open is a materially
        # weaker promise than "you have backups" implies.
        out["recoverable_with_phrase"] = any(
            r.get("kind") == "recovery" for r in header.get("recipients", [])
        )
    except Exception as exc:
        out["error"] = str(exc)
    return out


def _codec() -> Dict[str, object]:
    """Whether the audio/video codecs are present.

    PyAV, in-process, shipped with the app (LB-3): transcription, `/v1/audio/*`,
    podcast jingles and video all run through it, so no Mac needs Homebrew's
    ffmpeg any more — the work Mac has none.
    """
    from services.audio_codec import codec_ok

    return {"ok": codec_ok(), "speech": "pyav"}


def status(data_dir: Optional[Path] = None) -> Dict[str, object]:
    """Everything the Data Health panel shows.

    Each probe degrades to an error string of its own rather than raising: a
    panel that 500s because one probe failed tells the user nothing about the
    other five.
    """
    root = _data_dir(data_dir)
    out: Dict[str, object] = {"at": datetime.now(timezone.utc).isoformat()}

    def probe(name: str, fn):
        try:
            out[name] = fn()
        except Exception as exc:
            out[name] = {"error": str(exc)}

    probe("backup", lambda: _last_backup(root))

    def drills():
        from services import restore_service

        return restore_service.drill_status(root)

    probe("drills", drills)

    def schema():
        from services import migration_ledger

        # ⚠️ This probe reads the process's OWN database connection, not
        # `data_dir`. In the app they are the same thing; in a test that passes
        # a temp directory they are not, so the schema line describes the live
        # database either way. Stated rather than silently surprising.
        return {
            "version": migration_ledger.schema_version(),
            "ledger_head": migration_ledger.head(),
            "pending": len(migration_ledger.pending()),
        }

    probe("schema", schema)

    def memory():
        from services.model_sizing import (
            RESIDENT_RESERVE_GB, budget_gb, external_reserve_gb, working_set_gb,
        )

        return {
            "working_set_gb": round(working_set_gb(), 2),
            "resident_reserve_gb": RESIDENT_RESERVE_GB,
            "external_reserve_gb": external_reserve_gb(),
            "budget_gb": budget_gb(),
        }

    probe("memory", memory)

    def keys():
        from services import key_escrow, keyvault

        return {**keyvault.status(), "phrase_check": key_escrow.phrase_check()}

    probe("keys", keys)

    probe("codec", _codec)
    probe("volume", _volume)
    probe("dead_weight", lambda: find_dead_weight(root))

    def pending_restore():
        from services import restore_service

        return restore_service.pending_restore(root)

    probe("pending_restore", pending_restore)

    out["overall"] = _overall(out)
    return out


# What losing each key actually costs, so the panel grades honestly in BOTH
# directions. Over-stating trains people to ignore the panel just as surely as
# under-stating misleads them.
#
# `credentials` is the only genuinely critical one: it is the Fernet key for
# credentials.enc and auth/*.enc, and losing it makes those unreadable forever.
#
# `backup` reads as alarming and is not. Every archive is sealed to the recovery
# PUBLIC key independently of this device key (backup_service._write_archive),
# so the phrase still opens existing archives and future backups simply get a
# fresh key. Nothing is lost.
#
# `device_identity` is LB-12 pairing. Losing it means re-pairing this Mac.
_KEY_CONSEQUENCE = {
    "credentials": (
        "problem",
        "wiping the Keychain would make your saved logins and email accounts "
        "unreadable for good.",
    ),
    "backup": (
        "warning",
        "your existing backups still open with the phrase, so nothing is lost — "
        "but this Mac would generate a new key.",
    ),
    "device_identity": (
        "warning",
        "you would need to pair this Mac again.",
    ),
}


VOLUME_LOW_FREE_BYTES = 2 * 1024 ** 3
# Written by the app (src-tauri lib.rs `volume_compact_stamp_path`) after each compact,
# beside the data dir — the volume itself is not readable when the compact runs.
COMPACT_STAMP = Path.home() / "Library" / "Application Support" / "LocalBook.encryption-last-compact"


def _volume() -> Dict[str, object]:
    """The encrypted volume (LB-11): mounted?, size on disk, free inside, last compact."""
    from services import volume_gate

    if not volume_gate.encryption_enabled():
        return {"enabled": False}
    from services import volume_service

    st = volume_service.state()
    out: Dict[str, object] = {
        "enabled": True, "mounted": st.mounted, "image_bytes": st.image_bytes,
        "free_bytes": st.free_bytes, "last_compact": None,
    }
    try:
        if COMPACT_STAMP.exists():
            out["last_compact"] = datetime.fromtimestamp(COMPACT_STAMP.stat().st_mtime,
                                                         timezone.utc).isoformat()
    except OSError:
        pass
    return out


def _overall(parts: Dict[str, object]) -> Dict[str, object]:
    """One line the user can act on, and the reasons behind it.

    Ordered by consequence, not by tidiness: no backup at all outranks an
    unprotected key, which outranks a missing codec.
    """
    problems: List[str] = []
    warnings: List[str] = []

    backup = parts.get("backup") or {}
    if isinstance(backup, dict):
        if not backup.get("configured"):
            problems.append("No backup destination is set — nothing is being backed up.")
        elif backup.get("count", 0) == 0:
            problems.append("A destination is set but no backup has been taken yet.")
        elif backup.get("recoverable_with_phrase") is False:
            warnings.append(
                "Backups can only be opened by this Mac — set up a recovery phrase."
            )

    drills = parts.get("drills") or {}
    if isinstance(drills, dict):
        # No "N of 7". That gate was dropped on 2026-09-29: there was no code
        # reason for it on a local destination with slow growth, and the one
        # genuinely time-dependent bug it would have caught was found and fixed
        # directly. What still matters is binary — has a drill ever passed, and
        # did the last one pass?
        last = drills.get("last") or {}
        if drills.get("runs", 0) == 0:
            if (parts.get("backup") or {}).get("count"):
                warnings.append(
                    "Backups have never been test-restored. Run a drill to prove they work."
                )
        elif not last.get("ok"):
            problems.append(
                "The last restore drill FAILED — these backups may not restore."
            )

    keys = parts.get("keys") or {}
    if isinstance(keys, dict):
        for purpose in keys.get("unprotected_purposes") or []:
            severity, why = _KEY_CONSEQUENCE.get(
                purpose, ("problem", "wiping the Keychain would lose it")
            )
            line = (
                f"No recovery copy of the {purpose} key — {why} "
                f"Protect it in Settings → Recovery."
            )
            (problems if severity == "problem" else warnings).append(line)

        pc = keys.get("phrase_check") or {}
        if pc.get("due"):
            warnings.append(
                f"It has been {pc['days']} days since your recovery phrase was checked — "
                f"check you still have it in Settings → Recovery."
            )

    schema = parts.get("schema") or {}
    if isinstance(schema, dict) and schema.get("pending"):
        problems.append(f"{schema['pending']} data migration(s) have not been applied.")

    if parts.get("pending_restore"):
        warnings.append("A restore is staged and will apply on the next launch.")

    volume = parts.get("volume") or {}
    if isinstance(volume, dict) and volume.get("enabled"):
        if not volume.get("mounted"):
            problems.append("Encryption is on but the encrypted volume is not mounted.")
        elif volume.get("free_bytes") is not None and 0 < int(volume["free_bytes"]) < VOLUME_LOW_FREE_BYTES:
            warnings.append("Less than 2 GB free inside the encrypted volume.")

    codec = parts.get("codec") or {}
    if isinstance(codec, dict) and codec.get("ok") is False:
        warnings.append("No audio codec found — audio features will not work.")

    return {
        "ok": not problems,
        "state": "problem" if problems else ("warning" if warnings else "healthy"),
        "problems": problems,
        "warnings": warnings,
    }
