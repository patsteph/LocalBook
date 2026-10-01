"""A companion's share of LocalBook's memory (LB-4).

Beside `memory_store` rather than in it (that file is past 1200 lines). No schema
migration: every tag rides on a field that already exists —

* **Recall**: `conversation_id = "companion:<id>:<session_id>"`, and the row id
  is a hash of (companion, session, ts, role), so `INSERT OR IGNORE` makes a
  retried `sync-turn` a no-op instead of a duplicate turn.
* **Archival**: `namespace = "companion:<id>"` — a string column. (Adding a
  column would go through LanceDB's add-column path, which DROPS the table.)
* **Core**: `source_conversation_id` carries the same `companion:<id>:…` prefix.

One shared memory (user decision 2026-09-30): LocalBook's chat reads these too.
`purge()` is the other half of that bargain — everything a companion wrote goes
in one action, from every tier, including the keyword index.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PREFIX = "companion:"
# Companion ids reach LanceDB filter strings and LIKE patterns, so they are
# validated, never escaped. The manifest ids in use (`meeting-notes`, `jocasta`)
# all fit.
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SESSION = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")


class CompanionMemoryError(ValueError):
    """A bad companion or session id."""


def _store(store=None):
    if store is not None:
        return store
    from storage.memory_store import memory_store

    return memory_store


def check_ids(companion_id: str, session_id: Optional[str] = None) -> None:
    if not _ID.match(companion_id or ""):
        raise CompanionMemoryError(f"invalid companion id {companion_id!r}")
    if session_id is not None and not _SESSION.match(session_id or ""):
        raise CompanionMemoryError("session_id must be 1–128 of [A-Za-z0-9._:@-]")


def namespace(companion_id: str) -> str:
    check_ids(companion_id)
    return f"{PREFIX}{companion_id}"


def conversation_id(companion_id: str, session_id: str) -> str:
    check_ids(companion_id, session_id)
    return f"{PREFIX}{companion_id}:{session_id}"


def turn_id(companion_id: str, session_id: str, ts: str, role: str) -> str:
    raw = "\x1f".join((companion_id, session_id, ts, role))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def parse_ts(ts) -> datetime:
    """ISO-8601 or epoch seconds → naive UTC (recall stores naive UTC isoformat)."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).replace(tzinfo=None)
    s = str(ts).strip()
    try:
        return datetime.fromtimestamp(float(s), tz=timezone.utc).replace(tzinfo=None)
    except ValueError:
        pass
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def record_turn(companion_id: str, session_id: str, role: str, content: str, ts,
                store=None) -> bool:
    """Store one turn in recall. True if new, False if this exact turn was
    already recorded (a retry)."""
    s = _store(store)
    conv = conversation_id(companion_id, session_id)
    ts_key = str(ts)
    when = parse_ts(ts)
    conn = s._get_recall_connection()
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO recall_entries
               (id, conversation_id, notebook_id, role, content, timestamp,
                topics, entities, sentiment, is_summarized, summary)
               VALUES (?, ?, NULL, ?, ?, ?, '[]', '[]', NULL, 0, NULL)""",
            (turn_id(companion_id, session_id, ts_key, role), conv, role, content,
             when.isoformat()),
        )
        conn.commit()
        return (cur.rowcount or 0) > 0
    finally:
        conn.close()


def session_turns(companion_id: str, session_id: str, limit: int = 20,
                  store=None) -> List[Dict[str, str]]:
    """The most recent turns of one session, oldest first."""
    s = _store(store)
    conn = s._get_recall_connection()
    try:
        rows = conn.execute(
            """SELECT role, content, timestamp FROM recall_entries
               WHERE conversation_id = ? ORDER BY timestamp DESC LIMIT ?""",
            (conversation_id(companion_id, session_id), int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [{"role": r, "content": c, "ts": t} for r, c, t in reversed(rows)]


def purge(companion_id: str, store=None) -> Dict[str, int]:
    """Remove everything `companion_id` wrote, from every tier.

    Core entries the companion only UPDATED (same key as an existing fact) keep
    the new value — they belonged to LocalBook first, and there is no old value
    to restore. Entries it CREATED are removed.
    """
    s = _store(store)
    ns = namespace(companion_id)
    like = f"{ns}:%"
    out = {"recall": 0, "summaries": 0, "archival": 0, "fts": 0, "core": 0}

    conn = s._get_recall_connection()
    try:
        out["recall"] = conn.execute(
            "DELETE FROM recall_entries WHERE conversation_id LIKE ?", (like,)).rowcount or 0
        try:
            out["summaries"] = conn.execute(
                "DELETE FROM conversation_summaries WHERE conversation_id LIKE ?", (like,)).rowcount or 0
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()

    try:
        if "archival_memories" in s.archival_db.table_names():
            table = s.archival_db.open_table("archival_memories")
            df = table.to_pandas()
            mask = (df["namespace"] == ns) | df["source_id"].astype(str).str.startswith(f"{ns}:")
            ids = df[mask]["id"].tolist()
            if ids:
                table.delete(f"namespace = '{ns}' OR source_id LIKE '{ns}:%'")
                out["archival"] = len(ids)
                out["fts"] = s.delete_fts(ids)
                s.delete_archival_records(ids)
    except Exception as exc:
        logger.error("[companion-memory] archival purge for %s failed: %s", companion_id, exc)
        raise

    core = s.load_core_memory()
    keep = [e for e in core.entries
            if not str(e.source_conversation_id or "").startswith(f"{ns}:")]
    out["core"] = len(core.entries) - len(keep)
    if out["core"]:
        core.entries = keep
        s.save_core_memory(core)

    logger.warning("[companion-memory] purged %s: %s", companion_id, out)
    return out
