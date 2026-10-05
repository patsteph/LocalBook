"""What companions actually did, and when.

LB-2 of the v2.5.0 plan. Once an agent can search every notebook, read any
source and fetch the web through LocalBook, "what has it been doing?" stops
being a curiosity and becomes the only way to answer it. Settings → Companions
shows these rows and can purge them per companion.

**Arguments are hashed, never stored.** A tool call carries the user's own
questions and search terms — `ask_notebook("what did the oncologist say")` is
exactly the kind of thing that must not accumulate in a plaintext log that
LB-11 does not yet cover and LB-10 will back up. The hash still answers the
question the log is for: "is this the same call repeating?" A short, non-secret
`preview` of the tool NAME and the argument KEYS is kept, because a log you
cannot read at a glance does not get read.

Per-machine, never synced (LB-12h) — a call happened on one Mac.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TABLE = "companion_calls"

# Outcomes are a closed set so the Settings screen can group by them without
# guessing at free text.
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"
OUTCOME_DENIED = "denied"
OUTCOME_BUSY = "busy"
OUTCOMES = (OUTCOME_OK, OUTCOME_ERROR, OUTCOME_DENIED, OUTCOME_BUSY)


def _connection():
    from storage.database import get_db

    return get_db().get_connection()


def ensure_table() -> None:
    """Create the table if it is not there. Cheap, idempotent, called on use.

    Not folded into `database._init_schema` on purpose: that runs for every
    LocalBook process including ones that never serve a companion, and this
    table is only meaningful once `/mcp` is mounted.
    """
    conn = _connection()
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            companion_id TEXT    NOT NULL,
            tool         TEXT    NOT NULL,
            args_hash    TEXT    NOT NULL,
            args_preview TEXT,
            ts           REAL    NOT NULL,
            ms           INTEGER NOT NULL,
            outcome      TEXT    NOT NULL,
            detail       TEXT
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_companion_ts "
        f"ON {TABLE}(companion_id, ts DESC)"
    )
    conn.commit()


def hash_args(args: Optional[Dict[str, Any]]) -> str:
    """Stable hash of the arguments, so repeats are recognisable.

    Sorted keys and a canonical separator, so `{a:1, b:2}` and `{b:2, a:1}` are
    the same call — which is what "is this repeating?" means.
    """
    try:
        canonical = json.dumps(args or {}, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        canonical = repr(args)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def preview_args(args: Optional[Dict[str, Any]]) -> str:
    """Argument NAMES only — never their values."""
    if not args:
        return ""
    return ",".join(sorted(str(k) for k in args.keys()))


def record(
    *,
    companion_id: str,
    tool: str,
    args: Optional[Dict[str, Any]] = None,
    ms: int = 0,
    outcome: str = OUTCOME_OK,
    detail: Optional[str] = None,
) -> None:
    """Write one row. Never raises: an audit failure must not fail the call it
    describes, or the log becomes a new way for the feature to break."""
    if outcome not in OUTCOMES:
        outcome = OUTCOME_ERROR
    try:
        ensure_table()
        conn = _connection()
        conn.execute(
            f"INSERT INTO {TABLE} "
            f"(companion_id, tool, args_hash, args_preview, ts, ms, outcome, detail) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                companion_id,
                tool,
                hash_args(args),
                preview_args(args),
                time.time(),
                int(ms),
                outcome,
                (detail or "")[:500] or None,
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("[companion-audit] could not record %s/%s: %s", companion_id, tool, exc)


def recent(companion_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Newest first. `companion_id` None means every companion."""
    try:
        ensure_table()
        conn = _connection()
        limit = max(1, min(int(limit), 1000))
        if companion_id:
            rows = conn.execute(
                f"SELECT * FROM {TABLE} WHERE companion_id = ? ORDER BY ts DESC LIMIT ?",
                (companion_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM {TABLE} ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[companion-audit] could not read the log: %s", exc)
        return []


def purge(companion_id: Optional[str] = None) -> int:
    """Delete one companion's rows, or all of them. Returns how many went."""
    try:
        ensure_table()
        conn = _connection()
        if companion_id:
            cur = conn.execute(f"DELETE FROM {TABLE} WHERE companion_id = ?", (companion_id,))
        else:
            cur = conn.execute(f"DELETE FROM {TABLE}")
        conn.commit()
        return cur.rowcount or 0
    except Exception as exc:
        logger.warning("[companion-audit] could not purge: %s", exc)
        return 0


@dataclass
class CallTimer:
    """Times a tool call and writes exactly one row for it, whatever happens.

    Used as a context manager so an exception still produces a row — a log that
    only records successes answers the least interesting version of the
    question.
    """

    companion_id: str
    tool: str
    args: Optional[Dict[str, Any]] = None
    outcome: str = OUTCOME_OK
    detail: Optional[str] = None
    _start: float = 0.0

    def __enter__(self) -> "CallTimer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None and self.outcome == OUTCOME_OK:
            self.outcome = OUTCOME_ERROR
            self.detail = f"{exc_type.__name__}: {exc}"
        record(
            companion_id=self.companion_id,
            tool=self.tool,
            args=self.args,
            ms=int((time.perf_counter() - self._start) * 1000),
            outcome=self.outcome,
            detail=self.detail,
        )
        return False  # never swallow the exception
