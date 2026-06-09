"""notebook_router — cross-notebook auto-routing for Correspondent.

Capability L from the v2-information-cortex plan: incoming newsletter
content lands in the best-fit notebook by classification confidence.

Algorithm (kept simple — perf passes can cache embeddings later):
  1. Pull all notebook digests from curator_brain.
  2. Embed each digest's current_summary + the classification summary.
  3. Cosine similarity against each notebook.
  4. Sort descending; top match is the primary candidate.
  5. ≥ ROUTING_THRESHOLD → silent route; otherwise queue for approval
     with the top candidate + the runner-up exposed.

Thresholds are easy to tune from the agent config; defaults conservative
so the first ship over-queues rather than under-queues.

Phase 6 of v2-information-cortex.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROUTING_THRESHOLD = 0.75  # ≥ this → auto-route; below → approval queue.


@dataclass
class RoutingCandidate:
    notebook_id: str
    notebook_name: str
    confidence: float


@dataclass
class RoutingDecision:
    decision: str  # 'route' | 'queue' | 'no_match'
    top: Optional[RoutingCandidate] = None
    alternatives: List[RoutingCandidate] = field(default_factory=list)
    reason: str = ""


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def _embed(text: str) -> List[float]:
    """Embed text via ollama_service. Returns empty list on failure."""
    if not text or not text.strip():
        return []
    from services.ollama_service import ollama_service
    result = await ollama_service.embed(text=text[:6000])
    vecs = (result or {}).get("embeddings") or []
    return list(vecs[0]) if vecs and isinstance(vecs[0], list) else []


async def route(classification_summary: str, *, topic_tags: Optional[List[str]] = None,
                threshold: float = ROUTING_THRESHOLD,
                sender: Optional[str] = None,
                source_entities: Optional[List[Dict[str, Any]]] = None) -> RoutingDecision:
    """Decide which notebook this newsletter belongs to.

    Args:
        classification_summary: the LLM's one-line summary of the email body.
        topic_tags: optional tags to combine with the summary for embedding.
        threshold: cosine similarity cutoff (default ROUTING_THRESHOLD).
        sender: F5b (2026-06-08) — RFC-formatted From: header. When supplied,
            cosine scores get a per-notebook bias based on prior user
            corrections (+0.25 per redirect from this sender, capped +0.50).
            Lets one manual correction dominate routing for that sender.

    Returns:
        RoutingDecision with the chosen notebook, runner-up, and decision
        verb ('route' / 'queue' / 'no_match').
    """
    if not classification_summary:
        return RoutingDecision(decision="no_match", reason="empty summary")

    try:
        from services.curator_brain import curator_brain
        digests = curator_brain.get_all_digests() or []
    except Exception as e:
        logger.debug(f"[notebook_router] could not load digests: {e}")
        digests = []

    # Filter to notebooks that have a usable summary.
    digests = [d for d in digests if (d.get("current_summary") or "").strip()]
    if not digests:
        return RoutingDecision(decision="no_match", reason="no notebook digests available")

    query_text = classification_summary
    if topic_tags:
        query_text += "\n\nTopics: " + ", ".join(topic_tags)

    q_vec = await _embed(query_text)
    if not q_vec:
        return RoutingDecision(decision="no_match", reason="embedding failed")

    scored: List[RoutingCandidate] = []
    for d in digests:
        nb_vec = await _embed(d.get("current_summary") or "")
        if not nb_vec:
            continue
        score = _cosine(q_vec, nb_vec)
        scored.append(RoutingCandidate(
            notebook_id=d.get("notebook_id", ""),
            notebook_name=d.get("name") or d.get("notebook_id", "(unnamed)"),
            confidence=score,
        ))

    if not scored:
        return RoutingDecision(decision="no_match", reason="no notebooks embeddable")

    # F5b (2026-06-08) — apply sender→notebook learning. Bonus added on
    # top of cosine score for each notebook the user has previously
    # corrected this sender into.
    bias_applied = False
    if sender:
        try:
            from agents.correspondent import get_sender_routing_bias
            for c in scored:
                bonus = get_sender_routing_bias(sender, c.notebook_id)
                if bonus > 0:
                    c.confidence = min(1.0, c.confidence + bonus)
                    bias_applied = True
        except Exception as e:
            logger.debug(f"[notebook_router] sender bias skipped: {e}")

    # P1B.2 (2026-06-09) — entity-overlap bonus. Per design D: for each
    # entity shared between the incoming source and a notebook's top
    # entities, add +0.05 to that notebook's score, capped at +0.20.
    # Helps disambiguate cases where cosine ties two notebooks but only
    # one actually has shared subject-matter entities.
    entity_bonus_applied = False
    if source_entities:
        try:
            from services.entity_extractor import entity_extractor

            def _entity_key(e: Dict[str, Any]) -> str:
                return (e.get("type", ""), (e.get("name") or "").strip().lower())

            source_keys = {_entity_key(e) for e in source_entities if e.get("name")}
            if source_keys:
                for c in scored:
                    try:
                        nb_entities = entity_extractor.get_entities(c.notebook_id) or []
                    except Exception:
                        nb_entities = []
                    nb_keys = {
                        (getattr(e, "type", ""), (getattr(e, "name", "") or "").strip().lower())
                        for e in nb_entities[:100]
                    }
                    shared = len(source_keys & nb_keys)
                    if shared:
                        bonus = min(0.20, 0.05 * shared)
                        c.confidence = min(1.0, c.confidence + bonus)
                        entity_bonus_applied = True
        except Exception as e:
            logger.debug(f"[notebook_router] entity bonus skipped: {e}")

    scored.sort(key=lambda c: c.confidence, reverse=True)
    top = scored[0]
    alternatives = scored[1:3]

    reasons = []
    if bias_applied:
        reasons.append("sender-bias")
    if entity_bonus_applied:
        reasons.append("entity-overlap")
    reason_suffix = f" ({' + '.join(reasons)} applied)" if reasons else ""
    if top.confidence >= threshold:
        return RoutingDecision(decision="route", top=top, alternatives=alternatives,
                              reason=f"cosine {top.confidence:.2f} ≥ {threshold:.2f}{reason_suffix}")
    return RoutingDecision(decision="queue", top=top, alternatives=alternatives,
                          reason=f"cosine {top.confidence:.2f} < {threshold:.2f}{reason_suffix}")


# ---------------------------------------------------------------------------
# Phase 8 — explicit slug-based lookup for forward routing.
# When a user forwards an email with `#slug` in the subject, we resolve
# that to a notebook by slugified-name match rather than by similarity.
# ---------------------------------------------------------------------------

import re as _re


def _slugify(text: str) -> str:
    """Lowercase, replace runs of non-alphanumerics with hyphens, strip edges."""
    if not text:
        return ""
    s = text.lower()
    s = _re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def find_notebook_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    """Return the first curator-brain digest whose slugified name matches `slug`.

    Returns the raw digest dict (so callers see notebook_id + name + current_summary).
    None if no match — caller decides whether to fall through to similarity routing.
    """
    if not slug:
        return None
    target = _slugify(slug)
    if not target:
        return None
    try:
        from services.curator_brain import curator_brain
        digests = curator_brain.get_all_digests() or []
    except Exception as e:
        logger.debug(f"[notebook_router.find_notebook_by_slug] digest load failed: {e}")
        return None
    for d in digests:
        if _slugify(d.get("name") or "") == target:
            return d
    return None
