"""One-way import of this Mac's library into the Swift LocalBook app on the same Mac.

The Swift app (mac/LocalBook) asks for the library the way a paired phone does, but for EVERY notebook, device-only
ones included, and with the full text of each source and the Canvas notes. The original is never changed: every
call here is read-only.

Who may call: a process on this Mac (loopback only) that holds the per-launch app token (the same header the app's
own window sends; utils/auth_middleware.py checks it before any of this runs). The Swift app reads the token from the
data folder the user points it at.

Shape: the phone routes' own functions (services/sync/phone_library, phone_personal, phone_recipe, phone_studio),
run with phone_library.include_everything set, plus two reads of localbook.db the phone never needed.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import sqlite3
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

from services.sync import phone_library

logger = logging.getLogger(__name__)
router = APIRouter()

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _loopback(request: Request) -> None:
    host = (request.client.host if request.client else "") or ""
    if host not in LOOPBACK:
        logger.warning("[migrate] refused %s from %s", request.url.path, host)
        raise HTTPException(403, "the library can only be moved by an app on this Mac")


async def _everything(fn: Callable[..., Any], *args: Any) -> Any:
    """Run a phone-library function in a thread with every notebook visible."""
    ctx = contextvars.copy_context()
    ctx.run(phone_library.include_everything.set, True)
    return await asyncio.get_running_loop().run_in_executor(None, lambda: ctx.run(fn, *args))


async def _body(request: Request) -> Dict[str, Any]:
    try:
        raw = await request.body()
        return json.loads(raw) if raw else {}
    except ValueError:
        raise HTTPException(400, "bad JSON")


def _main_db() -> sqlite3.Connection:
    from services.sync import runtime
    path = runtime.data_dir() / runtime.DB_FILES["main"]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


@router.post("/hello")
async def hello(request: Request):
    _loopback(request)
    from services.sync import identity
    titles = await _everything(phone_library.notebook_titles)
    hidden = set(await asyncio.to_thread(phone_library.excluded))
    return {"name": identity.device_name(), "device_id": identity.device_id(), "proto": phone_library.PROTO,
            "embedder": phone_library.embedder_key(), "notebooks": len(titles), "device_only": len(hidden & set(titles))}


@router.post("/manifest")
async def manifest(request: Request):
    _loopback(request)
    return await _everything(phone_library.manifest)


@router.post("/recipe")
async def recipe(request: Request):
    _loopback(request)
    from services.sync import phone_recipe
    return await asyncio.to_thread(phone_recipe.recipe)


@router.post("/personal")
async def personal(request: Request):
    _loopback(request)
    from services.sync import phone_personal
    b = await _body(request)
    out = await _everything(phone_personal.personal, str(b.get("notebook_id", "")))
    if out is None:
        raise HTTPException(404, "no such notebook")
    return out


@router.post("/sources")
async def sources(request: Request):
    _loopback(request)
    b = await _body(request)
    notebook_id = str(b.get("notebook_id", ""))
    out = await _everything(phone_library.sources, notebook_id)
    if out is None:
        raise HTTPException(404, "no such notebook")
    try:
        from storage.source_store import source_store
        added = {str(r.get("id")): str(r.get("created_at") or "") for r in await source_store.list(notebook_id)}
        for src in out["sources"]:
            src["created"] = added.get(src["source_id"], "")
    except Exception as exc:
        logger.warning("[migrate] source dates not added: %s", exc)
    return out


@router.post("/chunks")
async def chunks(request: Request):
    _loopback(request)
    b = await _body(request)
    out = await _everything(lambda: phone_library.chunks(str(b.get("notebook_id", "")), source_ids=b.get("source_ids"),
                                                         after=b.get("after"), limit=int(b.get("limit", 200))))
    if out is None:
        raise HTTPException(404, "no such notebook")
    return out


SOURCE_TEXT_COLUMNS = ("id", "notebook_id", "filename", "content", "url", "author", "date", "format", "type", "notes",
                       "tags", "created_at")


@router.post("/source_text")
async def source_text(request: Request):
    """The full text of one source as this Mac extracted it, with what it knows about where it came from."""
    _loopback(request)
    b = await _body(request)
    notebook_id, source_id = str(b.get("notebook_id", "")), str(b.get("source_id", ""))
    if not notebook_id or not source_id:
        raise HTTPException(400, "notebook_id and source_id")

    def read() -> Optional[Dict[str, Any]]:
        conn = _main_db()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(sources)")}
            want = [c for c in SOURCE_TEXT_COLUMNS if c in cols]
            row = conn.execute(f"SELECT {', '.join(want)} FROM sources WHERE id = ? AND notebook_id = ?",
                               (source_id, notebook_id)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    out = await asyncio.to_thread(read)
    if out is None:
        raise HTTPException(404, "no such source")
    try:
        out["tags"] = json.loads(out.get("tags") or "[]")
    except ValueError:
        out["tags"] = []
    out["text"] = out.pop("content", None) or ""
    return out


@router.post("/notes")
async def notes(request: Request):
    """The Canvas notes of one notebook (typed, scanned, dictated), newest first."""
    _loopback(request)
    b = await _body(request)
    notebook_id = str(b.get("notebook_id", ""))
    if not notebook_id:
        raise HTTPException(400, "notebook_id")

    def read() -> List[Dict[str, Any]]:
        conn = _main_db()
        try:
            try:
                rows = conn.execute("""SELECT id, title, content_markdown, source_type, note_type, tags, created_at, updated_at
                                       FROM canvas_notes WHERE notebook_id = ? ORDER BY updated_at DESC""", (notebook_id,)).fetchall()
            except sqlite3.Error:
                return []
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["tags"] = json.loads(d.get("tags") or "[]")
                except ValueError:
                    d["tags"] = []
                d["text"] = d.pop("content_markdown", None) or ""
                out.append(d)
            return out
        finally:
            conn.close()

    return {"notebook_id": notebook_id, "notes": await asyncio.to_thread(read)}


@router.post("/topics")
async def topics(request: Request):
    _loopback(request)
    b = await request.json()
    from services.sync import phone_topics
    return await _everything(phone_topics.cloud, str(b.get("notebook_id", "")))


@router.post("/studio/list")
async def studio_list(request: Request):
    _loopback(request)
    from services.sync import phone_studio
    return {"items": await _everything(phone_studio.listing)}


@router.post("/studio/get")
async def studio_get(request: Request):
    _loopback(request)
    from services.sync import phone_studio
    b = await _body(request)
    return {"items": await _everything(phone_studio.get_items, list(b.get("items") or []))}


@router.post("/studio/file")
async def studio_file(request: Request):
    _loopback(request)
    from services.sync import phone_studio
    b = await _body(request)
    return await _everything(phone_studio.file_chunk, str(b.get("kind", "")), str(b.get("id", "")), int(b.get("offset", 0)))
