"""Browser Extension API endpoints

API for the LocalBook browser extension to capture pages,
extract metadata, and sync with the main application.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid

router = APIRouter(prefix="/browser", tags=["browser"])


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
        "version": "0.9.0",
        "timestamp": datetime.now().isoformat()
    }


@router.get("/notebooks", response_model=List[NotebookInfo])
async def list_notebooks_for_extension():
    """List notebooks for extension popup selector."""
    try:
        from storage.notebook_store import notebook_store
        from storage.source_store import source_store
        
        notebooks = await notebook_store.list()
        
        result = []
        for nb in notebooks:
            sources = await source_store.list(nb["id"])
            result.append(NotebookInfo(
                id=nb["id"],
                name=nb.get("title", "Untitled"),
                source_count=len(sources)
            ))
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/capture", response_model=CaptureResponse)
async def capture_page(request: PageCaptureRequest):
    """Capture a web page to a notebook with summarization."""
    try:
        from storage.source_store import source_store
        from services.rag_engine import rag_engine
        from agents.tools import summarize_page_tool, extract_page_metadata_tool
        
        source_id = str(uuid.uuid4())
        
        # Extract metadata if HTML provided
        metadata = {}
        if request.html_content:
            metadata = await extract_page_metadata_tool.ainvoke({
                "html_content": request.html_content,
                "url": request.url
            })
        
        # Calculate reading time
        word_count = len(request.content.split())
        reading_time = metadata.get("reading_time_minutes", max(1, word_count // 200))
        
        # Summarize content and extract key concepts
        summary_result = await summarize_page_tool.ainvoke({
            "content": request.content,
            "url": request.url
        })
        
        summary = summary_result.get("summary", "")
        key_concepts = summary_result.get("key_concepts", [])
        
        # Create source
        source_data = {
            "id": source_id,
            "notebook_id": request.notebook_id,
            "type": "web",
            "url": request.url,
            "title": request.title,
            "filename": request.title,
            "content": request.content,
            "summary": summary,
            "key_concepts": key_concepts,
            "word_count": word_count,
            "reading_time_minutes": reading_time,
            "meta_tags": metadata,
            "capture_type": request.capture_type,
            "created_at": datetime.now().isoformat()
        }
        
        await source_store.create(
            notebook_id=request.notebook_id,
            filename=request.title,
            metadata=source_data
        )
        
        # Index in RAG
        await rag_engine.ingest_document(
            notebook_id=request.notebook_id,
            source_id=source_id,
            text=request.content,
            filename=request.title,
            source_type="web"
        )
        
        return CaptureResponse(
            success=True,
            source_id=source_id,
            title=request.title,
            word_count=word_count,
            reading_time_minutes=reading_time,
            summary=summary[:500] if summary else None,
            key_concepts=key_concepts
        )
        
    except Exception as e:
        return CaptureResponse(
            success=False,
            title=request.title,
            word_count=0,
            reading_time_minutes=0,
            error=str(e)
        )


@router.post("/capture/selection", response_model=CaptureResponse)
async def capture_selection(request: SelectionCaptureRequest):
    """Capture selected text from a page."""
    try:
        from storage.source_store import source_store
        from services.rag_engine import rag_engine
        
        source_id = str(uuid.uuid4())
        word_count = len(request.selected_text.split())
        reading_time = max(1, word_count // 200)
        
        # Create source
        source_data = {
            "id": source_id,
            "notebook_id": request.notebook_id,
            "type": "web_selection",
            "url": request.url,
            "title": f"Selection from: {request.title}",
            "filename": f"Selection: {request.title[:50]}",
            "content": request.selected_text,
            "context": request.context,
            "word_count": word_count,
            "reading_time_minutes": reading_time,
            "capture_type": "selection",
            "created_at": datetime.now().isoformat()
        }
        
        await source_store.create(
            notebook_id=request.notebook_id,
            filename=f"Selection: {request.title[:50]}",
            metadata=source_data
        )
        
        # Index in RAG
        await rag_engine.ingest_document(
            notebook_id=request.notebook_id,
            source_id=source_id,
            text=request.selected_text,
            filename=f"Selection: {request.title[:50]}",
            source_type="web"
        )
        
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
    """Capture a YouTube video with transcript."""
    try:
        from api.web import fetch_youtube_transcript, get_youtube_video_info
        from storage.source_store import source_store
        from services.rag_engine import rag_engine
        
        # Get video info
        video_info = await get_youtube_video_info(request.video_url)
        
        # Get transcript if requested
        transcript = ""
        if request.include_transcript:
            transcript = await fetch_youtube_transcript(request.video_url)
        
        source_id = str(uuid.uuid4())
        content = f"Title: {video_info.get('title', '')}\n\n"
        content += f"Channel: {video_info.get('channel', '')}\n\n"
        if transcript:
            content += f"Transcript:\n{transcript}"
        
        word_count = len(content.split())
        reading_time = max(1, word_count // 200)
        
        # Create source
        source_data = {
            "id": source_id,
            "notebook_id": request.notebook_id,
            "type": "youtube",
            "url": request.video_url,
            "title": video_info.get("title", "YouTube Video"),
            "filename": video_info.get("title", "YouTube Video"),
            "content": content,
            "video_info": video_info,
            "word_count": word_count,
            "reading_time_minutes": reading_time,
            "capture_type": "youtube",
            "created_at": datetime.now().isoformat()
        }
        
        await source_store.create(
            notebook_id=request.notebook_id,
            filename=video_info.get("title", "YouTube Video"),
            metadata=source_data
        )
        
        # Index in RAG
        await rag_engine.ingest_document(
            notebook_id=request.notebook_id,
            source_id=source_id,
            text=content,
            filename=video_info.get("title", "YouTube Video"),
            source_type="youtube"
        )
        
        return CaptureResponse(
            success=True,
            source_id=source_id,
            title=video_info.get("title", "YouTube Video"),
            word_count=word_count,
            reading_time_minutes=reading_time
        )
        
    except Exception as e:
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
