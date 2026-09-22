"""Folder links + the ingest ledger.

A *folder link* is a directory on the user's disk that LocalBook watches and
ingests from. Two invariants govern everything here:

  1. **Read-only.** Nothing in this module — or in `services/folder_watcher.py`
     — ever writes, moves, renames or deletes inside a linked folder. The user's
     files are theirs. We stat them and read them, full stop.

  2. **Never ingest the same file twice.** `folder_seen` is the ledger. The
     `(mtime, size)` pair is the FAST check: a rescan of 500 files costs a
     `scandir` plus 500 stats, not 500 reads. `content_hash` is the CORRECT
     check: it catches a file that was renamed or moved within the folder, which
     mtime alone cannot, and it is cross-checked against `source_store` so a file
     already added by drag-and-drop is not duplicated either.

`notebook_id IS NULL` marks a Smart Folder — scanned identically, but the
destination is decided per file rather than fixed on the link.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)

def _default_patterns() -> List[str]:
    """Everything LocalBook can actually read — not a curated subset.

    A linked folder takes what is in it. The user picked the folder; asking
    them to also predict which file types their recorder writes is a question
    with no good answer and a bad failure mode (a format they didn't list is
    silently ignored forever). Sourced from `document_processor` so adding a
    format there makes it visible here with no second edit.
    """
    try:
        from services.document_processor import INGESTIBLE_EXTENSIONS
        return sorted(f"*.{e}" for e in INGESTIBLE_EXTENSIONS)
    except Exception:
        return ["*.md", "*.markdown", "*.txt", "*.pdf", "*.docx"]


DEFAULT_PATTERNS = _default_patterns()

# Mirrors collection_scheduler.INTERVALS — the cadence vocabulary the Collector
# already uses, so the user meets one set of words, not two.
VALID_FREQUENCIES = (
    "hourly", "every_2_hours", "every_4_hours", "every_8_hours",
    "twice_daily", "daily", "every_3_days", "weekly", "manual",
)


class FolderLinkPathError(ValueError):
    """The chosen path cannot be linked, with a reason fit to show the user."""


def validate_folder_path(raw: str) -> Path:
    """Resolve and vet a user-chosen folder. Raises FolderLinkPathError.

    Rejects LocalBook's own data directory: linking it would have the scanner
    ingesting the database it writes to, which is a loop, not a feature.
    """
    if not raw or not str(raw).strip():
        raise FolderLinkPathError("No folder selected.")
    p = Path(str(raw).strip()).expanduser()
    try:
        p = p.resolve()
    except OSError as e:
        raise FolderLinkPathError(f"Could not resolve that path ({e.__class__.__name__}).")
    if not p.exists():
        raise FolderLinkPathError(f"{p} does not exist.")
    if not p.is_dir():
        raise FolderLinkPathError(f"{p} is not a folder.")
    try:
        data_dir = Path(settings.data_dir).resolve()
        if p == data_dir or data_dir in p.parents or p in data_dir.parents:
            raise FolderLinkPathError(
                "That folder overlaps LocalBook's own data directory and cannot be linked."
            )
    except FolderLinkPathError:
        raise
    except Exception:
        pass
    return p


class FolderLinkStore:
    """CRUD over `folder_links` + `folder_seen`. Single writer for both."""

    def _db(self):
        from storage.database import get_db
        return get_db().get_connection()

    # ── links ────────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_link(row) -> Dict[str, Any]:
        d = dict(row)
        try:
            d["patterns"] = json.loads(d.get("patterns") or "[]") or list(DEFAULT_PATTERNS)
        except Exception:
            d["patterns"] = list(DEFAULT_PATTERNS)
        # Exclusions beat inclusions. A tool that writes the same notes twice —
        # once as markdown, once as a styled page — would otherwise put both in
        # the corpus, and a document indexed twice is worse than one indexed
        # once: it crowds retrieval with its own duplicate.
        try:
            d["exclude"] = json.loads(d.get("exclude") or "[]") or []
        except Exception:
            d["exclude"] = []
        d["enabled"] = bool(d.get("enabled"))
        d["recursive"] = bool(d.get("recursive"))
        d["is_smart"] = d.get("notebook_id") is None
        return d

    def list_links(self, notebook_id: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._db()
        if notebook_id:
            rows = conn.execute(
                "SELECT * FROM folder_links WHERE notebook_id = ? ORDER BY created_at",
                (notebook_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM folder_links ORDER BY created_at"
            ).fetchall()
        return [self._row_to_link(r) for r in rows]

    def get_link(self, link_id: str) -> Optional[Dict[str, Any]]:
        row = self._db().execute(
            "SELECT * FROM folder_links WHERE id = ?", (link_id,)
        ).fetchone()
        return self._row_to_link(row) if row else None

    def find_by_path(self, path: str, notebook_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """The same folder may feed several notebooks; the same folder twice into
        the SAME notebook is a mistake, not an intent."""
        row = self._db().execute(
            "SELECT * FROM folder_links WHERE path = ? AND notebook_id IS ?",
            (path, notebook_id),
        ).fetchone()
        return self._row_to_link(row) if row else None

    def create_link(
        self,
        *,
        path: str,
        notebook_id: Optional[str] = None,
        patterns: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        frequency: str = "hourly",
        recursive: bool = False,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        resolved = str(validate_folder_path(path))
        if frequency not in VALID_FREQUENCIES:
            raise FolderLinkPathError(f"Unknown frequency '{frequency}'.")
        existing = self.find_by_path(resolved, notebook_id)
        if existing:
            raise FolderLinkPathError("That folder is already linked to this notebook.")
        link_id = str(uuid.uuid4())
        conn = self._db()
        conn.execute(
            """INSERT INTO folder_links
               (id, notebook_id, path, patterns, exclude, frequency, enabled,
                recursive, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (link_id, notebook_id, resolved,
             json.dumps(patterns or list(DEFAULT_PATTERNS)),
             json.dumps(exclude or []),
             frequency, 1 if enabled else 0, 1 if recursive else 0,
             datetime.utcnow().isoformat()),
        )
        conn.commit()
        logger.info(f"[folder-links] linked {resolved} → {notebook_id or 'SMART'} ({frequency})")
        return self.get_link(link_id)

    _UPDATABLE = {"patterns", "exclude", "frequency", "enabled", "recursive",
                  "notebook_id"}

    def update_link(self, link_id: str, **fields) -> Optional[Dict[str, Any]]:
        sets, vals = [], []
        for k, v in fields.items():
            if k not in self._UPDATABLE or v is None:
                continue
            if k in ("patterns", "exclude"):
                v = json.dumps(list(v))
            elif k in ("enabled", "recursive"):
                v = 1 if v else 0
            elif k == "frequency" and v not in VALID_FREQUENCIES:
                raise FolderLinkPathError(f"Unknown frequency '{v}'.")
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            return self.get_link(link_id)
        conn = self._db()
        conn.execute(f"UPDATE folder_links SET {', '.join(sets)} WHERE id = ?",
                     (*vals, link_id))
        conn.commit()
        return self.get_link(link_id)

    def delete_link(self, link_id: str) -> bool:
        """Unlink. Deliberately leaves the ingested SOURCES alone — the user
        asked to stop watching a folder, not to lose what it already taught the
        notebook. Deleting sources is a separate, explicit act."""
        conn = self._db()
        cur = conn.execute("DELETE FROM folder_links WHERE id = ?", (link_id,))
        conn.execute("DELETE FROM folder_seen WHERE link_id = ?", (link_id,))
        conn.commit()
        return cur.rowcount > 0

    def touch_scan(self, link_id: str, *, error: Optional[str] = None,
                   ingested: int = 0) -> None:
        conn = self._db()
        conn.execute(
            """UPDATE folder_links
               SET last_scan_at = ?, last_error = ?, files_ingested = files_ingested + ?
               WHERE id = ?""",
            (datetime.utcnow().isoformat(), (error or None), int(ingested), link_id),
        )
        conn.commit()

    # ── the ledger ───────────────────────────────────────────────────────
    def seen_map(self, link_id: str) -> Dict[str, Dict[str, Any]]:
        """abs_path → ledger row, for one link. One query per scan."""
        rows = self._db().execute(
            "SELECT * FROM folder_seen WHERE link_id = ?", (link_id,)
        ).fetchall()
        return {r["abs_path"]: dict(r) for r in rows}

    def find_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        """Any link, any path — the ledger is global on content. A file moved
        from one watched folder to another is still the same file."""
        if not content_hash:
            return None
        row = self._db().execute(
            "SELECT * FROM folder_seen WHERE content_hash = ? AND status = 'ingested' LIMIT 1",
            (content_hash,),
        ).fetchone()
        return dict(row) if row else None

    def record(
        self,
        *,
        link_id: str,
        abs_path: str,
        mtime: float,
        size: int,
        content_hash: Optional[str] = None,
        source_id: Optional[str] = None,
        notebook_id: Optional[str] = None,
        status: str = "ingested",
        error: Optional[str] = None,
    ) -> None:
        now = datetime.utcnow().isoformat()
        conn = self._db()
        conn.execute(
            """INSERT INTO folder_seen
                 (link_id, abs_path, mtime, size, content_hash, source_id,
                  notebook_id, status, error, first_seen_at, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(link_id, abs_path) DO UPDATE SET
                 mtime = excluded.mtime, size = excluded.size,
                 content_hash = COALESCE(excluded.content_hash, folder_seen.content_hash),
                 source_id = COALESCE(excluded.source_id, folder_seen.source_id),
                 notebook_id = COALESCE(excluded.notebook_id, folder_seen.notebook_id),
                 status = excluded.status, error = excluded.error,
                 ingested_at = excluded.ingested_at""",
            (link_id, abs_path, float(mtime), int(size), content_hash, source_id,
             notebook_id, status, error, now,
             now if status == "ingested" else None),
        )
        conn.commit()

    def forget_path(self, link_id: str, abs_path: str) -> None:
        conn = self._db()
        conn.execute("DELETE FROM folder_seen WHERE link_id = ? AND abs_path = ?",
                     (link_id, abs_path))
        conn.commit()

    def stats(self, link_id: str) -> Dict[str, int]:
        row = self._db().execute(
            """SELECT
                 SUM(status = 'ingested') AS ingested,
                 SUM(status = 'failed')   AS failed,
                 SUM(status = 'skipped')  AS skipped
               FROM folder_seen WHERE link_id = ?""",
            (link_id,),
        ).fetchone()
        return {k: int(row[k] or 0) for k in ("ingested", "failed", "skipped")}


folder_link_store = FolderLinkStore()
