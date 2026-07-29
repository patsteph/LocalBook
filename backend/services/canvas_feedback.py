"""Journey Canvas feedback loop (2.2.0 Walk) — the user teaches the system what connects.

A user-drawn edge (POST /canvas/edge with state='user') is the STRONGEST learning signal
on the map. When it lands, three best-effort effects fire OFF the request path:

  1. **Event bus** — emit `canvas_edge_drawn` on `curator_event_bus` (actor="user"), so the
     brain/observability layer sees the gesture. Instant, never raises.
  2. **Knowledge graph** — resolve each edge endpoint node → its UNDERLYING concept
     (a `chat_turn`/exploration_query node → its topics; a `source` node → its concepts)
     and write a **user-authored `ConceptLink`** (`auto_detected=False`, `verified=True`)
     between them. ADDITIVE — uses the existing links table via `add_user_link`; it never
     alters retrieval scoring. If either endpoint has no underlying concept in the graph
     yet, there is nothing to link — we log and move on (fail-open).
  3. **Quality Signal** — record a first-class `user_connection` signal via
     `quality_signals.record_signal` (feeds QS Phase 2 later). Never raises.

Design guarantees (do NOT regress):
  - `schedule_user_edge_feedback(...)` NEVER raises and NEVER blocks the edge-creation
    response — it fire-and-forgets `process_user_edge` via `safe_create_task`.
  - Only `state == 'user'` edges trigger this (candidate/provenance/curator/researched are
    ignored — a candidate promoted to a user edge already arrives as state='user').
  - Every one of the three effects is independently wrapped: a failure in any never breaks
    edge creation and never stops the other two.
  - The blocking LanceDB write is offloaded via `asyncio.to_thread` so it never stalls the
    event loop.

See READFIRST/in-progress/journey-canvas.md §"Feedback loop".
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from utils.tasks import safe_create_task

logger = logging.getLogger(__name__)

_COMPONENT = "journey_canvas"
# The exploration-store journey window we scan to recover a chat-turn node's topics.
_TOPIC_LOOKUP_LIMIT = 500


# ──────────────────────────────────────────────────────────────────────────────
# Public entry points
# ──────────────────────────────────────────────────────────────────────────────
def schedule_user_edge_feedback(notebook_id: str, edge: Dict[str, Any]) -> None:
    """Fire-and-forget the feedback loop from the request path.

    Returns immediately; never raises; never blocks the edge-creation response.
    No-ops for any non-`user` edge state.
    """
    try:
        if not edge or edge.get("state") != "user":
            return
        safe_create_task(
            process_user_edge(notebook_id, edge),
            name="canvas-user-edge-feedback",
        )
    except Exception as e:  # scheduling itself must never break edge creation
        logger.debug(f"[canvas-feedback] schedule failed (non-fatal): {e}")


async def process_user_edge(notebook_id: str, edge: Dict[str, Any]) -> Dict[str, Any]:
    """Run the three feedback effects for one user-drawn edge. Never raises.

    Returns a small result dict `{emitted, linked, signal, skipped}` for
    telemetry + tests.
    """
    result: Dict[str, Any] = {
        "emitted": False, "linked": False, "signal": False, "skipped": None,
    }
    try:
        if not edge or edge.get("state") != "user":
            result["skipped"] = "non-user-state"
            return result

        source_node_id = edge.get("source") or ""
        target_node_id = edge.get("target") or ""

        # 1) Event bus — instant, self-contained.
        result["emitted"] = _emit_connection_event(notebook_id, edge)

        # 3) Quality Signal — instant append, self-contained. (Ordered before the
        #    graph write so a slow/absent graph never delays the observable signal.)
        result["signal"] = _record_connection_signal(notebook_id, edge)

        # 2) Knowledge graph — resolve both endpoints → concepts, write a user link.
        result["linked"] = await _write_user_link(
            notebook_id, source_node_id, target_node_id, edge
        )
    except Exception as e:  # belt-and-suspenders — this coroutine must never crash a task
        logger.warning(f"[canvas-feedback] process_user_edge failed (non-fatal): {e}")
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Effect 1 — event bus
# ──────────────────────────────────────────────────────────────────────────────
def _emit_connection_event(notebook_id: str, edge: Dict[str, Any]) -> bool:
    try:
        from services.curator_event_bus import event_bus

        event_bus.emit_now(
            actor="user",
            action="canvas_edge_drawn",
            notebook_id=notebook_id,
            payload={
                "edge_id": edge.get("id"),
                "source": edge.get("source"),
                "target": edge.get("target"),
                "label": edge.get("label", ""),
            },
            outcome="success",
        )
        return True
    except Exception as e:
        logger.debug(f"[canvas-feedback] event emit failed (non-fatal): {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Effect 3 — quality signal
# ──────────────────────────────────────────────────────────────────────────────
def _record_connection_signal(notebook_id: str, edge: Dict[str, Any]) -> bool:
    try:
        from services.quality_signals import record_signal

        label = (edge.get("label") or "").strip()
        detail = "user drew a connection on the Journey Canvas"
        if label:
            detail += f" ({label})"
        record_signal(
            "user_connection",
            _COMPONENT,
            detail,
            input_text=label,
            severity="info",  # positive teach signal, not a near-miss
            key=label,
            notebook_id=notebook_id,
        )
        return True
    except Exception as e:
        logger.debug(f"[canvas-feedback] signal record failed (non-fatal): {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Effect 2 — user-authored knowledge-graph link
# ──────────────────────────────────────────────────────────────────────────────
async def _write_user_link(
    notebook_id: str,
    source_node_id: str,
    target_node_id: str,
    edge: Dict[str, Any],
) -> bool:
    """Resolve both endpoints → underlying concepts, write a user link. Never raises."""
    try:
        nodes_by_id = _load_nodes(notebook_id)
        src_node = nodes_by_id.get(source_node_id)
        dst_node = nodes_by_id.get(target_node_id)
        if not src_node or not dst_node:
            logger.debug(
                f"[canvas-feedback] edge endpoint node(s) not found "
                f"({source_node_id}→{target_node_id}); skipping graph link"
            )
            return False

        src_concept = await _resolve_node_concept_id(notebook_id, src_node)
        dst_concept = await _resolve_node_concept_id(notebook_id, dst_node)
        if not src_concept or not dst_concept:
            logger.debug(
                "[canvas-feedback] no underlying concept for one/both endpoints "
                f"(src={bool(src_concept)}, dst={bool(dst_concept)}); nothing to link"
            )
            return False
        if src_concept == dst_concept:
            return False

        evidence = (edge.get("label") or "").strip() or "user-drawn on Journey Canvas"
        # Offload the blocking LanceDB write off the event loop.
        from services.knowledge_graph import knowledge_graph_service

        link = await asyncio.to_thread(
            knowledge_graph_service.add_user_link,
            src_concept,
            dst_concept,
            notebook_id,
            evidence=evidence,
        )
        return link is not None
    except Exception as e:
        logger.warning(f"[canvas-feedback] _write_user_link failed (non-fatal): {e}")
        return False


def _load_nodes(notebook_id: str) -> Dict[str, Dict[str, Any]]:
    """{node_id: node} for the notebook's canvas layout. Never raises."""
    try:
        from storage import canvas_layout_store as cl

        layout = cl.get_layout(notebook_id)
        return {n["id"]: n for n in layout.get("nodes", []) if n.get("id")}
    except Exception as e:
        logger.debug(f"[canvas-feedback] load nodes failed (non-fatal): {e}")
        return {}


async def _resolve_node_concept_id(
    notebook_id: str, node: Dict[str, Any]
) -> Optional[str]:
    """Map a canvas node → the id of its underlying EXISTING graph concept.

    - `source` node          → its most-frequent concept (via concept_ids_for_source).
    - `exploration_query`/    → resolve its topics (then its title as a fallback) against
      `chat_turn` node          existing concept names.
    - anything else          → try the node title as a concept name.

    Returns None when nothing resolves (we only ever link concepts already present).
    """
    try:
        from services.knowledge_graph import knowledge_graph_service as kg

        ref_type = (node.get("ref_type") or "").strip()
        ref_id = (node.get("ref_id") or "").strip()
        kind = (node.get("kind") or "").strip()

        # A source node → its underlying concepts (sync LanceDB scan, offloaded).
        if ref_type == "source" or kind == "source":
            if ref_id:
                ids = await asyncio.to_thread(kg.concept_ids_for_source, ref_id)
                if ids:
                    return ids[0]

        # A chat-turn node → its topics, resolved to existing concept names.
        candidate_names: List[str] = []
        if ref_type in ("exploration_query",) or kind == "chat_turn":
            candidate_names.extend(await _topics_for_query(notebook_id, ref_id))

        # Fallback for every node type: the node title as a concept name.
        title = (node.get("title") or "").strip()
        if title:
            candidate_names.append(title)

        for name in candidate_names:
            cid = kg.find_concept_id(name)
            if cid:
                return cid
        return None
    except Exception as e:
        logger.debug(f"[canvas-feedback] resolve concept failed (non-fatal): {e}")
        return None


async def _topics_for_query(notebook_id: str, query_id: str) -> List[str]:
    """Recover the `topics` list of an exploration-query chat-turn node. Never raises."""
    try:
        if not query_id:
            return []
        from storage.exploration_store import exploration_store

        journey = await exploration_store.get_journey(notebook_id, _TOPIC_LOOKUP_LIMIT)
        for q in journey.get("queries", []) or []:
            if str(q.get("id", "")) == query_id:
                topics = q.get("topics") or []
                return [str(t) for t in topics if t]
        return []
    except Exception as e:
        logger.debug(f"[canvas-feedback] topics lookup failed (non-fatal): {e}")
        return []
