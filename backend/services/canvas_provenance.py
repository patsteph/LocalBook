"""Canvas provenance edges — turn recorded made-from rows into canvas EDGES.

`curator_brain.record_provenance` has been writing "this artifact was built from these
sources" rows since 2026-07-31, the `provenance` edge state exists in the layout store,
and the frontend already styles, legends and arrow-heads it. The only missing link was
that NOTHING converted the rows into edges — so the aqua edges never appeared.

Pure module (no I/O, no store access) so it unit-tests in CI, matching the
`canvas_populate` / `canvas_artifacts` convention. The caller fetches the rows and passes
nodes that already carry their final ids (`canvas_layout_store.assign_node_ids`).

Derived, never trusted-and-persisted: provenance edges are re-derived on every populate,
exactly like the derived nodes they connect. The populate rebuild drops stale derived
edges, so a source removed from an artifact's provenance loses its edge on the next pass.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EDGE_STATE = "provenance"

# Fixed namespace for deterministic provenance-edge ids. Node ids already encode the
# notebook (uuid5 over `notebook:ref_type:ref_id`), so the endpoints alone identify an edge.
_EDGE_NS = uuid.UUID("b3d9c5a1-7e42-4f18-9c06-8a5f2d3b6e77")

# An artifact built from 20 sources would otherwise fan 20 aqua edges into one node and
# bury the map. Keep the first N in row order (generators append in citation order).
DEFAULT_MAX_PER_ARTIFACT = 8


def edge_id(source: str, target: str) -> str:
    """Deterministic id for a provenance edge, so re-deriving it across populates yields
    the same row rather than churning ids under anything that references them."""
    return str(uuid.uuid5(_EDGE_NS, f"{EDGE_STATE}:{source}:{target}"))


def derive_edges(
    nodes: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    *,
    max_per_artifact: int = DEFAULT_MAX_PER_ARTIFACT,
    skip_pairs: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Map-join provenance rows onto canvas nodes → `provenance` edges (origin → artifact).

    `nodes` must already carry final ids. `rows` are `curator_brain.get_provenance*` dicts:
    `{artifact_type, artifact_id, source_type, source_id, ...}`.

    The join is direct: a node's `(ref_type, ref_id)` matches `(artifact_type, artifact_id)`
    on one side and `(source_type, source_id)` on the other. `source_type` defaults to
    `"source"` in the writer and matches the source node's ref_type exactly; recording a
    question as the origin (`source_type="exploration_query"`) joins the same way with no
    special-casing here and no schema change.

    Rows whose artifact OR origin has no node on the map are dropped: source nodes only
    surface when a learning query referenced them, so an artifact built from an
    never-discussed source legitimately has nothing to point at.
    """
    by_ref: Dict[tuple, str] = {}
    for n in nodes or []:
        ref_type, ref_id, nid = n.get("ref_type"), n.get("ref_id"), n.get("id")
        if ref_type and ref_id and nid:
            by_ref.setdefault((str(ref_type), str(ref_id)), str(nid))

    edges: List[Dict[str, Any]] = []
    seen: set = set(skip_pairs or ())
    per_artifact: Dict[str, int] = {}
    dangling = 0

    for r in rows or []:
        try:
            target = by_ref.get((str(r.get("artifact_type") or ""), str(r.get("artifact_id") or "")))
            origin = by_ref.get((str(r.get("source_type") or "source"), str(r.get("source_id") or "")))
        except Exception:
            continue
        if not target or not origin or target == origin:
            dangling += 1
            continue
        if per_artifact.get(target, 0) >= max_per_artifact:
            continue
        pair = (origin, target)
        if pair in seen:
            continue
        seen.add(pair)
        per_artifact[target] = per_artifact.get(target, 0) + 1
        edges.append({
            "id": edge_id(origin, target),
            "source": origin,
            "target": target,
            "state": EDGE_STATE,
            "label": "",
            "meta": {"derived": True},
        })

    if dangling:
        logger.debug(f"[canvas] provenance: {len(edges)} edge(s), {dangling} row(s) with no node on the map")
    return edges
