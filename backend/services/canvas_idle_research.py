"""Journey Canvas idle-research pass (2.2.0 Walk W5).

While the app is idle, the Night Shift enrichment worker quietly enriches each
notebook's canvas so the map grows on its own — "the map forms its own connections
while you're away." Two moves, both conservative + never-raise:

  1. **Auto-suggested connections** (always, when idle): compute high-confidence
     candidate pairs over the current canvas nodes (`canvas_candidates.compute_candidates`)
     and persist the strongest NEW ones as `curator` (dashed, suggested) edges via
     `canvas_layout_store.upsert_edge`. Skips any pair already edged. A *higher* bar
     than the interactive auto-connect (no user in the loop → high precision, no fallback).

  2. **Gap research** (gated + rate-limited, AWAY-only): pick the top gap from
     `canvas_gaps.find_gaps` (a weakly-answered question), run ONE cheap
     `research_engine.web_search`, and if it yields something AND the gap's question
     node has a genuine latent partner on the canvas, record a `researched` edge from
     the question node to that partner carrying `meta.insight` (the web finding). At
     most one gap per whole pass. No new node is ever created (the layout store's node
     set is only written via the API/populate) — the insight rides an edge we can write.

The write API used here — `upsert_edge` — makes the enrichment appear on the next
canvas load with no endpoint change (the frontend already renders `curator`/`researched`
edges). This module NEVER blocks the user path: the worker only calls it when idle, and
each pass yields cooperatively (checks presence + queue depth between notebooks).

The scoring/selection logic (`select_new_curator_edges`, `best_partner_for`) is PURE so
it is CI-unit-tested with injected candidates — no live model, no KG, no network.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# ── Schedule (centralization rule): cadence read live via schedule_store ─────────────
# NOTE: this id is intentionally NOT yet in schedule_store.SCHEDULE_REGISTRY (that file
# is out of scope for this change). get_interval/is_enabled both tolerate an unknown id
# (default cadence + enabled-unless-explicitly-disabled), so the seam still works and an
# override written to schedule_overrides.json is honored. A follow-up should add a
# ScheduleDef(id="canvas-idle-research", cadence_kind=INTERVAL, default_seconds=1800,
# can_disable=True, tier="DEEP") so the Schedule Viewer can surface + toggle it.
SCHEDULE_ID = "canvas-idle-research"
DEFAULT_INTERVAL_S = 1800.0        # a full idle pass at most every 30 min

# Auto-suggested connections: fully unattended, so demand a HIGHER confidence than the
# interactive auto-connect (0.6 w/ fallback). No fallback here — draw only strong links.
IDLE_MIN_SCORE = 0.65
MAX_NEW_EDGES_PER_NB = 4           # gentle: a few new suggestions per notebook per pass

# Gap research: conservative. AWAY-only, one gap per whole pass, and its own long cooldown
# on top of the pass cadence so we never hammer web search.
RESEARCH_COOLDOWN_S = 3 * 3600.0   # ≥3h between any two gap-research web lookups
RESEARCH_MAX_RESULTS = 3
_INSIGHT_MAX_CHARS = 280

_EDGE_ORIGIN = "idle_research"


# ── Pure selection core (CI-unit-tested) ─────────────────────────────────────────────
def select_new_curator_edges(
    candidates: Sequence[Dict[str, Any]],
    existing_edges: Sequence[Dict[str, Any]],
    valid_ids: Sequence[str],
    *,
    min_score: float = IDLE_MIN_SCORE,
    max_new: int = MAX_NEW_EDGES_PER_NB,
) -> List[Dict[str, Any]]:
    """Pick the strongest NEW candidate pairs to persist as `curator` edges.

    `candidates`: `[{a_node, b_node, score, signal}]` (as returned by compute_candidates —
    already sorted score-desc). `existing_edges`: the notebook's current edges. `valid_ids`:
    node ids currently on the canvas (a candidate referencing a vanished node is dropped).

    A pair is kept only if BOTH endpoints are on the canvas, the pair isn't already edged
    (in either direction), and score ≥ min_score. Returns ready-to-write edge dicts. Pure,
    deterministic, never raises on well-formed input.
    """
    edged = {
        frozenset((e.get("source"), e.get("target")))
        for e in (existing_edges or [])
        if e.get("source") and e.get("target")
    }
    valid = set(valid_ids or [])
    out: List[Dict[str, Any]] = []
    for c in candidates or []:
        a, b = c.get("a_node"), c.get("b_node")
        if not a or not b or a == b:
            continue
        if a not in valid or b not in valid:
            continue
        if float(c.get("score", 0.0)) < min_score:
            continue
        pair = frozenset((a, b))
        if pair in edged:
            continue
        edged.add(pair)
        out.append({
            "source": a,
            "target": b,
            "state": "curator",
            "label": c.get("signal", ""),
            "meta": {"origin": _EDGE_ORIGIN, "score": c.get("score")},
        })
        if len(out) >= max_new:
            break
    return out


def best_partner_for(
    node_id: str,
    candidates: Sequence[Dict[str, Any]],
    *,
    min_score: float = IDLE_MIN_SCORE,
) -> Optional[str]:
    """The other endpoint of the highest-scoring candidate pair that includes `node_id`
    and clears `min_score`, else None. Used to give a researched gap-insight a real second
    endpoint (a genuine latent relation), never a fabricated one. Pure."""
    best: Optional[str] = None
    best_score = min_score
    for c in candidates or []:
        a, b = c.get("a_node"), c.get("b_node")
        score = float(c.get("score", 0.0))
        if score < best_score:
            continue
        other = b if a == node_id else (a if b == node_id else None)
        if other:
            best, best_score = other, score
    return best


def _candidate_nodes(layout: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Shape the stored layout nodes into what compute_candidates expects
    (`{id, ref_type, ref_id, title, text}`), extracting the real embed text via
    canvas_populate.snapshot_text (never the Artifact-envelope dict)."""
    from services.canvas_populate import snapshot_text

    out: List[Dict[str, Any]] = []
    for n in layout.get("nodes", []) or []:
        if not n.get("id"):
            continue
        out.append({
            "id": n["id"],
            "ref_type": n.get("ref_type"),
            "ref_id": n.get("ref_id"),
            "title": n.get("title", ""),
            "text": snapshot_text(n),
        })
    return out


# ── Async per-notebook enrichment ────────────────────────────────────────────────────
async def _draw_connections(
    notebook_id: str, layout: Dict[str, Any], candidates: List[Dict[str, Any]]
) -> int:
    from storage import canvas_layout_store as cl

    valid_ids = [n["id"] for n in layout.get("nodes", []) if n.get("id")]
    to_draw = select_new_curator_edges(candidates, layout.get("edges", []), valid_ids)
    drawn = 0
    for edge in to_draw:
        if cl.upsert_edge(notebook_id, edge):
            drawn += 1
    return drawn


async def _research_top_gap(
    notebook_id: str, layout: Dict[str, Any], candidates: List[Dict[str, Any]]
) -> bool:
    """Research the notebook's top gap and, if it yields something with a genuine latent
    partner on the canvas, write a `researched` edge with `meta.insight`. Returns True iff
    an edge was written. Never raises."""
    try:
        from storage.exploration_store import exploration_store
        from services import canvas_gaps

        journey = await exploration_store.get_journey(notebook_id, limit=200)
        gaps = canvas_gaps.find_gaps(journey, max_gaps=5)
        if not gaps:
            return False
        gap = gaps[0]
        gap_ref = str(gap.get("ref_id") or "")
        gap_node = next(
            (
                n for n in layout.get("nodes", [])
                if n.get("ref_type") == "exploration_query" and str(n.get("ref_id")) == gap_ref
            ),
            None,
        )
        if not gap_node:
            return False
        partner = best_partner_for(gap_node["id"], candidates)
        if not partner:
            return False  # no genuine relation on the canvas → don't fabricate an edge

        from services.research_engine import research_engine

        results = await research_engine.web_search(
            gap.get("query", ""), notebook_id, max_results=RESEARCH_MAX_RESULTS
        )
        if not results:
            return False
        top = results[0]
        insight = f"{top.title} — {top.snippet}".strip(" —")[:_INSIGHT_MAX_CHARS]
        if not insight:
            return False

        from storage import canvas_layout_store as cl

        written = cl.upsert_edge(notebook_id, {
            "source": gap_node["id"],
            "target": partner,
            "state": "researched",
            "label": "researched",
            "meta": {
                "origin": _EDGE_ORIGIN,
                "insight": insight,
                "url": top.url,
                "query": gap.get("query", ""),
                "reason": gap.get("reason", ""),
            },
        })
        if written:
            logger.info(
                f"[canvas-idle-research] researched gap in {notebook_id}: "
                f"{gap.get('query', '')[:60]!r}"
            )
            return True
        return False
    except Exception as e:  # never break the pass
        logger.debug(f"[canvas-idle-research] gap research failed ({notebook_id}): {e}")
        return False


async def enrich_notebook(
    notebook_id: str, *, allow_research: bool
) -> Dict[str, Any]:
    """One notebook's idle enrichment: draw new suggested connections, optionally research
    the top gap. Returns `{edges_drawn, researched}`. Never raises.

    `allow_research` is decided ONCE per pass by the caller (AWAY-tier + cooldown), so a
    single pass researches at most one gap total."""
    result = {"edges_drawn": 0, "researched": False}
    try:
        from storage import canvas_layout_store as cl
        from services import canvas_candidates

        layout = cl.get_layout(notebook_id)
        nodes = layout.get("nodes", []) or []
        if len(nodes) < 2:
            return result

        cand_nodes = _candidate_nodes(layout)
        candidates = await canvas_candidates.compute_candidates(notebook_id, cand_nodes)

        result["edges_drawn"] = await _draw_connections(notebook_id, layout, candidates)
        if allow_research:
            result["researched"] = await _research_top_gap(notebook_id, layout, candidates)
    except Exception as e:
        logger.debug(f"[canvas-idle-research] enrich_notebook failed ({notebook_id}): {e}")
    return result


# ── Notebook enumeration (only those with an existing canvas layout) ─────────────────
async def _notebooks_with_layout() -> List[str]:
    """Ids of notebooks that already have a canvas layout worth enriching (≥2 nodes).
    Never raises → [] on any error."""
    try:
        from storage.notebook_store import notebook_store
        from storage import canvas_layout_store as cl

        notebooks = await notebook_store.list()
        out: List[str] = []
        for nb in notebooks or []:
            nb_id = nb.get("id")
            if not nb_id:
                continue
            try:
                if len(cl.get_layout(nb_id).get("nodes", []) or []) >= 2:
                    out.append(nb_id)
            except Exception:
                continue
        return out
    except Exception as e:
        logger.debug(f"[canvas-idle-research] notebook enumeration failed: {e}")
        return []


async def run_idle_pass(worker: Any = None, allow_research: bool = True) -> Dict[str, Any]:
    """One idle-research pass across every notebook that has a canvas layout.

    Cooperative + preemptible: between notebooks it bails if the user returns
    (`presence.is_active()`) or the worker gets real work (`worker.queue_depth() > 0`),
    and trickles via `presence.background_pace_seconds()` so it never bursts. Gap research
    happens at most once per pass and only when `allow_research` (the caller's cooldown gate)
    AND the box is clearly idle (AWAY). Returns a small summary. Never raises."""
    summary = {"notebooks": 0, "edges_drawn": 0, "researched": 0}
    try:
        from services import presence

        # Research only when the caller permits (cooldown) AND clearly idle (overnight /
        # long away); one gap per pass.
        allow_research_remaining = allow_research and (
            presence.current_tier() >= presence.Tier.AWAY
        )

        nb_ids = await _notebooks_with_layout()
        for nb_id in nb_ids:
            # Yield the moment the user returns or real work arrives.
            if presence.is_active():
                break
            if worker is not None:
                try:
                    if worker.queue_depth() > 0:
                        break
                except Exception:
                    pass

            res = await enrich_notebook(nb_id, allow_research=allow_research_remaining)
            summary["notebooks"] += 1
            summary["edges_drawn"] += res.get("edges_drawn", 0)
            if res.get("researched"):
                summary["researched"] += 1
                allow_research_remaining = False  # one gap per whole pass

            # Gentle inter-unit trickle (matches the inline-background-loop pattern).
            try:
                pace = presence.background_pace_seconds()
            except Exception:
                pace = 1.0
            if pace > 0:
                await asyncio.sleep(pace)

        if summary["edges_drawn"] or summary["researched"]:
            logger.info(
                f"[canvas-idle-research] pass done: {summary['notebooks']} notebook(s), "
                f"{summary['edges_drawn']} suggested edge(s), "
                f"{summary['researched']} gap(s) researched"
            )
    except Exception as e:
        logger.warning(f"[canvas-idle-research] pass failed: {e}")
    return summary
