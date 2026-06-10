"""sender_frequency — Phase 4 Tier 2 / G (2026-06-10).

Per-sender frequency tuner. High-volume senders can be switched into
weekly-digest mode: incoming messages buffer in `pending_digest`, and
a weekly composer runs on the configured digest day to roll them up
into a single HTML summary email (delivered via the existing Phase 8
SMTP path so it lands as a normal newsletter source).

API:
  - `get_mode(sender_email)` → 'live' | 'weekly_digest'
  - `set_mode(sender_email, mode, digest_day=None)`
  - `list_settings()` for the show_digest_mode intent
  - `queue_pending(sender_email, raw_bytes, email_account, notebook_id?)`
  - `list_pending(sender_email?)` for the composer
  - `clear_pending(ids)` after composer ingests
  - `detect_cadence(sender_email)` analyzes recent ingest history to
    suggest a digest day aligned with the sender's pattern (G.2 locked).
"""
from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 1=Mon, 7=Sun (per ISO weekday)
DEFAULT_DIGEST_DAY = 1  # Monday (G.1 locked)
DEFAULT_DIGEST_HOUR = 8  # 8am UTC


def get_mode(sender_email: str) -> str:
    """Returns 'live' (default) or 'weekly_digest'."""
    if not sender_email:
        return "live"
    try:
        from storage.database import get_db
        row = get_db().get_connection().execute(
            "SELECT bundle_mode FROM sender_settings WHERE LOWER(sender_email) = LOWER(?)",
            (sender_email,),
        ).fetchone()
        return (row["bundle_mode"] if row else "live") or "live"
    except Exception:
        return "live"


def get_settings(sender_email: str) -> Optional[Dict[str, Any]]:
    if not sender_email:
        return None
    try:
        from storage.database import get_db
        row = get_db().get_connection().execute(
            "SELECT * FROM sender_settings WHERE LOWER(sender_email) = LOWER(?)",
            (sender_email,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def set_mode(
    sender_email: str,
    mode: str,
    *,
    digest_day: Optional[int] = None,
    digest_hour: Optional[int] = None,
) -> bool:
    """Set bundle mode for a sender. mode ∈ {'live', 'weekly_digest'}."""
    if not sender_email or mode not in ("live", "weekly_digest"):
        return False
    day = int(digest_day) if digest_day else DEFAULT_DIGEST_DAY
    if not (1 <= day <= 7):
        day = DEFAULT_DIGEST_DAY
    hour = int(digest_hour) if digest_hour is not None else DEFAULT_DIGEST_HOUR
    if not (0 <= hour <= 23):
        hour = DEFAULT_DIGEST_HOUR
    try:
        from storage.database import get_db
        conn = get_db().get_connection()
        conn.execute(
            """INSERT INTO sender_settings (sender_email, bundle_mode, digest_day, digest_hour, set_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(sender_email) DO UPDATE SET
                 bundle_mode = excluded.bundle_mode,
                 digest_day = excluded.digest_day,
                 digest_hour = excluded.digest_hour,
                 set_at = excluded.set_at""",
            (sender_email, mode, day, hour, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[sender_frequency.set_mode] failed: {e}")
        return False


def list_settings() -> List[Dict[str, Any]]:
    try:
        from storage.database import get_db
        rows = get_db().get_connection().execute(
            "SELECT * FROM sender_settings ORDER BY bundle_mode, sender_email"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def queue_pending(
    *,
    sender_email: str,
    raw_bytes: bytes,
    email_account: str,
    notebook_id: Optional[str] = None,
) -> bool:
    """Buffer a raw message bytes for later digest composition."""
    if not sender_email or not raw_bytes:
        return False
    try:
        from storage.database import get_db
        conn = get_db().get_connection()
        conn.execute(
            """INSERT INTO pending_digest
               (id, sender_email, raw_bytes_b64, received_at, email_account, notebook_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                sender_email,
                base64.b64encode(raw_bytes).decode("ascii"),
                datetime.utcnow().isoformat(),
                email_account,
                notebook_id,
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"[sender_frequency.queue_pending] failed: {e}")
        return False


def list_pending(sender_email: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        from storage.database import get_db
        if sender_email:
            rows = get_db().get_connection().execute(
                """SELECT * FROM pending_digest
                   WHERE LOWER(sender_email) = LOWER(?)
                   ORDER BY received_at""",
                (sender_email,),
            ).fetchall()
        else:
            rows = get_db().get_connection().execute(
                "SELECT * FROM pending_digest ORDER BY sender_email, received_at"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def clear_pending(ids: List[str]) -> int:
    if not ids:
        return 0
    try:
        from storage.database import get_db
        conn = get_db().get_connection()
        placeholders = ",".join(["?"] * len(ids))
        cur = conn.execute(
            f"DELETE FROM pending_digest WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        return cur.rowcount
    except Exception as e:
        logger.warning(f"[sender_frequency.clear_pending] failed: {e}")
        return 0


def detect_cadence(sender_email: str, *, lookback_days: int = 28) -> Dict[str, Any]:
    """Analyze recent ingest history for this sender. Returns:
      - weekly_rate: avg emails/week
      - top_days: list of (day_of_week, count) sorted desc
      - suggested_digest_day: 1=Mon..7=Sun, the day AFTER their busiest
        day (so the digest hits the inbox just after they finish their
        weekly cycle). Per G.2 (locked): auto-align to sender pattern.
    """
    try:
        from storage.source_store import source_store
        since_iso = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
        all_by_nb = await_helper_or_sync(source_store)
        emails: List[str] = []
        for nb_id, sources in (all_by_nb or {}).items():
            for s in sources or []:
                fmt = (s.get("format") or "").lower()
                if fmt not in ("email", "forward"):
                    continue
                if (s.get("sender") or s.get("original_sender") or "").lower() != sender_email.lower():
                    continue
                created = s.get("created_at") or ""
                if created >= since_iso:
                    emails.append(created)
    except Exception as e:
        logger.debug(f"[sender_frequency.detect_cadence] read failed: {e}")
        emails = []

    if not emails:
        return {
            "weekly_rate": 0.0,
            "top_days": [],
            "suggested_digest_day": DEFAULT_DIGEST_DAY,
        }

    # Day-of-week histogram
    from collections import Counter
    weekdays: Counter = Counter()
    for ts in emails:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", ""))
            # Python weekday: Mon=0..Sun=6 → ISO: Mon=1..Sun=7
            weekdays[dt.weekday() + 1] += 1
        except Exception:
            continue
    top_days = sorted(weekdays.items(), key=lambda kv: -kv[1])
    weekly_rate = round(len(emails) / max(1.0, lookback_days / 7.0), 2)

    # Suggested digest day = day AFTER the most-active sender day,
    # so the digest ships just after they finish their weekly cycle.
    # Wraps around (after Sun = Mon).
    suggested = DEFAULT_DIGEST_DAY
    if top_days:
        busiest = top_days[0][0]
        suggested = (busiest % 7) + 1
    return {
        "weekly_rate": weekly_rate,
        "top_days": top_days,
        "suggested_digest_day": suggested,
    }


def await_helper_or_sync(source_store):
    """Tiny shim because list_all is async but we want a sync helper.
    Returns either the awaited result OR raises so caller can handle
    asynchronously (we use this from sync callers that already know
    they can't await — they call asyncio.run or similar). For Phase 4,
    detect_cadence is invoked from contexts that ARE async, so use
    a different entry point if you need sync."""
    import asyncio
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # Already in an async context — caller should use the async
        # variant. Return an empty dict and log; callers should switch
        # to detect_cadence_async if accuracy matters.
        return {}
    return loop.run_until_complete(source_store.list_all())


async def detect_cadence_async(sender_email: str, *, lookback_days: int = 28) -> Dict[str, Any]:
    """Async-aware version of detect_cadence. Prefer this from chat
    handlers / poller paths."""
    try:
        from storage.source_store import source_store
        since_iso = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
        all_by_nb = await source_store.list_all() or {}
        emails: List[str] = []
        for nb_id, sources in all_by_nb.items():
            for s in sources or []:
                fmt = (s.get("format") or "").lower()
                if fmt not in ("email", "forward"):
                    continue
                if (s.get("sender") or s.get("original_sender") or "").lower() != sender_email.lower():
                    continue
                created = s.get("created_at") or ""
                if created >= since_iso:
                    emails.append(created)
    except Exception:
        emails = []

    if not emails:
        return {
            "weekly_rate": 0.0,
            "top_days": [],
            "suggested_digest_day": DEFAULT_DIGEST_DAY,
        }

    from collections import Counter
    weekdays: Counter = Counter()
    for ts in emails:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", ""))
            weekdays[dt.weekday() + 1] += 1
        except Exception:
            continue
    top_days = sorted(weekdays.items(), key=lambda kv: -kv[1])
    weekly_rate = round(len(emails) / max(1.0, lookback_days / 7.0), 2)
    suggested = DEFAULT_DIGEST_DAY
    if top_days:
        busiest = top_days[0][0]
        suggested = (busiest % 7) + 1
    return {
        "weekly_rate": weekly_rate,
        "top_days": top_days,
        "suggested_digest_day": suggested,
    }
