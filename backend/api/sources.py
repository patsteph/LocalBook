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


class MoveSourceRequest(BaseModel):
    target_notebook_id: str


class TagsRequest(BaseModel):
    tags: List[str]


class SingleTagRequest(BaseModel):
    tag: str

@router.get("/{notebook_id}")
async def list_sources(notebook_id: str):
    """List all sources for a notebook"""
    sources = await source_store.list(notebook_id)
    return sources

LARGE_FILE_THRESHOLD = 5 * 1024 * 1024  # 5 MB — files above this use background processing


async def _process_upload_background(
    content: bytes, filename: str, notebook_id: str, source_id: str
):
    """Background task: extract text, ingest into RAG, update source, notify frontend.
    
    Used for large files that would otherwise cause upload timeouts.
    """
    from api.constellation_ws import notify_source_updated
    from api.timeline import extract_timeline_for_source

    try:
        # 1. Extract text
        text = await document_processor._extract_text(content, filename)
        if not text or not text.strip():
            raise ValueError(f"No text content could be extracted from {filename}")

        print(f"[UPLOAD-BG] Extracted {len(text)} chars from {filename}")

        # 2. Ingest into RAG
        rag_result = await rag_engine.ingest_document(
            notebook_id=notebook_id,
            source_id=source_id,
            text=text,
            filename=filename,
            source_type=document_processor._get_file_type(filename, content),
        )
        chunks = rag_result.get("chunks", 0)
        characters = rag_result.get("characters", len(text))

        # 3. Update source to completed
        await source_store.update(notebook_id, source_id, {
            "chunks": chunks,
            "characters": characters,
            "status": "completed",
            "content": text,
        })
        print(f"[UPLOAD-BG] Ingested {filename}: {chunks} chunks, {characters} chars")

        # 4. Notify frontend via WebSocket
        await notify_source_updated({
            "notebook_id": notebook_id,
            "source_id": source_id,
            "status": "completed",
            "title": filename,
            "chunks": chunks,
            "characters": characters,
        })

        # 5. Background extras (all non-fatal)
        # Timeline extraction
        try:
            await extract_timeline_for_source(notebook_id, source_id, text, filename)
        except Exception:
            pass

        # Image processing for PDFs/PPTs
        file_ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
        if file_ext in ['pdf', 'pptx']:
            try:
                await document_processor.process_images_background(
                    content, notebook_id, source_id, filename
                )
            except Exception:
                pass

        # Auto-tag
        try:
            from services.auto_tagger import auto_tagger
            await auto_tagger.tag_source_in_notebook(
                notebook_id, source_id, filename, text[:3000]
            )
        except Exception:
            pass

    except Exception as e:
        import traceback
        print(f"[UPLOAD-BG] Failed for {filename}: {e}")
        traceback.print_exc()
        await source_store.update(notebook_id, source_id, {
            "status": "failed",
            "error": str(e)[:200],
        })
        try:
            await notify_source_updated({
                "notebook_id": notebook_id,
                "source_id": source_id,
                "status": "failed",
                "title": filename,
                "error": str(e)[:100],
            })
        except Exception:
            pass


@router.post("/upload")
async def upload_source(
    file: UploadFile = File(...),
    notebook_id: str = Form(...),
    background_tasks: BackgroundTasks = None
):
    """Upload and process a document.
    
    Large files (>5 MB) are processed in background to avoid timeout.
    The endpoint returns immediately with 'processing' status, and the
    frontend is notified via WebSocket when ingestion completes.
    """
    import traceback
    import uuid
    from api.timeline import extract_timeline_for_source
    from services.content_date_extractor import extract_content_date

    # Read file bytes
    content = await file.read()
    filename = file.filename
    file_size = len(content)
    
    print(f"[UPLOAD] Received file: {filename}, size: {file_size} bytes, notebook: {notebook_id}")

    # ── Large file → background processing (prevents timeout) ──────────
    if file_size > LARGE_FILE_THRESHOLD:
        print(f"[UPLOAD] Large file detected ({file_size / 1024 / 1024:.1f} MB), using background processing")
        try:
            file_format = document_processor._get_file_type(filename, content)
            source_id = str(uuid.uuid4())

            # Create source record immediately with "processing" status
            source_meta = {
                "type": file_format,
                "format": file_format,
                "size": file_size,
                "chunks": 0,
                "characters": 0,
                "status": "processing",
            }
            # Try to extract content date from filename
            try:
                cd = extract_content_date(filename, "")
                if cd:
                    source_meta["content_date"] = cd
            except Exception:
                pass

            source = await source_store.create(
                notebook_id=notebook_id,
                filename=filename,
                metadata={**source_meta, "id": source_id},
            )

            # Queue all heavy work as background task
            background_tasks.add_task(
                _process_upload_background,
                content, filename, notebook_id, source_id,
            )

            # Record engagement
            try:
                from services.collection_history import record_engagement
                record_engagement(notebook_id, "source_upload")
            except Exception:
                pass
            try:
                log_document_captured(notebook_id, filename, filename, "upload")
            except Exception:
                pass

            return {
                "source_id": source_id,
                "filename": filename,
                "format": file_format,
                "chunks": 0,
                "characters": 0,
                "status": "processing",
            }
        except Exception as e:
            error_msg = str(e)
            print(f"[UPLOAD] Error creating source for large file {filename}: {error_msg}")
            raise HTTPException(status_code=500, detail=f"Failed to process file: {error_msg}")

    # ── Normal-size file → synchronous processing (fast) ───────────────
    try:
        result = await document_processor.process(
            content=content,
            filename=filename,
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
                    filename
                )
                print(f"[UPLOAD] Queued timeline extraction for {filename}")
            
            # v1.0.5: Background image processing for PDFs/PPTs
            file_ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
            if file_ext in ['pdf', 'pptx']:
                background_tasks.add_task(
                    document_processor.process_images_background,
                    content,
                    notebook_id,
                    result["source_id"],
                    filename
                )
                print(f"[UPLOAD] Queued background image processing for {filename}")
        
        # Auto-tag the uploaded document (non-fatal)
        try:
            from services.auto_tagger import auto_tagger
            tag_text = (source.get("content", "") if source else "")[:3000]
            await auto_tagger.tag_source_in_notebook(
                notebook_id, result["source_id"], filename, tag_text
            )
        except Exception as tag_err:
            print(f"[UPLOAD] Auto-tagging failed (non-fatal): {tag_err}")

        # Log document capture event
        try:
            log_document_captured(notebook_id, filename, filename, "upload")
        except Exception:
            pass
        
        # Record engagement to suppress stale-research tombstone
        try:
            from services.collection_history import record_engagement
            record_engagement(notebook_id, "source_upload")
        except Exception:
            pass
        
        return result
    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        print(f"[UPLOAD] Error processing {filename}: {error_msg}")
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


@router.post("/{notebook_id}/{source_id}/move")
async def move_source(notebook_id: str, source_id: str, request: MoveSourceRequest):
    """Move a source (note or document) to a different notebook.

    1. Delete vectors from old notebook's LanceDB table
    2. Update notebook_id in source_store
    3. Re-ingest into new notebook's LanceDB table
    """
    source = await source_store.get(source_id)
    if not source or source.get("notebook_id") != notebook_id:
        raise HTTPException(status_code=404, detail="Source not found")

    target_id = request.target_notebook_id
    if target_id == notebook_id:
        raise HTTPException(status_code=400, detail="Source is already in this notebook")

    content = source.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="Source has no content to re-index")

    title = source.get("filename", "Untitled")
    source_type = source.get("type", "document")

    # 1. Delete vectors from old notebook
    try:
        await rag_engine.delete_source(notebook_id, source_id)
    except Exception as e:
        print(f"[MOVE] LanceDB cleanup from old notebook: {e}")

    # 2. Delete from old notebook in source_store
    await source_store.delete(notebook_id, source_id)

    # 3. Re-create in target notebook with same ID
    new_source = await source_store.create(
        notebook_id=target_id,
        filename=title,
        metadata={
            "id": source_id,
            "type": source_type,
            "format": source.get("format", "markdown"),
            "status": "processing",
            "chunks": 0,
            "characters": 0,
            "content": content,
        }
    )

    # 4. Re-ingest into new notebook's RAG
    try:
        result = await rag_engine.ingest_document(
            notebook_id=target_id,
            source_id=source_id,
            text=content,
            filename=title,
            source_type=source_type,
        )
        await source_store.update(target_id, source_id, {
            "chunks": result.get("chunks", 0),
            "characters": result.get("characters", len(content)),
            "status": "completed",
        })
    except Exception as e:
        await source_store.update(target_id, source_id, {
            "status": "failed",
            "error": str(e)[:200],
        })
        raise HTTPException(status_code=500, detail=f"Failed to re-index in target notebook: {e}")

    return {
        "message": f"Moved '{title}' to target notebook",
        "source_id": source_id,
        "target_notebook_id": target_id,
    }


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
