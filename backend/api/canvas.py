"""Canvas API — per-notebook spatial layout for the Journey Canvas (2.2.0, Crawl P1).

Load/save the layout, patch a node's position (the debounced drag hot path), create/
delete user-drawn edges, and persist the viewport. Candidate-dot suggestions
(GET /canvas/candidates) and first-entry population (POST /canvas/populate) arrive with
their engines in Crawl P5 / P2.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from storage import canvas_layout_store as cl

router = APIRouter(prefix="/canvas", tags=["canvas"])


class SaveLayoutRequest(BaseModel):
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    viewport: Optional[Dict[str, float]] = None


class PatchNodeRequest(BaseModel):
    x: float
    y: float
    width: Optional[float] = None
    height: Optional[float] = None


class EdgeRequest(BaseModel):
    source: str
    target: str
    state: str = "user"
    label: str = ""
    id: Optional[str] = None
    meta: Dict[str, Any] = {}


class ViewportRequest(BaseModel):
    x: float = 0.0
    y: float = 0.0
    zoom: float = 1.0


class CandidateNodeRef(BaseModel):
    """A currently-visible node the candidate engine may connect to another visible node."""
    id: str
    ref_type: Optional[str] = None
    ref_id: Optional[str] = None
    title: str = ""
    text: str = ""


class CandidatesRequest(BaseModel):
    nodes: List[CandidateNodeRef] = []


@router.get("/layout/{notebook_id}")
async def get_layout(notebook_id: str):
    """Full layout for a notebook: {nodes, edges, viewport}."""
    return cl.get_layout(notebook_id)


@router.put("/layout/{notebook_id}")
async def save_layout(notebook_id: str, req: SaveLayoutRequest):
    """Bulk replace of nodes + edges (autosave-on-idle). Viewport upserted if provided."""
    ok = cl.save_layout(notebook_id, req.nodes, req.edges, req.viewport)
    if not ok:
        raise HTTPException(status_code=500, detail="save_layout failed")
    return {"status": "saved", "nodes": len(req.nodes), "edges": len(req.edges)}


@router.patch("/node/{notebook_id}/{node_id}")
async def patch_node(notebook_id: str, node_id: str, req: PatchNodeRequest):
    """Move (and optionally resize) a single node — the debounced drag/resize hot path.
    width/height are persisted only when supplied (a pure-drag patch omits them)."""
    if not cl.patch_node(notebook_id, node_id, req.x, req.y, req.width, req.height):
        raise HTTPException(status_code=404, detail="node not found")
    return {"status": "patched", "id": node_id, "x": req.x, "y": req.y,
            "width": req.width, "height": req.height}


@router.post("/edge/{notebook_id}")
async def upsert_edge(notebook_id: str, req: EdgeRequest):
    """Create (or update) an edge — the user-drawn gesture in Crawl.

    A user-drawn edge (state='user') is the strongest learning signal on the map, so
    it fans out into the Walk-phase feedback loop (curator_event_bus emit + a
    user-authored knowledge-graph link + a Quality Signal). That fan-out is fired
    fire-and-forget AFTER persistence — it never blocks or fails this response.
    """
    if req.state not in cl.EDGE_STATES:
        raise HTTPException(status_code=422, detail=f"invalid edge state '{req.state}'")
    edge = cl.upsert_edge(notebook_id, req.model_dump())
    if edge is None:
        raise HTTPException(status_code=500, detail="upsert_edge failed")
    # Feedback loop — user edges only; never raises, never blocks this response.
    if edge.get("state") == "user":
        from services import canvas_feedback

        canvas_feedback.schedule_user_edge_feedback(notebook_id, edge)
    return edge


@router.delete("/edge/{notebook_id}/{edge_id}")
async def delete_edge(notebook_id: str, edge_id: str):
    """Remove an edge."""
    if not cl.delete_edge(notebook_id, edge_id):
        raise HTTPException(status_code=404, detail="edge not found")
    return {"status": "deleted", "id": edge_id}


@router.put("/viewport/{notebook_id}")
async def save_viewport(notebook_id: str, req: ViewportRequest):
    """Persist pan/zoom."""
    if not cl.save_viewport(notebook_id, req.x, req.y, req.zoom):
        raise HTTPException(status_code=500, detail="save_viewport failed")
    return {"status": "saved", "x": req.x, "y": req.y, "zoom": req.zoom}


@router.post("/candidates/{notebook_id}")
async def candidates(notebook_id: str, req: CandidatesRequest):
    """P5 candidate-dot engine — latent connections between the currently-visible nodes.

    Blends three already-available signals (KG shared entities + embedding similarity +
    shared-source overlap), bounds them (only visible↔visible, top-K per node, score ≥ 0.5,
    global cap), and returns transient suggestions — never persisted. The frontend renders
    amber paired dots; clicking one promotes it to a real `user` edge (POST /canvas/edge).

    POST (not GET) because the visible-node set — with titles/text for embeddings — is a body
    payload, and the Fetch spec forbids a body on GET.
    """
    from services import canvas_candidates

    nodes = [n.model_dump() for n in req.nodes]
    pairs = await canvas_candidates.compute_candidates(notebook_id, nodes)
    return {"candidates": pairs}


# Auto-connect: promote a HIGH-confidence candidate to a real (dashed, suggested) edge.
AUTO_CONNECT_THRESHOLD = 0.72


@router.post("/auto-connect/{notebook_id}")
async def auto_connect(notebook_id: str, req: CandidatesRequest):
    """Auto-connect (W3/W4): promote HIGH-confidence candidate pairs into 'curator'
    (dashed, suggested) edges the user can keep or delete — the map forms its own
    connections. Reuses the candidate engine; skips a pair already edged. Returns the
    updated CanvasLayout so the frontend renders the new edges. Never 500s."""
    import logging
    try:
        from services import canvas_candidates
        cands = await canvas_candidates.compute_candidates(
            notebook_id, [n.model_dump() for n in req.nodes]
        )
        existing = cl.get_layout(notebook_id)
        edged = {frozenset((e.get("source"), e.get("target"))) for e in existing.get("edges", [])}
        for c in cands:
            if c.get("score", 0.0) < AUTO_CONNECT_THRESHOLD:
                continue
            pair = frozenset((c["a_node"], c["b_node"]))
            if pair in edged:
                continue
            if cl.upsert_edge(notebook_id, {
                "source": c["a_node"], "target": c["b_node"],
                "state": "curator", "label": c.get("signal", ""),
            }):
                edged.add(pair)
        return cl.get_layout(notebook_id)
    except Exception as e:
        logging.getLogger(__name__).warning(f"[canvas] auto-connect failed ({notebook_id}): {e}")
        return cl.get_layout(notebook_id)


@router.get("/timeline/{notebook_id}")
async def get_timeline(notebook_id: str, limit: int = 200):
    """Unified journey feed (Walk read-layer): merges the three capture stores —
    exploration chat journey + activity ledger + curator brain — into one
    newest-first, de-duplicated, typed timeline for the replay scrubber.

    Read-only; never 500s. Returns [] on any failure (build_timeline itself is
    fail-open per store, this wrapper is the last-resort guard)."""
    from services import canvas_timeline

    try:
        return await canvas_timeline.build_timeline(notebook_id, limit)
    except Exception:
        return []


@router.get("/gaps/{notebook_id}")
async def get_gaps(notebook_id: str, limit: int = 200):
    """Run R3 — gap-detection: questions the notebook answered weakly (missing sources /
    low confidence) surfaced as 'what to explore next'. Read-only; never 500s → [] on failure."""
    try:
        from services import canvas_gaps
        from storage.exploration_store import exploration_store
        journey = await exploration_store.get_journey(notebook_id, limit)
        return {"gaps": canvas_gaps.find_gaps(journey)}
    except Exception:
        return {"gaps": []}


@router.get("/provenance/{artifact_id}")
async def get_provenance(artifact_id: str):
    """Walk provenance: the made-from edges for a generated artifact — what it was
    built from (the Canvas draws these as provenance edges). Never 500s."""
    try:
        from services.curator_brain import curator_brain
        return {"artifact_id": artifact_id, "sources": curator_brain.get_provenance(artifact_id)}
    except Exception:
        return {"artifact_id": artifact_id, "sources": []}


@router.get("/source-derivations/{source_id}")
async def get_source_derivations(source_id: str, notebook_id: str = ""):
    """Reverse provenance: the artifacts derived FROM a given source ('what did
    this source produce?'). Never 500s."""
    try:
        from services.curator_brain import curator_brain
        arts = curator_brain.get_derived_from_source(source_id, notebook_id or None)
        return {"source_id": source_id, "artifacts": arts}
    except Exception:
        return {"source_id": source_id, "artifacts": []}


@router.post("/populate/{notebook_id}")
async def populate(notebook_id: str, limit: int = 50):
    """Seed nodes from existing capture (Crawl P2): chat turns (exploration_store) + sources
    (activity_ledger). Idempotent — preserves existing node positions, adds only what's new."""
    import json as _json

    from services import canvas_populate
    from storage.exploration_store import exploration_store
    from services import activity_ledger

    journey = await exploration_store.get_journey(notebook_id, limit)
    raw_events = activity_ledger.recent_events(
        notebook_id, limit=limit, kinds=(activity_ledger.KIND_SOURCE_ADDED,)
    )
    source_events = []
    for ev in raw_events:
        payload = ev.get("payload_json")
        if isinstance(payload, str):
            try:
                payload = _json.loads(payload)
            except Exception:
                payload = {}
        source_events.append({"id": ev.get("id"), "ts": ev.get("ts"), "payload": payload or {}})

    existing = cl.get_layout(notebook_id)

    # Walk W1/W2: build nodes, gather the similarity signals, then cluster + spatially lay
    # out (a readable map) instead of the topics[0] grid. Fails open to the grid.
    from services import canvas_candidates
    nodes = canvas_populate.build_nodes(journey, source_events)
    cand = [{
        "id": f"{n.get('ref_type')}:{n.get('ref_id')}",
        "ref_type": n.get("ref_type"), "ref_id": n.get("ref_id"),
        "title": n.get("title", ""), "text": n.get("snapshot", ""),
    } for n in nodes]
    raw_pairs = await canvas_candidates.compute_raw_pairs(notebook_id, cand)
    seeded = canvas_populate.cluster_seed_layout(nodes, raw_pairs)
    merged, added = canvas_populate.merge_populate(seeded, existing.get("nodes", []))

    if not cl.save_layout(notebook_id, merged, existing["edges"], existing["viewport"]):
        raise HTTPException(status_code=500, detail="save_layout failed")
    # Return the full saved layout so the frontend applies it directly (it expects CanvasLayout).
    return cl.get_layout(notebook_id)


@router.post("/relayout/{notebook_id}")
async def relayout(notebook_id: str):
    """Auto-arrange ('tidy up'): re-cluster + re-position ALL current nodes into a readable
    map by similarity. Keeps every node; only rewrites positions. Never 500s destructively."""
    from services import canvas_populate, canvas_candidates
    existing = cl.get_layout(notebook_id)
    nodes = existing.get("nodes", [])
    cand = [{
        "id": n.get("id"), "ref_type": n.get("ref_type"), "ref_id": n.get("ref_id"),
        "title": n.get("title", ""), "text": (n.get("snapshot") or n.get("title") or ""),
    } for n in nodes if n.get("id")]
    raw_pairs = await canvas_candidates.compute_raw_pairs(notebook_id, cand)
    relaid = canvas_populate.relayout_existing(nodes, raw_pairs)
    cl.save_layout(notebook_id, relaid, existing["edges"], existing["viewport"])
    return cl.get_layout(notebook_id)
