"""Notes (typed, dictated, or read from a photo) made on a paired phone, added to a notebook here.

The phone sends the text with a client id it made up; sending the same id again returns the same note instead
of a second one, so a retry after a lost reply never duplicates it. The Mac indexes the note like any note it
creates itself, and the phone receives it back through the normal library sync."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from fastapi import BackgroundTasks, HTTPException

MAX_CHARS = 200_000
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


async def add_note(notebook_id: str, client_id: str, title: str, text: str, kind: str = "note") -> Dict[str, Any]:
    from services.sync import phone_library
    text = (text or "").strip()
    if not client_id or len(client_id) > 100:
        raise HTTPException(400, "a client id is required")
    if not text or len(text) > MAX_CHARS:
        raise HTTPException(400, f"note text of 1 to {MAX_CHARS} characters is required")
    if not await asyncio.to_thread(phone_library.is_visible, notebook_id):
        raise HTTPException(404, "no such notebook")
    async with _lock:
        ledger = _load()
        key = f"{notebook_id}:{client_id}"
        if key in ledger:
            return {"source_id": ledger[key], "duplicate": True}
        from api.sources import NoteCreateRequest, create_note
        made = await create_note(notebook_id, NoteCreateRequest(title=(title or "").strip()[:200] or "Phone note", content=text),
                                 BackgroundTasks())
        ledger[key] = made["source_id"]
        await asyncio.to_thread(_save, ledger)
        return {"source_id": made["source_id"], "duplicate": False}
