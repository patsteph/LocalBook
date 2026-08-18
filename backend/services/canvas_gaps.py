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
# NOTE: `confidence` is NOT a graded score. The frontend writes a binary
# (`effectiveIsLow ? 0.3 : 0.7`, ChatInterface.tsx) and the API defaults to 0.5, so this
# threshold really means "did the client's miss-phrase heuristic fire". Treat it as a
# boolean; never build a graded difficulty UI on it.
_LOW_CONF = 0.45


# ── Two refinements were tried here and REVERTED against real notebooks (2026-08-18).
# Both were recommended by the canvas round-3 audit; both are wrong in practice. Recorded
# so they are not "improved" back in:
#
# 1. **"No sources consulted ⇒ open loop"** (`sources_used == []`). Every single hit on real
#    data was an agent COMMAND, not an unanswered question — "collect now", "add a note …",
#    "subscribe to <rss url>", "scrape this youtube video …". Those legitimately consult no
#    sources. The learning gate below does not exclude them either, because @collector source
#    adds ARE classified as learning.
#
# 2. **"A later strong answer on the same topic closes the loop."** `exploration_queries.topics`
#    are NOT subjects — they are SOURCE TITLES, ~5 per query (e.g. "CS 153 '26: Frontier
#    Systems - …"). Any two queries in a notebook therefore share a "topic" almost always, so
#    this suppressed 2 of the 3 genuine gaps in the AI Research notebook. Real resolution
#    detection needs semantic similarity over the query TEXT, not this field.


def find_gaps(journey: Dict[str, Any], max_gaps: int = 12) -> List[Dict[str, Any]]:
    """Flag journey queries the notebook answered weakly — the canvas's OPEN LOOPS.
    Each gap: ``{query, reason, topics, ref_id}``. De-dups by query text; capped.
    Never raises.

    One refinement over the original pass, now that the result is drawn as a BADGE ON THE
    NODE rather than only listed in a side panel (a false positive is visible on the map):
    agent admin/status/config chatter is excluded via the same `is_learning_query` gate the
    canvas already applies when building nodes, so the panel and the map cannot disagree
    about what counts as part of the journey.
    """
    try:
        from services.canvas_populate import is_learning_query
    except Exception:  # pragma: no cover — never let an import break gap detection
        def is_learning_query(_q: Dict[str, Any]) -> bool:
            return True

    gaps: List[Dict[str, Any]] = []
    seen: set = set()
    for q in journey.get("queries", []) or []:
        query = (q.get("query") or "").strip()
        if not query or query.lower() in seen or not is_learning_query(q):
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
