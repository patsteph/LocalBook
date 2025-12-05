"""Sources API endpoints"""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from storage.source_store import source_store
from services.document_processor import document_processor

router = APIRouter()

@router.get("/{notebook_id}")
async def list_sources(notebook_id: str):
    """List all sources for a notebook"""
    sources = await source_store.list(notebook_id)
    return sources

@router.post("/upload")
async def upload_source(
    file: UploadFile = File(...),
    notebook_id: str = Form(...)
):
    """Upload and process a document"""

    # Save file temporarily
    content = await file.read()

    # Process document
    result = await document_processor.process(
        content=content,
        filename=file.filename,
        notebook_id=notebook_id
    )

    return result

@router.delete("/{notebook_id}/{source_id}")
async def delete_source(notebook_id: str, source_id: str):
    """Delete a source"""
    success = await source_store.delete(notebook_id, source_id)
    if not success:
        raise HTTPException(status_code=404, detail="Source not found")
    return {"message": "Source deleted successfully"}
