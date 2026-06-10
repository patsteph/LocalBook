"""routing_telemetry — Phase 4 Tier 2 / J (2026-06-10).

Logs each Correspondent routing decision so the user can audit threshold
tuning via `@correspondent show routing`.

One-row writes from `agents/correspondent.py` per IMAP-poll route call.
The histogram reader bins by 0.05 cosine buckets over a 14-day window.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def log_decision(
    *,
    sender: str,
    top_cosine: float,
    threshold: float,
    decision_verb: str,
    top_notebook_id: Optional[str] = None,
    bias_applied: Optional[str] = None,
) -> None:
    """Best-effort write. Failures are swallowed at debug — telemetry
    must never block the routing path."""
    try:
        from storage.database import get_db
        get_db().get_connection().execute(
            """INSERT INTO routing_decisions
               (ts, sender, top_cosine, threshold, decision_verb, top_notebook_id, bias_applied)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.utcnow().isoformat(),
                (sender or "")[:200],
                float(top_cosine),
                float(threshold),
                decision_verb[:32],
                top_notebook_id,
                bias_applied,
            ),
        )
        get_db().get_connection().commit()
    except Exception as e:
        logger.debug(f"[routing_telemetry] log failed: {e}")


def get_distribution(*, days: int = 14, bucket_size: float = 0.05) -> Dict[str, Any]:
    """Read recent decisions, bucket by cosine, return histogram payload
    plus summary stats.

    Returns:
      {
        "buckets": [{"lo": float, "hi": float, "auto": int, "queued": int}],
        "total": int,
        "auto_rate": float,
        "threshold": float (most recent),
        "window_days": int,
      }
    """
    try:
        from storage.database import get_db
        since_iso = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = get_db().get_connection().execute(
            """SELECT top_cosine, decision_verb, threshold
               FROM routing_decisions
               WHERE ts >= ?
               ORDER BY ts DESC""",
            (since_iso,),
        ).fetchall()
    except Exception as e:
        logger.debug(f"[routing_telemetry.get_distribution] read failed: {e}")
        return {"buckets": [], "total": 0, "auto_rate": 0.0, "threshold": 0.75, "window_days": days}

    rows = [dict(r) for r in rows]
    if not rows:
        return {"buckets": [], "total": 0, "auto_rate": 0.0, "threshold": 0.75, "window_days": days}

    # Bin 0..1 into bucket_size-width buckets.
    n_buckets = int(round(1.0 / bucket_size))
    buckets: List[Dict[str, Any]] = []
    for i in range(n_buckets):
        lo = round(i * bucket_size, 3)
        hi = round((i + 1) * bucket_size, 3)
        buckets.append({"lo": lo, "hi": hi, "auto": 0, "queued": 0})

    total = 0
    auto = 0
    manual = 0
    # Q6 (2026-06-10) — bucket adds a 'manual' column so the user can
    # see where their approvals are landing vs the auto-route line.
    for b in buckets:
        b["manual"] = 0
    for r in rows:
        cos = float(r.get("top_cosine") or 0.0)
        cos = max(0.0, min(0.999, cos))
        idx = int(cos / bucket_size)
        if idx >= n_buckets:
            idx = n_buckets - 1
        verb = r.get("decision_verb") or ""
        if verb == "route":
            buckets[idx]["auto"] += 1
            auto += 1
        elif verb == "manual_route":
            buckets[idx]["manual"] += 1
            manual += 1
        else:
            buckets[idx]["queued"] += 1
        total += 1

    threshold = float(rows[0].get("threshold") or 0.75)
    auto_rate = (auto / total) if total else 0.0
    return {
        "buckets": buckets,
        "total": total,
        "auto": auto,
        "manual": manual,
        "queued": total - auto - manual,
        "auto_rate": auto_rate,
        "threshold": threshold,
        "window_days": days,
    }
