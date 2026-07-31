"""Canvas clustering + spatial auto-layout (Journey Canvas Walk W1/W2).

Turns the flat populate output into a READABLE MAP instead of a wall of tiles on a
grid. Two moves:

  1. CONSOLIDATE — cap to the significant nodes (all sources kept; queries ranked by
     recency + connectivity) so a 40-source notebook isn't an overwhelming dump.
  2. CLUSTER + LAY OUT — group nodes by the SAME pairwise similarity the candidate-dot
     engine uses (KG shared concepts + embedding cosine + shared sources), then place
     each cluster as its own spatial REGION (packed left-to-right, wrapping) with the
     nodes grouped inside it — related things sit together, unrelated things spread apart.

All logic here is PURE + deterministic given the pairs; the async signal-gather is the
candidate engine's. Never raises.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence

from services.canvas_candidates import blended_score

# Layout wants topical NEIGHBOURHOODS, so embedding similarity (the universal signal,
# weighted only 0.35 in the candidate blend) must be able to group on its own. We take
# max(embed, blended) as the clustering score and threshold it. 0.6 groups related
# topics; TUNABLE once the user sees real notebooks in the app.
CLUSTER_THRESHOLD = 0.6


def _cluster_score(p: Dict[str, Any]) -> float:
    """Clustering similarity for a pair: embedding on its own scale OR the candidate
    blend, whichever is stronger — so topically-similar nodes group even without shared
    KG concepts."""
    return max(
        float(p.get("embed", 0.0) or 0.0),
        blended_score(p.get("concept", 0.0), p.get("embed", 0.0), p.get("shared", 0.0)),
    )
# Spatial constants (px, react-flow coordinate space).
NODE_W = 300
NODE_H = 190
COLS_PER_CLUSTER = 3
REGION_GAP = 170            # gap between cluster regions
MAX_ROW_W = 2400           # wrap cluster regions after this width


class _UF:
    """Tiny union-find for connected-components clustering."""
    def __init__(self, ids: Sequence[str]) -> None:
        self.p: Dict[str, str] = {i: i for i in ids}

    def find(self, x: str) -> str:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.p[self.find(a)] = self.find(b)


def significant_nodes(nodes: List[Dict[str, Any]], degrees: Dict[str, int],
                      max_query_nodes: int = 40) -> List[Dict[str, Any]]:
    """Consolidate: keep every SOURCE node, plus the most significant query nodes
    (most-connected first, then most-recent), capped so the map stays readable.
    `degrees`: node_id → how many similarity links it has (0 if unknown)."""
    sources = [n for n in nodes if n.get("kind") == "source"]
    queries = [n for n in nodes if n.get("kind") != "source"]
    queries.sort(key=lambda n: (degrees.get(n.get("id"), 0), n.get("created_at") or ""), reverse=True)
    return sources + queries[:max_query_nodes]


def cluster_nodes(nodes: List[Dict[str, Any]], raw_pairs: List[Dict[str, Any]],
                  threshold: float = CLUSTER_THRESHOLD) -> List[List[Dict[str, Any]]]:
    """Connected-components clustering over pairs whose blended score >= threshold.
    Returns clusters (lists of node dicts), largest first, then singletons. Never raises."""
    by_id = {n["id"]: n for n in nodes if n.get("id")}
    ids = list(by_id.keys())
    uf = _UF(ids)
    idset = set(ids)
    for p in raw_pairs or []:
        a, b = p.get("a"), p.get("b")
        if a in idset and b in idset and \
                _cluster_score(p) >= threshold:
            uf.union(a, b)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for i in ids:
        groups.setdefault(uf.find(i), []).append(by_id[i])
    return sorted(groups.values(), key=len, reverse=True)


def layout_clusters(clusters: List[List[Dict[str, Any]]]) -> Dict[str, Dict[str, float]]:
    """Pack cluster regions left-to-right (wrapping), nodes gridded inside each region.
    Returns {node_id: {"x", "y"}}. Regions never overlap (each gets a cw×ch box + gap)."""
    pos: Dict[str, Dict[str, float]] = {}
    x_cursor = 0.0
    y_cursor = 0.0
    row_h = 0.0
    for cluster in clusters:
        cols = min(COLS_PER_CLUSTER, max(1, len(cluster)))
        rows = math.ceil(len(cluster) / COLS_PER_CLUSTER)
        cw = cols * NODE_W
        ch = rows * NODE_H
        if x_cursor > 0 and x_cursor + cw > MAX_ROW_W:
            x_cursor = 0.0
            y_cursor += row_h + REGION_GAP
            row_h = 0.0
        for ni, node in enumerate(cluster):
            c = ni % COLS_PER_CLUSTER
            r = ni // COLS_PER_CLUSTER
            pos[node["id"]] = {"x": x_cursor + c * NODE_W, "y": y_cursor + r * NODE_H}
        x_cursor += cw + REGION_GAP
        row_h = max(row_h, ch)
    return pos


def degrees_from_pairs(nodes: List[Dict[str, Any]], raw_pairs: List[Dict[str, Any]],
                       threshold: float = CLUSTER_THRESHOLD) -> Dict[str, int]:
    """How many >=threshold similarity links each node has — the connectivity signal for
    significance ranking. Pure."""
    deg: Dict[str, int] = {n["id"]: 0 for n in nodes if n.get("id")}
    for p in raw_pairs or []:
        a, b = p.get("a"), p.get("b")
        if a in deg and b in deg and \
                _cluster_score(p) >= threshold:
            deg[a] += 1
            deg[b] += 1
    return deg
