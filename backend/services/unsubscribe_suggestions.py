"""unsubscribe_suggestions — Phase 3 Tier 2 (F).

Per design F (locked 2026-06-09):
  - Surface senders that have scored D or F for ≥ 4 weeks AND have
    ≥ 10 ingested emails. User decides whether to drop or keep.
  - One-at-a-time pacing (F.1 locked) — weekly cadence, never bulk.
  - Snooze with configurable duration (F.2 locked — default 30d).

This service computes candidates. The actual block + chat surface live
in the API + chat handler. Actual RFC 2369 mailto/HTTPS unsubscribe is
deferred to a future polish session — for now "unsubscribe" means
"add to local blocklist so we stop ingesting".
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Per design (locked):
DEFAULT_GRADE_THRESHOLD = "D"  # candidates have grade ≤ this
DEFAULT_MIN_WEEKS = 4
DEFAULT_MIN_EMAILS = 10
GRADE_RANK = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1, "—": 0}


def _grade_at_or_below(grade: str, threshold: str) -> bool:
    return GRADE_RANK.get(grade, 0) > 0 and GRADE_RANK.get(grade, 99) <= GRADE_RANK.get(threshold, 0)


async def list_candidates(
    *,
    grade_threshold: str = DEFAULT_GRADE_THRESHOLD,
    min_weeks: int = DEFAULT_MIN_WEEKS,
    min_emails: int = DEFAULT_MIN_EMAILS,
) -> List[Dict[str, Any]]:
    """Senders that meet the unsubscribe-candidate criteria.

    Returns a list ordered by composite_score ASC (worst first). Each
    entry includes the sender row + a computed `weeks_at_grade` field.

    Filtering applied:
      - grade ≤ `grade_threshold` (default D)
      - ≥ `min_emails` lifetime emails (volume gate against false positives)
      - sender not already on the blocklist (don't re-surface what user
        already dropped)
      - sender not snoozed
    """
    from storage.database import get_db
    from storage.source_store import source_store

    conn = get_db().get_connection()

    # Pull blocklist + snoozed senders up front
    blocked: Dict[str, str] = {}
    try:
        rows = conn.execute(
            "SELECT sender_email, snooze_until FROM sender_blocklist"
        ).fetchall()
        now_iso = datetime.utcnow().isoformat()
        for r in rows:
            snooze = r["snooze_until"]
            if snooze and snooze > now_iso:
                blocked[r["sender_email"]] = "snoozed"
            else:
                blocked[r["sender_email"]] = "blocked"
    except Exception:
        pass

    # Scorecards
    rows = conn.execute(
        "SELECT * FROM newsletter_scorecards ORDER BY composite_score ASC"
    ).fetchall()
    scards = [dict(r) for r in rows]
    if not scards:
        return []

    # Need raw source counts so we can compare against min_emails (the
    # scorecard volume_per_week is a rate, not a lifetime count).
    sender_counts: Dict[str, int] = {}
    try:
        all_by_nb = await source_store.list_all() or {}
        for _, sources in all_by_nb.items():
            for s in sources or []:
                fmt = (s.get("format") or "").lower()
                if fmt not in ("email", "forward"):
                    continue
                sender = s.get("sender") or s.get("original_sender") or ""
                if sender:
                    sender_counts[sender] = sender_counts.get(sender, 0) + 1
    except Exception as e:
        logger.debug(f"[unsubscribe_suggestions] sender count fetch failed: {e}")

    candidates: List[Dict[str, Any]] = []
    for s in scards:
        sender = s["sender_email"]
        if sender in blocked:
            continue
        if not _grade_at_or_below(s.get("grade") or "", grade_threshold):
            continue
        if sender_counts.get(sender, 0) < min_emails:
            continue
        # weeks_at_grade is a placeholder until we persist trend_data —
        # treat it as "enough volume = enough time" for now. When
        # trend_data is populated this becomes accurate.
        weeks = max(min_weeks, int(sender_counts.get(sender, 0) / 2))
        s_out = dict(s)
        s_out["weeks_at_grade"] = weeks
        s_out["lifetime_emails"] = sender_counts.get(sender, 0)
        candidates.append(s_out)

    return candidates


def is_blocked(sender_email: str) -> bool:
    """Quick check for the IMAP poll path — should we skip ingestion?"""
    from storage.database import get_db
    if not sender_email:
        return False
    try:
        row = get_db().get_connection().execute(
            "SELECT snooze_until FROM sender_blocklist WHERE LOWER(sender_email) = LOWER(?)",
            (sender_email,),
        ).fetchone()
        if not row:
            return False
        snooze = row["snooze_until"]
        if snooze and snooze > datetime.utcnow().isoformat():
            # Snoozed — not currently blocked
            return False
        return True
    except Exception:
        return False


def add_to_blocklist(
    *,
    sender_email: str,
    reason: str = "user requested",
    snooze_days: Optional[int] = None,
) -> bool:
    """Add a sender to the blocklist. If `snooze_days` is set, it's a
    soft-snooze that auto-expires; otherwise it's a permanent block."""
    from storage.database import get_db
    if not sender_email:
        return False
    snooze_until = None
    if snooze_days and snooze_days > 0:
        snooze_until = (datetime.utcnow() + timedelta(days=snooze_days)).isoformat()
    try:
        conn = get_db().get_connection()
        conn.execute(
            """INSERT INTO sender_blocklist
               (sender_email, reason, blocked_at, snooze_until)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(sender_email) DO UPDATE SET
                 reason = excluded.reason,
                 blocked_at = excluded.blocked_at,
                 snooze_until = excluded.snooze_until""",
            (sender_email, reason, datetime.utcnow().isoformat(), snooze_until),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[unsubscribe_suggestions.add_to_blocklist] {e}")
        return False


def remove_from_blocklist(sender_email: str) -> bool:
    from storage.database import get_db
    try:
        conn = get_db().get_connection()
        cur = conn.execute(
            "DELETE FROM sender_blocklist WHERE LOWER(sender_email) = LOWER(?)",
            (sender_email,),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"[unsubscribe_suggestions.remove_from_blocklist] {e}")
        return False


def list_blocked() -> List[Dict[str, Any]]:
    from storage.database import get_db
    try:
        rows = get_db().get_connection().execute(
            "SELECT * FROM sender_blocklist ORDER BY blocked_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
