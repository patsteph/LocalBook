"""Spaced-repetition review state for Journey Canvas nodes (Run R1 — recall).

Tracks, per (notebook, node), when a learning node was last reviewed and its next
due time on a lightweight SM-2-style schedule. Mirrors `canvas_layout_store`'s pattern:
shared production connection via `Database()`, lazy idempotent schema, never raises,
pure `_*(conn, ...)` helpers wrapped by never-raising public functions.

A node is "recall-eligible" = a learning node (chat turn / canvas answer). The API layer
joins these schedule rows against the live layout to decide what's DUE right now.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SCHEMA_READY = False

# SM-2-lite defaults.
_DEFAULT_EASE = 2.3
_MIN_EASE = 1.3
_DAY = 86400.0


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS canvas_recall (
            notebook_id   TEXT NOT NULL,
            node_id       TEXT NOT NULL,
            last_reviewed REAL NOT NULL,
            interval_days REAL NOT NULL,
            ease          REAL NOT NULL DEFAULT 2.3,
            reps          INTEGER NOT NULL DEFAULT 0,
            next_due      REAL NOT NULL,
            PRIMARY KEY (notebook_id, node_id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_canvas_recall_nb ON canvas_recall(notebook_id)")
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    global _SCHEMA_READY
    from storage.database import Database

    conn = Database().get_connection()
    if not _SCHEMA_READY:
        _ensure_schema(conn)
        _SCHEMA_READY = True
    return conn


# --------------------------------------------------------------------------
# Pure helpers (explicit connection — unit-testable).
# --------------------------------------------------------------------------
def _states(conn: sqlite3.Connection, notebook_id: str) -> Dict[str, Dict[str, Any]]:
    rows = conn.execute(
        "SELECT node_id, last_reviewed, interval_days, ease, reps, next_due "
        "FROM canvas_recall WHERE notebook_id = ?",
        (notebook_id,),
    ).fetchall()
    return {
        r[0]: {"last_reviewed": r[1], "interval_days": r[2], "ease": r[3],
               "reps": r[4], "next_due": r[5]}
        for r in rows
    }


def _next_interval(grade: str, interval: float, ease: float, reps: int) -> tuple[float, float]:
    """SM-2-lite. Returns (new_interval_days, new_ease). Grades: again / good / easy."""
    if grade == "again":
        return 1.0, max(_MIN_EASE, ease - 0.2)
    if grade == "easy":
        base = interval if interval > 0 else 4.0
        return max(4.0, base * ease * 1.3), ease + 0.15
    # "good" (default)
    if reps <= 0:
        return 1.0, ease
    if reps == 1:
        return 3.0, ease
    return max(1.0, (interval if interval > 0 else 3.0) * ease), ease


def _review(conn: sqlite3.Connection, notebook_id: str, node_id: str, grade: str,
            now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    row = conn.execute(
        "SELECT interval_days, ease, reps FROM canvas_recall WHERE notebook_id = ? AND node_id = ?",
        (notebook_id, node_id),
    ).fetchone()
    interval, ease, reps = (row[0], row[1], row[2]) if row else (0.0, _DEFAULT_EASE, 0)
    new_interval, new_ease = _next_interval(grade, interval, ease, reps)
    reps += 1
    next_due = now + new_interval * _DAY
    conn.execute(
        """
        INSERT INTO canvas_recall (notebook_id, node_id, last_reviewed, interval_days, ease, reps, next_due)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(notebook_id, node_id) DO UPDATE SET
            last_reviewed = excluded.last_reviewed,
            interval_days = excluded.interval_days,
            ease          = excluded.ease,
            reps          = excluded.reps,
            next_due      = excluded.next_due
        """,
        (notebook_id, node_id, now, new_interval, new_ease, reps, next_due),
    )
    conn.commit()
    return {"node_id": node_id, "interval_days": new_interval, "ease": new_ease,
            "reps": reps, "next_due": next_due}


# --------------------------------------------------------------------------
# Public API (never raises).
# --------------------------------------------------------------------------
def get_states(notebook_id: str) -> Dict[str, Dict[str, Any]]:
    """node_id → {last_reviewed, interval_days, ease, reps, next_due} for a notebook."""
    try:
        return _states(_get_conn(), notebook_id)
    except Exception as e:
        logger.warning(f"[canvas_recall] get_states failed ({notebook_id}): {e}")
        return {}


def review(notebook_id: str, node_id: str, grade: str) -> Dict[str, Any]:
    """Record a review outcome and advance the schedule. grade ∈ {again, good, easy}."""
    try:
        return _review(_get_conn(), notebook_id, node_id, grade)
    except Exception as e:
        logger.warning(f"[canvas_recall] review failed ({notebook_id}/{node_id}): {e}")
        return {"node_id": node_id, "interval_days": 1.0, "ease": _DEFAULT_EASE,
                "reps": 0, "next_due": time.time() + _DAY}
