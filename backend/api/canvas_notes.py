"""Canvas Notes API — persistent rich editor notes (Sprint 0)."""
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import logging

from storage.note_store import note_store
from storage.source_store import source_store
from services.voice_engine import voice_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/canvas-notes", tags=["canvas-notes"])


# =============================================================================
# Request / Response models
# =============================================================================

class NoteCreate(BaseModel):
    note_id: Optional[str] = Field(
        default=None,
        description="Optional client-supplied ID (canvas item ID). If omitted, a UUID is generated."
    )
    notebook_id: Optional[str] = Field(
        default=None,
        description="Associated notebook. Strongly recommended — notes without a notebook are orphans."
    )
    title: str = Field(default='')
    content_markdown: str = Field(default='')
    content_blocknote_json: str = Field(default='{}')
    source_type: str = Field(default='typed')
    note_type: str = Field(default='note')
    tags: Optional[List[str]] = Field(default=None)
    voice_weight: float = Field(default=1.0)


class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content_markdown: Optional[str] = None
    content_blocknote_json: Optional[str] = None
    notebook_id: Optional[str] = None
    source_type: Optional[str] = None
    note_type: Optional[str] = None
    tags: Optional[List[str]] = None
    voice_weight: Optional[float] = None
    saved_as_source_id: Optional[str] = None
    wikilinks_out: Optional[List[str]] = None


# =============================================================================
# Endpoints
# =============================================================================

@router.post("", status_code=201)
async def create_note(payload: NoteCreate):
    """Create a new canvas note (called by RichNoteEditor auto-save on first write)."""
    note = await note_store.create(
        note_id=payload.note_id,
        notebook_id=payload.notebook_id,
        title=payload.title,
        content_markdown=payload.content_markdown,
        content_blocknote_json=payload.content_blocknote_json,
        source_type=payload.source_type,
        note_type=payload.note_type,
        tags=payload.tags,
        voice_weight=payload.voice_weight,
    )
    if not note:
        raise HTTPException(status_code=500, detail="Failed to create note")
        
    if note.get('content_markdown'):
        # Pass voice observation
        voice_engine.add_observation(
            text_sample=note['content_markdown'],
            source_type=note.get('source_type', 'typed'),
            voice_weight=note.get('voice_weight', 1.0),
            notebook_id=note.get('notebook_id'),
            source_note_id=note['id']
        )
        
    return note


@router.get("/{note_id}")
async def get_note(note_id: str):
    """Fetch a single canvas note by ID."""
    note = await note_store.get(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@router.get("/{note_id}/backlinks")
async def get_backlinks(note_id: str):
    """Find all notes that link to this note_id via wikilinks_out."""
    all_notes = await note_store.list_all()
    backlinks = []
    
    target_note = await note_store.get(note_id)
    target_title = target_note.get('title', '') if target_note else ''
    
    for note in all_notes:
        if note['id'] == note_id:
            continue
        links = note.get('wikilinks_out', [])
        if note_id in links or (target_title and target_title in links):
            backlinks.append({
                "id": note['id'],
                "title": note.get('title', 'Untitled'),
                "type": "note"
            })
            
    return backlinks


@router.get("")
async def list_notes(notebook_id: Optional[str] = None, q: Optional[str] = None):
    """List canvas notes. Pass ?notebook_id=<id> to filter by notebook.
    Pass ?q=<query> to search both notes and sources for autocomplete.
    """
    if q is not None:
        q_lower = q.lower()
        results = []
        
        # 1. Search notes
        notes = await note_store.list_for_notebook(notebook_id) if notebook_id else await note_store.list_all()
        for note in notes:
            title = note.get('title', '')
            if q_lower in title.lower():
                results.append({
                    "id": note['id'],
                    "title": title,
                    "type": "note"
                })
                
        # 2. Search sources
        if notebook_id:
            sources = await source_store.list(notebook_id)
            for source in sources:
                filename = source.get('filename', '')
                title = source.get('title', filename)
                if q_lower in title.lower():
                    results.append({
                        "id": source['id'],
                        "title": title,
                        "type": "source"
                    })
        return results

    if notebook_id:
        return await note_store.list_for_notebook(notebook_id)
    return await note_store.list_all()


@router.patch("/{note_id}")
async def update_note(note_id: str, payload: NoteUpdate):
    """Partial update — called by the 500ms auto-save debounce in RichNoteEditor."""
    note = await note_store.get(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    updated = await note_store.update(
        note_id,
        title=payload.title,
        content_markdown=payload.content_markdown,
        content_blocknote_json=payload.content_blocknote_json,
        notebook_id=payload.notebook_id,
        source_type=payload.source_type,
        note_type=payload.note_type,
        tags=payload.tags,
        voice_weight=payload.voice_weight,
        saved_as_source_id=payload.saved_as_source_id,
        wikilinks_out=payload.wikilinks_out,
    )
    
    if updated and updated.get('content_markdown'):
        voice_engine.add_observation(
            text_sample=updated['content_markdown'],
            source_type=updated.get('source_type', 'typed'),
            voice_weight=updated.get('voice_weight', 1.0),
            notebook_id=updated.get('notebook_id'),
            source_note_id=updated['id']
        )
        
    return updated


@router.delete("/{note_id}", status_code=204)
async def delete_note(note_id: str):
    """Delete a canvas note (e.g., when user closes / discards it)."""
    deleted = await note_store.delete(note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")


@router.delete("", status_code=204)
async def delete_notebook_notes(notebook_id: str):
    """Delete all canvas notes for a notebook (e.g., on notebook deletion)."""
    await note_store.delete_all_for_notebook(notebook_id)
