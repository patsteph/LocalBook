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

import re
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

CHAT_KIND = "chat_turn"
SOURCE_KIND = "source"
_SOURCES_GROUP = "__sources__"

# Seed-layout grid spacing (deterministic — the user rearranges from here).
COL_W = 340.0
ROW_H = 200.0


# ─────────────────────────────────────────────────────────────────────────────
# Learning-vs-noise filter (2.2.0) — DETERMINISTIC, no LLM.
#
# WHY: `POST /canvas/populate` used to seed a node for EVERYTHING the user typed
# in chat, so agent admin/status/config commands ("@curator brain status",
# "@collector show pending", "@correspondent sync now") became canvas clutter
# with zero learning value. The Journey Canvas should show the *learning journey*,
# not the control panel.
#
# PRINCIPLE:
#   LEARNING → keep : genuine RAG questions/answers; source additions (upload,
#                     @collector add/subscribe/add_url); @research pulls / deep
#                     dives / site searches; generated learning artifacts.
#                     Source nodes (from `source_added`) are ALWAYS learning.
#   NOISE   → skip  : agent admin / status / config commands (see NOISE_INTENTS).
#
# HOW: chat-turn records carry only text today (no classified `intent`), so we
# key off the message TEXT — an @-agent mention plus an admin keyword ⇒ noise;
# @research and plain questions ⇒ learning. If a record ever DOES arrive with an
# `intent` field (see the coordination note in the module docstring / report),
# `NOISE_INTENTS` is consulted directly and takes precedence over the text rules.
#
# These constants are meant to be READ AND EDITED by the product owner as the
# taxonomy is refined. Add/remove intent ids and keyword phrases freely — the
# logic below needs no changes.
# ─────────────────────────────────────────────────────────────────────────────

# The three "admin" agents whose status/config chatter is noise. `@research` is
# deliberately absent — every research command is a learning pull.
_ADMIN_AGENTS = ("curator", "collector", "correspondent")

# Authoritative NOISE intent ids, mirrored from services/intent_classifier.py
# (CURATOR_INTENTS / COLLECTOR_INTENTS / CORRESPONDENT_INTENTS). Everything an
# agent does that is pure admin / status readout / configuration lives here.
# Anything NOT in this set (RAG questions, source adds, @research, synthesis
# artifacts like morning_brief / discover_patterns) is treated as learning.
NOISE_INTENTS: frozenset[str] = frozenset({
    # ── @curator: config + status readouts ──────────────────────────────────
    "set_name", "set_personality", "toggle_overwatch",
    "exclude_notebook", "include_notebook", "show_profile",
    "brain_status", "collection_schedule", "note_themes",
    "approve_connection", "dismiss_connection",
    "set_voice", "show_voice", "show_draft", "discard_draft",
    "suppress_brief_topic", "unsuppress_brief_topic", "list_suppressed_topics",
    # ── @collector: config + status readouts ─────────────────────────────────
    "show_status", "show_pending", "show_history", "source_health",
    "set_intent", "set_subject", "set_focus", "set_excluded",
    "set_mode", "set_approval", "set_schedule", "set_filters",
    # ── @correspondent: sync / queue / config / audit readouts ───────────────
    "sync_now", "show_accounts", "pause", "resume",
    "show_queue", "approve_queued", "reroute_queued", "dismiss_queued",
    "show_subscriptions", "approve_subscription", "dismiss_subscription",
    "show_senders", "forget_sender", "show_recent", "show_sender",
    "quiet_senders", "move_source", "backfill_articles", "backfill_status",
    "refresh_titles", "reprocess_articles", "reextract_articles",
    "article_pipeline_status", "diagnose_extraction", "show_cluster_articles",
    "show_unsubscribe_candidates", "unsubscribe_sender", "show_blocklist",
    "unblock_sender", "show_routing", "digest_mode", "live_mode",
    "show_digest_mode", "show_score", "show_scorecards", "score_sender",
    "show_articles", "show_articles_from_sender", "show_entities",
    "show_entities_for_sender", "try_unsubscribe", "confirm_unsubscribe",
    "show_unsubscribe_log",
})
# NOTE: `show_status`/`show_profile`/`set_name` are shared ids across agents —
# listing once covers all. `collect_now`/`approve_all` (collector) are omitted
# on purpose: they don't add a node themselves, and the sources they pull in
# arrive as separate always-learning `source_added` nodes.

# High-precision multi-word phrases. When an admin @-agent is mentioned, any of
# these appearing in the message marks it as noise. Multi-word by design so they
# don't false-positive on genuine questions ("what's the status of the merger").
_ADMIN_PHRASES: Tuple[str, ...] = (
    "brain status", "show status", "show profile", "show pending",
    "show history", "show queue", "show accounts", "show senders",
    "show subscriptions", "show scorecards", "show routing", "show blocklist",
    "show digest", "show draft", "show recent", "show articles", "show entities",
    "show score", "show voice", "source health", "collection schedule",
    "collection status", "morning brief", "weekly wrap", "weekly wrap-up",
    "devil's advocate", "devils advocate", "discover patterns", "show themes",
    "note themes", "approve connection", "dismiss connection", "approve all",
    "set name", "set personality", "set voice", "set focus", "set intent",
    "set subject", "set mode", "set approval", "set schedule", "set filters",
    "toggle overwatch", "exclude notebook", "include notebook", "sync now",
    "digest mode", "live mode", "forget sender", "refresh titles",
    "diagnose extraction", "pipeline status", "backfill status",
    "unsubscribe candidates", "scorecard", "routing histogram",
)

# Terse whole-message admin commands — matched only when they are essentially the
# ENTIRE message (after stripping the @-agent token). This lets "@collector
# status" register as noise without flagging "@curator what is the status of X".
_ADMIN_COMMANDS: frozenset[str] = frozenset({
    "status", "profile", "pending", "history", "config", "settings",
    "overwatch", "pause", "resume", "sync", "queue", "accounts",
    "senders", "subscriptions", "scorecards", "routing", "blocklist",
    "backfill", "reprocess", "reextract", "re-extract", "help",
})

_AGENT_TOKEN_RE = re.compile(r"@(curator|collector|correspondent|research)\b", re.IGNORECASE)


def _agent_mention(lowered: str) -> str | None:
    """Return the first @-agent named in the message, or None."""
    m = _AGENT_TOKEN_RE.search(lowered)
    return m.group(1).lower() if m else None


def _has_admin_signal(text: str) -> bool:
    """True if `text` (already lowercased, @-agent token stripped) reads as an
    admin/status/config command rather than a learning request."""
    stripped = text.strip()
    if stripped in _ADMIN_COMMANDS:
        return True
    return any(phrase in stripped for phrase in _ADMIN_PHRASES)


def is_learning_query(query_record: Dict[str, Any]) -> bool:
    """Deterministic learning-vs-noise classifier for a single chat-turn record.

    Learning (keep)  → genuine questions, @research pulls, @collector source adds,
                        any RAG answer that consulted sources.
    Noise (skip)     → agent admin / status / config commands.

    Uses only fields already present on an `exploration_store` query record
    (`query` text, `sources_used`, and optionally a future `intent`); needs no
    endpoint change. Never raises — an unclassifiable record defaults to learning
    so we err toward showing, not hiding, the user's journey.
    """
    text = str(query_record.get("query") or "").strip()
    if not text:
        return False  # empty message → nothing was learned

    # If a classified intent is ever attached to the record, trust it outright.
    # (Records carry no `intent` today — see the coordination note in the report.)
    intent = str(query_record.get("intent") or "").strip()
    if intent:
        return intent not in NOISE_INTENTS

    lowered = text.lower()
    agent = _agent_mention(lowered)

    # @research: every pull / deep-dive / site-search carries learning value.
    if agent == "research":
        return True

    # Admin agents: noise only when the message names an admin/status/config
    # action. A source-adding command ("@collector add https://…", "@collector
    # subscribe …") or a genuine cross-notebook question ("@curator what did I
    # save about X") has no admin signal → learning.
    if agent in _ADMIN_AGENTS:
        remainder = _AGENT_TOKEN_RE.sub("", lowered).strip()
        return not _has_admin_signal(remainder)

    # No @-agent prefix — a plain chat turn.
    # A bare admin command / status readout ("brain status", "collection status",
    # "morning brief") is noise EVEN when the readout happened to list sources — so
    # this MUST precede the sources_used check (the field bug: status commands whose
    # answer listed recent sources leaked onto the canvas as "learning").
    if _has_admin_signal(lowered):
        return False
    # A RAG answer that actually consulted sources is unambiguously learning.
    if query_record.get("sources_used"):
        return True
    # Everything else is a genuine question → learning.
    return True


def _truncate(text: str, n: int = 60) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _markdown_snapshot(md: str) -> Dict[str, Any]:
    """A minimal Artifact envelope the frontend <ArtifactRender> can render as-is."""
    return {"id": str(uuid.uuid4()), "type": "markdown", "payload": md}


def snapshot_text(node: Dict[str, Any]) -> str:
    """The real text for a node's embedding — the markdown payload STRING, never the
    Artifact envelope dict. Passing the dict (its repr) makes every node share the
    `{'id':.., 'type':'markdown', 'payload':..}` boilerplate, which inflates cosine
    similarity to ~0.95 across the board and destroys topic clustering. Falls back to title."""
    snap = node.get("snapshot")
    if isinstance(snap, dict):
        payload = snap.get("payload")
        if isinstance(payload, str) and payload.strip():
            return payload
    if isinstance(snap, str) and snap.strip():
        return snap
    return node.get("title", "") or ""


def build_nodes(journey: Dict[str, Any], source_events: List[Dict[str, Any]],
                source_titles: Optional[Dict[str, str]] = None,
                artifact_nodes: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Map raw capture → un-positioned nodes. `journey` = exploration_store.get_journey(...);
    `source_events` = activity_ledger.recent_events(kinds=(source_added,)) with `payload` dicts;
    `source_titles` = source_id → real name (from source_store) so sources aren't bare 'Source';
    `artifact_nodes` = pre-built thread nodes for generated artifacts (from
    `canvas_artifacts.list_notebook_artifacts`) — podcasts/quizzes/visuals/infographics/docs.

    Sources are the BASE LAYER: only sources a learning discussion actually REFERENCED
    (via `sources_used`) are surfaced — orphan sources stay off the map so it isn't
    dirtied by every uploaded file (field feedback 2026-08-03). Generated artifacts are ALWAYS
    learning — they're the threads of the journey — so they're appended without a noise gate."""
    source_titles = source_titles or {}
    nodes: List[Dict[str, Any]] = []
    referenced: set = set()

    for q in journey.get("queries", []) or []:
        # Learning-vs-noise gate: drop agent admin/status/config chatter so the
        # canvas shows the learning journey, not the control panel. See is_learning_query.
        if not is_learning_query(q):
            continue
        topics = q.get("topics") or []
        query_text = q.get("query", "")
        preview = q.get("answer_preview", "") or ""
        for sid in (q.get("sources_used") or []):
            referenced.add(str(sid))
        nodes.append({
            "kind": CHAT_KIND,
            "ref_type": "exploration_query",
            "ref_id": str(q.get("id", "")),
            "title": _truncate(query_text, 60),
            "snapshot": _markdown_snapshot(f"**Q:** {query_text}\n\n{preview}".strip()),
            "_group": topics[0] if topics else "",
            "created_at": q.get("timestamp"),
        })

    # Only REFERENCED sources (a source appears because a discussion used it), built straight
    # from the referenced set + source_store titles. NOT from source_added events — their
    # payload source_ids don't reliably line up with sources_used, which silently dropped
    # every source node (field bug 2026-08-03 r2). created_at pulled from an event if present.
    event_ts: Dict[str, Any] = {}
    event_title: Dict[str, str] = {}
    for ev in source_events or []:
        p = ev.get("payload") or {}
        esid = str(p.get("source_id") or "")
        if esid:
            event_ts.setdefault(esid, ev.get("ts"))
            t = p.get("title") or p.get("filename")
            if t:
                event_title.setdefault(esid, t)
    for sid in sorted(referenced):
        title = source_titles.get(sid) or event_title.get(sid)
        if not title:
            continue  # source no longer exists / no title known → don't add a bare "Source" tile
        nodes.append({
            "kind": SOURCE_KIND,
            "ref_type": "source",
            "ref_id": sid,
            "title": _truncate(title, 60),
            "snapshot": _markdown_snapshot(f"**Source:** {title}"),
            "_group": _SOURCES_GROUP,
            "created_at": event_ts.get(sid),
        })

    # Generated artifacts are threads of the journey — always included (already node-shaped).
    for a in (artifact_nodes or []):
        if a and a.get("ref_id"):
            nodes.append(a)

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


def cluster_seed_layout(
    nodes: List[Dict[str, Any]], raw_pairs: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Similarity-driven layout (Walk W1/W2): consolidate to the SIGNIFICANT nodes, cluster
    by the candidate engine's pairwise signal, and place each cluster as its own spatial
    REGION — a readable MAP instead of the topics[0] grid. Falls back to `seed_layout` when
    there are no pairs (fresh/degenerate notebook). Never raises."""
    try:
        from services import canvas_cluster as cc
        if not raw_pairs:
            return seed_layout(nodes)
        # local stable id per node (ref_type:ref_id) for clustering — NOT persisted.
        ided = [{**n, "id": f"{n.get('ref_type')}:{n.get('ref_id')}"} for n in nodes]
        degrees = cc.degrees_from_pairs(ided, raw_pairs)
        sig = cc.significant_nodes(ided, degrees)
        pos = cc.layout_clusters(cc.cluster_journey_nodes(sig, raw_pairs))
        out: List[Dict[str, Any]] = []
        for n in sig:
            m = {k: v for k, v in n.items() if k not in ("_group", "id")}
            p = pos.get(n["id"], {"x": 0.0, "y": 0.0})
            m["x"], m["y"], m["z"] = round(p["x"]), round(p["y"]), 0
            if m.get("created_at") is None:
                m.pop("created_at", None)
            out.append(m)
        return out
    except Exception:
        return seed_layout(nodes)


def relayout_existing(
    existing_nodes: List[Dict[str, Any]], raw_pairs: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """The 'auto-arrange / tidy up' action: re-cluster + re-position ALL current nodes into
    a readable map by similarity. Unlike populate this KEEPS every node (they're already on
    the canvas) and only rewrites x/y. Grid fallback on empty pairs. Never raises."""
    try:
        from services import canvas_cluster as cc
        nodes = [n for n in existing_nodes if n.get("id")]
        if not nodes:
            return existing_nodes
        if not raw_pairs:
            for i, n in enumerate(nodes):
                n["x"], n["y"] = (i % 4) * cc.NODE_W, (i // 4) * cc.NODE_H
            return nodes
        pos = cc.layout_clusters(cc.cluster_journey_nodes(nodes, raw_pairs))
        for n in nodes:
            p = pos.get(n["id"])
            if p:
                n["x"], n["y"] = round(p["x"]), round(p["y"])
        return nodes
    except Exception:
        return existing_nodes


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
