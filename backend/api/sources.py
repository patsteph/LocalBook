"""Sources API endpoints"""
import asyncio
import json
from typing import List
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from storage.source_store import source_store
from services.document_processor import document_processor
from services.rag_engine import rag_engine
from services.topic_modeling import topic_modeling_service
from services.event_logger import log_document_captured
from services.progress_reporter import ProgressReporter
import logging
logger = logging.getLogger(__name__)

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


class ExpandLinksRequest(BaseModel):
    """User-selected outgoing URLs to scrape at depth+1."""
    selected_urls: List[str]


@router.get("/{notebook_id}")
async def list_sources(notebook_id: str):
    """List all sources for a notebook"""
    sources = await source_store.list(notebook_id)
    return sources


# ─── Outgoing-link expansion (depth+1) ─────────────────────────────────────

@router.get("/{notebook_id}/{source_id}/outgoing-links")
async def list_outgoing_links(notebook_id: str, source_id: str):
    """Return the outgoing links extracted at capture, marked with dedup status.

    Each link gets `already_captured: bool` so the UI can pre-disable
    checkboxes for links the user has already brought in (anywhere across
    notebooks). Sources at depth >= 1 return an empty list — depth+1 is a
    hard cap, no chained recursion.
    """
    source = await source_store.get(source_id)
    if not source or (source.get("notebook_id") or "") != notebook_id:
        raise HTTPException(status_code=404, detail="Source not found in this notebook")
    raw_links = source.get("outbound_links") or []
    depth = int(source.get("depth") or 0)
    if depth >= 1:
        # Hard cap: refuse to surface links from a depth-1 source so the
        # UI can't even tempt the user into recursion.
        return {
            "source_id": source_id,
            "depth": depth,
            "links": [],
            "expansion_blocked": True,
            "reason": "Depth+1 expansion only — this source was already an expansion result.",
        }
    # Build a cross-notebook dedup index ONCE so we don't hit the store
    # per link.
    grouped = await source_store.list_all()
    existing_urls: set[str] = set()
    for sources in grouped.values():
        for s in sources:
            u = (s.get("url") or "").strip()
            if u:
                existing_urls.add(u)
    annotated = []
    for link in raw_links:
        if not isinstance(link, dict):
            continue
        url = (link.get("url") or "").strip()
        if not url:
            continue
        annotated.append({
            "url": url,
            "text": link.get("text") or "",
            "context": link.get("context") or "",
            "already_captured": url in existing_urls,
        })
    return {
        "source_id": source_id,
        "depth": depth,
        "links": annotated,
        "total": len(annotated),
        "expansion_blocked": False,
    }


@router.post("/{notebook_id}/{source_id}/expand-links")
async def expand_outgoing_links(
    notebook_id: str,
    source_id: str,
    request: ExpandLinksRequest,
):
    """Submit a depth+1 expansion job for the user-selected URLs.

    Returns a job_id immediately; the actual scraping runs in the
    background via services/job_queue.py. Poll /jobs/{job_id} for
    progress, or subscribe to the JobQueue progress stream.

    Each successfully scraped article lands in the notebook's approval
    queue with `parent_source_id`, `discovery_url`, and
    `cross_notebook_matches` stamped on it — never auto-approved, even
    at high relevance, so the user reviews every result.
    """
    from services.link_expander import submit_expansion, LinkExpansionError
    try:
        job_id = await submit_expansion(
            source_id=source_id,
            notebook_id=notebook_id,
            selected_urls=request.selected_urls,
        )
    except LinkExpansionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "status": "submitted",
        "job_id": job_id,
        "notebook_id": notebook_id,
        "source_id": source_id,
        "selected_count": len(request.selected_urls),
    }

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
        except Exception as _e:
            logger.debug(f"[sources] {type(_e).__name__}: {_e}")

        # Image processing for PDFs/PPTs
        file_ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
        if file_ext in ['pdf', 'pptx']:
            try:
                await document_processor.process_images_background(
                    content, notebook_id, source_id, filename
                )
            except Exception as _e:
                logger.debug(f"[sources] {type(_e).__name__}: {_e}")

        # Auto-tag
        try:
            from services.auto_tagger import auto_tagger
            await auto_tagger.tag_source_in_notebook(
                notebook_id, source_id, filename, text[:3000]
            )
        except Exception as _e:
            logger.debug(f"[sources] {type(_e).__name__}: {_e}")

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
        except Exception as _e:
            logger.debug(f"[sources] {type(_e).__name__}: {_e}")


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
            except Exception as _e:
                logger.debug(f"[sources] {type(_e).__name__}: {_e}")

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
            except Exception as _e:
                logger.debug(f"[sources] {type(_e).__name__}: {_e}")
            try:
                log_document_captured(notebook_id, filename, filename, "upload")
            except Exception as _e:
                logger.debug(f"[sources] {type(_e).__name__}: {_e}")

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
        except Exception as _e:
            logger.debug(f"[sources] {type(_e).__name__}: {_e}")
        
        # Record engagement to suppress stale-research tombstone
        try:
            from services.collection_history import record_engagement
            record_engagement(notebook_id, "source_upload")
        except Exception as _e:
            logger.debug(f"[sources] {type(_e).__name__}: {_e}")
        
        return result
    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        print(f"[UPLOAD] Error processing {filename}: {error_msg}")
        print(f"[UPLOAD] Traceback:\n{tb}")
        raise HTTPException(status_code=500, detail=f"Failed to process file: {error_msg}")


# =========================================================================
# Streaming Upload with Granular Progress (v1.6.2)
# =========================================================================
# New SSE-based upload endpoint that reports stage-by-stage ingestion progress
# so users can see the full RAG journey (extract -> chunk -> summarize ->
# embed -> index). The legacy POST /upload endpoint is untouched; agents,
# extension captures, and other non-UI callers continue to use it.

async def _run_upload_with_reporter(
    *,
    content: bytes,
    filename: str,
    notebook_id: str,
    reporter: ProgressReporter,
    do_auto_tag: bool,
    do_timeline: bool,
    do_image_extract: bool,
):
    """Execute the full ingestion pipeline, emitting progress via reporter.

    On success emits a terminal `complete` event with the source result.
    On failure emits a terminal `error` event with the message.
    Always closes the reporter's queue when done.
    """
    import traceback
    from api.timeline import extract_timeline_for_source

    try:
        await reporter.emit("received", 3, f"Received {filename} ({len(content):,} bytes)")

        # Run the full document-processor pipeline with the reporter threaded in
        result = await document_processor.process(
            content=content,
            filename=filename,
            notebook_id=notebook_id,
            reporter=reporter,
        )

        source_id = result.get("source_id")
        chunks = result.get("chunks", 0)
        characters = result.get("characters", 0)

        # Fetch full source for downstream tasks
        source = await source_store.get(source_id) if source_id else None

        # Auto-tag (foreground so it shows in the journey)
        if do_auto_tag and source:
            try:
                await reporter.emit("tagging", 96, "Auto-tagging with notebook topics...")
                from services.auto_tagger import auto_tagger
                tag_text = (source.get("content", "") or "")[:3000]
                await auto_tagger.tag_source_in_notebook(
                    notebook_id, source_id, filename, tag_text,
                )
            except Exception as tag_err:
                logger.debug(f"[sources] auto-tag failed (non-fatal): {tag_err}")

        # Fire-and-forget background extras (timeline extraction, image OCR).
        # safe_create_task ensures any exception is logged rather than
        # silently swallowed by the GC — same behaviour the rest of the
        # codebase converged on.
        from utils.tasks import safe_create_task
        if source_id and source and source.get("content"):
            if do_timeline:
                safe_create_task(
                    extract_timeline_for_source(
                        notebook_id, source_id, source["content"], filename,
                    ),
                    name=f"timeline-{source_id}",
                )
            if do_image_extract:
                file_ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
                if file_ext in ['pdf', 'pptx']:
                    safe_create_task(
                        document_processor.process_images_background(
                            content, notebook_id, source_id, filename,
                        ),
                        name=f"image-ocr-{source_id}",
                    )

        # Log & engagement (non-fatal)
        try:
            log_document_captured(notebook_id, filename, filename, "upload")
        except Exception as _e:
            logger.debug(f"[sources] {type(_e).__name__}: {_e}")
        try:
            from services.collection_history import record_engagement
            record_engagement(notebook_id, "source_upload")
        except Exception as _e:
            logger.debug(f"[sources] {type(_e).__name__}: {_e}")

        await reporter.complete(
            f"Ready — {chunks} chunks, {characters:,} chars",
            details={
                "source_id": source_id,
                "chunks": chunks,
                "characters": characters,
                "format": result.get("format"),
                "filename": filename,
            },
        )

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[UPLOAD-STREAM] Error for {filename}: {e}\n{tb}")
        await reporter.error(str(e)[:300], details={"filename": filename})
    finally:
        await reporter.close()


@router.post("/upload/stream")
async def upload_source_stream(
    file: UploadFile = File(...),
    notebook_id: str = Form(...),
):
    """Upload a document and stream granular progress events (SSE).

    Emits stage-by-stage updates so the UI can show the full RAG journey:
    received -> detecting -> extracting -> analyzing -> creating_record ->
    chunking -> summarizing -> hyde_questions -> embedding -> indexing ->
    tagging -> complete (or error).

    Each event is JSON on a single `data:` line:
      {"stage": "embedding", "percent": 72, "message": "...", "details": {...}}

    The final event has stage="complete" (with source_id/chunks/characters in
    details) or stage="error" (with the failure message).

    The legacy POST /upload endpoint remains unchanged for non-UI callers.
    """
    content = await file.read()
    filename = file.filename or "upload"
    print(f"[UPLOAD-STREAM] Received {filename}, size={len(content)} bytes, notebook={notebook_id}")

    reporter = ProgressReporter()

    # Kick off the ingestion as a background task so the streamer can drain
    # the queue concurrently. The task runs to completion even if the client
    # disconnects — no data loss, matches the existing background-task pattern.
    from utils.tasks import safe_create_task
    safe_create_task(
        _run_upload_with_reporter(
            content=content,
            filename=filename,
            notebook_id=notebook_id,
            reporter=reporter,
            do_auto_tag=True,
            do_timeline=True,
            do_image_extract=True,
        ),
        name=f"upload-{filename}",
    )

    async def _sse_generator():
        # Initial ping so the client immediately knows the connection is live
        yield ": ping\n\n"
        while True:
            evt = await reporter.queue.get()
            if evt is None:
                # Sentinel: reporter.close() called — stream is finished
                yield "event: done\ndata: {}\n\n"
                return
            yield evt.to_sse()

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
        except Exception as _e:
            logger.debug(f"[sources] {type(_e).__name__}: {_e}")

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
    """Move a source (note, document, web capture, collector item) to a
    different notebook.

    Preserves ALL metadata: tags, url, author, dates, notes, web/collector
    provenance, content_date, etc. Previously this endpoint copied only
    type/format/content, silently dropping tags and every other extra field.

    If the source has no cached `content` (common for old web captures whose
    text lives only in LanceDB chunks), we still do the metadata move and
    mark the new row as `needs_reindex` rather than hard-failing. That's
    what was causing the "Failed to move source" toast the user was seeing.
    """
    from storage.notebook_store import notebook_store

    source = await source_store.get(source_id)
    if not source or source.get("notebook_id") != notebook_id:
        raise HTTPException(status_code=404, detail="Source not found")

    target_id = request.target_notebook_id
    if target_id == notebook_id:
        raise HTTPException(status_code=400, detail="Source is already in this notebook")

    # Verify target notebook actually exists — better than silently orphaning.
    try:
        target_nb = await notebook_store.get(target_id)
    except Exception:
        target_nb = None
    if not target_nb:
        raise HTTPException(status_code=404, detail=f"Target notebook not found: {target_id}")

    content = source.get("content") or ""
    title = source.get("filename", "Untitled")
    source_type = source.get("type", "document")

    # Preserve every field except the ones source_store.create() manages itself.
    # Keep `created_at` so the source's original date isn't reset by the move.
    RESERVED = {"id", "notebook_id", "status", "chunks", "characters", "error"}
    preserved = {k: v for k, v in source.items() if k not in RESERVED}
    preserved["id"] = source_id
    preserved["chunks"] = 0
    preserved["characters"] = 0
    preserved["status"] = "processing" if content else "needs_reindex"

    # 1. Clean up vectors in the OLD notebook's LanceDB table. Non-fatal —
    #    worst case is orphaned chunks in the old table that will never be
    #    queried (their notebook_id no longer references them).
    try:
        await rag_engine.delete_source(notebook_id, source_id)
    except Exception as e:
        logger.warning(f"[MOVE] LanceDB cleanup from old notebook failed (non-fatal): {e}")

    # 2. Atomic move in source_store. create() issues INSERT OR REPLACE so
    #    the single row's notebook_id flips in one SQL statement — no window
    #    where the source exists in both notebooks or in neither.
    await source_store.create(
        notebook_id=target_id,
        filename=title,
        metadata=preserved,
    )

    # 3. Re-ingest into target notebook if we have content. If not, the move
    #    still succeeded at the metadata level; user can delete or manually
    #    repopulate from the needs_reindex status.
    if content:
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
            # Move itself already succeeded — source is in target nb but not
            # queryable yet. Return 200 with a reindex_error so the frontend
            # can show a partial-success toast instead of a hard failure.
            await source_store.update(target_id, source_id, {
                "status": "failed",
                "error": str(e)[:200],
            })
            return {
                "message": f"Moved '{title}' but re-indexing failed",
                "source_id": source_id,
                "target_notebook_id": target_id,
                "reindexed": False,
                "reindex_error": str(e)[:200],
            }
    else:
        logger.info(f"[MOVE] {source_id} had no cached content; marked needs_reindex in target")

    return {
        "message": f"Moved '{title}' to target notebook",
        "source_id": source_id,
        "target_notebook_id": target_id,
        "reindexed": bool(content),
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
