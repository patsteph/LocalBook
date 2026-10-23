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
