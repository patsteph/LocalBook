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


class CanvasChatRef(BaseModel):
    """A selected node the canvas-chat should scope its RAG to."""
    ref_type: Optional[str] = None
    ref_id: Optional[str] = None


class CanvasChatRequest(BaseModel):
    query: str
    nodes: List[CanvasChatRef] = []


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


# Auto-connect: promote confident candidates to 'curator' (dashed, suggested) edges.
# Candidates already floor at 0.5 (MIN_SCORE) + top-K + global cap, so they're a curated
# set. 0.6 = "confident enough to draw"; the old 0.72 needed near-full concept overlap AND
# high embedding sim → it essentially never fired ("nothing happened"). If nothing clears
# 0.6 but latent links exist, we still draw the strongest few so the feature always acts.
AUTO_CONNECT_THRESHOLD = 0.6
AUTO_CONNECT_FALLBACK_N = 3


@router.post("/auto-connect/{notebook_id}")
async def auto_connect(notebook_id: str, req: CandidatesRequest):
    """Auto-connect (W3/W4): promote confident candidate pairs into 'curator' (dashed,
    suggested) edges the user can keep or delete — the map forms its own connections.
    Reuses the candidate engine; skips a pair already edged. When no pair clears the
    confidence bar but candidates exist, draws the strongest few (they're deletable
    suggestions, not assertions) so the button never silently no-ops. Returns the updated
    CanvasLayout. Never 500s."""
    import logging
    try:
        from services import canvas_candidates
        cands = await canvas_candidates.compute_candidates(
            notebook_id, [n.model_dump() for n in req.nodes]
        )
        existing = cl.get_layout(notebook_id)
        edged = {frozenset((e.get("source"), e.get("target"))) for e in existing.get("edges", [])}

        # cands are sorted score-desc by the engine. Take everything ≥ threshold; if that's
        # empty, fall back to the strongest few candidates (all already ≥ MIN_SCORE=0.5).
        confident = [c for c in cands if c.get("score", 0.0) >= AUTO_CONNECT_THRESHOLD]
        to_draw = confident or cands[:AUTO_CONNECT_FALLBACK_N]

        drawn = 0
        for c in to_draw:
            pair = frozenset((c["a_node"], c["b_node"]))
            if pair in edged:
                continue
            if cl.upsert_edge(notebook_id, {
                "source": c["a_node"], "target": c["b_node"],
                "state": "curator", "label": c.get("signal", ""),
            }):
                edged.add(pair)
                drawn += 1
        logging.getLogger(__name__).info(
            f"[canvas] auto-connect {notebook_id}: {len(cands)} candidates, drew {drawn} edges")
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


# Run R1 — recall. A learning node is a chat turn or a canvas-generated answer.
_RECALL_KINDS = {"chat_turn"}
_RECALL_REF_TYPES = {"exploration_query", "canvas_answer"}


class RecallGradeRequest(BaseModel):
    grade: str = "good"  # again | good | easy


@router.get("/recall/{notebook_id}")
async def get_recall(notebook_id: str, limit: int = 20):
    """Run R1 — spaced-repetition recall: the learning nodes DUE for review right now
    ('what to revisit'). Never-reviewed learning nodes are due; reviewed ones are due when
    their SM-2 next_due has passed. Most-overdue first. Read-only; never 500s → [] on error."""
    import time as _time
    try:
        from storage import canvas_recall_store as recall
        layout = cl.get_layout(notebook_id)
        states = recall.get_states(notebook_id)
        now = _time.time()
        due: List[Dict[str, Any]] = []
        for n in layout.get("nodes", []):
            if n.get("kind") not in _RECALL_KINDS and n.get("ref_type") not in _RECALL_REF_TYPES:
                continue
            st = states.get(n.get("id"))
            next_due = st.get("next_due") if st else 0.0  # never reviewed → due now
            if next_due <= now:
                due.append({
                    "id": n.get("id"), "title": n.get("title", ""),
                    "snapshot": n.get("snapshot"), "ref_type": n.get("ref_type"),
                    "reps": (st or {}).get("reps", 0), "overdue": now - next_due,
                })
        due.sort(key=lambda d: d["overdue"], reverse=True)
        total_learning = sum(
            1 for n in layout.get("nodes", [])
            if n.get("kind") in _RECALL_KINDS or n.get("ref_type") in _RECALL_REF_TYPES
        )
        return {"due": due[:limit], "due_count": len(due), "total": total_learning}
    except Exception:
        return {"due": [], "due_count": 0, "total": 0}


@router.post("/recall/{notebook_id}/{node_id}")
async def post_recall(notebook_id: str, node_id: str, req: RecallGradeRequest):
    """Grade a recall review (again/good/easy) → advance the spaced-repetition schedule."""
    grade = req.grade if req.grade in ("again", "good", "easy") else "good"
    from storage import canvas_recall_store as recall
    return recall.review(notebook_id, node_id, grade)


@router.post("/chat/{notebook_id}")
async def canvas_chat(notebook_id: str, req: CanvasChatRequest):
    """Run R2 — chat with a selection: ask a question scoped to the SOURCES behind the
    selected nodes (source nodes contribute themselves; question nodes contribute the
    sources they were answered from). Falls back to the whole notebook when the selection
    resolves to no sources. Returns {answer, sources, source_ids} — the frontend can drop
    the answer back onto the canvas as a new node ('the map generates the map'). Never 500s."""
    import logging
    try:
        query = (req.query or "").strip()
        if not query:
            raise HTTPException(status_code=422, detail="empty query")

        source_ids: set = set()
        chat_qids: set = set()
        for r in req.nodes:
            if r.ref_type == "source" and r.ref_id:
                source_ids.add(str(r.ref_id))
            elif r.ref_type == "exploration_query" and r.ref_id:
                chat_qids.add(str(r.ref_id))
        if chat_qids:
            from storage.exploration_store import exploration_store
            journey = await exploration_store.get_journey(notebook_id, limit=500)
            for q in journey.get("queries", []) or []:
                if str(q.get("id", "")) in chat_qids:
                    source_ids.update(str(s) for s in (q.get("sources_used") or []))

        from services.rag_engine import rag_engine
        result = await rag_engine.query(
            notebook_id, query, source_ids=list(source_ids) or None, top_k=5,
        )
        return {
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
            "source_ids": sorted(source_ids),
            "scoped": bool(source_ids),
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.getLogger(__name__).warning(f"[canvas] chat failed ({notebook_id}): {e}")
        return {"answer": "I couldn't answer that over the selected nodes.", "sources": [],
                "source_ids": [], "scoped": False}


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


# Derived node kinds — auto-generated from capture. A rebuild replaces these fresh
# each Populate; genuinely user-placed nodes (any other ref_type) are preserved.
from services.canvas_artifacts import ARTIFACT_REF_TYPES as _ARTIFACT_REF_TYPES
# Derived (auto-populated) node ref_types — refreshed on every Populate; user-placed nodes are NOT
# in this set and are always preserved. Artifacts (podcasts/quizzes/visuals/…) are derived threads.
_DERIVED_REFS = {"exploration_query", "source", *_ARTIFACT_REF_TYPES}


@router.post("/populate/{notebook_id}")
async def populate(notebook_id: str, limit: int = 50):
    """Rebuild the journey map from capture (Crawl P2 + Walk W1/W2): learning chat turns
    (exploration_store, noise-filtered) + the sources those discussions REFERENCED
    (orphan sources stay off the map), clustered by topic into a readable layout.

    A fresh REBUILD of the derived nodes — the journey is auto-derived, so re-deriving
    it cleans out stale noise/renamed sources and reflects the current filters. Genuine
    user-placed nodes (notes) and their edges are preserved; stale derived edges drop."""
    import json as _json

    from services import canvas_populate, canvas_candidates, canvas_artifacts
    from storage.exploration_store import exploration_store
    from storage.source_store import source_store
    from services import activity_ledger

    journey = await exploration_store.get_journey(notebook_id, limit)
    # Generated artifacts (podcasts/quizzes/visuals/infographics/docs) → thread nodes. Never raises.
    artifact_nodes = await canvas_artifacts.list_notebook_artifacts(notebook_id)
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

    # Real source names (the source_added payload lacks a title → bare "Source" tiles).
    try:
        srcs = await source_store.list(notebook_id)
        source_titles = {s.get("id"): (s.get("filename") or s.get("title") or "") for s in (srcs or [])}
    except Exception:
        source_titles = {}

    # Build nodes (noise-filtered turns + referenced sources), gather similarity signals,
    # then cluster + spatially lay out (a readable topic map). Fails open to the grid.
    nodes = canvas_populate.build_nodes(journey, source_events, source_titles, artifact_nodes)
    cand = [{
        "id": f"{n.get('ref_type')}:{n.get('ref_id')}",
        "ref_type": n.get("ref_type"), "ref_id": n.get("ref_id"),
        "title": n.get("title", ""), "text": canvas_populate.snapshot_text(n),
    } for n in nodes]
    raw_pairs = await canvas_candidates.compute_raw_pairs(notebook_id, cand)
    seeded = canvas_populate.cluster_seed_layout(nodes, raw_pairs)

    # Phase 2 — STABLE/accretive sub-topics: stamp each thread's topic_id (None = orphan) and persist
    # the sub-topic cards (identity + centroid + auto title/synthesis). Never raises; on failure the
    # threads simply carry no topic_id (the map still renders, ungrouped). Card LAYOUT is Phase 3.
    from services import canvas_subtopics
    await canvas_subtopics.assign_and_persist(notebook_id, seeded)

    # Rebuild: preserve user-placed (non-derived) nodes + edges between them; replace
    # derived nodes with the fresh clustered set; drop stale derived edges.
    existing = cl.get_layout(notebook_id)
    preserved = [n for n in existing.get("nodes", []) if n.get("ref_type") not in _DERIVED_REFS]
    kept_ids = {n.get("id") for n in preserved}
    kept_edges = [e for e in existing.get("edges", [])
                  if e.get("source") in kept_ids and e.get("target") in kept_ids]
    final_nodes = preserved + seeded

    if not cl.save_layout(notebook_id, final_nodes, kept_edges, existing["viewport"]):
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
        "title": n.get("title", ""), "text": canvas_populate.snapshot_text(n),
    } for n in nodes if n.get("id")]
    raw_pairs = await canvas_candidates.compute_raw_pairs(notebook_id, cand)
    relaid = canvas_populate.relayout_existing(nodes, raw_pairs)
    cl.save_layout(notebook_id, relaid, existing["edges"], existing["viewport"])
    return cl.get_layout(notebook_id)
