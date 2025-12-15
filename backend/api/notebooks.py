"""Notebooks API endpoints"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from storage.notebook_store import notebook_store

router = APIRouter()

class NotebookCreate(BaseModel):
    title: str
    description: Optional[str] = None

class Notebook(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    color: Optional[str] = None
    created_at: str
    updated_at: str
    source_count: int = 0

class NotebookColorUpdate(BaseModel):
    color: str

@router.get("/")
async def list_notebooks():
    """List all notebooks"""
    notebooks = await notebook_store.list()
    return {"notebooks": notebooks}

@router.post("/", response_model=Notebook)
async def create_notebook(notebook: NotebookCreate):
    """Create a new notebook"""
    result = await notebook_store.create(notebook.title, notebook.description)
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
    """Delete a notebook"""
    success = await notebook_store.delete(notebook_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return {"message": "Notebook deleted successfully"}

@router.put("/{notebook_id}/color")
async def update_notebook_color(notebook_id: str, update: NotebookColorUpdate):
    """Update a notebook's color"""
    result = await notebook_store.update_color(notebook_id, update.color)
    if not result:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return result

@router.get("/colors/palette")
async def get_color_palette():
    """Get available color palette"""
    return {"colors": notebook_store.get_color_palette()}
