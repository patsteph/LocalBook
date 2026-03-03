"""Sources API endpoints"""
from typing import List
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from pydantic import BaseModel
from storage.source_store import source_store
from services.document_processor import document_processor
from services.rag_engine import rag_engine
from services.topic_modeling import topic_modeling_service
from services.event_logger import log_document_captured

router = APIRouter()


class NoteCreateRequest(BaseModel):
    title: str
    content: str


class NoteUpdateRequest(BaseModel):
    title: str | None = None
    content: str


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
        
        # Fetch source record for background tasks and auto-tagging
        source = None
        if result.get("source_id"):
            source = await source_store.get(result["source_id"])

        # Auto-extract timeline in background (fire and forget)
        if background_tasks and source:
            if source and source.get("content"):
                background_tasks.add_task(
                    extract_timeline_for_source,
                    notebook_id,
                    result["source_id"],
                    source["content"],
                    file.filename
                )
                print(f"[UPLOAD] Queued timeline extraction for {file.filename}")
            
            # v1.0.5: Background image processing for PDFs/PPTs
            # Text is indexed immediately, images processed in parallel in background
            file_ext = file.filename.lower().split('.')[-1] if '.' in file.filename else ''
            if file_ext in ['pdf', 'pptx']:
                background_tasks.add_task(
                    document_processor.process_images_background,
                    content,
                    notebook_id,
                    result["source_id"],
                    file.filename
                )
                print(f"[UPLOAD] Queued background image processing for {file.filename}")
        
        # Auto-tag the uploaded document (non-fatal)
        try:
            from services.auto_tagger import auto_tagger
            tag_text = (source.get("content", "") if source else "")[:3000]
            await auto_tagger.tag_source_in_notebook(
                notebook_id, result["source_id"], file.filename, tag_text
            )
        except Exception as tag_err:
            print(f"[UPLOAD] Auto-tagging failed (non-fatal): {tag_err}")

        # Log document capture event
        try:
            log_document_captured(notebook_id, file.filename, file.filename, "upload")
        except Exception:
            pass
        
        return result
    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        print(f"[UPLOAD] Error processing {file.filename}: {error_msg}")
        print(f"[UPLOAD] Traceback:\n{tb}")
        raise HTTPException(status_code=500, detail=f"Failed to process file: {error_msg}")

@router.post("/{notebook_id}/note")
async def create_note(notebook_id: str, request: NoteCreateRequest, background_tasks: BackgroundTasks):
    """Create a user note as a RAG-searchable source.
    
    Notes are first-class sources with type='note' and format='markdown'.
    They are indexed into LanceDB just like uploaded documents.
    """
    from api.timeline import extract_timeline_for_source

    title = request.title.strip() or "Untitled Note"
    text = request.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Note content cannot be empty")

    # 1. Create source record (processing status)
    source = await source_store.create(
        notebook_id=notebook_id,
        filename=title,
        metadata={
            "type": "note",
            "format": "markdown",
            "size": len(text.encode("utf-8")),
            "chunks": 0,
            "characters": 0,
            "status": "processing",
        }
    )

    # 2. Ingest into RAG
    try:
        result = await rag_engine.ingest_document(
            notebook_id=notebook_id,
            source_id=source["id"],
            text=text,
            filename=title,
            source_type="note"
        )

        # 3. Update source with results + full content
        await source_store.update(notebook_id, source["id"], {
            "chunks": result.get("chunks", 0),
            "characters": result.get("characters", len(text)),
            "status": "completed",
            "content": text,
        })

        # Timeline extraction in background
        if background_tasks:
            background_tasks.add_task(
                extract_timeline_for_source,
                notebook_id, source["id"], text, title
            )

        # Auto-tag the note (non-fatal, same as browser/web/collector paths)
        try:
            from services.auto_tagger import auto_tagger
            await auto_tagger.tag_source_in_notebook(
                notebook_id, source["id"], title, text[:3000]
            )
        except Exception as tag_err:
            print(f"[NOTE] Auto-tagging failed (non-fatal): {tag_err}")

        try:
            log_document_captured(notebook_id, title, title, "note")
        except Exception:
            pass

        return {
            "source_id": source["id"],
            "filename": title,
            "format": "markdown",
            "type": "note",
            "chunks": result.get("chunks", 0),
            "characters": result.get("characters", len(text)),
            "status": "completed",
        }
    except Exception as e:
        await source_store.delete(notebook_id, source["id"])
        raise HTTPException(status_code=500, detail=f"Failed to create note: {e}")


@router.put("/{notebook_id}/{source_id}/note")
async def update_note(notebook_id: str, source_id: str, request: NoteUpdateRequest):
    """Update a note's content and re-index in RAG.
    
    Deletes old vectors, re-ingests with new content.
    """
    source = await source_store.get(source_id)
    if not source or source.get("notebook_id") != notebook_id:
        raise HTTPException(status_code=404, detail="Source not found")
    if source.get("type") != "note":
        raise HTTPException(status_code=400, detail="Source is not a note")

    text = request.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Note content cannot be empty")

    title = (request.title.strip() if request.title else None) or source.get("filename", "Untitled Note")

    # 1. Delete old vectors from LanceDB
    try:
        await rag_engine.delete_source(notebook_id, source_id)
    except Exception as e:
        print(f"[NOTE] LanceDB cleanup for re-index: {e}")

    # 2. Re-ingest
    try:
        result = await rag_engine.ingest_document(
            notebook_id=notebook_id,
            source_id=source_id,
            text=text,
            filename=title,
            source_type="note"
        )

        # 3. Update source record
        await source_store.update(notebook_id, source_id, {
            "filename": title,
            "chunks": result.get("chunks", 0),
            "characters": result.get("characters", len(text)),
            "content": text,
            "status": "completed",
        })

        return {
            "source_id": source_id,
            "filename": title,
            "chunks": result.get("chunks", 0),
            "characters": result.get("characters", len(text)),
            "status": "completed",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update note: {e}")


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


@router.post("/{notebook_id}/auto-tag-all")
async def auto_tag_all_sources(notebook_id: str, background_tasks: BackgroundTasks):
    """Auto-tag all untagged sources in a notebook using LLM.
    
    Finds sources with no tags, runs auto_tagger on each (with semaphore to
    limit concurrent LLM calls), and stores the results.
    Runs in background so the UI doesn't block.
    """
    sources = await source_store.list(notebook_id)
    untagged = [s for s in sources if not s.get("tags")]
    
    if not untagged:
        return {"message": "All sources already tagged", "queued": 0, "total": len(sources)}
    
    async def _backfill():
        from services.auto_tagger import auto_tagger
        import asyncio
        
        tagged_count = 0
        for source in untagged:
            try:
                content = ""
                content_data = await source_store.get_content(notebook_id, source["id"])
                if content_data:
                    content = content_data.get("content", "")
                
                tags = await auto_tagger.tag_source_in_notebook(
                    notebook_id=notebook_id,
                    source_id=source["id"],
                    title=source.get("filename", ""),
                    content=content,
                )
                if tags:
                    tagged_count += 1
            except Exception as e:
                print(f"[AutoTag Backfill] Failed for {source.get('filename', source['id'])}: {e}")
        
        print(f"[AutoTag Backfill] Done: tagged {tagged_count}/{len(untagged)} sources in {notebook_id}")
    
    background_tasks.add_task(_backfill)
    
    return {
        "message": f"Auto-tagging {len(untagged)} untagged sources in background",
        "queued": len(untagged),
        "already_tagged": len(sources) - len(untagged),
        "total": len(sources)
    }


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
