"""Journey Canvas — generated artifacts → thread nodes (2.2.0 Canvas evolution, Phase 1).

Every thing a user makes in a notebook — podcasts, videos, quizzes, visuals, infographics,
documents — is a "thread" of their learning journey. Those artifacts live in per-type stores
(`audio_store`, `video_store`, `quiz_store`, `visual_store`, `infographic_store`, `content_store`),
NOT on the canvas. This module reads them and maps each into a canvas NODE dict (the same shape
`canvas_populate.build_nodes` emits: kind/ref_type/ref_id/title/snapshot/created_at/_group), so the
map becomes the single home of everything made — grouped into sub-topic cards in Phase 2+.

Node body renders through the existing `<ArtifactRender>` via the `snapshot` Artifact envelope; the
hybrid `ref_type`/`ref_id` links back to the live object (open / re-sync). Snapshots are COMPACT (a
card per medium, or the real SVG/infographic where it's already renderable) — a card of many full
interactive players would be too heavy; the live object opens on demand.

The per-store `_node_*` builders are PURE (row dict → node dict) so they're CI-unit-tested; the async
`list_notebook_artifacts` does the store reads. Every store read is guarded — one failing store never
sinks the others (fail-open, never raise).
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ARTIFACT_KIND = "artifact"
# ref_types this module emits — registered as DERIVED in api/canvas.py so Populate refreshes them
# (they're auto-derived from the artifact stores, not user-placed nodes).
ARTIFACT_REF_TYPES = ("audio", "video", "quiz", "visual", "infographic", "document")

_TITLE_MAX = 60


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _md(payload: str) -> Dict[str, Any]:
    return {"id": str(uuid.uuid4()), "type": "markdown", "payload": payload}


def _node(ref_type: str, ref_id: str, title: str, snapshot: Dict[str, Any],
          created_at: Any, topic: str = "", source_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "kind": ARTIFACT_KIND,
        "ref_type": ref_type,
        "ref_id": str(ref_id),
        "title": _truncate(title, _TITLE_MAX),
        "snapshot": snapshot,
        "_group": topic or "",          # seed grouping; Phase 2 re-assigns to a persisted sub-topic
        "created_at": created_at,
        "source_ids": [str(s) for s in (source_ids or [])],
    }


# ── Pure per-type row → node builders ─────────────────────────────────────────
def _node_audio(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if str(r.get("status") or "").lower() in ("failed", "error"):
        return None
    topic = r.get("topic") or "Podcast"
    dur = r.get("duration_minutes")
    sub = f"\n\n_🎙️ Podcast · {dur} min_" if dur else "\n\n_🎙️ Podcast_"
    return _node("audio", r.get("audio_id"), topic, _md(f"**{topic}**{sub}"),
                 r.get("created_at"), topic)


def _node_video(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if str(r.get("status") or "").lower() in ("failed", "error"):
        return None
    topic = r.get("topic") or "Video"
    return _node("video", r.get("video_id"), topic, _md(f"**{topic}**\n\n_🎬 Video_"),
                 r.get("created_at"), topic)


def _node_quiz(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    topic = r.get("topic") or "Quiz"
    n = r.get("num_questions")
    diff = r.get("difficulty") or "medium"
    meta = f"{n} questions · {diff}" if n else diff
    return _node("quiz", r.get("quiz_id"), topic, _md(f"**{topic}**\n\n_❓ Quiz · {meta}_"),
                 r.get("created_at"), topic)


def _node_visual(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    title = r.get("title") or r.get("topic") or "Visual"
    svg = r.get("svg_markup")
    mermaid = r.get("mermaid_code")
    if isinstance(svg, str) and svg.strip():
        snap = {"id": str(uuid.uuid4()), "type": "svg", "payload": svg}
    elif isinstance(mermaid, str) and mermaid.strip():
        snap = {"id": str(uuid.uuid4()), "type": "mermaid", "payload": mermaid}
    else:
        snap = _md(f"**{title}**\n\n_📊 Visual_")
    return _node("visual", r.get("visual_id"), title, snap, r.get("created_at"), r.get("topic") or "")


def _node_infographic(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    title = r.get("title") or r.get("topic") or "Infographic"
    payload = r.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = None
    if payload:
        # infographic_store persists the whole Artifact payload — render it for real.
        snap = {"id": str(uuid.uuid4()), "type": "json:infographic", "payload": payload}
    else:
        snap = _md(f"**{title}**\n\n_🖼️ Infographic_")
    return _node("infographic", r.get("infographic_id"), title, snap,
                 r.get("created_at"), r.get("topic") or "")


def _node_document(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    title = r.get("title") or r.get("topic") or "Document"
    body = r.get("content") or r.get("markdown") or ""
    if isinstance(body, str) and body.strip():
        snap = _md(_truncate(body, 600))
    else:
        snap = _md(f"**{title}**\n\n_📄 Document_")
    rid = r.get("id") or r.get("content_id") or r.get("document_id")
    return _node("document", rid, title, snap, r.get("created_at"), r.get("topic") or "")


# ── Async: read every store → nodes (guarded per store) ───────────────────────
async def _safe_list(store_getter, notebook_id: str) -> List[Dict[str, Any]]:
    try:
        store = store_getter()
        rows = await store.list(notebook_id)
        return list(rows or [])
    except Exception as e:
        logger.debug(f"[canvas_artifacts] store read skipped: {type(e).__name__}: {e}")
        return []


async def list_notebook_artifacts(notebook_id: str) -> List[Dict[str, Any]]:
    """All generated artifacts for a notebook mapped to canvas thread nodes. Never raises."""
    nodes: List[Dict[str, Any]] = []

    def _emit(rows: List[Dict[str, Any]], builder) -> None:
        for r in rows:
            try:
                n = builder(r)
                if n and n.get("ref_id"):
                    nodes.append(n)
            except Exception as e:
                logger.debug(f"[canvas_artifacts] node build skipped: {type(e).__name__}: {e}")

    _emit(await _safe_list(lambda: __import__("storage.audio_store", fromlist=["audio_store"]).audio_store,
                           notebook_id), _node_audio)
    _emit(await _safe_list(lambda: __import__("storage.video_store", fromlist=["video_store"]).video_store,
                           notebook_id), _node_video)
    _emit(await _safe_list(lambda: __import__("storage.quiz_store", fromlist=["quiz_store"]).quiz_store,
                           notebook_id), _node_quiz)
    _emit(await _safe_list(lambda: __import__("storage.visual_store", fromlist=["visual_store"]).visual_store,
                           notebook_id), _node_visual)
    _emit(await _safe_list(lambda: __import__("storage.infographic_store", fromlist=["infographic_store"]).infographic_store,
                           notebook_id), _node_infographic)
    # Documents (content_store) — best-effort; shape varies, so guarded like the rest.
    try:
        from storage.content_store import content_store as _cs
        if hasattr(_cs, "list"):
            _emit(await _safe_list(lambda: _cs, notebook_id), _node_document)
    except Exception:
        pass

    return nodes
