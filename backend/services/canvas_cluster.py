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
    """Clustering similarity for a pair: the STRONGEST of three signals — embedding cosine,
    raw shared-source overlap, or the candidate blend. The shared-source term stands on its
    OWN (not just its 0.15 weight inside the blend) so a question clusters with the SOURCES
    it was answered from — the strongest structural signal on a journey map ('this discussion
    drew on these sources'). Without this the blend caps a pure question↔source pair at 0.15,
    far below threshold, and the map segregates by node TYPE (all sources, all questions)
    instead of by TOPIC. Two questions sharing a source transitively join one topic cluster."""
    return max(
        float(p.get("embed", 0.0) or 0.0),
        float(p.get("shared", 0.0) or 0.0),
        blended_score(p.get("concept", 0.0), p.get("embed", 0.0), p.get("shared", 0.0)),
    )
# Spatial constants (px, react-flow coordinate space). NODE_W/H are the cell PITCH —
# the fixed 300x180 tile (set in the frontend) plus a gap, so tiles never overlap.
NODE_W = 336
NODE_H = 212
REGION_GAP = 190           # gap between cluster regions
MAX_ROW_W = 3400           # wrap cluster regions after this width


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


# Embedding-only topic threshold for the two-phase journey clustering. Higher than
# CLUSTER_THRESHOLD (0.6) because it scores a single signal (embedding cosine) rather than
# the max-of-signals blend — 0.7 gives clean topic groups on real data (a shared "base"
# source no longer chains unrelated questions into one blob; see field diag 2026-08-03 r2).
EMBED_CLUSTER_THRESHOLD = 0.7


def cluster_journey_nodes(nodes: List[Dict[str, Any]], raw_pairs: List[Dict[str, Any]],
                          threshold: float = EMBED_CLUSTER_THRESHOLD) -> List[List[Dict[str, Any]]]:
    """Two-phase journey clustering that avoids source-hub mega-merges (field bug 2026-08-03 r2):

      1. **Topics** = connected components of CHAT nodes by EMBEDDING similarity only
         (>= threshold). Sources are excluded from this graph so one shared "base" source
         can't transitively chain 50 unrelated questions into a single blob.
      2. **Attach** each SOURCE to the ONE topic where most of the questions that cited it
         landed (majority vote, deterministic tie-break). A source JOINS a topic; it never
         bridges two. Sources cited only by unclustered questions become their own singletons.

    Returns clusters (largest first), same shape as `cluster_nodes`. Never raises."""
    by_id = {n["id"]: n for n in nodes if n.get("id")}

    def _is_source(n: Dict[str, Any]) -> bool:
        return n.get("ref_type") == "source" or n.get("kind") == "source"

    def _is_chat(n: Dict[str, Any]) -> bool:
        return n.get("ref_type") == "exploration_query" or n.get("kind") == "chat_turn"

    chat_ids = {nid for nid, n in by_id.items() if _is_chat(n)}
    src_ids = {nid for nid, n in by_id.items() if _is_source(n)}
    other_ids = [nid for nid, n in by_id.items() if nid not in chat_ids and nid not in src_ids]

    # Phase 1 — union-find over chat↔chat EMBEDDING links only.
    uf = _UF(list(chat_ids))
    for p in raw_pairs or []:
        a, b = p.get("a"), p.get("b")
        if a in chat_ids and b in chat_ids and float(p.get("embed", 0.0) or 0.0) >= threshold:
            uf.union(a, b)
    roots: Dict[str, List[Dict[str, Any]]] = {}
    for nid in chat_ids:
        roots.setdefault(uf.find(nid), []).append(by_id[nid])
    root_order = list(roots.keys())
    cluster_of_root = {r: i for i, r in enumerate(root_order)}
    clusters: List[List[Dict[str, Any]]] = [list(roots[r]) for r in root_order]

    # Phase 2 — attach sources by majority vote of their citing questions' topics.
    # A source↔chat pair with shared>0 marks a citation (overlap via shared_source_signal).
    cited_by: Dict[str, List[str]] = {}
    for p in raw_pairs or []:
        if float(p.get("shared", 0.0) or 0.0) <= 0.0:
            continue
        a, b = p.get("a"), p.get("b")
        if a in src_ids and b in chat_ids:
            cited_by.setdefault(a, []).append(b)
        elif b in src_ids and a in chat_ids:
            cited_by.setdefault(b, []).append(a)
    for sid in src_ids:
        counts: Dict[str, int] = {}
        for cid in cited_by.get(sid, []):
            r = uf.find(cid)
            counts[r] = counts.get(r, 0) + 1
        if counts:
            best = max(sorted(counts), key=lambda r: counts[r])  # deterministic tie-break
            clusters[cluster_of_root[best]].append(by_id[sid])
        else:
            clusters.append([by_id[sid]])  # cited only by unclustered/dropped questions
    for nid in other_ids:
        clusters.append([by_id[nid]])
    return sorted(clusters, key=len, reverse=True)


def layout_clusters(clusters: List[List[Dict[str, Any]]]) -> Dict[str, Dict[str, float]]:
    """Pack cluster regions left-to-right (wrapping), nodes gridded inside each region.
    Returns {node_id: {"x", "y"}}. Regions never overlap (each gets a cw×ch box + gap)."""
    pos: Dict[str, Dict[str, float]] = {}
    x_cursor = 0.0
    y_cursor = 0.0
    row_h = 0.0
    for cluster in clusters:
        n = len(cluster)
        # roughly-square region: ~sqrt(n) columns (capped) so a big cluster (e.g. 44
        # sources) is a compact block, not a tall single strip.
        cols = max(1, min(8, math.ceil(math.sqrt(n))))
        rows = math.ceil(n / cols)
        cw = cols * NODE_W
        ch = rows * NODE_H
        if x_cursor > 0 and x_cursor + cw > MAX_ROW_W:
            x_cursor = 0.0
            y_cursor += row_h + REGION_GAP
            row_h = 0.0
        for ni, node in enumerate(cluster):
            pos[node["id"]] = {"x": x_cursor + (ni % cols) * NODE_W,
                               "y": y_cursor + (ni // cols) * NODE_H}
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
