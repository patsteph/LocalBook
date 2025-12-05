"""Re-indexing API endpoints for fixing sources that weren't properly ingested"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from storage.source_store import source_store
from storage.notebook_store import notebook_store
from services.rag_engine import rag_service

router = APIRouter()


class ReindexResponse(BaseModel):
    message: str
    processed: int
    failed: int
    details: list


@router.post("/notebook/{notebook_id}")
async def reindex_notebook(notebook_id: str, force: bool = False):
    """Re-index all sources in a notebook that have content but weren't properly ingested.
    
    Args:
        notebook_id: The notebook to reindex
        force: If True, reindex all sources even if they already have chunks
    """
    
    notebook = await notebook_store.get(notebook_id)
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook not found")
    
    sources = await source_store.list(notebook_id)
    
    processed = 0
    failed = 0
    details = []
    
    for source in sources:
        source_id = source.get("id")
        filename = source.get("filename", "Unknown")
        
        # Check if source has content
        content_data = await source_store.get_content(notebook_id, source_id)
        
        if not content_data or not content_data.get("content"):
            details.append({
                "source_id": source_id,
                "filename": filename,
                "status": "skipped",
                "reason": "No content available"
            })
            continue
        
        text = content_data["content"]
        
        # Check if already has chunks (skip unless force=True)
        if not force and source.get("chunks", 0) > 0:
            details.append({
                "source_id": source_id,
                "filename": filename,
                "status": "skipped",
                "reason": f"Already has {source.get('chunks')} chunks (use force=true to reindex)"
            })
            continue
        
        # Re-ingest into RAG system
        try:
            source_type = source.get("type", source.get("format", "document"))
            result = await rag_service.ingest_document(
                notebook_id=notebook_id,
                source_id=source_id,
                text=text,
                filename=filename,
                source_type=source_type
            )
            
            # Update source with chunk count
            await source_store.update(notebook_id, source_id, {
                "chunks": result.get("chunks", 0),
                "characters": result.get("characters", len(text)),
                "status": "completed"
            })
            
            processed += 1
            details.append({
                "source_id": source_id,
                "filename": filename,
                "status": "success",
                "chunks": result.get("chunks", 0)
            })
            
        except Exception as e:
            failed += 1
            details.append({
                "source_id": source_id,
                "filename": filename,
                "status": "failed",
                "error": str(e)
            })
    
    return ReindexResponse(
        message=f"Re-indexed {processed} sources, {failed} failed",
        processed=processed,
        failed=failed,
        details=details
    )


@router.get("/status/{notebook_id}")
async def get_index_status(notebook_id: str):
    """Get indexing status for all sources in a notebook"""
    
    notebook = await notebook_store.get(notebook_id)
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook not found")
    
    sources = await source_store.list(notebook_id)
    
    indexed = 0
    not_indexed = 0
    no_content = 0
    
    source_status = []
    
    for source in sources:
        source_id = source.get("id")
        chunks = source.get("chunks", 0)
        has_content = bool(source.get("content"))
        
        if chunks > 0:
            indexed += 1
            status = "indexed"
        elif has_content:
            not_indexed += 1
            status = "not_indexed"
        else:
            no_content += 1
            status = "no_content"
        
        source_status.append({
            "source_id": source_id,
            "filename": source.get("filename", "Unknown"),
            "chunks": chunks,
            "has_content": has_content,
            "status": status
        })
    
    return {
        "notebook_id": notebook_id,
        "total_sources": len(sources),
        "indexed": indexed,
        "not_indexed": not_indexed,
        "no_content": no_content,
        "sources": source_status
    }
