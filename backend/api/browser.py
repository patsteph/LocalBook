"""Browser Extension API endpoints

API for the LocalBook browser extension to capture pages,
extract metadata, and sync with the main application.

v1.0.5: Added multimodal image extraction for web captures.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid

router = APIRouter(prefix="/browser", tags=["browser"])

from version import APP_VERSION
from api.constellation_ws import notify_source_updated
from services.event_logger import log_document_captured
from services.content_date_extractor import extract_content_date
import logging
logger = logging.getLogger(__name__)


class OutboundLink(BaseModel):
    """A single outgoing link extracted from a captured page.

    Matches the `OutboundLink` shape the browser extension already produces
    in extension/types.ts. Persisted alongside the source so the user can
    later choose to follow specific links via the depth+1 expander.
    """
    url: str
    text: str = ""
    context: str = ""


class PageCaptureRequest(BaseModel):
    """Request to capture a web page."""
    url: str
    title: str
    content: str  # Text content of page
    notebook_id: str
    html_content: Optional[str] = None  # Raw HTML for metadata extraction
    capture_type: str = "page"  # page, selection, youtube, pdf
    # Outgoing links extracted by the extension at capture time. Optional —
    # older extension builds and non-extension callers won't send this.
    # Persisted to source.metadata_json.outbound_links so the user can
    # later expand them via /sources/{id}/expand-links (depth+1 cap).
    outbound_links: Optional[List[OutboundLink]] = None


class SelectionCaptureRequest(BaseModel):
    """Request to capture selected text."""
    url: str
    title: str
    selected_text: str
    notebook_id: str
    context: Optional[str] = None  # Surrounding text


class YouTubeCaptureRequest(BaseModel):
    """Request to capture a YouTube video."""
    video_url: str
    notebook_id: str
    include_transcript: bool = True


class MetadataExtractionRequest(BaseModel):
    """Request to extract metadata from HTML."""
    html_content: str
    url: str


class SummarizeRequest(BaseModel):
    """Request to summarize page content."""
    content: str
    url: str
    max_length: int = 500


class CaptureResponse(BaseModel):
    """Response from capture operation."""
    success: bool
    source_id: Optional[str] = None
    title: str
    word_count: int
    reading_time_minutes: int
    summary: Optional[str] = None
    key_concepts: List[str] = []
    error: Optional[str] = None
    # "processing" when the source exists and is already stored but its enrichment
    # (RAG ingest, curator scoring, tags, summary) is still running behind the
    # response — the extension polls /browser/capture-status for the rest. Paths that
    # still finish inline keep the default. Older extension builds ignore the field.
    status: str = "completed"


class NotebookInfo(BaseModel):
    """Notebook info for extension popup."""
    id: str
    name: str
    source_count: int


@router.get("/status")
async def get_status():
    """Check if LocalBook backend is running."""
    return {
        "status": "online",
        "version": APP_VERSION,
        "timestamp": datetime.now().isoformat()
    }


@router.get("/notebooks", response_model=List[NotebookInfo])
async def list_notebooks_for_extension():
    """List notebooks for extension popup selector."""
    try:
        from storage.notebook_store import notebook_store
        from storage.source_store import source_store
        
        notebooks = await notebook_store.list()
        source_counts = await source_store.count_by_notebook()
        
        result = []
        for nb in notebooks:
            result.append(NotebookInfo(
                id=nb["id"],
                name=nb.get("title", "Untitled"),
                source_count=source_counts.get(nb["id"], 0)
            ))
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/exists")
async def source_exists(notebook_id: str, url: str):
    """Is this URL already a source in this notebook?

    The extension asked this by GETting /sources/{notebook_id} and searching the
    result client-side — and `source_store.list()` is `SELECT *` with metadata_json
    merged in, i.e. every source's FULL TEXT, serialized over localhost, before every
    single capture. This answers the same question with one field per source.

    Matches on the URL as stored and with the trailing slash / fragment normalised, so
    a cleaned URL still finds a source captured with the raw one.
    """
    from storage.source_store import source_store

    def _norm(u: str) -> str:
        return (u or "").split("#")[0].rstrip("/").lower()

    target = _norm(url)
    if not target:
        return {"exists": False}
    try:
        for s in await source_store.list(notebook_id):
            stored = s.get("url") or ""
            if stored == url or _norm(stored) == target:
                return {
                    "exists": True,
                    "source_id": s.get("id"),
                    "title": s.get("title") or s.get("filename") or "",
                    "status": s.get("status") or "completed",
                }
    except Exception as e:
        # Never block a capture on the dedup check — the caller treats a failure as
        # "not found" and proceeds, which is what it did when this was client-side.
        logger.warning(f"[browser] exists check failed: {type(e).__name__}: {e}")
    return {"exists": False}


@router.get("/capture-status/{notebook_id}/{source_id}")
async def capture_status(notebook_id: str, source_id: str):
    """What has landed for a capture so far.

    /browser/capture now returns as soon as the source is stored, so the extension
    polls this to fill in the chunk count and the topics once the background
    enrichment finishes. Deliberately narrow: no `content`, no `html`, no metadata
    blob — this is polled, so it must stay cheap.
    """
    from storage.source_store import source_store

    source = await source_store.get(source_id)
    if not source or (source.get("notebook_id") or "") != notebook_id:
        raise HTTPException(status_code=404, detail="Source not found in this notebook")

    summary = source.get("summary") or ""
    return {
        "source_id": source_id,
        "status": source.get("status") or "processing",
        "title": source.get("title") or source.get("filename") or "",
        "chunks": int(source.get("chunks") or 0),
        "word_count": int(source.get("word_count") or 0),
        "topics": source.get("topics") or [],
        "key_concepts": source.get("key_concepts") or [],
        "summary_present": bool(summary),
        "error": source.get("error"),
    }


async def process_web_images_background(
    notebook_id: str,
    source_id: str,
    html_content: str,
    base_url: str,
    page_title: str
):
    """Background task to extract and describe images from web page.
    
    v1.0.5: Added for multimodal web capture - extracts images from HTML,
    describes them with vision model, and appends to the indexed source.
    """
    try:
        from services.multimodal_extractor import multimodal_extractor
        from services.rag_engine import rag_engine
        
        print(f"[BROWSER] Starting background image extraction for {page_title}")
        
        # Extract and describe images
        image_descriptions = await multimodal_extractor.extract_and_describe_html(
            html_content=html_content,
            source_id=source_id,
            base_url=base_url,
            page_title=page_title
        )
        
        if not image_descriptions:
            print(f"[BROWSER] No meaningful images found in {page_title}")
            return
        
        # Format for indexing
        image_text = multimodal_extractor.format_for_indexing(image_descriptions)
        
        if image_text:
            # Append to existing document in RAG
            result = await rag_engine.append_to_document(
                notebook_id=notebook_id,
                source_id=source_id,
                text=image_text
            )
            print(f"[BROWSER] Added {result.get('chunks_added', 0)} image chunks to {page_title}")
        
    except Exception as e:
        print(f"[BROWSER] Background image extraction failed: {e}")
        import traceback
        traceback.print_exc()


# How much of a capture's text the auto-tagger sees. Explicit budget rather than an
# inline slice, per the truncation rule — tags come from the opening of a document.
_AUTO_TAG_MAX_CHARS = 3000
# Below this there is no realistic chance of a described-worthy image in the markup.
_MIN_HTML_FOR_IMAGE_PASS = 1000


def _queue_web_image_pass(
    notebook_id: str,
    source_id: str,
    html_content: str,
    base_url: str,
    page_title: str,
) -> None:
    """Hand the vision pass to the Enrichment Worker rather than the event loop.

    Describing every image on a page is vision-model work, and it used to ride on
    FastAPI's BackgroundTasks — meaning it began the instant the capture responded,
    on the request loop, whether or not the user was doing something else. It is also
    the one genuinely deferrable half of a capture: the page's text is searchable
    without it. DAYDREAM tier, coalesced per source, so it runs when the machine is
    quiet and a second capture of the same source collapses into one job.

    Called only AFTER the ingest has completed — the job appends to the document the
    ingest creates, so the two must never race.
    """
    try:
        from services.enrichment_jobs import EnrichmentJob, JobTier
        from services.enrichment_worker import enrichment_worker

        enrichment_worker.enqueue(EnrichmentJob(
            key=f"web-images:{notebook_id}:{source_id}",
            tier=JobTier.DAYDREAM,
            label="web-images",
            notebook_id=notebook_id,
            # A THUNK: the worker cancels and re-runs a job, and a coroutine can only
            # be awaited once (see services/enrichment_jobs.py).
            factory=lambda: process_web_images_background(
                notebook_id=notebook_id,
                source_id=source_id,
                html_content=html_content,
                base_url=base_url,
                page_title=page_title,
            ),
        ))
        logger.info(f"[browser] queued image pass for {source_id} (DAYDREAM)")
    except Exception as e:
        logger.warning(f"[browser] could not queue image pass: {type(e).__name__}: {e}")


async def _finish_web_capture_background(
    notebook_id: str,
    source_id: str,
    content: str,
    title: str,
    url: str,
    source_type: str,
    html_content: Optional[str] = None,
    user_weight_bonus: float = 1.5,
    curator_source_type: Optional[str] = None,
    summarize: bool = True,
) -> None:
    """Everything that used to run BEFORE /browser/capture answered the extension.

    The capture request awaited three LLM calls — curator scoring, the page summary
    (on the MAIN model, with a 12k-char prompt) and auto-tagging — plus the entire RAG
    ingest, so scraping a long article held the connection for minutes behind a
    spinner. `api/web.py::quick_add` has done it the other way round for a long time
    ("returns INSTANTLY … scraping + ingestion happens in background"); this is that
    shape applied to the extension's path, and `_process_web_source_background` in the
    same module is the model for the failure handling.

    Order is deliberate: **ingest first**, because "searchable" is what the user is
    actually waiting for — the LLM enrichments only decorate the source afterwards.
    Every enrichment step is independently non-fatal. The ingest is not: if it fails
    the source is marked `failed` and the failure is pushed, because a source left
    sitting in `processing` is the one outcome this must never produce.

    Shared with selection capture, which differs only in its curator weighting (a
    highlight is a stronger signal) and in having nothing worth summarising.
    """
    from storage.source_store import source_store
    from services.rag_engine import rag_engine

    # ── the part that makes the source usable ──────────────────────────────────
    try:
        rag_result = await rag_engine.ingest_document(
            notebook_id=notebook_id,
            source_id=source_id,
            text=content,
            filename=title,
            source_type=source_type,
        )
        chunks = rag_result.get("chunks", 0) if rag_result else 0
        await source_store.update(notebook_id, source_id, {
            "chunks": chunks,
            "status": "completed",
            "content": content,
        })
        await notify_source_updated({
            "notebook_id": notebook_id,
            "source_id": source_id,
            "status": "completed",
            "title": title,
            "chunks": chunks,
        })
        logger.info(f"[browser] ingested {title[:80]!r}: {chunks} chunks")
    except Exception as e:
        logger.error(f"[browser] ingest failed for {title[:80]!r}: {e}", exc_info=True)
        try:
            await source_store.update(notebook_id, source_id, {
                "status": "failed",
                "error": str(e)[:200],
            })
            await notify_source_updated({
                "notebook_id": notebook_id,
                "source_id": source_id,
                "status": "failed",
                "title": title,
                "error": str(e)[:100],
            })
        except Exception as _e:
            logger.debug(f"[browser] {type(_e).__name__}: {_e}")
        return

    # ── enrichment: the source is already searchable, so none of this is fatal ──
    updates: dict = {}

    try:
        from agents.curator import curator
        curator_scoring = await curator.score_user_item(
            notebook_id=notebook_id,
            title=title,
            content=content,
            url=url,
            source_type=curator_source_type or source_type,
            user_weight_bonus=user_weight_bonus,  # the user explicitly captured this
        )
        updates.update({
            "curator_scoring": curator_scoring,
            "topics": curator_scoring.get("topics", []),
            "entities": curator_scoring.get("entities", []),
            "importance": curator_scoring.get("importance", "medium"),
        })
        logger.info(
            f"[browser] curator scored {title[:60]!r}: "
            f"relevance={curator_scoring.get('relevance_score', 0):.2f}"
        )
    except Exception as e:
        logger.warning(f"[browser] curator scoring failed (non-fatal): {type(e).__name__}: {e}")

    try:
        from services.auto_tagger import auto_tagger
        await auto_tagger.tag_source_in_notebook(
            notebook_id, source_id, title, content[:_AUTO_TAG_MAX_CHARS],
        )
    except Exception as e:
        logger.warning(f"[browser] auto-tagging failed (non-fatal): {type(e).__name__}: {e}")

    if summarize:
        try:
            from agents.tools import summarize_page_tool
            summary_result = await summarize_page_tool.ainvoke({"content": content, "url": url})
            updates["summary"] = summary_result.get("summary", "")
            updates["key_concepts"] = summary_result.get("key_concepts", [])
        except Exception as e:
            logger.warning(f"[browser] summarization failed (non-fatal): {type(e).__name__}: {e}")

    if updates:
        try:
            await source_store.update(notebook_id, source_id, updates)
            # Second push so the extension's status poll and the app both see the
            # topics/summary land, not just the chunk count.
            await notify_source_updated({
                "notebook_id": notebook_id,
                "source_id": source_id,
                "status": "completed",
                "title": title,
                "chunks": chunks,
            })
        except Exception as e:
            logger.warning(f"[browser] could not store enrichment: {type(e).__name__}: {e}")

    if html_content and len(html_content) > _MIN_HTML_FOR_IMAGE_PASS:
        _queue_web_image_pass(
            notebook_id=notebook_id,
            source_id=source_id,
            html_content=html_content,
            base_url=url,
            page_title=title,
        )


async def _capture_remote_document(url: str, notebook_id: str, title: str, background_tasks: BackgroundTasks) -> CaptureResponse:
    """Download a remote document (PDF, PPTX, etc.) and process it.
    
    Used when the browser extension encounters a URL pointing to a file
    that can't be meaningfully extracted from DOM content.
    """
    import httpx
    from storage.source_store import source_store
    from services.rag_engine import rag_engine
    from services.document_processor import document_processor
    from agents.curator import curator

    try:
        # Download the file
        print(f"[BROWSER] Downloading remote document: {url}")
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            response = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) LocalBook/1.0"
            })
            if response.status_code != 200:
                return CaptureResponse(
                    success=False, title=title, word_count=0, reading_time_minutes=0,
                    error=f"Failed to download file (HTTP {response.status_code})"
                )
            content_bytes = response.content

        if len(content_bytes) < 100:
            return CaptureResponse(
                success=False, title=title, word_count=0, reading_time_minutes=0,
                error="Downloaded file is empty or too small"
            )

        # Detect file type from URL or content
        filename = title
        url_lower = url.lower().split('?')[0].split('#')[0]
        if url_lower.endswith('.pdf') or b'%PDF' in content_bytes[:10]:
            filename = title if title else "document.pdf"
            if not filename.lower().endswith('.pdf'):
                filename += ".pdf"
        elif url_lower.endswith('.pptx'):
            filename = title if title else "presentation.pptx"
            if not filename.lower().endswith('.pptx'):
                filename += ".pptx"
        elif url_lower.endswith('.docx'):
            filename = title if title else "document.docx"
            if not filename.lower().endswith('.docx'):
                filename += ".docx"
        elif url_lower.endswith('.xlsx'):
            filename = title if title else "spreadsheet.xlsx"
            if not filename.lower().endswith('.xlsx'):
                filename += ".xlsx"

        # Extract text using document_processor
        text = await document_processor._extract_text(content_bytes, filename)
        if not text or len(text.strip()) < 50:
            return CaptureResponse(
                success=False, title=title, word_count=0, reading_time_minutes=0,
                error="Could not extract text from downloaded file"
            )

        word_count = len(text.split())
        char_count = len(text)
        reading_time = max(1, word_count // 200)
        file_format = document_processor._get_file_type(filename, content_bytes)

        print(f"[BROWSER] Extracted {word_count} words from remote {file_format}: {url}")

        # Record token savings from document content vs web search
        try:
            from services.rag_metrics import rag_metrics
            rag_metrics.record_token_savings(text)
        except Exception as e:
            print(f"[BROWSER] Could not record token savings: {e}")

        # Score through Curator
        curator_scoring = await curator.score_user_item(
            notebook_id=notebook_id,
            title=title,
            content=text[:3000],
            url=url,
            source_type=file_format,
            user_weight_bonus=1.5
        )

        source_id = str(uuid.uuid4())
        source_data = {
            "id": source_id,
            "notebook_id": notebook_id,
            "type": file_format,
            "format": file_format,
            "url": url,
            "title": title,
            "filename": title,
            "content": text,
            "word_count": word_count,
            "char_count": char_count,
            "characters": char_count,
            "reading_time_minutes": reading_time,
            "capture_type": f"remote_{file_format}",
            "status": "processing",
            "chunks": 0,
            "created_at": datetime.now().isoformat(),
            "user_provided": True,
            "curator_scoring": curator_scoring,
            "topics": curator_scoring.get("topics", []),
            "entities": curator_scoring.get("entities", []),
            "importance": curator_scoring.get("importance", "medium"),
        }

        await source_store.create(
            notebook_id=notebook_id,
            filename=title,
            metadata=source_data,
        )

        # Index in RAG
        rag_result = await rag_engine.ingest_document(
            notebook_id=notebook_id,
            source_id=source_id,
            text=text,
            filename=title,
            source_type=file_format,
        )
        chunks = rag_result.get("chunks", 0) if rag_result else 0
        await source_store.update(notebook_id, source_id, {
            "chunks": chunks,
            "status": "completed",
            "content": text,
        })

        # Auto-tag (non-fatal)
        try:
            from services.auto_tagger import auto_tagger
            await auto_tagger.tag_source_in_notebook(notebook_id, source_id, title, text[:3000])
        except Exception as _e:
            logger.debug(f"[browser] {type(_e).__name__}: {_e}")

        # Notify frontend
        await notify_source_updated({
            "notebook_id": notebook_id,
            "source_id": source_id,
            "status": "completed",
            "chunks": chunks,
        })

        # Background image processing for PDFs/PPTs
        if file_format in ['pdf', 'pptx']:
            background_tasks.add_task(
                document_processor.process_images_background,
                content_bytes, notebook_id, source_id, filename,
            )

        try:
            log_document_captured(notebook_id, url, title, f"remote_{file_format}")
        except Exception as _e:
            logger.debug(f"[browser] {type(_e).__name__}: {_e}")

        return CaptureResponse(
            success=True,
            source_id=source_id,
            title=title,
            word_count=word_count,
            reading_time_minutes=reading_time,
            key_concepts=curator_scoring.get("topics", []),
        )

    except Exception as e:
        import traceback
        print(f"[BROWSER] Remote document capture failed: {e}")
        traceback.print_exc()
        return CaptureResponse(
            success=False, title=title, word_count=0, reading_time_minutes=0,
            error=str(e),
        )


def _is_document_url(url: str) -> Optional[str]:
    """Detect if a URL points to a downloadable document.
    
    Returns the file type string if detected, None otherwise.
    """
    if not url:
        return None
    url_lower = url.lower().split('?')[0].split('#')[0]
    
    # Direct file extensions
    for ext, ftype in [('.pdf', 'pdf'), ('.pptx', 'pptx'), ('.docx', 'docx'),
                       ('.xlsx', 'xlsx'), ('.doc', 'doc'), ('.ppt', 'ppt')]:
        if url_lower.endswith(ext):
            return ftype
    
    return None


def _is_google_doc_url(url: str) -> Optional[str]:
    """Detect Google Docs/Slides/Sheets URLs.
    
    Returns export URL if detected, None otherwise.
    """
    import re
    # Google Docs: docs.google.com/document/d/{id}/...
    m = re.search(r'docs\.google\.com/document/d/([a-zA-Z0-9_-]+)', url)
    if m:
        return f"https://docs.google.com/document/d/{m.group(1)}/export?format=txt"
    
    # Google Slides: docs.google.com/presentation/d/{id}/...
    m = re.search(r'docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)', url)
    if m:
        return f"https://docs.google.com/presentation/d/{m.group(1)}/export/pptx"
    
    # Google Sheets: docs.google.com/spreadsheets/d/{id}/...
    m = re.search(r'docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)', url)
    if m:
        return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
    
    return None


@router.post("/capture", response_model=CaptureResponse)
async def capture_page(request: PageCaptureRequest, background_tasks: BackgroundTasks):
    """Capture a web page to a notebook with summarization.
    
    Routes to specialized handlers for:
    - YouTube URLs → transcript extraction
    - ArXiv URLs → auto-download PDF and extract full paper
    - PDF/PPTX/DOCX URLs → download and extract with document_processor
    - Google Docs/Slides/Sheets → export and extract
    - Regular web pages → trafilatura extraction
    
    v1.0.5: Now triggers background image extraction for multimodal content.
    v1.1.1: Added document URL detection for PDFs, PPTX, Google Docs, ArXiv.
    """
    try:
        from storage.source_store import source_store
        from services.rag_engine import rag_engine  # still used by the ArXiv branch below
        from agents.tools import extract_page_metadata_tool
        import trafilatura
        import asyncio

        # INFO-level so extension captures are VISIBLE in backend.log. The rest of this module
        # logs via print()/logger.debug — print() is swallowed (no terminal in the bundled app)
        # and debug is below INFO, so extension traffic looked invisible in a `tail` even though
        # it routes through here (user report 2026-07-24). This one line makes it observable.
        logger.info(f"[browser] capture: {(request.url or '')[:120]} → notebook={request.notebook_id}")

        # Auto-detect YouTube URLs and redirect to YouTube capture pipeline
        # YouTube pages yield garbage from DOM extraction — transcript is what we need
        import re
        if re.search(r'(youtube\.com/(watch|shorts/|live/|embed/|v/)|youtu\.be/)', request.url or ""):
            print(f"[BROWSER] YouTube URL detected in page capture, redirecting to YouTube pipeline")
            yt_request = YouTubeCaptureRequest(
                video_url=request.url,
                notebook_id=request.notebook_id,
                include_transcript=True
            )
            return await capture_youtube(yt_request)
        
        # Auto-detect ArXiv URLs → download and extract the actual PDF
        if re.search(r'arxiv\.org/(abs|html|pdf)/', request.url or ""):
            print(f"[BROWSER] ArXiv URL detected, downloading PDF: {request.url}")
            from services.web_scraper import web_scraper
            scrape_result = await web_scraper._scrape_arxiv_pdf(request.url)
            if scrape_result.get("success") and scrape_result.get("text"):
                text = scrape_result["text"]
                arxiv_title = scrape_result.get("title", request.title)
                word_count = len(text.split())
                char_count = len(text)
                reading_time = max(1, word_count // 200)
                
                # Score + ingest
                from agents.curator import curator
                curator_scoring = await curator.score_user_item(
                    notebook_id=request.notebook_id,
                    title=arxiv_title, content=text[:3000],
                    url=request.url, source_type="pdf", user_weight_bonus=1.5,
                )
                source_id = str(uuid.uuid4())
                await source_store.create(
                    notebook_id=request.notebook_id, filename=arxiv_title,
                    metadata={
                        "id": source_id, "notebook_id": request.notebook_id,
                        "type": "pdf", "format": "pdf", "url": request.url,
                        "title": arxiv_title, "filename": arxiv_title,
                        "content": text, "word_count": word_count,
                        "char_count": char_count, "characters": char_count,
                        "reading_time_minutes": reading_time,
                        "capture_type": "arxiv_pdf", "status": "processing",
                        "chunks": 0, "created_at": datetime.now().isoformat(),
                        "user_provided": True, "curator_scoring": curator_scoring,
                        "topics": curator_scoring.get("topics", []),
                        "importance": curator_scoring.get("importance", "medium"),
                    },
                )
                rag_result = await rag_engine.ingest_document(
                    notebook_id=request.notebook_id, source_id=source_id,
                    text=text, filename=arxiv_title, source_type="pdf",
                )
                chunks = rag_result.get("chunks", 0) if rag_result else 0
                await source_store.update(request.notebook_id, source_id, {
                    "chunks": chunks, "status": "completed", "content": text,
                })
                await notify_source_updated({
                    "notebook_id": request.notebook_id,
                    "source_id": source_id, "status": "completed", "chunks": chunks,
                })
                try:
                    from services.auto_tagger import auto_tagger
                    await auto_tagger.tag_source_in_notebook(
                        request.notebook_id, source_id, arxiv_title, text[:3000],
                    )
                except Exception as _e:
                    logger.debug(f"[browser] {type(_e).__name__}: {_e}")
                try:
                    log_document_captured(request.notebook_id, request.url, arxiv_title, "arxiv_pdf")
                except Exception as _e:
                    logger.debug(f"[browser] {type(_e).__name__}: {_e}")
                # Record token savings from PDF content vs web search
                try:
                    from services.rag_metrics import rag_metrics
                    rag_metrics.record_token_savings(text)
                except Exception as e:
                    print(f"[BROWSER] Could not record token savings: {e}")
                
                print(f"[BROWSER] ArXiv PDF captured: {arxiv_title} ({word_count} words, {chunks} chunks)")
                return CaptureResponse(
                    success=True, source_id=source_id, title=arxiv_title,
                    word_count=word_count, reading_time_minutes=reading_time,
                    key_concepts=curator_scoring.get("topics", []),
                )
            else:
                print(f"[BROWSER] ArXiv PDF extraction failed, falling back to page capture")
        
        # Auto-detect document URLs (PDF, PPTX, DOCX, etc.)
        doc_type = _is_document_url(request.url)
        if doc_type:
            print(f"[BROWSER] Document URL detected ({doc_type}): {request.url}")
            return await _capture_remote_document(
                request.url, request.notebook_id, request.title, background_tasks,
            )
        
        # Auto-detect Google Docs/Slides/Sheets
        google_export_url = _is_google_doc_url(request.url)
        if google_export_url:
            print(f"[BROWSER] Google Doc detected: {request.url} → export: {google_export_url}")
            return await _capture_remote_document(
                google_export_url, request.notebook_id, request.title, background_tasks,
            )
        
        # Extract the article text. The extension sends BOTH its own extraction and the
        # page HTML; trafilatura on the HTML is the more robust extractor, so it wins
        # when it yields MORE text — but only then. It used to win on any result over
        # 100 chars, which meant a long page whose HTML had been truncated by the
        # extension's size cap could be ingested PARTIAL while the fuller text sat
        # unused in `request.content`. Same class of silent content loss as the
        # chunking fault in READFIRST/done/chunking-duplication.md.
        extension_content = request.content.strip() if request.content else ""
        content = extension_content
        metadata: dict = {}

        if request.html_content:
            loop = asyncio.get_event_loop()

            # Both of these are CPU-bound and off-thread, so they overlap.
            def extract_with_trafilatura(html):
                return trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=True,
                    no_fallback=False,  # Use fallback extractors if main fails
                    favor_precision=False  # Favor recall - get more content
                )

            extracted, metadata = await asyncio.gather(
                loop.run_in_executor(None, extract_with_trafilatura, request.html_content),
                extract_page_metadata_tool.ainvoke({
                    "html_content": request.html_content,
                    "url": request.url,
                }),
                return_exceptions=True,
            )
            if isinstance(extracted, BaseException):
                logger.warning(f"[browser] trafilatura failed: {extracted}")
                extracted = None
            if not isinstance(metadata, dict):
                # Covers both a raised exception (gather returns it) and a tool wrapper
                # that serialised the result into something else — `metadata.get(...)`
                # feeds the title and reading time below and must not be the thing that
                # fails a capture.
                if isinstance(metadata, BaseException):
                    logger.warning(f"[browser] metadata extraction failed (non-critical): {metadata}")
                metadata = {}

            trafilatura_content = (extracted or "").strip()
            if len(trafilatura_content) > len(extension_content):
                content = trafilatura_content
                logger.info(
                    f"[browser] trafilatura won: {len(content.split())} words "
                    f"(extension had {len(extension_content.split())})"
                )
            elif extension_content:
                logger.info(
                    f"[browser] keeping the extension's extraction: "
                    f"{len(extension_content.split())} words "
                    f"(trafilatura had {len(trafilatura_content.split())})"
                )

        word_count = len(content.split()) if content else 0
        char_count = len(content) if content else 0
        
        if word_count < 10:
            print(f"[BROWSER] Capture rejected: content too short ({word_count} words) for {request.url}")
            return CaptureResponse(
                success=False,
                title=request.title,
                word_count=word_count,
                reading_time_minutes=0,
                error=f"Page content is empty or too short ({word_count} words). This may be a JavaScript-rendered page that requires the page to fully load, or a page that blocks content extraction."
            )
        
        source_id = str(uuid.uuid4())
        print(f"[BROWSER] Capturing page: {request.url} ({word_count} words)")

        # Detect the correct source type from the URL so YouTube videos /
        # arxiv papers captured via the extension are labeled consistently
        # (not as generic WEB).
        from services.web_scraper import web_scraper as _ws
        if _ws._is_youtube_url(request.url):
            resolved_source_type = "youtube"
        elif _ws._is_arxiv_url(request.url):
            resolved_source_type = "arxiv"
        else:
            resolved_source_type = "web"

        # Record token savings from scraping vs web search
        try:
            from services.rag_metrics import rag_metrics
            rag_metrics.record_token_savings(content)
        except Exception as e:
            print(f"[BROWSER] Could not record token savings: {e}")
        
        # Calculate reading time
        reading_time = metadata.get("reading_time_minutes", max(1, word_count // 200))
        
        # Use metadata title if available and better than request title
        # This fixes Medium and other sites where document.title is generic but og:title has the article title
        best_title = request.title
        metadata_title = metadata.get("title", "")
        if metadata_title and len(metadata_title) > 5:
            # Prefer metadata title if request title is too generic (site name only)
            generic_titles = ["medium", "linkedin", "twitter", "facebook", "youtube", "substack", "reddit"]
            request_lower = request.title.lower().strip() if request.title else ""
            if not request.title or request_lower in generic_titles or len(request_lower) < 10:
                best_title = metadata_title
                print(f"[BROWSER] Using metadata title: '{best_title}' (request was: '{request.title}')")
        
        # Extract content_date from title + early content
        content_date = None
        try:
            content_date = extract_content_date(best_title, content[:800] if content else "")
            if not content_date and metadata.get("date"):
                content_date = extract_content_date("", metadata["date"])
        except Exception as _e:
            logger.debug(f"[browser] {type(_e).__name__}: {_e}")
        
        # Create source with initial status + Curator scoring metadata
        source_data = {
            "id": source_id,
            "notebook_id": request.notebook_id,
            "type": resolved_source_type,
            "format": resolved_source_type,
            "url": request.url,
            "title": best_title,
            "filename": best_title,
            "content": content,
            "word_count": word_count,
            "char_count": char_count,
            "characters": char_count,
            "reading_time_minutes": reading_time,
            "meta_tags": metadata,
            "capture_type": request.capture_type,
            "status": "processing",
            "chunks": 0,
            "created_at": datetime.now().isoformat(),
            "user_provided": True,
            # summary / key_concepts / curator_scoring / topics / entities /
            # importance are filled in by _finish_web_capture_background.
            # Depth+1 expansion: persist the outgoing links the extension
            # extracted so the user can later choose which to follow.
            # Stored as a list of dicts so JSON serialisation is trivial.
            # depth=0 means "this is the root capture, not the result of an
            # expansion"; parent_source_id stays unset for root captures.
            "outbound_links": [link.model_dump() for link in (request.outbound_links or [])],
            "depth": 0,
            "parent_source_id": None,
        }
        if content_date:
            source_data["content_date"] = content_date
        
        await source_store.create(
            notebook_id=request.notebook_id,
            filename=best_title,
            metadata=source_data
        )
        
        # Everything expensive happens AFTER the response: the RAG ingest and the three
        # LLM calls (curator scoring, auto-tagging, the page summary) used to sit in
        # front of it, which is why capturing a long article took minutes. Same fast
        # path as api/web.py::quick_add. The image pass is queued from in there, once
        # the ingest it appends to has actually completed.
        background_tasks.add_task(
            _finish_web_capture_background,
            notebook_id=request.notebook_id,
            source_id=source_id,
            content=content,
            title=best_title,
            url=request.url,
            source_type=resolved_source_type,
            html_content=request.html_content,
        )

        logger.info(
            f"[browser] captured {best_title[:80]!r} ({word_count} words) — "
            f"enriching in background"
        )
        try:
            log_document_captured(request.notebook_id, request.url, best_title, "web_capture")
        except Exception as _e:
            logger.debug(f"[browser] {type(_e).__name__}: {_e}")
        return CaptureResponse(
            success=True,
            source_id=source_id,
            title=best_title,
            word_count=word_count,
            reading_time_minutes=reading_time,
            status="processing",
        )
        
    except Exception as e:
        import traceback
        print(f"[BROWSER] Capture failed for {request.url}: {e}")
        traceback.print_exc()
        return CaptureResponse(
            success=False,
            title=request.title,
            word_count=0,
            reading_time_minutes=0,
            error=str(e)
        )


@router.post("/capture/selection", response_model=CaptureResponse)
async def capture_selection(request: SelectionCaptureRequest, background_tasks: BackgroundTasks):
    """
    Capture selected text from a page.

    Selections are HIGH-VALUE user signals - the user explicitly identified
    this content as important. We score through Curator with a 2.0x weight
    bonus to heavily influence future learning and discovery.

    Like /browser/capture, the curator call and the ingest happen AFTER the response.
    A selection is short, so the wait was shorter — but a context menu that spins for
    an LLM call is still the wrong shape, and leaving one capture endpoint synchronous
    while the other is not is how the two drift apart.
    """
    try:
        from storage.source_store import source_store

        source_id = str(uuid.uuid4())
        word_count = len(request.selected_text.split())
        char_count = len(request.selected_text)
        reading_time = max(1, word_count // 200)
        
        print(f"[BROWSER] Selection capture: {word_count} words from {request.url}")
        
        # Record token savings from selection vs web search
        try:
            from services.rag_metrics import rag_metrics
            rag_metrics.record_token_savings(request.selected_text)
        except Exception as e:
            print(f"[BROWSER] Could not record token savings: {e}")
        
        # Create source; Curator scores it in the background with a 2.0x weight
        # (deliberate highlight = the strongest signal the user gives us).
        source_data = {
            "id": source_id,
            "notebook_id": request.notebook_id,
            "type": "web_selection",
            "format": "web",
            "url": request.url,
            "title": f"Selection from: {request.title}",
            "filename": f"Selection: {request.title[:50]}",
            "content": request.selected_text,
            "context": request.context,
            "word_count": word_count,
            "char_count": char_count,
            "characters": char_count,
            "reading_time_minutes": reading_time,
            "capture_type": "selection",
            "status": "processing",
            "chunks": 0,
            "created_at": datetime.now().isoformat(),
            "user_provided": True,
            "is_highlight": True,
            # Selections are always high importance; the curator's own scoring,
            # topics and entities land from the background task.
            "importance": "high",
        }

        await source_store.create(
            notebook_id=request.notebook_id,
            filename=f"Selection: {request.title[:50]}",
            metadata=source_data
        )

        background_tasks.add_task(
            _finish_web_capture_background,
            notebook_id=request.notebook_id,
            source_id=source_id,
            content=request.selected_text,
            title=f"Selection: {request.title[:50]}",
            url=request.url,
            source_type="web",
            html_content=None,          # no page HTML on a selection → no image pass
            user_weight_bonus=2.0,      # a deliberate highlight is the strongest signal
            curator_source_type="highlight",
            summarize=False,            # a highlight is already the summary
        )

        try:
            log_document_captured(request.notebook_id, request.url, f"Selection: {request.title}", "web_selection")
        except Exception as _e:
            logger.debug(f"[browser] {type(_e).__name__}: {_e}")
        return CaptureResponse(
            success=True,
            source_id=source_id,
            title=f"Selection from: {request.title}",
            word_count=word_count,
            reading_time_minutes=reading_time,
            status="processing",
        )

    except Exception as e:
        return CaptureResponse(
            success=False,
            title=request.title,
            word_count=0,
            reading_time_minutes=0,
            error=str(e)
        )


@router.post("/capture/youtube", response_model=CaptureResponse)
async def capture_youtube(request: YouTubeCaptureRequest):
    """Capture a YouTube video with transcript.
    
    Uses web_scraper's existing YouTube support (YouTubeTranscriptApi + oEmbed)
    to fetch transcript and video metadata, then indexes into the notebook.
    """
    try:
        from services.web_scraper import web_scraper
        from storage.source_store import source_store
        from services.rag_engine import rag_engine
        
        # Use web_scraper's YouTube extraction (handles ID parsing, transcript, errors)
        scrape_result = await web_scraper._scrape_youtube(request.video_url)
        
        if not scrape_result.get("success"):
            error_msg = scrape_result.get("error", "YouTube extraction failed")
            print(f"[BROWSER] YouTube scrape failed: {error_msg}")
            return CaptureResponse(
                success=False,
                title="YouTube Video",
                word_count=0,
                reading_time_minutes=0,
                error=error_msg
            )
        
        title = scrape_result.get("title", "YouTube Video")
        transcript = scrape_result.get("text", "")
        
        # Build content: title + transcript
        source_id = str(uuid.uuid4())
        content = f"Title: {title}\n\n"
        if transcript:
            content += f"Transcript:\n{transcript}"
        else:
            content += "(No transcript available)"
        
        word_count = len(content.split())
        char_count = len(content)
        reading_time = max(1, word_count // 200)
        
        print(f"[BROWSER] YouTube capture: '{title}' ({word_count} words)")
        
        # Record token savings from transcript vs web search
        try:
            from services.rag_metrics import rag_metrics
            rag_metrics.record_token_savings(content)
        except Exception as e:
            print(f"[BROWSER] Could not record token savings: {e}")
        
        # Score through Curator for learning
        from agents.curator import curator
        curator_scoring = await curator.score_user_item(
            notebook_id=request.notebook_id,
            title=title,
            content=content[:3000],
            url=request.video_url,
            source_type="youtube",
            user_weight_bonus=1.5
        )
        
        # Create source with initial status
        source_data = {
            "id": source_id,
            "notebook_id": request.notebook_id,
            "type": "youtube",
            "format": "youtube",
            "url": request.video_url,
            "title": title,
            "filename": title,
            "content": content,
            "word_count": word_count,
            "char_count": char_count,
            "characters": char_count,
            "reading_time_minutes": reading_time,
            "capture_type": "youtube",
            "status": "processing",
            "chunks": 0,
            "created_at": datetime.now().isoformat(),
            "user_provided": True,
            "curator_scoring": curator_scoring,
            "topics": curator_scoring.get("topics", []),
            "entities": curator_scoring.get("entities", []),
            "importance": curator_scoring.get("importance", "medium")
        }
        
        await source_store.create(
            notebook_id=request.notebook_id,
            filename=title,
            metadata=source_data
        )
        
        # Index in RAG
        rag_result = await rag_engine.ingest_document(
            notebook_id=request.notebook_id,
            source_id=source_id,
            text=content,
            filename=title,
            source_type="youtube"
        )
        
        # Update source with RAG results
        chunks = rag_result.get("chunks", 0) if rag_result else 0
        await source_store.update(request.notebook_id, source_id, {
            "chunks": chunks,
            "status": "completed",
            "content": content,
        })
        
        # Auto-tag the source (non-fatal)
        try:
            from services.auto_tagger import auto_tagger
            await auto_tagger.tag_source_in_notebook(
                request.notebook_id, source_id,
                title, content[:3000]
            )
        except Exception as tag_err:
            print(f"[BROWSER] Auto-tagging YouTube failed (non-fatal): {tag_err}")
        
        # Notify frontend via WebSocket to refresh notebook counts
        await notify_source_updated({
            "notebook_id": request.notebook_id,
            "source_id": source_id,
            "status": "completed",
            "chunks": chunks
        })
        
        try:
            log_document_captured(request.notebook_id, request.video_url, title, "youtube")
        except Exception as _e:
            logger.debug(f"[browser] {type(_e).__name__}: {_e}")
        return CaptureResponse(
            success=True,
            source_id=source_id,
            title=title,
            word_count=word_count,
            reading_time_minutes=reading_time,
            key_concepts=curator_scoring.get("topics", [])
        )
        
    except Exception as e:
        import traceback
        print(f"[BROWSER] YouTube capture failed: {e}")
        traceback.print_exc()
        return CaptureResponse(
            success=False,
            title="YouTube Video",
            word_count=0,
            reading_time_minutes=0,
            error=str(e)
        )


@router.post("/metadata")
async def extract_metadata(request: MetadataExtractionRequest):
    """Extract metadata from HTML content."""
    try:
        from agents.tools import extract_page_metadata_tool
        
        result = await extract_page_metadata_tool.ainvoke({
            "html_content": request.html_content,
            "url": request.url
        })
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/summarize")
async def summarize_content(request: SummarizeRequest):
    """Summarize page content and extract key concepts."""
    try:
        from agents.tools import _summarize_page_impl
        
        # Call implementation directly (not through @tool wrapper which can serialize result)
        result = await _summarize_page_impl(
            content=request.content,
            url=request.url
        )
        
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════
# Extension-Assisted Scrape Queue (Phase 3 fallback)
# ═════════════════════════════════════════════════════════════════════

class ExtensionScrapeResult(BaseModel):
    """Result from extension scraping a page."""
    content: str
    title: str = ""
    html: Optional[str] = None


@router.get("/pending-scrapes")
async def get_pending_scrapes():
    """Extension polls this to find URLs the backend needs scraped."""
    from services.browser_scrape_queue import browser_scrape_queue
    pending = browser_scrape_queue.get_pending()
    return {"requests": pending, "count": len(pending)}


@router.post("/scrape-result/{request_id}")
async def submit_scrape_result(request_id: str, result: ExtensionScrapeResult):
    """Extension submits scraped page content for a pending request."""
    from services.browser_scrape_queue import browser_scrape_queue
    found = browser_scrape_queue.submit_result(
        request_id=request_id,
        content=result.content,
        title=result.title,
        html=result.html or "",
    )
    if not found:
        raise HTTPException(status_code=404, detail="Scrape request not found or expired")
    return {"success": True}
