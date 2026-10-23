"""Notes (typed, dictated, or read from a photo) made on a paired phone, added to a notebook here.

The phone sends the text with a client id it made up; sending the same id again returns the same note instead
of a second one, so a retry after a lost reply never duplicates it. The Mac indexes the note like any note it
creates itself, and the phone receives it back through the normal library sync."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Dict

from fastapi import BackgroundTasks, HTTPException

logger = logging.getLogger(__name__)

MAX_CHARS = 200_000
MAX_PHOTO_BYTES = 6_000_000
_lock = asyncio.Lock()


def _ledger_path():
    from services.sync import runtime
    return runtime.data_dir() / "phone_notes.json"


def _load() -> Dict[str, str]:
    try:
        return json.loads(_ledger_path().read_text())
    except (OSError, ValueError):
        return {}


def _save(ledger: Dict[str, str]) -> None:
    path = _ledger_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger))
    tmp.replace(path)


_inflight: Dict[str, tuple] = {}      # notes being created right now, by ledger key
_after: set = set()                                 # follow-up work (timeline) kept alive until it finishes


async def _attach_photo(notebook_id: str, key: str, title: str, jpeg: bytes) -> None:
    """Add the photo itself as a picture source, next to the note made from its text. Once per photo; never fatal:
    the note is already safe by the time this runs."""
    async with _lock:
        if f"{key}#photo" in _load():
            return
    try:
        from services.document_processor import document_processor
        from services.post_ingest import finalize_source
        from storage.source_store import source_store
        name = (title.strip()[:80] or "Phone photo") + ".jpg"
        result = await document_processor.process(content=jpeg, filename=name, notebook_id=notebook_id)
        sid = result.get("source_id")
        if not sid:
            return
        source = await source_store.get(sid)
        await finalize_source(notebook_id, sid, name, (source or {}).get("content") or "",
                              raw_bytes=jpeg, origin="phone", notify=False)
        async with _lock:
            ledger = _load()
            ledger[f"{key}#photo"] = sid
            await asyncio.to_thread(_save, ledger)
        logger.warning("[phone-notes] %s: photo added as source %s", key, sid)
    except Exception as exc:
        logger.warning("[phone-notes] %s: photo not added: %s", key, exc)


async def _create(notebook_id: str, client_id: str, key: str, title: str, text: str,
                  started: "asyncio.Future[str]", jpeg: bytes = b"") -> str:
    """Create and index the note and return its source id; `started` gets the id as soon as the source exists.

    Runs as its own task: indexing a note (embedding, generated questions, tagging) can take longer than the
    phone is willing to wait, and a request that is dropped half way must not leave a half-made source behind.
    The phone is answered once the source exists and is in the ledger, without waiting for the indexing."""
    from api.sources import NoteCreateRequest, make_note
    background = BackgroundTasks()

    async def _recorded(source_id: str) -> None:
        logger.warning("[phone-notes] %s: source %s created, answering the phone; indexing continues", key, source_id)
        async with _lock:
            ledger = _load()
            ledger[key] = source_id
            await asyncio.to_thread(_save, ledger)
        if not started.done():
            started.set_result(source_id)

    try:
        made = await make_note(notebook_id, NoteCreateRequest(title=title.strip()[:200] or "Phone note", content=text),
                               background, on_created=_recorded, enable_hyde=False)   # HyDE waits for an idle Mac; a note is searchable without it
    except BaseException as exc:
        if not started.done():
            started.set_exception(exc)
        else:                                   # indexing failed after we answered: the Mac removed the source again
            async with _lock:
                ledger = _load()
                ledger.pop(key, None)
                await asyncio.to_thread(_save, ledger)
            logger.warning("[phone-notes] indexing %s failed after the phone was answered: %s", key, exc)
        raise

    async def _timeline() -> None:
        try:
            await background()          # the note's timeline extraction: nothing runs it unless we do
        except Exception as exc:        # non-fatal, same as for a note made on this Mac
            logger.warning("[phone-notes] timeline extraction failed: %s", exc)

    logger.warning("[phone-notes] %s: indexed", key)
    if jpeg:
        await _attach_photo(notebook_id, key, title, jpeg)
    task = asyncio.create_task(_timeline())
    _after.add(task)
    task.add_done_callback(_after.discard)
    return made["source_id"]


async def add_note(notebook_id: str, client_id: str, title: str, text: str, kind: str = "note",
                   photo_b64: str = "") -> Dict[str, Any]:
    from services.sync import phone_library
    text = (text or "").strip()
    if not client_id or len(client_id) > 100:
        raise HTTPException(400, "a client id is required")
    if not text or len(text) > MAX_CHARS:
        raise HTTPException(400, f"note text of 1 to {MAX_CHARS} characters is required")
    if not await asyncio.to_thread(phone_library.is_visible, notebook_id):
        raise HTTPException(404, "no such notebook")
    jpeg = b""
    if photo_b64:
        try:
            jpeg = base64.b64decode(photo_b64, validate=True)
        except ValueError:
            raise HTTPException(400, "the photo is not valid base64")
        if len(jpeg) > MAX_PHOTO_BYTES or not jpeg.startswith(b"\xff\xd8"):
            raise HTTPException(400, "the photo must be a JPEG of at most 6 MB")
    key = f"{notebook_id}:{client_id}"
    logger.warning("[phone-notes] %s: note received (%d characters)", key, len(text))
    async with _lock:
        ledger = _load()
        if key in ledger:
            return {"source_id": ledger[key], "duplicate": True}
        task = _inflight.get(key)
        if task is None:        # a retry that arrives while the first try is still indexing waits for that one
            started = asyncio.get_running_loop().create_future()
            task = asyncio.create_task(_create(notebook_id, client_id, key, title or "", text, started, jpeg))
            task.add_done_callback(lambda t, k=key: (_inflight.pop(k, None), t.cancelled() or t.exception()))
            _inflight[key] = (task, started)
        else:
            task, started = task
    # the answer is the source id, as soon as the source exists; indexing carries on in the task
    source_id = await asyncio.shield(started)
    return {"source_id": source_id, "duplicate": False}
