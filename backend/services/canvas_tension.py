"""Canvas TENSION edges — "these two sources disagree."

The highest-novelty signal on the map: everything else says what you did, this says where
your sources pull against each other.

⚠️ SOURCE CHOICE (canvas round-3 audit + verified against real data 2026-08-18). The original
plan assumed `topic_perspectives`. It is the WEAKEST of the three candidates:
  * its `contested` label is unreachable for any cluster with >=3 sources (branch-order bug,
    `topic_perspectives.py:326-329`), and
  * its "conflict" test is a regex negation XOR that the file's own docstring disclaims.
`contradiction_detector` gives real LLM judgment with both source ids, type and severity — but
its `_contradiction_cache` is an in-process dict (`:50`), so nothing survives a restart and it
cannot seed a map.

So this reads `curator_brain.source_stances`: the only DURABLE disagreement data in the
codebase. Each source carries a stance toward the notebook thesis
(supports / contradicts / tangential / off_topic) with a confidence and a rationale, upserted
one row per (source, notebook). A source that CONTRADICTS the thesis is in tension with each
source that SUPPORTS it — that pair is the edge.

Pure (no I/O) so it unit-tests in CI, matching the canvas_populate / canvas_provenance
convention. Derived, never trusted-and-persisted: re-derived on every populate.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EDGE_STATE = "tension"

_EDGE_NS = uuid.UUID("2c7e4f61-9a3d-4b28-8e15-7d0c6a9b3f42")

# One contradicting source against a wall of supporting ones would fan dozens of edges into a
# single node and bury the map. Keep the strongest few by the supporter's own confidence.
DEFAULT_MAX_PER_SOURCE = 4

# Below this we are not confident enough to tell the user two sources disagree. Stance
# confidence is a real 0..1 model score here (unlike `exploration_queries.confidence`, which is
# a frontend binary — do not confuse the two).
MIN_CONFIDENCE = 0.5


def edge_id(a: str, b: str) -> str:
    """Deterministic and ORDER-INDEPENDENT: tension is symmetric, so the same pair must yield
    the same edge whichever source is scanned first."""
    lo, hi = (a, b) if a <= b else (b, a)
    return str(uuid.uuid5(_EDGE_NS, f"{EDGE_STATE}:{lo}:{hi}"))


def derive_edges(
    nodes: List[Dict[str, Any]],
    stances: List[Dict[str, Any]],
    *,
    max_per_source: int = DEFAULT_MAX_PER_SOURCE,
    min_confidence: float = MIN_CONFIDENCE,
    skip_pairs: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Pair every CONTRADICTING source with the SUPPORTING sources on the map.

    `nodes` must already carry final ids. `stances` are `curator_brain` rows:
    `{source_id, stance, confidence, rationale}`.

    Only sources that are actually ON the map produce edges — a stance about a source the user
    never discussed has nothing to point at.
    """
    by_source: Dict[str, str] = {}
    for n in nodes or []:
        if n.get("ref_type") == "source" and n.get("ref_id") and n.get("id"):
            by_source.setdefault(str(n["ref_id"]), str(n["id"]))

    def _usable(row: Dict[str, Any], want: str) -> bool:
        if str(row.get("stance") or "") != want:
            return False
        try:
            if float(row.get("confidence") or 0.0) < min_confidence:
                return False
        except (TypeError, ValueError):
            return False
        return str(row.get("source_id") or "") in by_source

    against = [r for r in (stances or []) if _usable(r, "contradicts")]
    if not against:
        return []
    # Strongest supporters first, so a cap keeps the most meaningful disagreements.
    supporting = sorted(
        (r for r in (stances or []) if _usable(r, "supports")),
        key=lambda r: -float(r.get("confidence") or 0.0),
    )

    edges: List[Dict[str, Any]] = []
    seen: set = set(skip_pairs or ())
    for opp in against:
        opp_node = by_source[str(opp["source_id"])]
        drawn = 0
        for sup in supporting:
            if drawn >= max_per_source:
                break
            sup_node = by_source[str(sup["source_id"])]
            if sup_node == opp_node:
                continue
            pair = (opp_node, sup_node) if opp_node <= sup_node else (sup_node, opp_node)
            if pair in seen:
                continue
            seen.add(pair)
            drawn += 1
            rationale = str(opp.get("rationale") or "").strip()
            edges.append({
                "id": edge_id(opp_node, sup_node),
                "source": opp_node,
                "target": sup_node,
                "state": EDGE_STATE,
                "label": "",
                "meta": {
                    "derived": True,
                    # Why these two disagree — surfaced on hover, so the edge explains itself
                    # instead of just asserting a conflict.
                    "rationale": rationale[:280],
                    "confidence": round(float(opp.get("confidence") or 0.0), 2),
                },
            })
    if edges:
        logger.debug(f"[canvas] tension: {len(edges)} edge(s) from {len(against)} contradicting source(s)")
    return edges
