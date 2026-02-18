"""LangGraph Collection Pipeline

Refactors the Collector→Curator orchestration into a checkpointed StateGraph
with human-in-the-loop approval via interrupt().

Nodes:
  create_task    → Curator generates smart collection task
  fetch_content  → Collector fetches from RSS/web/news/arXiv
  process_items  → Score, dedup, contextualize
  judge_items    → Curator judges all items
  apply_decisions→ Route: auto-approve, reject, or interrupt for user review

The graph is compiled with a MemorySaver checkpointer so each collection
run can resume after failure or after a user approves queued items.
"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class CollectionState(TypedDict):
    """State that flows through the collection pipeline."""
    notebook_id: str
    # Task created by Curator
    task: Optional[Dict[str, Any]]
    # Raw fetched items (serialised dicts, not Pydantic — for checkpointing)
    raw_items: List[Dict[str, Any]]
    # Processed items after scoring/dedup
    processed_items: List[Dict[str, Any]]
    # Curator judgments parallel to processed_items
    judgments: List[Dict[str, Any]]
    # Items pending user approval (interrupt payload)
    pending_approval: List[Dict[str, Any]]
    # Final result counters
    result: Dict[str, Any]
    # Pipeline metadata
    error: Optional[str]
    started_at: str
    deadline: float  # unix timestamp budget


# ---------------------------------------------------------------------------
# Helpers — serialise CollectedItem ↔ dict for checkpointing
# ---------------------------------------------------------------------------

def _item_to_dict(item) -> Dict[str, Any]:
    """Convert a CollectedItem Pydantic model to a plain dict."""
    d = item.model_dump() if hasattr(item, "model_dump") else item.dict()
    # datetime → isoformat string for JSON
    for k in ("collected_at",):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat()
    return d


def _dict_to_item(d: Dict[str, Any]):
    """Convert a dict back to a CollectedItem."""
    from agents.collector import CollectedItem
    return CollectedItem(**d)


# ---------------------------------------------------------------------------
# Node: create_task
# ---------------------------------------------------------------------------

async def create_task_node(state: CollectionState) -> Dict:
    """Curator creates a smart collection task for this notebook."""
    from agents.collector import get_collector
    from agents.curator import curator

    notebook_id = state["notebook_id"]
    deadline = state.get("deadline", 0)

    collector = get_collector(notebook_id)
    config = collector.get_config()

    if not config.intent or not config.intent.strip():
        return {
            "error": "Collector not configured",
            "result": {"items_collected": 0, "error": "Collector not configured"},
        }

    task = await curator._create_collection_task(notebook_id, config)
    task["_deadline"] = deadline

    logger.info(f"[CollectionGraph] Task created for {notebook_id}")
    return {"task": task}


# ---------------------------------------------------------------------------
# Node: fetch_content
# ---------------------------------------------------------------------------

async def fetch_content_node(state: CollectionState) -> Dict:
    """Collector fetches content from configured sources."""
    from agents.collector import get_collector

    if state.get("error"):
        return {}

    notebook_id = state["notebook_id"]
    task = state["task"]
    collector = get_collector(notebook_id)

    try:
        items = await collector.execute_collection_task(task)
        raw = [_item_to_dict(it) for it in items] if items else []
        logger.info(f"[CollectionGraph] Fetched {len(raw)} items for {notebook_id}")
        return {"raw_items": raw}
    except Exception as e:
        logger.error(f"[CollectionGraph] Fetch failed: {e}")
        return {"raw_items": [], "error": f"Fetch failed: {e}"}


# ---------------------------------------------------------------------------
# Node: judge_items
# ---------------------------------------------------------------------------

async def judge_items_node(state: CollectionState) -> Dict:
    """Curator judges all fetched items."""
    from agents.curator import curator
    from agents.collector import get_collector

    raw = state.get("raw_items", [])
    if not raw:
        return {
            "judgments": [],
            "result": {"items_collected": 0, "message": "No items found"},
        }

    notebook_id = state["notebook_id"]
    deadline = state.get("deadline", 0)
    collector = get_collector(notebook_id)
    config = collector.get_config()

    items = [_dict_to_item(d) for d in raw]

    try:
        judgment_results = await curator.judge_collection(
            collector_id=notebook_id,
            proposed_items=items,
            notebook_intent=config.intent,
            deadline=deadline,
        )
        judgments = []
        for j in judgment_results:
            judgments.append({
                "decision": j.decision.value if hasattr(j.decision, "value") else str(j.decision),
                "reason": getattr(j, "reason", ""),
                "modifications": getattr(j, "modifications", None),
            })
        return {"judgments": judgments}
    except Exception as e:
        logger.error(f"[CollectionGraph] Judgment failed: {e}")
        # Auto-defer all to user if judgment crashes
        judgments = [{"decision": "defer_to_user", "reason": f"Judgment error: {e}"}] * len(raw)
        return {"judgments": judgments}


# ---------------------------------------------------------------------------
# Node: apply_decisions
# ---------------------------------------------------------------------------

async def apply_decisions_node(state: CollectionState) -> Dict:
    """Apply curator judgments: approve, reject, or queue for user review.

    Items that need user review are collected into pending_approval.
    If any exist, the next edge routes to the approval_interrupt node.
    """
    from agents.collector import get_collector, CollectedItem

    raw = state.get("raw_items", [])
    judgments = state.get("judgments", [])
    notebook_id = state["notebook_id"]

    collector = get_collector(notebook_id)

    CONFIDENCE_FLOOR = 0.50

    approved = 0
    rejected = 0
    filtered = 0
    pending = []
    approved_titles: List[Dict] = []

    for item_dict, judgment in zip(raw, judgments):
        item = _dict_to_item(item_dict)
        decision = judgment.get("decision", "defer_to_user")

        # Hard confidence floor
        if item.overall_confidence < CONFIDENCE_FLOOR:
            filtered += 1
            continue

        if decision == "approve":
            try:
                was_stored = await collector._store_approved_item(item)
                if was_stored:
                    approved += 1
                    approved_titles.append({
                        "id": item.id, "title": item.title,
                        "source": item.source_name,
                        "confidence": item.overall_confidence,
                    })
                else:
                    filtered += 1
            except Exception:
                filtered += 1

        elif decision == "reject":
            rejected += 1

        else:
            # Needs user review
            pending.append({
                **item_dict,
                "_judgment_reason": judgment.get("reason", ""),
            })

    result = {
        "items_collected": len(raw),
        "items_approved": approved,
        "items_rejected": rejected,
        "items_filtered": filtered,
        "items_pending": len(pending),
        "auto_approved": approved_titles,
    }

    # Record collection run
    try:
        from services.collection_history import record_collection_run
        config = collector.get_config()
        record_collection_run(
            notebook_id=notebook_id,
            items_found=len(raw),
            items_approved=approved,
            items_pending=len(pending),
            items_rejected=rejected,
            sources_checked=len(config.sources.get("rss_feeds", [])) + len(config.sources.get("web_pages", [])),
            trigger="langgraph",
            keywords_used=(state.get("task") or {}).get("focus_areas", [])[:5],
        )
    except Exception:
        pass

    logger.info(f"[CollectionGraph] Decisions: {approved} approved, {rejected} rejected, "
                f"{filtered} filtered, {len(pending)} pending review")

    return {
        "result": result,
        "pending_approval": pending,
    }


# ---------------------------------------------------------------------------
# Node: approval_interrupt
# ---------------------------------------------------------------------------

def approval_interrupt_node(state: CollectionState) -> Command[Literal["store_approved", "__end__"]]:
    """Pause execution and surface pending items to the user for review.

    The caller resumes with a list of approved item IDs:
        Command(resume=["item-id-1", "item-id-2"])
    """
    pending = state.get("pending_approval", [])
    if not pending:
        return Command(goto="__end__")

    # Surface items for UI
    payload = {
        "action": "approve_items",
        "notebook_id": state["notebook_id"],
        "items": [
            {
                "id": p["id"],
                "title": p["title"],
                "url": p.get("url"),
                "preview": p.get("preview", "")[:200],
                "confidence": p.get("overall_confidence", 0),
                "reason": p.get("_judgment_reason", ""),
            }
            for p in pending
        ],
    }

    approved_ids = interrupt(payload)

    if not approved_ids:
        return Command(goto="__end__")

    return Command(
        goto="store_approved",
        update={"pending_approval": [
            p for p in pending if p["id"] in set(approved_ids)
        ]},
    )


# ---------------------------------------------------------------------------
# Node: store_approved (after user approval)
# ---------------------------------------------------------------------------

async def store_approved_node(state: CollectionState) -> Dict:
    """Store items that the user approved from the interrupt."""
    from agents.collector import get_collector

    pending = state.get("pending_approval", [])
    notebook_id = state["notebook_id"]
    collector = get_collector(notebook_id)

    newly_approved = 0
    for item_dict in pending:
        item = _dict_to_item(item_dict)
        try:
            was_stored = await collector._store_approved_item(item)
            if was_stored:
                newly_approved += 1
        except Exception as e:
            logger.error(f"[CollectionGraph] Failed to store user-approved item: {e}")

    # Update result counters
    result = dict(state.get("result", {}))
    result["items_approved"] = result.get("items_approved", 0) + newly_approved
    result["items_pending"] = max(0, result.get("items_pending", 0) - newly_approved)
    result["user_approved"] = newly_approved

    return {"result": result, "pending_approval": []}


# ---------------------------------------------------------------------------
# Edge: should we interrupt for approval?
# ---------------------------------------------------------------------------

def needs_approval(state: CollectionState) -> str:
    """Route to approval_interrupt if there are pending items, else end."""
    pending = state.get("pending_approval", [])
    if pending:
        return "approval_interrupt"
    return "__end__"


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

def build_collection_graph() -> StateGraph:
    """Construct and compile the collection pipeline graph."""
    builder = StateGraph(CollectionState)

    builder.add_node("create_task", create_task_node)
    builder.add_node("fetch_content", fetch_content_node)
    builder.add_node("judge_items", judge_items_node)
    builder.add_node("apply_decisions", apply_decisions_node)
    builder.add_node("approval_interrupt", approval_interrupt_node)
    builder.add_node("store_approved", store_approved_node)

    builder.add_edge(START, "create_task")
    builder.add_edge("create_task", "fetch_content")
    builder.add_edge("fetch_content", "judge_items")
    builder.add_edge("judge_items", "apply_decisions")
    builder.add_conditional_edges("apply_decisions", needs_approval)
    builder.add_edge("approval_interrupt", "store_approved")
    builder.add_edge("store_approved", END)

    return builder


# Singleton checkpointer — MemorySaver for dev, swap to SqliteSaver for prod
_checkpointer = MemorySaver()

collection_graph = build_collection_graph().compile(checkpointer=_checkpointer)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def run_collection(
    notebook_id: str,
    specific_query: Optional[str] = None,
    timeout_seconds: int = 120,
) -> Dict[str, Any]:
    """Run the full collection pipeline for a notebook.

    Returns the final result dict. If items need approval, the graph
    will be paused at the interrupt node — call resume_approval() to continue.
    """
    thread_id = f"collection-{notebook_id}-{int(time.time())}"
    deadline = time.time() + timeout_seconds

    initial_state: CollectionState = {
        "notebook_id": notebook_id,
        "task": None,
        "raw_items": [],
        "processed_items": [],
        "judgments": [],
        "pending_approval": [],
        "result": {},
        "error": None,
        "started_at": datetime.utcnow().isoformat(),
        "deadline": deadline,
    }

    config = {"configurable": {"thread_id": thread_id}}

    result = await collection_graph.ainvoke(initial_state, config=config)

    # Check for interrupt (pending approval)
    interrupt_data = result.get("__interrupt__")
    if interrupt_data:
        return {
            **result.get("result", {}),
            "status": "awaiting_approval",
            "thread_id": thread_id,
            "pending_items": interrupt_data[0].value if interrupt_data else {},
        }

    return {
        **result.get("result", {}),
        "status": "completed",
        "thread_id": thread_id,
    }


async def resume_approval(
    thread_id: str,
    approved_item_ids: List[str],
) -> Dict[str, Any]:
    """Resume a paused collection pipeline after user approves items."""
    config = {"configurable": {"thread_id": thread_id}}

    result = await collection_graph.ainvoke(
        Command(resume=approved_item_ids),
        config=config,
    )

    return {
        **result.get("result", {}),
        "status": "completed",
        "thread_id": thread_id,
    }
