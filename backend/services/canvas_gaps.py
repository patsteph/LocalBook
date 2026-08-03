"""Canvas gap-detection (Journey Canvas Run R3).

Surfaces where the notebook is WEAK — questions the user asked that the corpus
couldn't confidently answer — as "what to explore next." Pure over the
exploration-store journey (each query carries a confidence + an answer preview);
never raises. The Canvas/Studio can turn these into Collector/research nudges.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Phrases a low-signal answer tends to contain when the corpus lacked the info.
_MISS_MARKERS = (
    "couldn't find", "could not find", "not in the documents", "not in your documents",
    "no information", "isn't covered", "not covered", "don't have", "do not have",
    "unable to find", "no relevant", "wasn't able to find", "not available in",
)
_LOW_CONF = 0.45


def find_gaps(journey: Dict[str, Any], max_gaps: int = 12) -> List[Dict[str, Any]]:
    """Flag journey queries the notebook answered weakly. Each gap:
    ``{query, reason, topics, ref_id}``. De-dups by query text; capped. Never raises."""
    gaps: List[Dict[str, Any]] = []
    seen: set = set()
    for q in journey.get("queries", []) or []:
        query = (q.get("query") or "").strip()
        if not query or query.lower() in seen:
            continue
        preview = (q.get("answer_preview") or "").lower()
        conf = q.get("confidence")
        reason = None
        if any(m in preview for m in _MISS_MARKERS):
            reason = "the answer wasn't in your sources"
        elif isinstance(conf, (int, float)) and 0 <= conf < _LOW_CONF:
            reason = "low-confidence answer"
        if reason:
            seen.add(query.lower())
            gaps.append({
                "query": query,
                "reason": reason,
                "topics": q.get("topics") or [],
                "ref_id": str(q.get("id", "")),
            })
        if len(gaps) >= max_gaps:
            break
    return gaps
