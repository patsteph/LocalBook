"""After the swap: prove the encrypted volume holds what the kept copy holds.

LB-11, the simplified flow. The wizard offers to remove the plaintext copy only
on the strength of this check — it replaces "look at your notebooks", which is
weaker than the machine comparing every table and every file.

Two phases, because of two constraints that pull opposite ways:

* **Databases, at import** (`verify_databases`), right after the swap and before
  anything opens a database — the only moment row counts cannot have drifted
  because the running app wrote to them. Cheap: integrity + COUNT(*) per table.
* **Files, on a background thread after startup** (`verify_files`). Hashing a
  large corpus at import would blow the 30 s Tauri gives the backend to answer
  health, and a watchdog restart mid-hash would re-run it forever. The price of
  running late is that the app may have touched files since; a file rewritten
  after the swap (mtime later than the swap) is counted, not failed, and a file
  missing from a directory the app modified since is counted the same way.

The result lives in `last_apply` as `verified`, beside the data dir. The files
counted rather than failed are NAMED there (`changed_files` / `removed_files`,
capped at `NAMED_LIMIT`), so what was waved through is always inspectable.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from services import encryption_migration as em

logger = logging.getLogger(__name__)

# How many changed/removed file names to keep. The counts are always exact.
NAMED_LIMIT = 50


def _kept_copy(last: Optional[Dict[str, object]]) -> Optional[Path]:
    if not last or not last.get("applied") or not last.get("plaintext_kept_at"):
        return None
    path = Path(str(last["plaintext_kept_at"]))
    return path if path.is_dir() else None


def _swap_time(last: Dict[str, object]) -> float:
    try:
        return datetime.fromisoformat(str(last.get("at"))).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _save_verified(verified: Dict[str, object]) -> None:
    """Merge `verified` into last_apply. Temp + replace: a torn write here would
    lose the record of where the plaintext copy is."""
    path = em._last_apply_path()
    try:
        data = json.loads(path.read_text())
    except Exception:
        return
    data["verified"] = verified
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _finalise(v: Dict[str, object]) -> Dict[str, object]:
    """`ok` only once BOTH phases have run."""
    if "databases" in v and "files" in v:
        v["state"] = "done"
        v["ok"] = (not v.get("db_errors") and not v.get("row_count_drift")
                   and all(d.get("ok") for d in v["databases"].values())
                   and not v.get("mismatched_files"))
    else:
        v["state"] = "running"
        v.pop("ok", None)
    return v


def needs_databases() -> bool:
    last = em.last_apply()
    return _kept_copy(last) is not None and "databases" not in (last.get("verified") or {})


def needs_files() -> bool:
    last = em.last_apply()
    return _kept_copy(last) is not None and "files" not in (last.get("verified") or {})


def verify_databases() -> Optional[Dict[str, object]]:
    """Phase A. Call only before any database is opened (main.py, at import)."""
    last = em.last_apply()
    kept = _kept_copy(last)
    if kept is None:
        return None
    started = time.monotonic()
    report = em.MigrationReport(stage="verifying swap")
    try:
        # _verify's database half only: an `only` set that matches no file.
        em._verify(kept, em._data_dir(), report, only=set())
    except Exception as exc:
        report.errors.append(str(exc))
    v = dict(last.get("verified") or {})
    v.update({
        "databases": report.databases,
        "row_count_drift": report.row_count_drift,
        "db_errors": report.errors,
        "db_seconds": round(time.monotonic() - started, 2),
    })
    v = _finalise(v)
    _save_verified(v)
    logger.info("[encrypt] swap check, databases: %s",
                "ok" if not report.errors and not report.row_count_drift
                else f"PROBLEMS {report.errors or report.row_count_drift}")
    return v


def verify_files() -> Optional[Dict[str, object]]:
    """Phase B. Blocking — run off the event loop."""
    last = em.last_apply()
    kept = _kept_copy(last)
    if kept is None:
        return None
    started = time.monotonic()
    target = em._data_dir()
    swapped_at = _swap_time(last)
    checked = 0
    mismatched, changed, removed = [], [], []
    for entry in kept.rglob("*"):
        if not entry.is_file():
            continue
        rel = entry.relative_to(kept)
        if em._should_skip(rel):
            continue
        dst = target / rel
        checked += 1
        try:
            if not dst.is_file():
                parent = dst.parent
                if parent.is_dir() and parent.stat().st_mtime > swapped_at:
                    removed.append(str(rel))   # the app has changed this folder since
                else:
                    mismatched.append(f"{rel} (missing)")
                continue
            if dst.stat().st_mtime > swapped_at:
                changed.append(str(rel))   # rewritten by the app since the swap
                continue
            if em._sha256(entry) != em._sha256(dst):
                mismatched.append(str(rel))
        except OSError as exc:
            mismatched.append(f"{rel} (unreadable: {exc})")
    v = dict((em.last_apply() or {}).get("verified") or {})
    v.update({
        "files": checked,
        "changed_since_swap": len(changed),
        "removed_since_swap": len(removed),
        "changed_files": sorted(changed)[:NAMED_LIMIT],
        "removed_files": sorted(removed)[:NAMED_LIMIT],
        "mismatched_files": mismatched,
        "file_seconds": round(time.monotonic() - started, 2),
    })
    v = _finalise(v)
    _save_verified(v)
    logger.info("[encrypt] swap check, files: %d checked, %d changed since, %d problems",
                checked, len(changed), len(mismatched))
    return v


def verified_for(copy_path: str) -> Optional[Dict[str, object]]:
    """The verification that belongs to this kept copy, if any."""
    last = em.last_apply() or {}
    if str(last.get("plaintext_kept_at")) != str(copy_path):
        return None
    return last.get("verified")
