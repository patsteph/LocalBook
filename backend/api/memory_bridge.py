"""The memory bridge routes (LB-4): a companion reads and writes LocalBook's memory.

`/memory/prefetch`, `/memory/sync-turn`, `/memory/session-end` — each requires a
companion key with scope `memory` (401 unknown key, 403 without the scope), so
they are exempt from the app token by exact path in `utils/auth_middleware.py`.
The rest of `/memory` is LocalBook's own and stays app-token only.

`DELETE /companions/{companion_id}/memory` is the other half of "one shared
memory": the user removes everything a companion wrote, from Settings. That one
takes the app token — it is the user's action, not the companion's.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from api.openai_compat import _require_companion_key
from storage.companion_memory import CompanionMemoryError

router = APIRouter()

MAX_TURN_CHARS = 20_000


class PrefetchRequest(BaseModel):
    query: str = ""
    session_id: str
    k: int = Field(8, ge=1, le=50)
    char_budget: int = Field(2000, ge=100, le=20_000)


class SyncTurnRequest(BaseModel):
    session_id: str
    user: str = ""
    assistant: str = ""
    ts: str | float
    source: Optional[str] = None


class SessionEndRequest(BaseModel):
    session_id: str


class AddRequest(BaseModel):
    content: str
    category: str = ""
    source: Optional[str] = None


def _bad(exc: Exception):
    raise HTTPException(400, str(exc))


@router.post("/memory/prefetch")
async def prefetch(req: PrefetchRequest, authorization: Optional[str] = Header(None)):
    who = _require_companion_key(authorization, "memory")
    from services import memory_bridge
    try:
        return await memory_bridge.prefetch(who.companion_id, req.query, req.session_id,
                                            k=req.k, char_budget=req.char_budget)
    except CompanionMemoryError as exc:
        _bad(exc)


@router.post("/memory/sync-turn")
async def sync_turn(req: SyncTurnRequest, authorization: Optional[str] = Header(None)):
    who = _require_companion_key(authorization, "memory")
    if len(req.user) > MAX_TURN_CHARS or len(req.assistant) > MAX_TURN_CHARS:
        raise HTTPException(413, f"a turn is limited to {MAX_TURN_CHARS} characters")
    if not (req.user.strip() or req.assistant.strip()):
        raise HTTPException(400, "nothing to record: user and assistant are both empty")
    from services import memory_bridge
    try:
        return await asyncio.to_thread(memory_bridge.sync_turn, who.companion_id, req.session_id,
                                       req.user, req.assistant, req.ts, req.source or "")
    except (CompanionMemoryError, ValueError) as exc:
        _bad(exc)


@router.post("/memory/session-end")
async def session_end(req: SessionEndRequest, authorization: Optional[str] = Header(None)):
    who = _require_companion_key(authorization, "memory")
    from services import memory_bridge
    try:
        return await asyncio.to_thread(memory_bridge.session_end, who.companion_id, req.session_id)
    except CompanionMemoryError as exc:
        _bad(exc)


@router.post("/memory/add")
async def add(req: AddRequest, authorization: Optional[str] = Header(None)):
    """One durable fact from a companion (Jocasta contract gap 1). Tagged to the
    calling key's companion — `source` in the body is informational only."""
    who = _require_companion_key(authorization, "memory")
    from services import memory_bridge
    try:
        return await asyncio.to_thread(memory_bridge.add_memory, who.companion_id,
                                       req.content, req.category)
    except (CompanionMemoryError, ValueError) as exc:
        _bad(exc)


@router.delete("/companions/{companion_id}/memory")
async def forget_companion(companion_id: str):
    """Remove everything this companion wrote, from every memory tier."""
    from storage import companion_memory
    try:
        return {"purged": await asyncio.to_thread(companion_memory.purge, companion_id)}
    except CompanionMemoryError as exc:
        _bad(exc)
