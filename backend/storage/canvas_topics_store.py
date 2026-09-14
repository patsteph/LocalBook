"""Canvas Topics Store — persisted per-notebook SUB-TOPIC cards (Canvas evolution, Phase 2).

A sub-topic card is a durable object with its OWN identity: an embedding centroid (so new threads
accrete to it across Populates instead of the map re-clustering from scratch), an auto-generated
title + one-line synthesis, a member count, and card geometry + collapsed state. Threads
(`canvas_nodes.topic_id`) point here.

Mirrors `canvas_layout_store`: shared `localbook.db` via `storage.database.Database`, lazy idempotent
schema, internal `_*(conn, …)` helpers for unit tests, public wrappers that never raise.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SCHEMA_READY = False


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS canvas_topics (
            id            TEXT PRIMARY KEY,
            notebook_id   TEXT NOT NULL,
            title         TEXT DEFAULT '',
            synthesis     TEXT DEFAULT '',
            centroid_json TEXT DEFAULT '[]',
            member_count  INTEGER DEFAULT 0,
            x             REAL DEFAULT 0,
            y             REAL DEFAULT 0,
            width         REAL,
            height        REAL,
            collapsed     INTEGER DEFAULT 1,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_canvas_topics_nb ON canvas_topics(notebook_id)")
    conn.commit()


def _now() -> str:
    return datetime.utcnow().isoformat()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        centroid = json.loads(row["centroid_json"] or "[]")
    except Exception:
        centroid = []
    return {
        "id": row["id"],
        "notebook_id": row["notebook_id"],
        "title": row["title"] or "",
        "synthesis": row["synthesis"] or "",
        "centroid": centroid,
        "member_count": row["member_count"] or 0,
        "x": row["x"] or 0.0,
        "y": row["y"] or 0.0,
        "width": row["width"],
        "height": row["height"],
        "collapsed": bool(row["collapsed"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ── internal (connection-injected, unit-tested) ───────────────────────────────
def _list_topics(conn: sqlite3.Connection, notebook_id: str) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM canvas_topics WHERE notebook_id = ? ORDER BY created_at", (notebook_id,)
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _upsert_topic(conn: sqlite3.Connection, notebook_id: str, topic: Dict[str, Any]) -> str:
    now = _now()
    tid = topic.get("id") or str(uuid.uuid4())
    exists = conn.execute("SELECT 1 FROM canvas_topics WHERE id = ?", (tid,)).fetchone()
    centroid_json = json.dumps([float(x) for x in (topic.get("centroid") or [])])
    if exists:
        # Preserve geometry/collapsed unless explicitly provided (stability across Populates).
        sets = ["title = ?", "synthesis = ?", "centroid_json = ?", "member_count = ?", "updated_at = ?"]
        vals: List[Any] = [topic.get("title", ""), topic.get("synthesis", ""), centroid_json,
                           int(topic.get("member_count", 0)), now]
        for col in ("x", "y", "width", "height", "collapsed"):
            if col in topic and topic[col] is not None:
                sets.append(f"{col} = ?")
                vals.append(int(topic[col]) if col == "collapsed" else float(topic[col]))
        vals.append(tid)
        conn.execute(f"UPDATE canvas_topics SET {', '.join(sets)} WHERE id = ?", vals)
    else:
        conn.execute(
            "INSERT INTO canvas_topics (id, notebook_id, title, synthesis, centroid_json, "
            "member_count, x, y, width, height, collapsed, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, notebook_id, topic.get("title", ""), topic.get("synthesis", ""), centroid_json,
             int(topic.get("member_count", 0)), float(topic.get("x", 0.0)), float(topic.get("y", 0.0)),
             (float(topic["width"]) if topic.get("width") is not None else None),
             (float(topic["height"]) if topic.get("height") is not None else None),
             int(topic.get("collapsed", 1)), now, now),
        )
    conn.commit()
    return tid


def _delete_missing(conn: sqlite3.Connection, notebook_id: str, keep_ids: List[str]) -> None:
    rows = conn.execute("SELECT id FROM canvas_topics WHERE notebook_id = ?", (notebook_id,)).fetchall()
    keep = set(keep_ids or [])
    for (tid,) in rows:
        if tid not in keep:
            conn.execute("DELETE FROM canvas_topics WHERE id = ?", (tid,))
    conn.commit()


def _set_geometry(conn: sqlite3.Connection, notebook_id: str, topic_id: str,
                  x: float, y: float, width: Optional[float], height: Optional[float]) -> None:
    conn.execute(
        "UPDATE canvas_topics SET x=?, y=?, width=?, height=?, updated_at=? WHERE id=? AND notebook_id=?",
        (float(x), float(y), (float(width) if width is not None else None),
         (float(height) if height is not None else None), _now(), topic_id, notebook_id))
    conn.commit()


def _set_collapsed(conn: sqlite3.Connection, notebook_id: str, topic_id: str, collapsed: bool) -> None:
    conn.execute("UPDATE canvas_topics SET collapsed=?, updated_at=? WHERE id=? AND notebook_id=?",
                 (1 if collapsed else 0, _now(), topic_id, notebook_id))
    conn.commit()


# ── public (shared connection, never raise) ───────────────────────────────────
def _get_conn() -> sqlite3.Connection:
    global _SCHEMA_READY
    from storage.database import Database
    conn = Database().get_connection()
    if not _SCHEMA_READY:
        _ensure_schema(conn)
        _SCHEMA_READY = True
    return conn


def list_topics(notebook_id: str) -> List[Dict[str, Any]]:
    try:
        return _list_topics(_get_conn(), notebook_id)
    except Exception as e:
        logger.warning(f"[canvas_topics] list failed ({notebook_id}): {e}")
        return []


def upsert_topic(notebook_id: str, topic: Dict[str, Any]) -> Optional[str]:
    try:
        return _upsert_topic(_get_conn(), notebook_id, topic)
    except Exception as e:
        logger.warning(f"[canvas_topics] upsert failed ({notebook_id}): {e}")
        return None


def delete_missing(notebook_id: str, keep_ids: List[str]) -> None:
    try:
        _delete_missing(_get_conn(), notebook_id, keep_ids)
    except Exception as e:
        logger.warning(f"[canvas_topics] delete_missing failed ({notebook_id}): {e}")


def set_geometry(notebook_id: str, topic_id: str, x: float, y: float,
                 width: Optional[float] = None, height: Optional[float] = None) -> None:
    try:
        _set_geometry(_get_conn(), notebook_id, topic_id, x, y, width, height)
    except Exception as e:
        logger.warning(f"[canvas_topics] set_geometry failed: {e}")


def set_collapsed(notebook_id: str, topic_id: str, collapsed: bool) -> None:
    try:
        _set_collapsed(_get_conn(), notebook_id, topic_id, collapsed)
    except Exception as e:
        logger.warning(f"[canvas_topics] set_collapsed failed: {e}")
