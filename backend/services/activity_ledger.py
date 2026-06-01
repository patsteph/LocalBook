"""
Activity Ledger — single source of truth for notebook activity.

Phase B (2026-05-22). Replaces the five overlapping JSON files
(collection_history, last_engagement, query_outcomes, collection_patterns,
run_syntheses) with one append-only SQLite event log. Derived signals
(stagnation, engagement, growth rate) are computed as VIEWS over the log
instead of maintaining separate state files.

Event kinds (discriminator):
    collector_run_scheduled   - scheduled fetcher run (with items_found/approved)
    collector_run_manual      - user-triggered collector run
    collector_item_approved   - one item passed through approval queue
    source_added              - source added to notebook (payload.via specifies how)
    query_ran                 - RAG query executed
    config_updated            - collector/curator config touched
    approval_queue_cleared    - user batch-approved or cleared pending queue

`actor` is who emitted: 'user' | '@collector' | '@curator' | 'extension' | 'system'

We keep dual-writing to the old collection_history.json + last_engagement.json
during the deprecation window (see Phase B.3 wiring). The ledger is the new
source of truth; the JSONs are deprecated but still read by code that hasn't
migrated. Once everything reads from the ledger, we remove the dual writes.

Failure mode: writes never raise. If SQLite is unavailable, log and drop.
The ledger is observability + intelligence input; losing an event hurts
analytics but never breaks user-visible behavior.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Discriminator values - keep in sync with code that reads them.
KIND_COLLECTOR_RUN_SCHEDULED = "collector_run_scheduled"
KIND_COLLECTOR_RUN_MANUAL = "collector_run_manual"
KIND_COLLECTOR_ITEM_APPROVED = "collector_item_approved"
KIND_SOURCE_ADDED = "source_added"
KIND_QUERY_RAN = "query_ran"
KIND_CONFIG_UPDATED = "config_updated"
KIND_APPROVAL_QUEUE_CLEARED = "approval_queue_cleared"


# Grace + threshold defaults match the old collection_history.py constants so
# behavior carries over cleanly during the migration.
ENGAGEMENT_GRACE_DAYS = 3
COLLECTOR_DRY_MILD_DAYS = 5
COLLECTOR_DRY_MODERATE_DAYS = 10
COLLECTOR_DRY_PLATEAU_DAYS = 15


# --------------------------------------------------------------------------
# Schema (idempotent — safe to call on every backend boot).
# --------------------------------------------------------------------------

def _ensure_schema(conn) -> None:
    """Create activity_events table + indexes if not present.

    Called lazily from the first record_event() so we don't fight database.py's
    init order. Safe to call repeatedly.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notebook_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            actor TEXT,
            payload_json TEXT DEFAULT '{}'
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_activity_nb_ts "
        "ON activity_events(notebook_id, ts DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_activity_nb_kind_ts "
        "ON activity_events(notebook_id, kind, ts DESC)"
    )
    conn.commit()


_SCHEMA_READY = False


def _get_conn():
    """Get the shared SQLite connection from database.py.

    Lazy-imports to avoid circular import during backend boot.
    """
    global _SCHEMA_READY
    from storage.database import Database
    conn = Database().get_connection()
    if not _SCHEMA_READY:
        try:
            _ensure_schema(conn)
            _SCHEMA_READY = True
        except Exception as e:
            logger.warning(f"[activity_ledger] schema init failed: {e}")
    return conn


# --------------------------------------------------------------------------
# Writer
# --------------------------------------------------------------------------

def record_event(
    notebook_id: str,
    kind: str,
    actor: str = "user",
    payload: Optional[Dict[str, Any]] = None,
    ts: Optional[datetime] = None,
) -> None:
    """Append one event to the ledger.

    Never raises. Notebook_id is required (events are always notebook-scoped
    for now; we can add nullable for cross-notebook events later if needed).
    """
    if not notebook_id:
        logger.debug(f"[activity_ledger] skipping {kind}: no notebook_id")
        return
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO activity_events (notebook_id, ts, kind, actor, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                notebook_id,
                (ts or datetime.utcnow()).isoformat(),
                kind,
                actor,
                json.dumps(payload or {}, default=str),
            ),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"[activity_ledger] record_event failed ({kind}): {e}")


# --------------------------------------------------------------------------
# Derived views (Phase B.2)
# --------------------------------------------------------------------------
#
# These are the queries the rest of the system uses to make decisions. By
# expressing them as SQL over the ledger we get one consistent answer instead
# of five different files disagreeing.

# Events that count as user engagement (suppress stagnation alerts when recent).
ENGAGEMENT_KINDS = (
    KIND_SOURCE_ADDED,
    KIND_COLLECTOR_RUN_MANUAL,
    KIND_CONFIG_UPDATED,
    KIND_APPROVAL_QUEUE_CLEARED,
    KIND_QUERY_RAN,
)

# Events that count as collector-driven growth (used to decide if the
# collector has gone dry).
COLLECTOR_GROWTH_KINDS = (
    KIND_COLLECTOR_ITEM_APPROVED,
)


def engagement_active(
    notebook_id: str,
    grace_days: int = ENGAGEMENT_GRACE_DAYS,
) -> Dict[str, Any]:
    """Has the user engaged with this notebook within grace_days?

    Returns:
        {
            "active": bool,
            "last_ts": ISO str | None,
            "last_kind": str | None,
            "days_since": int | None,
        }
    """
    try:
        conn = _get_conn()
        placeholders = ",".join("?" * len(ENGAGEMENT_KINDS))
        row = conn.execute(
            f"""
            SELECT ts, kind FROM activity_events
            WHERE notebook_id = ? AND kind IN ({placeholders})
            ORDER BY ts DESC LIMIT 1
            """,
            (notebook_id, *ENGAGEMENT_KINDS),
        ).fetchone()
        if not row:
            return {"active": False, "last_ts": None, "last_kind": None, "days_since": None}
        last_ts_str = row[0] if not hasattr(row, "keys") else row["ts"]
        last_kind = row[1] if not hasattr(row, "keys") else row["kind"]
        try:
            last_dt = datetime.fromisoformat(last_ts_str)
        except Exception:
            return {"active": False, "last_ts": last_ts_str, "last_kind": last_kind, "days_since": None}
        days_since = (datetime.utcnow() - last_dt).days
        return {
            "active": days_since < grace_days,
            "last_ts": last_ts_str,
            "last_kind": last_kind,
            "days_since": days_since,
        }
    except Exception as e:
        logger.debug(f"[activity_ledger] engagement_active failed: {e}")
        return {"active": False, "last_ts": None, "last_kind": None, "days_since": None}


def collector_dry(
    notebook_id: str,
    threshold_days: int = COLLECTOR_DRY_MILD_DAYS,
) -> Dict[str, Any]:
    """Is the collector failing to find new approved items?

    Returns:
        {
            "dry": bool,
            "severity": None | 'mild' | 'moderate' | 'plateau',
            "days_since_growth": int,
            "last_growth_ts": ISO str | None,
        }
    """
    try:
        conn = _get_conn()
        # Last collector growth event for this notebook.
        placeholders = ",".join("?" * len(COLLECTOR_GROWTH_KINDS))
        row = conn.execute(
            f"""
            SELECT ts FROM activity_events
            WHERE notebook_id = ? AND kind IN ({placeholders})
            ORDER BY ts DESC LIMIT 1
            """,
            (notebook_id, *COLLECTOR_GROWTH_KINDS),
        ).fetchone()
        last_growth_ts = (row[0] if not hasattr(row, "keys") else row["ts"]) if row else None

        # Has the collector run at all? If never run, "dry" is meaningless.
        run_row = conn.execute(
            """
            SELECT ts FROM activity_events
            WHERE notebook_id = ? AND kind = ?
            ORDER BY ts DESC LIMIT 1
            """,
            (notebook_id, KIND_COLLECTOR_RUN_SCHEDULED),
        ).fetchone()
        if not run_row:
            return {
                "dry": False,
                "severity": None,
                "days_since_growth": 0,
                "last_growth_ts": last_growth_ts,
            }

        now = datetime.utcnow()
        if last_growth_ts:
            try:
                last_dt = datetime.fromisoformat(last_growth_ts)
                days_since = (now - last_dt).days
            except Exception:
                days_since = 0
        else:
            # Collector has run but never approved an item — measure from first run.
            first_run_row = conn.execute(
                """
                SELECT ts FROM activity_events
                WHERE notebook_id = ? AND kind = ?
                ORDER BY ts ASC LIMIT 1
                """,
                (notebook_id, KIND_COLLECTOR_RUN_SCHEDULED),
            ).fetchone()
            if first_run_row:
                first_ts = first_run_row[0] if not hasattr(first_run_row, "keys") else first_run_row["ts"]
                try:
                    days_since = (now - datetime.fromisoformat(first_ts)).days
                except Exception:
                    days_since = 0
            else:
                days_since = 0

        if days_since >= COLLECTOR_DRY_PLATEAU_DAYS:
            severity: Optional[str] = "plateau"
        elif days_since >= COLLECTOR_DRY_MODERATE_DAYS:
            severity = "moderate"
        elif days_since >= COLLECTOR_DRY_MILD_DAYS:
            severity = "mild"
        else:
            severity = None

        return {
            "dry": severity is not None,
            "severity": severity,
            "days_since_growth": days_since,
            "last_growth_ts": last_growth_ts,
        }
    except Exception as e:
        logger.debug(f"[activity_ledger] collector_dry failed: {e}")
        return {"dry": False, "severity": None, "days_since_growth": 0, "last_growth_ts": None}


def notebook_growth_rate(
    notebook_id: str,
    window_days: int = 7,
) -> float:
    """Source-add events per day over the last window_days.

    Used by Phase D triggers ("engagement_relevant") to decide if a notebook
    is hot enough to mention. Returns 0.0 if nothing happened in the window.
    """
    try:
        conn = _get_conn()
        cutoff = (datetime.utcnow() - timedelta(days=window_days)).isoformat()
        row = conn.execute(
            """
            SELECT COUNT(*) FROM activity_events
            WHERE notebook_id = ? AND kind = ? AND ts >= ?
            """,
            (notebook_id, KIND_SOURCE_ADDED, cutoff),
        ).fetchone()
        count = row[0] if row else 0
        return count / max(window_days, 1)
    except Exception as e:
        logger.debug(f"[activity_ledger] growth_rate failed: {e}")
        return 0.0


def last_activity(notebook_id: str) -> Optional[str]:
    """Max ts across all events for the notebook. None if no activity."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT MAX(ts) FROM activity_events WHERE notebook_id = ?",
            (notebook_id,),
        ).fetchone()
        if not row or not row[0]:
            return None
        return row[0]
    except Exception as e:
        logger.debug(f"[activity_ledger] last_activity failed: {e}")
        return None


def recent_events(
    notebook_id: str,
    limit: int = 50,
    kinds: Optional[Tuple[str, ...]] = None,
) -> List[Dict[str, Any]]:
    """Read recent events for a notebook (newest first). Diagnostics + UI."""
    try:
        conn = _get_conn()
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            rows = conn.execute(
                f"""
                SELECT id, notebook_id, ts, kind, actor, payload_json
                FROM activity_events
                WHERE notebook_id = ? AND kind IN ({placeholders})
                ORDER BY ts DESC LIMIT ?
                """,
                (notebook_id, *kinds, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, notebook_id, ts, kind, actor, payload_json
                FROM activity_events
                WHERE notebook_id = ?
                ORDER BY ts DESC LIMIT ?
                """,
                (notebook_id, limit),
            ).fetchall()
        out = []
        for r in rows:
            payload_str = r[5] if not hasattr(r, "keys") else r["payload_json"]
            try:
                payload = json.loads(payload_str or "{}")
            except Exception:
                payload = {}
            out.append({
                "id": r[0] if not hasattr(r, "keys") else r["id"],
                "notebook_id": r[1] if not hasattr(r, "keys") else r["notebook_id"],
                "ts": r[2] if not hasattr(r, "keys") else r["ts"],
                "kind": r[3] if not hasattr(r, "keys") else r["kind"],
                "actor": r[4] if not hasattr(r, "keys") else r["actor"],
                "payload": payload,
            })
        return out
    except Exception as e:
        logger.debug(f"[activity_ledger] recent_events failed: {e}")
        return []
