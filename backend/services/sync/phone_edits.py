"""What a paired peer may change in the library: sources it can see (delete, move) and the notebook list (new,
rename, colour, section, delete). Each call runs the same handler the desktop window uses (api/sources.py,
api/notebooks.py), so a peer can do nothing the owner could not do here, and it does it the same way.

The Swift Mac app (Phase 2 of docs/swift-migration/) needs these for its sidebar; the phone may use them too.
Visibility is the phone rule: a notebook kept on this Mac only (sync/scope.py) is not there to a peer, so it can
neither be edited nor be the target of a move.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_ID = re.compile(r"[A-Za-z0-9_\-]{1,100}")


def _sources_api():
    from api import sources
    return sources


def _notebooks_api():
    from api import notebooks
    return notebooks


async def _visible(notebook_id: str) -> None:
    from services.sync import phone_library
    if not _ID.fullmatch(notebook_id or "") or not await asyncio.to_thread(phone_library.is_visible, notebook_id):
        raise HTTPException(404, "no such notebook")


def _clean(text: str, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


# ── sources ──────────────────────────────────────────────────────────────────


async def delete_source(notebook_id: str, source_id: str) -> Dict[str, Any]:
    await _visible(notebook_id)
    if not _ID.fullmatch(source_id or ""):
        raise HTTPException(400, "not a source id")
    out = await _sources_api().delete_source(notebook_id, source_id)
    logger.warning("[phone-edits] %s/%s deleted by a peer", notebook_id[:8], source_id)
    return {"source_id": source_id, "deleted": True, "notes": out.get("cleanup_notes")}


async def move_source(notebook_id: str, source_id: str, target_notebook_id: str) -> Dict[str, Any]:
    await _visible(notebook_id)
    await _visible(target_notebook_id)
    if not _ID.fullmatch(source_id or ""):
        raise HTTPException(400, "not a source id")
    api = _sources_api()
    out = await api.move_source(notebook_id, source_id, api.MoveSourceRequest(target_notebook_id=target_notebook_id))
    logger.warning("[phone-edits] %s/%s moved to %s by a peer", notebook_id[:8], source_id, target_notebook_id[:8])
    return {"source_id": source_id, "target_notebook_id": target_notebook_id, "reindexed": bool(out.get("reindexed")),
            "problem": out.get("reindex_error")}


# ── notebooks ────────────────────────────────────────────────────────────────


async def create_notebook(title: str, color: Optional[str] = None) -> Dict[str, Any]:
    title = _clean(title, 200)
    if not title:
        raise HTTPException(400, "a title is required")
    api = _notebooks_api()
    nb = await api.create_notebook(api.NotebookCreate(title=title, color=color or None))
    nb = nb if isinstance(nb, dict) else nb.model_dump()
    logger.warning("[phone-edits] notebook %r created by a peer", title)
    return {"id": nb.get("id"), "title": nb.get("title"), "color": nb.get("color")}


async def rename_notebook(notebook_id: str, title: str) -> Dict[str, Any]:
    await _visible(notebook_id)
    title = _clean(title, 200)
    if not title:
        raise HTTPException(400, "a title is required")
    api = _notebooks_api()
    await api.rename_notebook(notebook_id, api.NotebookRename(title=title))
    return {"id": notebook_id, "title": title}


async def color_notebook(notebook_id: str, color: str) -> Dict[str, Any]:
    await _visible(notebook_id)
    color = (color or "").strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise HTTPException(400, "a colour like #3B82F6 is required")
    api = _notebooks_api()
    await api.update_notebook_color(notebook_id, api.NotebookColorUpdate(color=color))
    return {"id": notebook_id, "color": color}


async def delete_notebook(notebook_id: str) -> Dict[str, Any]:
    await _visible(notebook_id)
    await _notebooks_api().delete_notebook(notebook_id)
    logger.warning("[phone-edits] notebook %s deleted by a peer", notebook_id[:8])
    return {"id": notebook_id, "deleted": True}


async def move_notebook(notebook_id: str, section_id: Optional[str]) -> Dict[str, Any]:
    """Put a notebook in a section, or in none (section_id null)."""
    await _visible(notebook_id)
    if section_id is not None and not _ID.fullmatch(section_id):
        raise HTTPException(400, "not a section id")
    api = _notebooks_api()
    await api.move_notebook(notebook_id, api.NotebookMove(section_id=section_id))
    return {"id": notebook_id, "section_id": section_id}


# ── sections ─────────────────────────────────────────────────────────────────


async def create_section(name: str) -> Dict[str, Any]:
    name = _clean(name, 100)
    if not name:
        raise HTTPException(400, "a name is required")
    api = _notebooks_api()
    s = await api.create_section(api.SectionCreate(name=name))
    return {"id": s.get("id"), "name": s.get("name")}


async def rename_section(section_id: str, name: str) -> Dict[str, Any]:
    name = _clean(name, 100)
    if not name or not _ID.fullmatch(section_id or ""):
        raise HTTPException(400, "a section id and a name are required")
    api = _notebooks_api()
    await api.update_section(section_id, api.SectionUpdate(name=name))
    return {"id": section_id, "name": name}


async def delete_section(section_id: str) -> Dict[str, Any]:
    if not _ID.fullmatch(section_id or ""):
        raise HTTPException(400, "not a section id")
    await _notebooks_api().delete_section(section_id)
    return {"id": section_id, "deleted": True}


def sections() -> list:
    """{id, name} for every section, in the desktop's order, so a peer can offer "Move to section"."""
    from services.sync import runtime
    import sqlite3
    path = runtime.data_dir() / runtime.DB_FILES["main"]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        return [{"id": i, "name": n} for i, n in conn.execute("SELECT id, name FROM notebook_sections ORDER BY sort_order, created_at")]
    except sqlite3.Error:
        return []
    finally:
        conn.close()
