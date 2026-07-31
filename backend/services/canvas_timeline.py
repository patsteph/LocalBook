"""Canvas timeline — unified read-layer merging the three capture stores.

Journey Canvas Walk phase (2.2.0). Pure READ-ONLY: merges the three per-notebook
capture stores into ONE chronological, typed, de-duplicated journey feed that the
Walk replay scrubber consumes.

Stores merged (read-only — we call ONLY each store's read/get method, never a writer):
  - exploration_store.get_journey  -> RAG chat queries (topics / sources / confidence)
  - activity_ledger.recent_events  -> source_added / query_ran / collector lifecycle
  - curator_brain.recent_events    -> @curator/@collector/@research post-action events

Uniform entry shape (one dict per journey event):
    {
      "ts":       ISO-8601 str,          # sort key
      "kind":     str,                   # event verb ("chat_query" | "source_added" | ...)
      "source":   "exploration"|"ledger"|"brain",
      "title":    str,                   # short human label
      "ref_type": str | None,            # live-object type ("exploration_query"|"source"|...)
      "ref_id":   str | None,            # live-object id (stringified)
      "meta":     dict,                  # store-specific extras (actor/intent/topics/...)
    }

Ordering: NEWEST-FIRST (descending ISO timestamp).

De-dup: overlapping events — most importantly a source add recorded in BOTH the
activity ledger and the curator brain — collapse to a single entry. Key is
(ref_type, ref_id) when both present, else (source, kind, ts). On a key collision
the higher-priority store wins (ledger > exploration > brain): the activity ledger
is the canonical activity trail, so its source rows are preferred over the brain's.

Never raises: each store is read behind its own try/except seam; a failing store
contributes nothing and the others still return. Total failure yields [].
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Store priority for de-dup (LOWER wins on a key collision). The read order in
# build_timeline mirrors this so a simple keep-first pass honours the priority.
_SOURCE_ORDER = ("ledger", "exploration", "brain")


def _truncate(text: Optional[str], n: int = 120) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# Read seams — each wraps exactly ONE store's read method. Lazy-import so this
# module (and its CI-pure tests) never open a production store unless a seam is
# actually invoked; tests monkeypatch these three seams with fixtures.
# --------------------------------------------------------------------------

async def _read_exploration_journey(notebook_id: str, limit: int) -> Dict[str, Any]:
    """exploration_store.get_journey(notebook_id, limit) — never raises."""
    try:
        from storage.exploration_store import exploration_store
        return await exploration_store.get_journey(notebook_id, limit) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[canvas_timeline] exploration read failed: {e}")
        return {}


def _read_ledger_events(notebook_id: str, limit: int) -> List[Dict[str, Any]]:
    """activity_ledger.recent_events(notebook_id, limit) — never raises."""
    try:
        from services import activity_ledger
        return activity_ledger.recent_events(notebook_id, limit=limit) or []
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[canvas_timeline] ledger read failed: {e}")
        return []


def _read_brain_events(notebook_id: str, limit: int) -> List[Dict[str, Any]]:
    """curator_brain.recent_events(limit, notebook_id=...) — never raises."""
    try:
        from services.curator_brain import curator_brain
        return curator_brain.recent_events(limit=limit, notebook_id=notebook_id) or []
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[canvas_timeline] brain read failed: {e}")
        return []


# --------------------------------------------------------------------------
# Pure mappers — store row -> uniform entry. No I/O; individually testable.
# --------------------------------------------------------------------------

def _map_exploration(journey: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for q in (journey or {}).get("queries", []) or []:
        if not isinstance(q, dict):
            continue
        qid = q.get("id")
        out.append({
            "ts": q.get("timestamp"),
            "kind": "chat_query",
            "source": "exploration",
            "title": _truncate(q.get("query")) or "Chat query",
            "ref_type": "exploration_query",
            "ref_id": str(qid) if qid is not None else None,
            "meta": {
                "topics": q.get("topics") or [],
                "sources_used": q.get("sources_used") or [],
                "confidence": q.get("confidence"),
                "answer_preview": q.get("answer_preview") or "",
            },
        })
    return out


def _map_ledger(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("kind") or "event"
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        ref_type: Optional[str] = None
        ref_id: Optional[str] = None
        if kind == "source_added":
            sid = payload.get("source_id")
            if sid is not None:
                ref_type, ref_id = "source", str(sid)
            title = payload.get("title") or payload.get("filename") or "Source added"
        elif kind == "query_ran":
            title = _truncate(payload.get("query")) or "Query ran"
        else:
            title = kind.replace("_", " ")
        out.append({
            "ts": ev.get("ts"),
            "kind": kind,
            "source": "ledger",
            "title": title,
            "ref_type": ref_type,
            "ref_id": ref_id,
            "meta": {"actor": ev.get("actor"), "payload": payload},
        })
    return out


def _map_brain(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        action = ev.get("action") or "event"
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        ref_type: Optional[str] = None
        ref_id: Optional[str] = None
        if action in ("source_added", "source_ingested"):
            sid = payload.get("source_id")
            if sid is not None:
                ref_type, ref_id = "source", str(sid)
            title = payload.get("filename") or payload.get("title") or "Source ingested"
        else:
            title = (ev.get("intent") or action).replace("_", " ")
        out.append({
            "ts": ev.get("ts"),
            "kind": action,
            "source": "brain",
            "title": title,
            "ref_type": ref_type,
            "ref_id": ref_id,
            "meta": {
                "actor": ev.get("actor"),
                "intent": ev.get("intent"),
                "outcome": ev.get("outcome"),
                "payload": payload,
            },
        })
    return out


def _dedup_key(entry: Dict[str, Any]) -> Tuple[Any, ...]:
    """Stable identity for collapsing the same event seen in two stores.

    Prefer the live-object ref (a source add is the same event whether it came
    from the ledger or the brain); fall back to (source, kind, ts) which never
    collides across stores.
    """
    if entry.get("ref_type") and entry.get("ref_id") is not None:
        return ("ref", entry["ref_type"], str(entry["ref_id"]))
    return ("evt", entry.get("source"), entry.get("kind"), entry.get("ts"))


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

async def build_timeline(notebook_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Merge the three capture stores into one newest-first, de-duplicated feed.

    Read-only. Never raises — a failing store contributes nothing; the others
    still return. Returns at most `limit` entries.
    """
    if not notebook_id:
        return []

    # Collect in de-dup priority order (ledger > exploration > brain) so a
    # simple keep-first pass keeps the preferred store's copy of a shared event.
    # Each seam is already fail-open; the extra guards here make a single store
    # (even a monkeypatched-to-raise one) unable to sink the whole merge.
    entries: List[Dict[str, Any]] = []
    try:
        entries.extend(_map_ledger(_read_ledger_events(notebook_id, limit)))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[canvas_timeline] ledger lane failed: {e}")
    try:
        entries.extend(_map_exploration(await _read_exploration_journey(notebook_id, limit)))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[canvas_timeline] exploration lane failed: {e}")
    try:
        entries.extend(_map_brain(_read_brain_events(notebook_id, limit)))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[canvas_timeline] brain lane failed: {e}")

    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for e in entries:
        key = _dedup_key(e)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)

    # Newest-first. Entries with a missing ts sink to the bottom (empty sorts low).
    deduped.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return deduped[: max(limit, 0)]
