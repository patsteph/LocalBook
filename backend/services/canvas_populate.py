"""Journey Canvas populate — seed nodes from EXISTING capture (2.2.0 Crawl P2).

Merges the richest, already-durable per-notebook capture into canvas nodes on first
entry: chat-turn nodes from `exploration_store` (queries + answer previews + the
sources they used) and source nodes from `activity_ledger` (`source_added` events).
The unified-timeline read-layer + the net-new made-from edges are Walk-phase; Crawl
reads what stores 1–3 already hold.

The mapping / seed-layout / merge logic here is PURE (no I/O) so it's CI-unit-tested;
the endpoint in api/canvas.py does the store reads and the save. Merge is idempotent:
re-populate preserves every existing node's position and adds only nodes not yet present,
so it never clobbers a user-arranged map.
"""
from __future__ import annotations

import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Tuple

CHAT_KIND = "chat_turn"
SOURCE_KIND = "source"
_SOURCES_GROUP = "__sources__"

# Seed-layout grid spacing (deterministic — the user rearranges from here).
COL_W = 340.0
ROW_H = 200.0


def _truncate(text: str, n: int = 60) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _markdown_snapshot(md: str) -> Dict[str, Any]:
    """A minimal Artifact envelope the frontend <ArtifactRender> can render as-is."""
    return {"id": str(uuid.uuid4()), "type": "markdown", "payload": md}


def build_nodes(journey: Dict[str, Any], source_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map raw capture → un-positioned nodes. `journey` = exploration_store.get_journey(...);
    `source_events` = activity_ledger.recent_events(kinds=(source_added,)) with `payload` dicts."""
    nodes: List[Dict[str, Any]] = []

    for q in journey.get("queries", []) or []:
        topics = q.get("topics") or []
        query_text = q.get("query", "")
        preview = q.get("answer_preview", "") or ""
        nodes.append({
            "kind": CHAT_KIND,
            "ref_type": "exploration_query",
            "ref_id": str(q.get("id", "")),
            "title": _truncate(query_text, 60),
            "snapshot": _markdown_snapshot(f"**Q:** {query_text}\n\n{preview}".strip()),
            "_group": topics[0] if topics else "",
            "created_at": q.get("timestamp"),
        })

    for ev in source_events or []:
        payload = ev.get("payload") or {}
        sid = payload.get("source_id") or str(ev.get("id", ""))
        title = payload.get("title") or payload.get("filename") or "Source"
        nodes.append({
            "kind": SOURCE_KIND,
            "ref_type": "source",
            "ref_id": str(sid),
            "title": _truncate(title, 60),
            "snapshot": _markdown_snapshot(f"**Source:** {title}"),
            "_group": _SOURCES_GROUP,
            "created_at": ev.get("ts"),
        })

    return nodes


def seed_layout(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Assign deterministic seed positions: one column per topic group, sources last."""
    groups: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for n in nodes:
        groups.setdefault(n.get("_group", ""), []).append(n)
    # sources column always rightmost for a stable, readable seed
    ordered = sorted(groups.items(), key=lambda kv: (kv[0] == _SOURCES_GROUP, kv[0]))

    out: List[Dict[str, Any]] = []
    for col, (_group, group_nodes) in enumerate(ordered):
        for row, n in enumerate(group_nodes):
            m = {k: v for k, v in n.items() if k != "_group"}
            m["x"] = col * COL_W
            m["y"] = row * ROW_H
            m["z"] = 0
            if m.get("created_at") is None:
                m.pop("created_at", None)
            out.append(m)
    return out


def merge_populate(
    new_nodes: List[Dict[str, Any]], existing_nodes: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], int]:
    """Preserve every existing node (positions + user-added), append only derived nodes whose
    (ref_type, ref_id) isn't already present. Returns (merged_nodes, added_count)."""
    existing_refs = {
        (n.get("ref_type"), n.get("ref_id")) for n in existing_nodes if n.get("ref_id")
    }
    merged = list(existing_nodes)
    added = 0
    for n in new_nodes:
        key = (n.get("ref_type"), n.get("ref_id"))
        if key in existing_refs:
            continue
        merged.append(n)
        existing_refs.add(key)
        added += 1
    return merged, added


def populate_layout(
    journey: Dict[str, Any],
    source_events: List[Dict[str, Any]],
    existing_layout: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], int]:
    """Full pure pipeline: build → seed → merge with existing. Returns (merged_nodes, added)."""
    seeded = seed_layout(build_nodes(journey, source_events))
    return merge_populate(seeded, existing_layout.get("nodes", []))
