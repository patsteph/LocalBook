"""Canvas card+thread layout (Canvas evolution, Phase 3) — deterministic, server-side.

Turns the Phase-2 sub-topic assignment into a spatial parent→card→thread layout:
- one react-flow GROUP node per sub-topic (the card: title + synthesis + count), sized to its threads;
- each THREAD placed INSIDE its card (parent_id = card node id, position RELATIVE to the card),
  ordered by time — the journey weave;
- ORPHANS (topic_id=None) in an "unsorted" lane below the cards.

Replaces the old `layout_clusters` √n grid. The LLM never emits coordinates — this is pure geometry
(CI-unit-tested). Collapse/expand is a frontend concern; here we lay out the expanded card and the
frontend shrinks it to the header when collapsed.
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict
from typing import Any, Dict, List

TOPIC_KIND = "topic"

# Card geometry.
_CHIP_W = 200.0
_CHIP_H = 120.0
_CHIP_GAP = 12.0
_CARD_PAD = 16.0
_HEADER_H = 64.0          # room for title + synthesis + count
# Golden-angle spiral step. Phyllotaxis gives ~uniform neighbor spacing ≈ _SPIRAL_SPACING, so this
# must exceed a large card's diagonal (~560px for a 3-4 col card) to avoid overlap.
_SPIRAL_SPACING = 720.0


def _topic_node_id(topic_id: str) -> str:
    return f"topic:{topic_id}"


def _card_dims(n_threads: int) -> "tuple[int, float, float]":
    """(cols, width, height) of a card holding n_threads chips."""
    n = max(1, n_threads)
    cols = max(1, min(4, int(math.ceil(math.sqrt(n)))))
    rows = int(math.ceil(n / cols))
    w = _CARD_PAD * 2 + cols * _CHIP_W + (cols - 1) * _CHIP_GAP
    h = _HEADER_H + _CARD_PAD + rows * _CHIP_H + (rows - 1) * _CHIP_GAP + _CARD_PAD
    return cols, w, h


def layout_topics_and_threads(topics: List[Dict[str, Any]],
                              nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return the full node list to persist: topic GROUP nodes + threads (with `parent_id` + relative
    position) + orphans (absolute, in a lane). `topics` = surviving sub-topics
    (id/title/synthesis/member_count); `nodes` = all thread nodes carrying `topic_id`."""
    by_topic: Dict[str, Dict[str, Any]] = {t["id"]: t for t in topics if t.get("id")}
    threads: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    orphans: List[Dict[str, Any]] = []
    for n in nodes:
        tid = n.get("topic_id")
        if tid and tid in by_topic:
            threads[tid].append(n)
        else:
            n["parent_id"] = None
            orphans.append(n)

    out: List[Dict[str, Any]] = []
    # Biggest topics first → they land near the center of the constellation.
    ordered = sorted(by_topic.values(), key=lambda t: -len(threads.get(t["id"], [])))

    # ORGANIC scatter (not a grid of rows): a phyllotaxis / golden-angle spiral. Card i sits at
    # angle i·137.5° and radius ∝ √i, which spreads cards out evenly like a constellation / the
    # dots on a sunflower — no rows, roughly-uniform spacing, deterministic (no randomness).
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for i, t in enumerate(ordered):
        members = sorted(threads.get(t["id"], []), key=lambda n: (n.get("created_at") or ""))
        cols, cw, ch = _card_dims(len(members))
        r = _SPIRAL_SPACING * math.sqrt(i + 0.5)
        theta = i * golden
        gx = r * math.cos(theta) - cw / 2.0     # center the card on the spiral point
        gy = r * math.sin(theta) - ch / 2.0

        group_id = _topic_node_id(t["id"])
        out.append({
            "id": group_id,
            "kind": TOPIC_KIND,
            "ref_type": TOPIC_KIND,
            "ref_id": t["id"],
            "title": t.get("title", "") or "Topic",
            "snapshot": {"id": str(uuid.uuid4()), "type": "json:topic-card",
                         "payload": {"title": t.get("title", ""), "synthesis": t.get("synthesis", ""),
                                     "count": len(members)}},
            "x": gx, "y": gy, "width": cw, "height": ch,
            "topic_id": None, "parent_id": None,
        })
        # Threads inside the card — positions RELATIVE to the group node, time-ordered.
        for k, n in enumerate(members):
            n["parent_id"] = group_id
            n["x"] = _CARD_PAD + (k % cols) * (_CHIP_W + _CHIP_GAP)
            n["y"] = _HEADER_H + (k // cols) * (_CHIP_H + _CHIP_GAP)
            n["width"] = _CHIP_W
            n["height"] = _CHIP_H
            out.append(n)

    # Orphans scatter on an OUTER ring of the same spiral — visible as loose "stars" to be connected.
    base = len(ordered)
    for k, n in enumerate(orphans):
        r = _SPIRAL_SPACING * math.sqrt(base + k + 2.0)
        theta = (base + k) * golden
        n["x"] = r * math.cos(theta)
        n["y"] = r * math.sin(theta)
        n["width"] = _CHIP_W
        n["height"] = _CHIP_H
        out.append(n)

    return out
