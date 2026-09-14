"""Canvas TENSION edges — "these two sources disagree."

The highest-novelty signal on the map: everything else says what you did, this says where
your sources pull against each other.

⚠️ SOURCE CHOICE (canvas round-3 audit + verified against real data 2026-08-18). The original
plan assumed `topic_perspectives`. It is the WEAKEST of the three candidates:
  * its `contested` label is unreachable for any cluster with >=3 sources (branch-order bug,
    `topic_perspectives.py:326-329`), and
  * its "conflict" test is a regex negation XOR that the file's own docstring disclaims.
So there are TWO sources here, strongest first:

1. `derive_from_contradictions` — DETECTED conflicts from `contradiction_store.source_pairs`:
   a real pairwise LLM judgment with both source ids, a type and a severity. Unusable until
   2026-08-18, when contradiction reports gained a store; they had lived in an in-process dict
   that died on every restart, so a map could never be seeded from them.
2. `derive_edges` — INFERRED conflict from `curator_brain.source_stances`, the disagreement
   data that was already durable. Each source carries a stance toward the notebook thesis
   (supports / contradicts / tangential / off_topic) with a confidence and a rationale. A
   source that CONTRADICTS the thesis is in tension with each source that SUPPORTS it.

The populate endpoint runs (1) first and lets it claim a pair, so a measured conflict always
beats an inferred one for the same two sources.

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


# Severity → how loud the edge should be. `contradiction_detector` grades every conflict.
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


def derive_from_contradictions(
    nodes: List[Dict[str, Any]],
    pairs: List[Dict[str, Any]],
    *,
    skip_pairs: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Edges from DETECTED contradictions — `contradiction_store.source_pairs` rows
    (`{source_a_id, source_b_id, severity, contradiction_type, explanation}`).

    Stronger than the stance signal below: a real LLM judgment that two specific claims
    conflict, with both source ids and a severity, rather than an inference from each source's
    stance toward the thesis. It became usable only once contradiction reports were persisted —
    they previously lived in an in-process dict that died on every restart.
    """
    by_source: Dict[str, str] = {}
    for n in nodes or []:
        if n.get("ref_type") == "source" and n.get("ref_id") and n.get("id"):
            by_source.setdefault(str(n["ref_id"]), str(n["id"]))

    edges: List[Dict[str, Any]] = []
    seen: set = set(skip_pairs or ())
    for p in pairs or []:
        a = by_source.get(str(p.get("source_a_id") or ""))
        b = by_source.get(str(p.get("source_b_id") or ""))
        if not a or not b or a == b:
            continue
        key = (a, b) if a <= b else (b, a)
        if key in seen:
            continue
        seen.add(key)
        sev = str(p.get("severity") or "").lower()
        edges.append({
            "id": edge_id(a, b),
            "source": a,
            "target": b,
            "state": EDGE_STATE,
            "label": "",
            "meta": {
                "derived": True,
                "severity": sev,
                "kind": p.get("contradiction_type") or "",
                # Why these two disagree — shown on hover.
                "rationale": str(p.get("explanation") or "")[:280],
                "weight": _SEVERITY_RANK.get(sev, 1),
            },
        })
    return edges


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
