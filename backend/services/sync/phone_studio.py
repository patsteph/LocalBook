"""Studio items made on a paired phone, stored here exactly like the ones this Mac makes.

A briefing, a set of flashcards (kept as a written piece), a quiz, or an audio overview arrives with an id the
phone made up. It is written into the same tables the Studio tab and the Journey Canvas already read, so it shows up
there, and the normal Mac-to-Mac sync carries it to the other Macs. Sending the same id again changes nothing.
Audio files arrive in chunks and the row is only written once the whole file is here and verified."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List

from fastapi import HTTPException

logger = logging.getLogger(__name__)

MAX_TEXT = 400_000
MAX_AUDIO_BYTES = 120_000_000
KINDS = ("doc", "cards", "quiz", "audio")
_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_lock = asyncio.Lock()


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _conn():
    from storage.database import get_db
    return get_db().get_connection()


def _check(notebook_id: str, item_id: str) -> None:
    from services.sync import phone_library
    if not _ID.match(item_id or ""):
        raise HTTPException(400, "bad item id")
    if not phone_library.is_visible(notebook_id):
        raise HTTPException(404, "no such notebook")


def _cards_markdown(cards: List[Dict[str, Any]]) -> str:
    return "\n\n".join(f"**{c.get('front', '').strip()}**\n\n{c.get('back', '').strip()}" for c in cards if c.get("front"))


def _quiz_questions(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for i, q in enumerate(questions):
        choices = [str(c) for c in (q.get("choices") or [])]
        ans = q.get("answer")
        if not choices or not isinstance(ans, int) or not 0 <= ans < len(choices):
            continue
        out.append({"id": f"q{i + 1}", "question": str(q.get("question", "")), "answer": choices[ans],
                    "explanation": str(q.get("why") or ""), "difficulty": "medium",
                    "question_type": "multiple_choice", "options": choices, "source_reference": None})
    return out


def _put_sync(kind: str, item_id: str, notebook_id: str, title: str, topic: str, created: str, body: Any) -> Dict[str, Any]:
    c = _conn()
    now = created or _now()
    if kind in ("doc", "cards"):
        text = str(body) if kind == "doc" else _cards_markdown(body if isinstance(body, list) else [])
        if not text.strip() or len(text) > MAX_TEXT:
            raise HTTPException(400, "empty or too long")
        skill, name = ("phone_briefing", "Briefing") if kind == "doc" else ("phone_flashcards", "Flashcards")
        c.execute("""INSERT OR IGNORE INTO content_generations
                     (content_id, notebook_id, skill_id, skill_name, content, topic, sources_used, created_at, updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?)""", (item_id, notebook_id, skill, name, text, topic or title, 0, now, now))
    elif kind == "quiz":
        qs = _quiz_questions(body if isinstance(body, list) else [])
        if not qs:
            raise HTTPException(400, "no usable questions")
        c.execute("""INSERT OR IGNORE INTO quiz_generations
                     (quiz_id, notebook_id, topic, difficulty, num_questions, questions_json, source_summary,
                      sources_used, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (item_id, notebook_id, topic or title, "medium", len(qs), json.dumps(qs), "Made on a phone", 0, now, now))
    else:
        raise HTTPException(400, "unknown kind")
    c.commit()
    return {"ok": True, "id": item_id}


async def put(kind: str, item_id: str, notebook_id: str, title: str, topic: str, created: str, body: Any) -> Dict[str, Any]:
    if kind not in KINDS or kind == "audio":
        raise HTTPException(400, "unknown kind")
    await asyncio.to_thread(_check, notebook_id, item_id)
    async with _lock:
        return await asyncio.to_thread(_put_sync, kind, item_id, notebook_id, title, topic, created, body)


def _audio_sync(item_id: str, notebook_id: str, offset: int, total: int, digest: str, data_b64: str,
                title: str, topic: str, created: str, script: str, duration: float, minutes: int) -> Dict[str, Any]:
    from services.sync import blobs
    if total <= 0 or total > MAX_AUDIO_BYTES:
        raise HTTPException(400, "bad audio size")
    rel = f"audio/{item_id}.m4a"
    res = blobs.write_chunk(rel, offset, data_b64, total, digest)
    if not res.get("done"):
        return {"next": res["next"], "done": False}
    c = _conn()
    now = created or _now()
    path = str(blobs.safe_path(rel))
    c.execute("""INSERT OR IGNORE INTO audio_generations
                 (audio_id, notebook_id, script, topic, duration_minutes, host1_gender, host2_gender, accent, skill_id,
                  audio_file_path, duration_seconds, status, error_message, created_at, updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (item_id, notebook_id, script, topic or title, minutes, "female", "male", "us", None, path, duration,
               "completed", None, now, now))
    c.commit()
    return {"next": res["next"], "done": True}


async def put_audio(b: Dict[str, Any]) -> Dict[str, Any]:
    item_id, notebook_id = str(b.get("id", "")), str(b.get("notebook_id", ""))
    await asyncio.to_thread(_check, notebook_id, item_id)
    async with _lock:
        try:
            return await asyncio.to_thread(
                _audio_sync, item_id, notebook_id, int(b.get("offset", 0)), int(b.get("total", 0)), str(b.get("sha256", "")),
                str(b.get("data", "")), str(b.get("title", "")), str(b.get("topic", "")), str(b.get("created", "")),
                str(b.get("script", ""))[:MAX_TEXT], float(b.get("duration", 0) or 0), max(1, round(float(b.get("duration", 0) or 0) / 60)))
        except ValueError as e:
            raise HTTPException(409, str(e))


def _delete_sync(item_id: str) -> Dict[str, Any]:
    from services.sync import blobs
    c = _conn()
    removed = 0
    for table, col in (("content_generations", "content_id"), ("quiz_generations", "quiz_id"), ("audio_generations", "audio_id")):
        removed += c.execute(f"DELETE FROM {table} WHERE {col} = ?", (item_id,)).rowcount
    c.commit()
    try:
        blobs.safe_path(f"audio/{item_id}.m4a").unlink(missing_ok=True)
    except (blobs.BlobPathError, OSError):
        pass
    return {"ok": True, "removed": removed}


async def delete(item_id: str) -> Dict[str, Any]:
    """Remove an item the phone made (and deleted there). Unknown ids are fine: deleting twice does nothing."""
    if not _ID.match(item_id or ""):
        raise HTTPException(400, "bad item id")
    async with _lock:
        return await asyncio.to_thread(_delete_sync, item_id)


# ── what this Mac has made, for the phone's Studio shelf ─────────────────────
#
# Diff-only: `listing` is small (id, kind, title, notebook, revision); the phone asks for the full text of only the
# items it doesn't have or whose revision changed, and for audio/video files one item at a time, on request.

_MAC_KINDS = ("doc", "quiz", "audio", "visual", "video")


def _visible_notebooks() -> Dict[str, str]:
    from services.sync import phone_library
    titles = phone_library.notebook_titles()
    hidden = set(phone_library.excluded())
    return {k: v for k, v in titles.items() if k not in hidden}


def _file_rel(path: Any) -> Any:
    from services.sync import blobs
    rel = blobs._rel(path)
    if not rel:
        return None
    try:
        return rel if blobs.safe_path(rel).exists() else None
    except blobs.BlobPathError:
        return None


def _size(rel: str) -> int:
    from services.sync import blobs
    return blobs.safe_path(rel).stat().st_size


def _doc_title(skill: str, topic: str) -> str:
    return f"{skill}: {topic}" if topic and topic.lower() != skill.lower() else (skill or "Document")


def listing() -> List[Dict[str, Any]]:
    nbs = _visible_notebooks()
    c = _conn()
    out: List[Dict[str, Any]] = []
    for r in c.execute("SELECT content_id, notebook_id, skill_name, topic, created_at, updated_at FROM content_generations"):
        if r["notebook_id"] in nbs:
            out.append({"kind": "doc", "id": r["content_id"], "notebook_id": r["notebook_id"],
                        "title": _doc_title(r["skill_name"] or "", r["topic"] or ""), "created": r["created_at"], "rev": r["updated_at"]})
    for r in c.execute("SELECT quiz_id, notebook_id, topic, created_at, updated_at FROM quiz_generations"):
        if r["notebook_id"] in nbs:
            out.append({"kind": "quiz", "id": r["quiz_id"], "notebook_id": r["notebook_id"],
                        "title": f"Quiz: {r['topic']}" if r["topic"] else "Quiz", "created": r["created_at"], "rev": r["updated_at"]})
    for r in c.execute("SELECT audio_id, notebook_id, topic, audio_file_path, created_at, updated_at FROM audio_generations WHERE status = 'completed'"):
        rel = _file_rel(r["audio_file_path"]) if r["notebook_id"] in nbs else None
        if rel:
            out.append({"kind": "audio", "id": r["audio_id"], "notebook_id": r["notebook_id"],
                        "title": f"Audio overview: {r['topic']}" if r["topic"] else "Audio overview", "created": r["created_at"],
                        "rev": r["updated_at"], "bytes": _size(rel)})
    for r in c.execute("SELECT visual_id, notebook_id, title, topic, svg_markup, created_at, updated_at FROM visual_generations"):
        if r["notebook_id"] in nbs and (r["svg_markup"] or "").strip():
            out.append({"kind": "visual", "id": r["visual_id"], "notebook_id": r["notebook_id"],
                        "title": r["title"] or (f"Visual: {r['topic']}" if r["topic"] else "Visual"), "created": r["created_at"], "rev": r["updated_at"]})
    for r in c.execute("SELECT video_id, notebook_id, topic, video_file_path, created_at, updated_at FROM video_generations WHERE status = 'completed'"):
        rel = _file_rel(r["video_file_path"]) if r["notebook_id"] in nbs else None
        if rel:
            out.append({"kind": "video", "id": r["video_id"], "notebook_id": r["notebook_id"],
                        "title": f"Video: {r['topic']}" if r["topic"] else "Video", "created": r["created_at"],
                        "rev": r["updated_at"], "bytes": _size(rel)})
    return out


def _quiz_for_phone(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Multiple-choice and true/false questions, in the phone's shape. Other kinds need typed answers: skipped."""
    out = []
    for q in questions:
        options = [str(o) for o in (q.get("options") or [])]
        if not options and str(q.get("question_type", "")).lower() in ("true_false", "true/false", "boolean"):
            options = ["True", "False"]
        ans = str(q.get("answer", "")).strip()
        if len(options) < 2:
            continue
        idx = next((i for i, o in enumerate(options) if o.strip().lower() == ans.lower()), -1)
        if idx < 0:
            continue
        out.append({"question": str(q.get("question", "")), "choices": options, "answer": idx,
                    "why": str(q.get("explanation") or "") or None})
    return out


def get_items(wanted: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    nbs = _visible_notebooks()
    c = _conn()
    out: List[Dict[str, Any]] = []
    for w in wanted[:25]:
        kind, item_id = str(w.get("kind", "")), str(w.get("id", ""))
        if kind == "doc":
            r = c.execute("SELECT * FROM content_generations WHERE content_id = ?", (item_id,)).fetchone()
            if r and r["notebook_id"] in nbs:
                out.append({"kind": kind, "id": item_id, "content": r["content"]})
        elif kind == "quiz":
            r = c.execute("SELECT * FROM quiz_generations WHERE quiz_id = ?", (item_id,)).fetchone()
            if r and r["notebook_id"] in nbs:
                try:
                    qs = _quiz_for_phone(json.loads(r["questions_json"] or "[]"))
                except ValueError:
                    qs = []
                out.append({"kind": kind, "id": item_id, "questions": qs})
        elif kind == "audio":
            r = c.execute("SELECT * FROM audio_generations WHERE audio_id = ?", (item_id,)).fetchone()
            rel = _file_rel(r["audio_file_path"]) if r and r["notebook_id"] in nbs else None
            if rel:
                out.append({"kind": kind, "id": item_id, "script": r["script"] or "", "duration": r["duration_seconds"] or 0,
                            "path": rel, "bytes": _size(rel)})
        elif kind == "visual":
            r = c.execute("SELECT * FROM visual_generations WHERE visual_id = ?", (item_id,)).fetchone()
            if r and r["notebook_id"] in nbs:
                out.append({"kind": kind, "id": item_id, "svg": r["svg_markup"] or ""})
        elif kind == "video":
            r = c.execute("SELECT * FROM video_generations WHERE video_id = ?", (item_id,)).fetchone()
            rel = _file_rel(r["video_file_path"]) if r and r["notebook_id"] in nbs else None
            if rel:
                out.append({"kind": kind, "id": item_id, "duration": r["duration_seconds"] or 0, "path": rel, "bytes": _size(rel)})
    return out


def file_chunk(kind: str, item_id: str, offset: int) -> Dict[str, Any]:
    """One piece of an audio or video file this Mac made. The path comes from the database, never from the phone."""
    from services.sync import blobs
    table, col, idcol = (("audio_generations", "audio_file_path", "audio_id") if kind == "audio"
                         else ("video_generations", "video_file_path", "video_id"))
    if kind not in ("audio", "video"):
        raise HTTPException(400, "unknown kind")
    r = _conn().execute(f"SELECT notebook_id, {col} AS p FROM {table} WHERE {idcol} = ?", (item_id,)).fetchone()
    rel = _file_rel(r["p"]) if r and r["notebook_id"] in _visible_notebooks() else None
    if not rel:
        raise HTTPException(404, "no such file")
    return blobs.read_chunk(rel, offset)
