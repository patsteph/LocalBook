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
    """Create (or update) an edge — the user-drawn gesture in Crawl."""
    if req.state not in cl.EDGE_STATES:
        raise HTTPException(status_code=422, detail=f"invalid edge state '{req.state}'")
    edge = cl.upsert_edge(notebook_id, req.model_dump())
    if edge is None:
        raise HTTPException(status_code=500, detail="upsert_edge failed")
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
    merged, added = canvas_populate.populate_layout(journey, source_events, existing)
    if not cl.save_layout(notebook_id, merged, existing["edges"], existing["viewport"]):
        raise HTTPException(status_code=500, detail="save_layout failed")
    return {"status": "populated", "added": added, "total_nodes": len(merged)}
