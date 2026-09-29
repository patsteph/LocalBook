"""A single cursor-paged feed over everything LocalBook did.

LB-2's `events_since`, which **replaces LB-5's webhooks entirely**. Jocasta polls
this from Hermes's heartbeat: there is no receiver, no retry queue, and nothing
to deliver twice — the client holds a cursor and asks what is new.

Two tables feed it, and they are genuinely separate databases:

    curator_brain/brain.db :: events        what the agents did (@curator, @collector,
                                            @research) — actor, action, intent, outcome
    LocalBook.db           :: activity_events  what happened in the notebooks —
                                            sources added, chats, quizzes, highlights

**Why the cursor is composite.** Each table has its own `AUTOINCREMENT` id, so
there is no single number that means "caught up on both". The cursor carries one
id per source (`b<N>.a<M>`). A timestamp cursor was the obvious alternative and
is wrong: two events can share a `ts` to the second, and anything that skips on
equality loses events silently while looking like it works.

**Ordering, stated honestly.** Within one page, events are merged and sorted by
`ts`. Across pages they can interleave slightly, because each source is paged by
its own id. The contract is *everything, eventually, at least once, in roughly
time order* — which is what a polling agent needs. It is not a total order, and
nothing downstream should assume one.

A consequence worth knowing: one busy source CAN fill an entire page, so a
recent event from the quiet source waits for the next poll. Nothing is lost —
each source's cursor only advances past rows that were actually returned — but a
caller that polls once and stops has not necessarily seen everything. `has_more`
says so; keep polling until it is false.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SOURCE_CURATOR = "curator"
SOURCE_ACTIVITY = "activity"

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

_CURSOR_RE = re.compile(r"^b(\d+)\.a(\d+)$")


# ── cursor ──────────────────────────────────────────────────────────────────


def encode_cursor(brain_id: int, activity_id: int) -> str:
    return f"b{max(0, int(brain_id))}.a{max(0, int(activity_id))}"


def parse_cursor(cursor: Optional[str]) -> Tuple[int, int]:
    """(brain_id, activity_id). An absent or unparseable cursor starts from zero.

    Starting over is the safe failure: the caller re-reads events it has already
    seen, which a polling client can de-duplicate. Guessing a position forward
    would drop events and look like nothing happened.
    """
    if not cursor:
        return 0, 0
    match = _CURSOR_RE.match(str(cursor).strip())
    if not match:
        logger.warning("[event-feed] unparseable cursor %r — starting from the beginning", cursor)
        return 0, 0
    return int(match.group(1)), int(match.group(2))


# ── readers ─────────────────────────────────────────────────────────────────


def _load_json(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return {"raw": str(raw)[:500]}


def _read_curator(after_id: int, limit: int, kinds: Optional[List[str]]) -> List[Dict[str, Any]]:
    """Agent actions from brain.db. Its own database, its own connection."""
    try:
        from services.curator_brain import curator_brain

        conn = curator_brain._conn
        sql = "SELECT id, ts, notebook_id, actor, action, intent, payload, outcome " \
              "FROM events WHERE id > ?"
        params: List[Any] = [after_id]
        if kinds:
            sql += f" AND action IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    except Exception as exc:
        # One source being unavailable must not blank the whole feed — an agent
        # polling for notebook activity should still get it if brain.db is busy.
        logger.warning("[event-feed] curator events unavailable: %s", exc)
        return []

    return [
        {
            "source": SOURCE_CURATOR,
            "id": row["id"],
            "ts": row["ts"],
            "kind": row["action"],
            "notebook_id": row["notebook_id"],
            "actor": row["actor"],
            "payload": {
                **_load_json(row["payload"]),
                **({"intent": row["intent"]} if row["intent"] else {}),
                **({"outcome": row["outcome"]} if row["outcome"] else {}),
            },
        }
        for row in rows
    ]


def _read_activity(after_id: int, limit: int, kinds: Optional[List[str]]) -> List[Dict[str, Any]]:
    """Notebook activity from the main database."""
    try:
        from services.activity_ledger import _get_conn

        conn = _get_conn()
        sql = "SELECT id, ts, notebook_id, kind, actor, payload_json " \
              "FROM activity_events WHERE id > ?"
        params: List[Any] = [after_id]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        sql += " ORDER BY id ASC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    except Exception as exc:
        logger.warning("[event-feed] activity events unavailable: %s", exc)
        return []

    return [
        {
            "source": SOURCE_ACTIVITY,
            "id": row["id"],
            "ts": row["ts"],
            "kind": row["kind"],
            "notebook_id": row["notebook_id"],
            "actor": row["actor"],
            "payload": _load_json(row["payload_json"]),
        }
        for row in rows
    ]


# ── the feed ────────────────────────────────────────────────────────────────


def events_since(
    cursor: Optional[str] = None,
    kinds: Optional[List[str]] = None,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """Everything since `cursor`, with a new cursor to pass back next time.

    `kinds` filters on the event kind in BOTH sources (curator `action`,
    activity `kind`).

    The returned cursor advances only past events actually included in this
    page, so nothing can be skipped by truncation.
    """
    # `limit or DEFAULT` would short-circuit on 0 and silently return a full
    # page where the caller asked for none. None means "use the default"; any
    # number given is clamped, including 0.
    if limit is None:
        limit = DEFAULT_LIMIT
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))
    brain_after, activity_after = parse_cursor(cursor)

    # Each source is asked for a full page. Between them that can be up to 2×
    # limit rows, trimmed below — cheap, and it keeps either source from
    # starving the other when one is much busier.
    curator_rows = _read_curator(brain_after, limit, kinds)
    activity_rows = _read_activity(activity_after, limit, kinds)

    merged = sorted(curator_rows + activity_rows, key=lambda e: (e["ts"] or "", e["id"]))
    has_more = len(merged) > limit
    page = merged[:limit]

    # Advance each source only as far as this page actually reached. Taking the
    # max id per source across the WHOLE query instead would skip whatever the
    # trim dropped.
    new_brain = brain_after
    new_activity = activity_after
    for event in page:
        if event["source"] == SOURCE_CURATOR:
            new_brain = max(new_brain, event["id"])
        else:
            new_activity = max(new_activity, event["id"])

    return {
        "events": page,
        "cursor": encode_cursor(new_brain, new_activity),
        "has_more": has_more or len(curator_rows) == limit or len(activity_rows) == limit,
    }


def known_kinds(limit: int = 100) -> List[str]:
    """Distinct event kinds present, so a caller can discover what to filter on
    rather than guess at string constants."""
    kinds = set()
    try:
        from services.curator_brain import curator_brain

        for row in curator_brain._conn.execute(
            "SELECT DISTINCT action FROM events LIMIT ?", (limit,)
        ):
            kinds.add(row[0])
    except Exception as exc:
        logger.warning("[event-feed] could not list curator kinds: %s", exc)
    try:
        from services.activity_ledger import _get_conn

        for row in _get_conn().execute(
            "SELECT DISTINCT kind FROM activity_events LIMIT ?", (limit,)
        ):
            kinds.add(row[0])
    except Exception as exc:
        logger.warning("[event-feed] could not list activity kinds: %s", exc)
    return sorted(k for k in kinds if k)
