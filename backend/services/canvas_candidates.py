"""Journey Canvas candidate-dot engine (2.2.0 Crawl P5).

Computes *latent* connections between the currently-visible canvas nodes from three
signals we already have — no new capture:

  1. **Shared entities (KG)** — strongest/cheapest. `knowledge_graph.get_connections_for_source`
     already ranks related sources by shared-concept count (source↔source pairs).
  2. **Embedding similarity** — cosine over each node's `knowledge_graph.get_embedding`
     (same arctic 1024-dim; link precedent threshold 0.5). Works for every node kind.
  3. **Shared-source overlap** — a chat-turn's `sources_used[]` intersected with source-node
     ids (and chat↔chat via their source sets), as an overlap coefficient.

Blended: ``0.5·concept + 0.35·embed + 0.15·shared_source`` (spec §"Candidate-dot engine").

**Bounding** (the noise fix): only between visible nodes; per-node top-K=3; score ≥ 0.5;
global cap ~12. Candidates are **transient** — recomputed on demand, never persisted.

The scoring + bounding math is PURE (`score_and_bound`, `cosine`, `overlap_coefficient`) so
it is CI-unit-tested with injected signal values — no live model. `compute_candidates` is the
thin async orchestrator that gathers the real signals and calls the pure core.
"""
from __future__ import annotations

import asyncio
import logging
from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ── Blend weights (must sum to 1.0) + bounds (spec §"Candidate-dot engine") ──────────
W_CONCEPT = 0.5
W_EMBED = 0.35
W_SHARED = 0.15

TOP_K = 3          # max candidate dots per node
MIN_SCORE = 0.5    # confidence threshold — an invitation, not noise
GLOBAL_CAP = 12    # hard ceiling across the whole canvas

# Shared-concept count that saturates the concept signal to 1.0.
CONCEPT_SATURATION = 5.0
# Don't try to embed/score an unbounded visible set — keep it snappy.
MAX_NODES = 60


# ── Pure signal helpers (unit-tested) ────────────────────────────────────────────────
def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors, clamped to [0, 1]. Empty/degenerate → 0.0."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    sim = dot / ((na ** 0.5) * (nb ** 0.5))
    if sim < 0.0:
        return 0.0
    if sim > 1.0:
        return 1.0
    return sim


def overlap_coefficient(a: set, b: set) -> float:
    """Szymkiewicz–Simpson overlap: |A∩B| / min(|A|,|B|). 0.0 if either set is empty.

    Chosen over Jaccard so a chat-turn that cites a single source scores 1.0 against that
    source node (the strongest, most literal "shared source" relationship)."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / float(min(len(a), len(b)))


def jaccard(a: set, b: set) -> float:
    """|A∩B| / |A∪B|. 0.0 if both empty. Stricter than overlap: a single source shared
    between two multi-source questions scores LOW (not 1.0), so one common "base" source
    can't collapse otherwise-unrelated questions into one giant cluster."""
    if not a or not b:
        return 0.0
    union = len(a | b)
    return (len(a & b) / float(union)) if union else 0.0


def shared_source_signal(a: set, b: set, a_type: Optional[str], b_type: Optional[str]) -> float:
    """Shared-source similarity, type-aware. A source↔chat pair uses OVERLAP (a source is
    fully 'used by' the question that cited it → 1.0, so it attaches to that question's
    cluster). A chat↔chat (or source↔source) pair uses JACCARD, so two questions co-cluster
    only when their source sets SUBSTANTIALLY overlap — not merely share one common doc."""
    if {a_type, b_type} == {"source", "exploration_query"}:
        return overlap_coefficient(a, b)
    return jaccard(a, b)


def blended_score(concept: float, embed: float, shared: float) -> float:
    """Weighted blend of the three normalized (0–1) signals."""
    return W_CONCEPT * concept + W_EMBED * embed + W_SHARED * shared


def dominant_signal(concept: float, embed: float, shared: float) -> str:
    """Which signal contributed the most weighted mass (ties: concept > embed > shared)."""
    contributions = (
        ("concept", W_CONCEPT * concept),
        ("embed", W_EMBED * embed),
        ("shared_source", W_SHARED * shared),
    )
    return max(contributions, key=lambda kv: kv[1])[0]


# ── Pure scoring + bounding core (the CI-unit-tested entry point) ─────────────────────
def score_and_bound(
    raw_pairs: Sequence[Dict[str, Any]],
    *,
    top_k: int = TOP_K,
    min_score: float = MIN_SCORE,
    global_cap: int = GLOBAL_CAP,
) -> List[Dict[str, Any]]:
    """Blend → threshold → per-node top-K → global-cap.

    `raw_pairs`: each ``{"a": node_id, "b": node_id, "concept": f, "embed": f, "shared": f}``
    with signals already normalized to [0, 1]. Returns the bounded candidate list
    ``[{"a_node", "b_node", "score", "signal"}]`` sorted by score desc.

    Degree-bounding is greedy: pairs are considered highest-score-first, and a pair is kept
    only if *both* endpoints still have room (< top_k), so every node shows at most top_k
    dots and the strongest links win the budget. Deterministic: ties break on (a, b) id.
    """
    scored: List[Tuple[float, str, str, str]] = []
    for p in raw_pairs:
        a = p["a"]
        b = p["b"]
        if a == b:
            continue
        concept = _clamp01(p.get("concept", 0.0))
        embed = _clamp01(p.get("embed", 0.0))
        shared = _clamp01(p.get("shared", 0.0))
        # A strong SINGLE signal is enough to suggest a link — the blend alone caps a
        # chat↔chat pair (concept always 0) at 0.5·0 + 0.35·embed + 0.15·shared, so on a
        # question-heavy canvas nothing ever cleared min_score and the dots/auto-connect
        # were always empty. max() lets high embedding topic-similarity qualify on its own.
        score = max(blended_score(concept, embed, shared), embed, concept)
        if score < min_score:
            continue
        scored.append((score, a, b, dominant_signal(concept, embed, shared)))

    # Highest score first; deterministic tie-break on the (a, b) id pair.
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))

    degree: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for score, a, b, signal in scored:
        if len(out) >= global_cap:
            break
        if degree.get(a, 0) >= top_k or degree.get(b, 0) >= top_k:
            continue
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
        out.append({"a_node": a, "b_node": b, "score": round(score, 4), "signal": signal})
    return out


def _clamp01(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def build_raw_pairs(
    nodes: Sequence[Dict[str, Any]],
    embeddings: Dict[str, Sequence[float]],
    concept_counts: Dict[Tuple[str, str], float],
    source_sets: Dict[str, set],
) -> List[Dict[str, Any]]:
    """Pure assembly of every unordered visible-node pair's three normalized signals.

    - `embeddings`: node_id → vector (may be empty → embed signal 0).
    - `concept_counts`: unordered ``(source_id_a, source_id_b)`` (sorted tuple) → shared-concept
      count; normalized by CONCEPT_SATURATION. Non-source pairs simply have no entry → 0.
    - `source_sets`: node_id → set of source ids backing that node.
    """
    raw: List[Dict[str, Any]] = []
    for na, nb in combinations(nodes, 2):
        a_id = na["id"]
        b_id = nb["id"]

        concept = 0.0
        ra, rb = na.get("ref_id"), nb.get("ref_id")
        if ra and rb:
            key = (ra, rb) if ra <= rb else (rb, ra)
            count = concept_counts.get(key, 0.0)
            if count:
                concept = min(1.0, count / CONCEPT_SATURATION)

        embed = cosine(embeddings.get(a_id, []), embeddings.get(b_id, []))
        shared = shared_source_signal(
            source_sets.get(a_id, set()), source_sets.get(b_id, set()),
            na.get("ref_type"), nb.get("ref_type"),
        )

        if concept or embed or shared:
            raw.append({"a": a_id, "b": b_id, "concept": concept, "embed": embed, "shared": shared})
    return raw


# ── Async orchestrator — gathers real signals, then calls the pure core ───────────────
def _node_text(node: Dict[str, Any]) -> str:
    return f"{node.get('title', '')}\n{node.get('text', '')}".strip()


async def compute_candidates(notebook_id: str, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Gather the three signals for the visible `nodes` and return bounded candidate pairs.

    Never raises — any signal failure degrades to that signal being absent (0). A blank or
    single-node canvas returns []. `nodes` items: ``{id, ref_type, ref_id, title, text}``.
    """
    try:
        nodes = [n for n in (nodes or []) if n.get("id")][:MAX_NODES]
        if len(nodes) < 2:
            return []

        from services.knowledge_graph import knowledge_graph_service as kg

        embeddings = await _gather_embeddings(kg, nodes)
        concept_counts = await _gather_concept_counts(kg, notebook_id, nodes)
        source_sets = await _gather_source_sets(notebook_id, nodes)

        raw = build_raw_pairs(nodes, embeddings, concept_counts, source_sets)
        return score_and_bound(raw)
    except Exception as e:  # pragma: no cover — belt-and-suspenders; never break the canvas
        logger.warning(f"[canvas_candidates] compute failed ({notebook_id}): {e}")
        return []


async def compute_raw_pairs(notebook_id: str, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The raw per-pair similarity signals (BEFORE dot-bounding), reused by the clustering
    layout — so the map's spatial grouping and its candidate dots share one signal.
    Each pair: ``{a, b, concept, embed, shared}``. Never raises → [] on any failure."""
    try:
        nodes = [n for n in (nodes or []) if n.get("id")][:MAX_NODES]
        if len(nodes) < 2:
            return []
        from services.knowledge_graph import knowledge_graph_service as kg
        embeddings = await _gather_embeddings(kg, nodes)
        concept_counts = await _gather_concept_counts(kg, notebook_id, nodes)
        source_sets = await _gather_source_sets(notebook_id, nodes)
        return build_raw_pairs(nodes, embeddings, concept_counts, source_sets)
    except Exception as e:
        logger.warning(f"[canvas_candidates] raw pairs failed ({notebook_id}): {e}")
        return []


async def _gather_embeddings(kg, nodes: List[Dict[str, Any]]) -> Dict[str, Sequence[float]]:
    async def _one(node: Dict[str, Any]) -> Tuple[str, Sequence[float]]:
        try:
            text = _node_text(node)
            if not text:
                return node["id"], []
            return node["id"], (await kg.get_embedding(text) or [])
        except Exception:
            return node["id"], []

    results = await asyncio.gather(*[_one(n) for n in nodes])
    return {nid: vec for nid, vec in results}


async def _gather_concept_counts(
    kg, notebook_id: str, nodes: List[Dict[str, Any]]
) -> Dict[Tuple[str, str], float]:
    """KG shared-concept counts, keyed by sorted (source_id, source_id), for visible source
    nodes only. Restricted to the visible set so an off-canvas source never leaks a dot."""
    visible_source_ids = {
        n["ref_id"] for n in nodes if n.get("ref_type") == "source" and n.get("ref_id")
    }
    if len(visible_source_ids) < 2:
        return {}

    async def _one(sid: str) -> Tuple[str, List[Dict[str, Any]]]:
        try:
            conns = await kg.get_connections_for_source(sid, notebook_id)
            return sid, conns.get("related_sources", []) or []
        except Exception:
            return sid, []

    results = await asyncio.gather(*[_one(sid) for sid in visible_source_ids])

    counts: Dict[Tuple[str, str], float] = {}
    for sid, related in results:
        for rel in related:
            other = rel.get("source_id")
            if other not in visible_source_ids or other == sid:
                continue
            key = (sid, other) if sid <= other else (other, sid)
            count = float(rel.get("shared_concepts", 0) or 0)
            # KG connections are directional lookups; keep the max seen for the pair.
            if count > counts.get(key, 0.0):
                counts[key] = count
    return counts


async def _gather_source_sets(notebook_id: str, nodes: List[Dict[str, Any]]) -> Dict[str, set]:
    """Per-node backing-source sets: a source node backs itself; a chat-turn node backs the
    sources it used (looked up from exploration_store by the query id in ref_id)."""
    source_sets: Dict[str, set] = {}
    chat_ref_ids = set()
    for n in nodes:
        if n.get("ref_type") == "source" and n.get("ref_id"):
            source_sets[n["id"]] = {n["ref_id"]}
        elif n.get("ref_type") == "exploration_query" and n.get("ref_id"):
            chat_ref_ids.add(str(n["ref_id"]))

    if chat_ref_ids:
        try:
            from storage.exploration_store import exploration_store

            journey = await exploration_store.get_journey(notebook_id, limit=500)
            used_by_query: Dict[str, set] = {}
            for q in journey.get("queries", []) or []:
                qid = str(q.get("id", ""))
                if qid in chat_ref_ids:
                    used_by_query[qid] = set(q.get("sources_used", []) or [])
            for n in nodes:
                if n.get("ref_type") == "exploration_query" and n.get("ref_id"):
                    source_sets[n["id"]] = used_by_query.get(str(n["ref_id"]), set())
        except Exception as e:
            logger.debug(f"[canvas_candidates] source-set lookup failed: {e}")

    return source_sets
