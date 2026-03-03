"""Notebooks API endpoints"""
import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from storage.notebook_store import notebook_store
from api.settings import _load_app_preferences
from config import settings

router = APIRouter()

class NotebookCreate(BaseModel):
    title: str
    description: Optional[str] = None
    color: Optional[str] = None

class Notebook(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    color: Optional[str] = None
    created_at: str
    updated_at: str
    source_count: int = 0
    is_primary: bool = False

class NotebookColorUpdate(BaseModel):
    color: str

class NotebookRename(BaseModel):
    title: str

class SectionCreate(BaseModel):
    name: str

class SectionUpdate(BaseModel):
    name: Optional[str] = None
    collapsed: Optional[bool] = None

class SectionReorder(BaseModel):
    section_ids: list

class NotebookMove(BaseModel):
    section_id: Optional[str] = None
    sort_order: Optional[int] = None

@router.get("/")
async def list_notebooks():
    """List all notebooks, with primary notebook first"""
    notebooks = await notebook_store.list()
    
    # Get primary notebook ID
    prefs = _load_app_preferences()
    primary_id = prefs.get("primary_notebook_id")
    
    # Mark primary and sort to top
    for nb in notebooks:
        nb["is_primary"] = nb.get("id") == primary_id
    
    # Sort: primary first, then by updated_at desc
    notebooks.sort(key=lambda x: (not x.get("is_primary", False), x.get("updated_at", "")), reverse=False)
    # Fix: primary=True should be first (not False sorts before not True)
    notebooks.sort(key=lambda x: (0 if x.get("is_primary") else 1, x.get("title", "").lower()))
    
    return {"notebooks": notebooks, "primary_notebook_id": primary_id}

@router.post("/", response_model=Notebook)
async def create_notebook(notebook: NotebookCreate):
    """Create a new notebook"""
    result = await notebook_store.create(notebook.title, notebook.description, notebook.color)
    return result

@router.get("/{notebook_id}", response_model=Notebook)
async def get_notebook(notebook_id: str):
    """Get a specific notebook"""
    notebook = await notebook_store.get(notebook_id)
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return notebook

@router.delete("/{notebook_id}")
async def delete_notebook(notebook_id: str):
    """Delete a notebook and all associated data"""
    success = await notebook_store.delete(notebook_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notebook not found")
    
    # Clean up all associated data
    cleanup_errors = []
    
    # 1. Remove collector config + data directory
    try:
        notebook_data_dir = Path(settings.data_dir) / "notebooks" / notebook_id
        if notebook_data_dir.exists():
            shutil.rmtree(notebook_data_dir)
            print(f"[CLEANUP] Deleted notebook data dir: {notebook_data_dir}")
    except Exception as e:
        cleanup_errors.append(f"data dir: {e}")
    
    # 2. Clear collector from in-memory registry
    try:
        from agents.collector import clear_collector_cache
        clear_collector_cache(notebook_id)
        print(f"[CLEANUP] Cleared collector cache for {notebook_id}")
    except Exception as e:
        cleanup_errors.append(f"collector cache: {e}")
    
    # 3. Delete archival memories for this notebook
    try:
        from storage.memory_store import memory_store
        if memory_store:
            memory_store.delete_notebook_memories(notebook_id)
            print(f"[CLEANUP] Deleted archival memories for {notebook_id}")
    except Exception as e:
        cleanup_errors.append(f"archival memories: {e}")
    
    # 4. Delete findings for this notebook
    try:
        from storage.findings_store import findings_store
        if findings_store:
            await findings_store.delete_notebook_findings(notebook_id)
            print(f"[CLEANUP] Deleted findings for {notebook_id}")
    except Exception as e:
        cleanup_errors.append(f"findings: {e}")
    
    # 5. Delete sources for this notebook
    try:
        from storage.source_store import source_store
        if source_store:
            await source_store.delete_all(notebook_id)
            print(f"[CLEANUP] Deleted sources for {notebook_id}")
    except Exception as e:
        cleanup_errors.append(f"sources: {e}")
    
    if cleanup_errors:
        print(f"[CLEANUP] Non-fatal cleanup errors for {notebook_id}: {cleanup_errors}")
    
    return {"message": "Notebook deleted successfully"}

@router.put("/{notebook_id}/color")
async def update_notebook_color(notebook_id: str, update: NotebookColorUpdate):
    """Update a notebook's color"""
    result = await notebook_store.update_color(notebook_id, update.color)
    if not result:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return result

@router.put("/{notebook_id}/rename")
async def rename_notebook(notebook_id: str, body: NotebookRename):
    """Rename a notebook"""
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    result = await notebook_store.update(notebook_id, {"title": title})
    if not result:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return result

@router.put("/{notebook_id}/move")
async def move_notebook(notebook_id: str, body: NotebookMove):
    """Move a notebook to a section and/or set its sort order"""
    updates = {}
    if body.section_id is not None:
        updates["section_id"] = body.section_id if body.section_id else None
    if body.sort_order is not None:
        updates["sort_order"] = body.sort_order
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    result = await notebook_store.update(notebook_id, updates)
    if not result:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return result

@router.get("/colors/palette")
async def get_color_palette():
    """Get available color palette"""
    return {"colors": notebook_store.get_color_palette()}

# --- Notebook Sections ---

@router.get("/sections/list")
async def list_sections():
    """List all notebook sections"""
    sections = await notebook_store.list_sections()
    return {"sections": sections}

@router.post("/sections/")
async def create_section(body: SectionCreate):
    """Create a new notebook section"""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Section name cannot be empty")
    section = await notebook_store.create_section(name)
    return section

@router.put("/sections/{section_id}")
async def update_section(section_id: str, body: SectionUpdate):
    """Update a section's name or collapsed state"""
    updates = {}
    if body.name is not None:
        updates["name"] = body.name.strip()
    if body.collapsed is not None:
        updates["collapsed"] = body.collapsed
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    result = await notebook_store.update_section(section_id, updates)
    if not result:
        raise HTTPException(status_code=404, detail="Section not found")
    return result

@router.delete("/sections/{section_id}")
async def delete_section(section_id: str):
    """Delete a section (notebooks in it become unsectioned)"""
    success = await notebook_store.delete_section(section_id)
    if not success:
        raise HTTPException(status_code=404, detail="Section not found")
    return {"message": "Section deleted"}

@router.put("/sections/reorder")
async def reorder_sections(body: SectionReorder):
    """Reorder sections by providing ordered list of section IDs"""
    await notebook_store.reorder_sections(body.section_ids)
    return {"message": "Sections reordered"}
