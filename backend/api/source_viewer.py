"""Source viewer API endpoints"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from storage.source_store import source_store
from storage.highlights_store import highlights_store

router = APIRouter()


class NoteSave(BaseModel):
    """Request model for saving notes"""
    notebook_id: str
    source_id: str
    notes: str


class HighlightCreate(BaseModel):
    """Request model for creating a highlight - matches frontend HighlightCreate"""
    notebook_id: str
    source_id: str
    start_offset: int
    end_offset: int
    highlighted_text: str
    color: Optional[str] = "yellow"
    annotation: Optional[str] = ""


class HighlightUpdate(BaseModel):
    """Request model for updating a highlight annotation"""
    annotation: str


class Highlight(BaseModel):
    """Highlight model - matches frontend Highlight interface"""
    highlight_id: str
    notebook_id: str
    source_id: str
    start_offset: int
    end_offset: int
    highlighted_text: str
    color: str
    annotation: str
    created_at: str
    updated_at: str


# ============ Content Endpoints ============

@router.get("/content/{notebook_id}/{source_id}")
async def get_source_content(notebook_id: str, source_id: str):
    """Get source content for viewing"""
    content = await source_store.get_content(notebook_id, source_id)
    if not content:
        raise HTTPException(status_code=404, detail="Source content not found")
    return content


# ============ Notes Endpoints ============

@router.get("/notes/{notebook_id}/{source_id}")
async def get_notes(notebook_id: str, source_id: str):
    """Get notes for a source"""
    notes = await source_store.get_notes(notebook_id, source_id)
    return {"notes": notes}


@router.post("/notes")
async def save_notes(note: NoteSave):
    """Save notes for a source"""
    success = await source_store.save_notes(
        notebook_id=note.notebook_id,
        source_id=note.source_id,
        notes=note.notes
    )
    if not success:
        raise HTTPException(status_code=404, detail="Source not found")
    return {"message": "Notes saved successfully"}


# ============ Highlights Endpoints ============

@router.post("/highlights", response_model=Highlight)
async def create_highlight(highlight: HighlightCreate):
    """Create a new highlight"""
    result = await highlights_store.create(
        notebook_id=highlight.notebook_id,
        source_id=highlight.source_id,
        start_offset=highlight.start_offset,
        end_offset=highlight.end_offset,
        highlighted_text=highlight.highlighted_text,
        color=highlight.color or "yellow",
        annotation=highlight.annotation or ""
    )
    return result


@router.get("/highlights/{notebook_id}/{source_id}")
async def list_highlights(notebook_id: str, source_id: str):
    """List all highlights for a source"""
    highlights = await highlights_store.list(notebook_id, source_id)
    return highlights


@router.patch("/highlights/{highlight_id}", response_model=Highlight)
async def update_highlight(highlight_id: str, update: HighlightUpdate):
    """Update a highlight's annotation"""
    result = await highlights_store.update(highlight_id, {"annotation": update.annotation})
    if not result:
        raise HTTPException(status_code=404, detail="Highlight not found")
    return result


@router.delete("/highlights/{highlight_id}")
async def delete_highlight(highlight_id: str):
    """Delete a highlight"""
    success = await highlights_store.delete(highlight_id)
    if not success:
        raise HTTPException(status_code=404, detail="Highlight not found")
    return {"message": "Highlight deleted successfully"}
