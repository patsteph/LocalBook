"""correspondent_telemetry — Phase 5 Tier 2 (2026-06-10).

Append-only event log for the dashboard metrics that needed real data:
  - approval throughput (event_type='approval', duration_ms)
  - dedup hit rate (event_type='dedup_hit')
  - IMAP delete success rate (event_type='imap_delete', payload.ok)
  - inflow count (event_type='inflow') for dedup rate denominator

All writes are fire-and-forget: telemetry failures must never block the
ingest / approve / delete paths.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EVENT_APPROVAL = "approval"
EVENT_DEDUP_HIT = "dedup_hit"
EVENT_IMAP_DELETE = "imap_delete"
EVENT_INFLOW = "inflow"


def log_event(
    *,
    event_type: str,
    sender: Optional[str] = None,
    item_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Best-effort write. Catches every exception at debug — telemetry
    is observational, not load-bearing."""
    try:
        from storage.database import get_db
        conn = get_db().get_connection()
        conn.execute(
            """INSERT INTO correspondent_events
               (ts, event_type, sender, item_id, duration_ms, payload_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                datetime.utcnow().isoformat(),
                event_type[:64],
                (sender or "")[:200] or None,
                (item_id or "")[:64] or None,
                int(duration_ms) if duration_ms is not None else None,
                json.dumps(payload or {}),
            ),
        )
        conn.commit()
    except Exception as e:
        logger.debug(f"[correspondent_telemetry.log_event] {e}")


def _iso_since(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=days)).isoformat()


def get_approval_throughput(*, days: int = 7) -> Dict[str, Any]:
    """Avg seconds queued → approved over the window."""
    try:
        from storage.database import get_db
        row = get_db().get_connection().execute(
            """SELECT AVG(duration_ms) AS avg_ms, COUNT(*) AS n
               FROM correspondent_events
               WHERE event_type = ? AND ts >= ? AND duration_ms IS NOT NULL""",
            (EVENT_APPROVAL, _iso_since(days)),
        ).fetchone()
        if not row or not row["n"]:
            return {"avg_seconds": None, "n": 0}
        return {
            "avg_seconds": (row["avg_ms"] or 0) / 1000.0,
            "n": int(row["n"]),
        }
    except Exception as e:
        logger.debug(f"[correspondent_telemetry.get_approval_throughput] {e}")
        return {"avg_seconds": None, "n": 0}


def get_dedup_rate(*, days: int = 30) -> Dict[str, Any]:
    """dedup_hit count / (dedup_hit + inflow) over the window."""
    try:
        from storage.database import get_db
        conn = get_db().get_connection()
        since = _iso_since(days)
        dedup = conn.execute(
            "SELECT COUNT(*) AS n FROM correspondent_events WHERE event_type = ? AND ts >= ?",
            (EVENT_DEDUP_HIT, since),
        ).fetchone()
        inflow = conn.execute(
            "SELECT COUNT(*) AS n FROM correspondent_events WHERE event_type = ? AND ts >= ?",
            (EVENT_INFLOW, since),
        ).fetchone()
        d = int(dedup["n"] or 0)
        i = int(inflow["n"] or 0)
        total = d + i
        if total == 0:
            return {"rate": None, "dedup_hits": 0, "inflows": 0}
        return {
            "rate": d / total,
            "dedup_hits": d,
            "inflows": i,
        }
    except Exception as e:
        logger.debug(f"[correspondent_telemetry.get_dedup_rate] {e}")
        return {"rate": None, "dedup_hits": 0, "inflows": 0}


def get_imap_delete_rate(*, days: int = 30) -> Dict[str, Any]:
    """success / total imap_delete events over the window."""
    try:
        from storage.database import get_db
        rows = get_db().get_connection().execute(
            """SELECT payload_json FROM correspondent_events
               WHERE event_type = ? AND ts >= ?""",
            (EVENT_IMAP_DELETE, _iso_since(days)),
        ).fetchall()
        total = 0
        ok = 0
        for r in rows:
            try:
                p = json.loads(r["payload_json"] or "{}")
            except Exception:
                p = {}
            total += 1
            if p.get("ok"):
                ok += 1
        if total == 0:
            return {"rate": None, "total": 0, "ok": 0}
        return {
            "rate": ok / total,
            "total": total,
            "ok": ok,
        }
    except Exception as e:
        logger.debug(f"[correspondent_telemetry.get_imap_delete_rate] {e}")
        return {"rate": None, "total": 0, "ok": 0}
