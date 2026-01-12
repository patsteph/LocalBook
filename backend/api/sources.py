"""Sources API endpoints"""
import asyncio
from typing import List
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from pydantic import BaseModel
from storage.source_store import source_store
from services.document_processor import document_processor
from services.rag_engine import rag_engine
from services.topic_modeling import topic_modeling_service

router = APIRouter()


class TagsRequest(BaseModel):
    tags: List[str]


class SingleTagRequest(BaseModel):
    tag: str

@router.get("/{notebook_id}")
async def list_sources(notebook_id: str):
    """List all sources for a notebook"""
    sources = await source_store.list(notebook_id)
    return sources

@router.post("/upload")
async def upload_source(
    file: UploadFile = File(...),
    notebook_id: str = Form(...),
    background_tasks: BackgroundTasks = None
):
    """Upload and process a document"""
    import traceback
    from api.timeline import extract_timeline_for_source

    # Save file temporarily
    content = await file.read()
    
    print(f"[UPLOAD] Received file: {file.filename}, size: {len(content)} bytes, notebook: {notebook_id}")

    # Process document
    try:
        result = await document_processor.process(
            content=content,
            filename=file.filename,
            notebook_id=notebook_id
        )
        
        # Auto-extract timeline in background (fire and forget)
        if background_tasks and result.get("source_id"):
            # Get the processed text content for timeline extraction
            source = await source_store.get(result["source_id"])
            if source and source.get("content"):
                background_tasks.add_task(
                    extract_timeline_for_source,
                    notebook_id,
                    result["source_id"],
                    source["content"],
                    file.filename
                )
                print(f"[UPLOAD] Queued timeline extraction for {file.filename}")
        
        return result
    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        print(f"[UPLOAD] Error processing {file.filename}: {error_msg}")
        print(f"[UPLOAD] Traceback:\n{tb}")
        raise HTTPException(status_code=500, detail=f"Failed to process file: {error_msg}")

@router.delete("/{notebook_id}/{source_id}")
async def delete_source(notebook_id: str, source_id: str):
    """Delete a source and all its indexed data.
    
    This is designed to be robust - even if some cleanup steps fail
    (e.g., for sources that were never fully indexed), the source
    will still be removed from the sources list.
    """
    # First verify the source exists
    source = await source_store.get(source_id)
    if not source or source.get("notebook_id") != notebook_id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    errors = []
    
    # Delete from LanceDB (vector embeddings) - non-blocking
    try:
        await rag_engine.delete_source(notebook_id, source_id)
        print(f"[SOURCES] Deleted source {source_id} from LanceDB")
    except Exception as e:
        # This can fail for sources that were never indexed (e.g., empty content)
        print(f"[SOURCES] Note: LanceDB cleanup skipped for {source_id}: {e}")
        errors.append(f"LanceDB: {e}")
    
    # Delete from topic model - non-blocking
    try:
        await topic_modeling_service.delete_source(source_id)
        print(f"[SOURCES] Deleted source {source_id} from topic model")
    except Exception as e:
        print(f"[SOURCES] Note: Topic model cleanup skipped for {source_id}: {e}")
        errors.append(f"Topic model: {e}")
    
    # Delete from sources.json - this is the critical step
    try:
        success = await source_store.delete(notebook_id, source_id)
        if not success:
            raise HTTPException(status_code=404, detail="Source not found in storage")
        print(f"[SOURCES] Deleted source {source_id} from sources.json")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[SOURCES] Critical error deleting from sources.json: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete source: {e}")
    
    # Return success even if some cleanup steps had non-critical errors
    return {
        "message": "Source deleted successfully",
        "cleanup_notes": errors if errors else None
    }


# =========================================================================
# Document Tagging Endpoints (v0.6.0)
# =========================================================================

@router.get("/{notebook_id}/{source_id}/tags")
async def get_source_tags(notebook_id: str, source_id: str):
    """Get tags for a specific source"""
    source = await source_store.get(source_id)
    if not source or source.get("notebook_id") != notebook_id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    tags = await source_store.get_tags(notebook_id, source_id)
    return {"tags": tags}


@router.put("/{notebook_id}/{source_id}/tags")
async def set_source_tags(notebook_id: str, source_id: str, request: TagsRequest):
    """Set tags for a source (replaces existing tags)"""
    source = await source_store.get(source_id)
    if not source or source.get("notebook_id") != notebook_id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    success = await source_store.set_tags(notebook_id, source_id, request.tags)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update tags")
    
    return {"tags": await source_store.get_tags(notebook_id, source_id)}


@router.post("/{notebook_id}/{source_id}/tags")
async def add_source_tag(notebook_id: str, source_id: str, request: SingleTagRequest):
    """Add a single tag to a source"""
    source = await source_store.get(source_id)
    if not source or source.get("notebook_id") != notebook_id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    success = await source_store.add_tag(notebook_id, source_id, request.tag)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to add tag")
    
    return {"tags": await source_store.get_tags(notebook_id, source_id)}


@router.delete("/{notebook_id}/{source_id}/tags/{tag}")
async def remove_source_tag(notebook_id: str, source_id: str, tag: str):
    """Remove a tag from a source"""
    source = await source_store.get(source_id)
    if not source or source.get("notebook_id") != notebook_id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    success = await source_store.remove_tag(notebook_id, source_id, tag)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to remove tag")
    
    return {"tags": await source_store.get_tags(notebook_id, source_id)}


@router.get("/{notebook_id}/tags/all")
async def get_all_notebook_tags(notebook_id: str):
    """Get all unique tags used in a notebook with counts"""
    tags = await source_store.get_all_tags(notebook_id)
    return {"tags": tags}


@router.get("/{notebook_id}/tags/{tag}/sources")
async def get_sources_by_tag(notebook_id: str, tag: str):
    """Get all sources with a specific tag"""
    sources = await source_store.get_sources_by_tag(notebook_id, tag)
    return sources
