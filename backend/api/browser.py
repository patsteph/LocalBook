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


class PageCaptureRequest(BaseModel):
    """Request to capture a web page."""
    url: str
    title: str
    content: str  # Text content of page
    notebook_id: str
    html_content: Optional[str] = None  # Raw HTML for metadata extraction
    capture_type: str = "page"  # page, selection, youtube, pdf


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
        except Exception:
            pass

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
        except Exception:
            pass

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
        from services.rag_engine import rag_engine
        from agents.tools import summarize_page_tool, extract_page_metadata_tool
        import trafilatura
        import asyncio
        
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
                except Exception:
                    pass
                try:
                    log_document_captured(request.notebook_id, request.url, arxiv_title, "arxiv_pdf")
                except Exception:
                    pass
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
        
        # Try to extract content using trafilatura (same as web research panel)
        # This is MUCH more robust than the extension's document.body.innerText
        content = ""
        if request.html_content:
            print(f"[BROWSER] Using trafilatura for robust extraction: {request.url}")
            loop = asyncio.get_event_loop()
            
            # Extract main content using trafilatura (runs in thread pool)
            def extract_with_trafilatura(html):
                return trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=True,
                    no_fallback=False,  # Use fallback extractors if main fails
                    favor_precision=False  # Favor recall - get more content
                )
            
            extracted = await loop.run_in_executor(None, extract_with_trafilatura, request.html_content)
            
            if extracted and len(extracted.strip()) > 100:
                content = extracted.strip()
                print(f"[BROWSER] Trafilatura extracted {len(content.split())} words")
            else:
                # Fallback to extension-provided content
                print("[BROWSER] Trafilatura returned insufficient content, using extension content")
                content = request.content.strip() if request.content else ""
        else:
            # No HTML provided, use extension content directly
            content = request.content.strip() if request.content else ""
        
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
        
        # Score through Curator for learning (user-provided content gets bonus weight)
        from agents.curator import curator
        curator_scoring = await curator.score_user_item(
            notebook_id=request.notebook_id,
            title=request.title,
            content=content,
            url=request.url,
            source_type="web",
            user_weight_bonus=1.5  # User explicitly captured this
        )
        print(f"[BROWSER] Curator scored: relevance={curator_scoring['relevance_score']:.2f}, topics={curator_scoring['topics']}")
        
        # Extract metadata if HTML provided (non-critical, continue on failure)
        metadata = {}
        try:
            if request.html_content:
                metadata = await extract_page_metadata_tool.ainvoke({
                    "html_content": request.html_content,
                    "url": request.url
                })
        except Exception as meta_err:
            print(f"[BROWSER] Metadata extraction failed (non-critical): {meta_err}")
        
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
        
        # Summarize content and extract key concepts (non-critical, continue on failure)
        summary = ""
        key_concepts = []
        try:
            summary_result = await summarize_page_tool.ainvoke({
                "content": content,
                "url": request.url
            })
            summary = summary_result.get("summary", "")
            key_concepts = summary_result.get("key_concepts", [])
        except Exception as sum_err:
            print(f"[BROWSER] Summarization failed (non-critical): {sum_err}")
        
        # Extract content_date from title + early content
        content_date = None
        try:
            content_date = extract_content_date(best_title, content[:800] if content else "")
            if not content_date and metadata.get("date"):
                content_date = extract_content_date("", metadata["date"])
        except Exception:
            pass
        
        # Create source with initial status + Curator scoring metadata
        source_data = {
            "id": source_id,
            "notebook_id": request.notebook_id,
            "type": "web",
            "format": "web",
            "url": request.url,
            "title": best_title,
            "filename": best_title,
            "content": content,
            "summary": summary,
            "key_concepts": key_concepts,
            "word_count": word_count,
            "char_count": char_count,
            "characters": char_count,
            "reading_time_minutes": reading_time,
            "meta_tags": metadata,
            "capture_type": request.capture_type,
            "status": "processing",
            "chunks": 0,
            "created_at": datetime.now().isoformat(),
            # Curator scoring for learning
            "user_provided": True,
            "curator_scoring": curator_scoring,
            "topics": curator_scoring.get("topics", []),
            "entities": curator_scoring.get("entities", []),
            "importance": curator_scoring.get("importance", "medium")
        }
        if content_date:
            source_data["content_date"] = content_date
        
        await source_store.create(
            notebook_id=request.notebook_id,
            filename=best_title,
            metadata=source_data
        )
        
        # Index in RAG
        rag_result = await rag_engine.ingest_document(
            notebook_id=request.notebook_id,
            source_id=source_id,
            text=content,
            filename=best_title,
            source_type="web"
        )
        
        # Update source with RAG results (same as document_processor does)
        chunks = rag_result.get("chunks", 0) if rag_result else 0
        await source_store.update(request.notebook_id, source_id, {
            "chunks": chunks,
            "status": "completed",
            "content": content,
        })
        
        # Auto-tag the source (non-fatal)
        try:
            from services.auto_tagger import auto_tagger
            await auto_tagger.tag_source_in_notebook(request.notebook_id, source_id, best_title, content[:3000])
        except Exception as tag_err:
            print(f"[BROWSER] Auto-tagging failed (non-fatal): {tag_err}")
        
        # Notify frontend via WebSocket to refresh notebook counts
        await notify_source_updated({
            "notebook_id": request.notebook_id,
            "source_id": source_id,
            "status": "completed",
            "chunks": chunks
        })
        
        # v1.0.5: Trigger background image extraction for multimodal content
        if request.html_content and len(request.html_content) > 1000:
            background_tasks.add_task(
                process_web_images_background,
                notebook_id=request.notebook_id,
                source_id=source_id,
                html_content=request.html_content,
                base_url=request.url,
                page_title=best_title
            )
            print(f"[BROWSER] Queued background image extraction for: {best_title}")
        
        print(f"[BROWSER] Successfully captured: {best_title} ({chunks} chunks)")
        try:
            log_document_captured(request.notebook_id, request.url, best_title, "web_capture")
        except Exception:
            pass
        return CaptureResponse(
            success=True,
            source_id=source_id,
            title=best_title,
            word_count=word_count,
            reading_time_minutes=reading_time,
            summary=summary[:500] if summary else None,
            key_concepts=key_concepts
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
async def capture_selection(request: SelectionCaptureRequest):
    """
    Capture selected text from a page.
    
    Selections are HIGH-VALUE user signals - the user explicitly identified
    this content as important. We score through Curator with a 2.0x weight
    bonus to heavily influence future learning and discovery.
    """
    try:
        from storage.source_store import source_store
        from services.rag_engine import rag_engine
        from agents.curator import curator
        
        source_id = str(uuid.uuid4())
        word_count = len(request.selected_text.split())
        char_count = len(request.selected_text)
        reading_time = max(1, word_count // 200)
        
        print(f"[BROWSER] Selection capture: {word_count} words from {request.url}")
        
        # Score through Curator with HIGH weight - selections are explicit user interest
        # 2.0x bonus because user took deliberate action to highlight this
        curator_scoring = await curator.score_user_item(
            notebook_id=request.notebook_id,
            title=f"Selection: {request.title}",
            content=request.selected_text,
            url=request.url,
            source_type="highlight",  # Mark as highlight for special treatment
            user_weight_bonus=2.0  # Double weight for explicit selection
        )
        print(f"[BROWSER] Selection scored: relevance={curator_scoring['relevance_score']:.2f}, topics={curator_scoring['topics']}")
        
        # Create source with Curator scoring metadata
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
            # Curator scoring for learning
            "user_provided": True,
            "is_highlight": True,
            "curator_scoring": curator_scoring,
            "topics": curator_scoring.get("topics", []),
            "entities": curator_scoring.get("entities", []),
            "importance": "high"  # Selections are always high importance
        }
        
        await source_store.create(
            notebook_id=request.notebook_id,
            filename=f"Selection: {request.title[:50]}",
            metadata=source_data
        )
        
        # Index in RAG
        rag_result = await rag_engine.ingest_document(
            notebook_id=request.notebook_id,
            source_id=source_id,
            text=request.selected_text,
            filename=f"Selection: {request.title[:50]}",
            source_type="web"
        )
        
        # Update source with RAG results
        chunks = rag_result.get("chunks", 0) if rag_result else 0
        await source_store.update(request.notebook_id, source_id, {
            "chunks": chunks,
            "status": "completed",
            "content": request.selected_text,
        })
        
        # Auto-tag the source (non-fatal)
        try:
            from services.auto_tagger import auto_tagger
            await auto_tagger.tag_source_in_notebook(request.notebook_id, source_id, request.title, request.selected_text[:3000])
        except Exception as tag_err:
            print(f"[BROWSER] Auto-tagging selection failed (non-fatal): {tag_err}")
        
        # Notify frontend via WebSocket to refresh notebook counts
        await notify_source_updated({
            "notebook_id": request.notebook_id,
            "source_id": source_id,
            "status": "completed",
            "chunks": chunks
        })
        
        try:
            log_document_captured(request.notebook_id, request.url, f"Selection: {request.title}", "web_selection")
        except Exception:
            pass
        return CaptureResponse(
            success=True,
            source_id=source_id,
            title=f"Selection from: {request.title}",
            word_count=word_count,
            reading_time_minutes=reading_time
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
        except Exception:
            pass
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
