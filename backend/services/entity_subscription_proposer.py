"""entity_subscription_proposer — Phase 13 of v2-information-cortex.

Source-graph expansion (capability I): after each newsletter ingest, look
at the LLM-extracted summary for named entities (people, papers, podcasts,
publications), bound the candidates, and propose them via Phase 7's
subscription queue with `kind='entity'`.

User approval triggers a downstream path in `agents/correspondent.
approve_subscription` (Phase 13.C extension) that creates a small
"entity-watch" source — a placeholder the user can later research.

Fire-and-forget from `ingest_newsletter`; never blocks the primary path.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# We deliberately keep this tight in v1. False positives waste user
# attention; better to surface 0 candidates than 5 noisy ones.
MAX_ENTITY_PROPOSALS_PER_INGEST = 2

_ALLOWED_TYPES = {"person", "document", "paper", "podcast", "publication", "company"}

# Cheap heuristic: a capitalized 2-3 word run is a likely named entity
# when no LLM-backed extractor is available.
_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")

_STOP_PHRASES = {
    "the", "a", "an", "this", "that", "newsletter", "subscribe", "unsubscribe",
    "today", "yesterday", "tomorrow", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday",
}


def _heuristic_candidates(summary: str) -> List[Dict[str, str]]:
    """Pull at most a few capitalized-name spans from the summary."""
    seen: set = set()
    out: List[Dict[str, str]] = []
    for match in _NAME_RE.finditer(summary or ""):
        name = match.group(1).strip()
        key = name.lower()
        if key in seen:
            continue
        if any(w in _STOP_PHRASES for w in key.split()):
            continue
        if len(name) < 4:
            continue
        seen.add(key)
        out.append({"name": name, "type": "person"})
        if len(out) >= 6:
            break
    return out


async def _try_extractor_candidates(
    notebook_id: str, source_id: str, summary: str,
) -> List[Dict[str, str]]:
    """Call the existing entity extractor when available. Falls back to
    the regex heuristic on any error."""
    try:
        from services.entity_extractor import entity_extractor
        entities = await entity_extractor.extract_from_text(
            text=summary, notebook_id=notebook_id, source_id=source_id, use_llm=False,
        )
        out: List[Dict[str, str]] = []
        for e in entities or []:
            etype = (getattr(e, "type", "") or "").lower()
            name = getattr(e, "name", "") or ""
            if not name or etype not in _ALLOWED_TYPES:
                continue
            out.append({"name": name.strip(), "type": etype})
            if len(out) >= 6:
                break
        return out or _heuristic_candidates(summary)
    except Exception as e:
        logger.debug(f"[entity_subscription_proposer] extractor unavailable: {e}")
        return _heuristic_candidates(summary)


async def propose_entities_from_summary(
    *, notebook_id: str, source_id: str, summary: str, sender: str = "",
) -> int:
    """Propose up to MAX_ENTITY_PROPOSALS_PER_INGEST candidates as
    `kind='entity'` items on the Phase 7 subscription queue.

    Returns the number of new items actually added (deduped).
    """
    if not summary or not summary.strip():
        return 0
    candidates = await _try_extractor_candidates(notebook_id, source_id, summary)
    if not candidates:
        return 0

    # Dedupe against the existing subscription queue.
    try:
        from agents.correspondent import correspondent_agent, _load_subscriptions, _save_subscriptions
        existing = _load_subscriptions()
        existing_names = {
            (i.get("entity_name") or "").lower()
            for i in existing if i.get("kind") == "entity"
        }
    except Exception:
        existing = []
        existing_names = set()
        _load_subscriptions = None
        _save_subscriptions = None

    added = 0
    new_items: List[Dict[str, Any]] = []
    for c in candidates[:MAX_ENTITY_PROPOSALS_PER_INGEST * 3]:
        name = c.get("name", "").strip()
        if not name or name.lower() in existing_names:
            continue
        new_items.append({
            "id": str(uuid4()),
            "kind": "entity",
            "status": "pending",
            "title": name,
            "entity_name": name,
            "entity_type": c.get("type", "person"),
            "suggested_notebook_id": notebook_id,
            "source_email": {"sender": sender, "summary": summary[:300]},
            "created_at": __import__("datetime").datetime.utcnow().isoformat(),
        })
        existing_names.add(name.lower())
        added += 1
        if added >= MAX_ENTITY_PROPOSALS_PER_INGEST:
            break

    if not new_items or _save_subscriptions is None:
        return 0
    existing.extend(new_items)
    try:
        _save_subscriptions(existing)
    except Exception as e:
        logger.debug(f"[entity_subscription_proposer] save failed: {e}")
        return 0
    return added
