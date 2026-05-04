"""Capture API — session management, image upload, and WebSocket push.

Endpoints:
    POST   /capture/session           Create a new capture session
    GET    /capture/session/{id}      Session status
    POST   /capture/upload/{id}       Receive image from iPhone
    GET    /capture/page/{id}         Serve the mobile capture page
    WS     /capture/ws/{id}           WebSocket: push OCR results to Mac
    DELETE /capture/session/{id}      Close session, cleanup
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import (
    APIRouter,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse

from services.capture_queue import CapturePageResult, CaptureQueue

logger = logging.getLogger(__name__)
capture_router = APIRouter()

# ── Session storage (in-memory, lives for the process lifetime) ──────────────

MAX_PAGES_PER_SESSION = 100
SESSION_TIMEOUT_SECS = 30 * 60  # 30 minutes


@dataclass
class CapturePageInfo:
    """Metadata for a single captured page."""
    path: str
    status: str = "received"   # received → classifying → processing → complete | error
    content_type: str = ""
    received_at: float = field(default_factory=time.time)


@dataclass
class CaptureSession:
    """A phone capture session."""
    session_id: str
    token: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    pages: List[CapturePageInfo] = field(default_factory=list)
    queue: Optional[CaptureQueue] = field(default=None, repr=False)
    ws_connections: List[WebSocket] = field(default_factory=list, repr=False)
    temp_dir: str = ""

    def __post_init__(self):
        if not self.expires_at:
            self.expires_at = self.created_at + SESSION_TIMEOUT_SECS

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def touch(self):
        """Reset the expiration timer."""
        self.expires_at = time.time() + SESSION_TIMEOUT_SECS


_sessions: Dict[str, CaptureSession] = {}


def _get_session(session_id: str, token: Optional[str] = None) -> CaptureSession:
    """Retrieve and validate a session."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.is_expired:
        _cleanup_session(session_id)
        raise HTTPException(status_code=410, detail="Session expired")
    if token is not None and not secrets.compare_digest(session.token, token):
        raise HTTPException(status_code=401, detail="Invalid token")
    session.touch()
    return session


def _cleanup_session(session_id: str):
    """Remove session and clean up temp files."""
    session = _sessions.pop(session_id, None)
    if session:
        if session.queue:
            asyncio.create_task(session.queue.stop())
        if session.temp_dir and os.path.isdir(session.temp_dir):
            shutil.rmtree(session.temp_dir, ignore_errors=True)
        logger.info(f"[capture] Session {session_id[:8]} cleaned up")


# ── Process function (OCR via scan_pipeline) ─────────────────────────────────

async def _process_capture(file_path: str) -> tuple[str, str]:
    """OCR a single captured image. Returns (content_type, ocr_text).

    Uses the scan_pipeline's classify_and_ocr to auto-detect content type
    (document, math, whiteboard, drawing, photo) and route to the
    appropriate mode-specific prompt.
    """
    from services.scan_pipeline import scan_pipeline

    return await scan_pipeline.classify_and_ocr(file_path)


# ── API Routes ───────────────────────────────────────────────────────────────

@capture_router.post("/session")
async def create_session():
    """Create a new capture session and start the capture server."""
    from services.capture_server import (
        get_capture_url,
        start_capture_server,
        is_running,
    )

    session_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)

    # Create temp directory for captured images
    temp_dir = tempfile.mkdtemp(prefix=f"localbook_capture_{session_id[:8]}_")

    session = CaptureSession(
        session_id=session_id,
        token=token,
        temp_dir=temp_dir,
    )

    # Set up the processing queue
    queue = CaptureQueue(session_id=session_id)

    async def _on_page_complete(result: CapturePageResult):
        """Push result to all WebSocket subscribers."""
        msg = {
            "type": f"page_{result.status}",
            "page_index": result.page_index,
            "content_type": result.content_type,
            "ocr_text": result.ocr_text,
            "error": result.error,
        }
        dead = []
        for ws in session.ws_connections:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            session.ws_connections.remove(ws)

        # Update page status
        if result.page_index < len(session.pages):
            page = session.pages[result.page_index]
            page.status = result.status
            page.content_type = result.content_type

    queue.subscribe(_on_page_complete)
    queue.start(_process_capture)
    session.queue = queue
    _sessions[session_id] = session

    # Start capture server if not running
    if not is_running():
        await start_capture_server()

    capture_url = get_capture_url(session_id, token)

    logger.info(
        f"[capture] Session {session_id[:8]} created — "
        f"capture URL: {capture_url}"
    )

    return {
        "session_id": session_id,
        "token": token,
        "capture_url": capture_url,
        "ws_url": f"ws://localhost:8000/capture/ws/{session_id}",
    }


@capture_router.get("/session/{session_id}")
async def get_session_status(
    session_id: str,
    authorization: Optional[str] = Header(None),
):
    """Get session status and page count."""
    token = _extract_token(authorization)
    session = _get_session(session_id, token)
    return {
        "session_id": session.session_id,
        "page_count": len(session.pages),
        "stats": session.queue.stats if session.queue else {},
        "pages": [
            {
                "index": i,
                "status": p.status,
                "content_type": p.content_type,
            }
            for i, p in enumerate(session.pages)
        ],
    }


@capture_router.post("/upload/{session_id}")
async def upload_capture(
    session_id: str,
    image: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
    t: Optional[str] = Query(None),
):
    """Receive a captured image from the iPhone."""
    token = _extract_token(authorization) or t
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    session = _get_session(session_id, token)

    if len(session.pages) >= MAX_PAGES_PER_SESSION:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {MAX_PAGES_PER_SESSION} pages per session",
        )

    # Save the uploaded image
    page_index = len(session.pages)
    ext = os.path.splitext(image.filename or "image.jpg")[1] or ".jpg"
    file_name = f"page_{page_index:03d}{ext}"
    file_path = os.path.join(session.temp_dir, file_name)

    contents = await image.read()
    with open(file_path, "wb") as f:
        f.write(contents)

    page = CapturePageInfo(path=file_path, status="received")
    session.pages.append(page)

    # Notify WebSocket subscribers about the new page
    msg = {
        "type": "page_received",
        "page_index": page_index,
        "file_name": file_name,
    }
    for ws in session.ws_connections:
        try:
            await ws.send_json(msg)
        except Exception:
            pass

    # Enqueue for processing
    if session.queue:
        await session.queue.enqueue(file_path, page_index)

    logger.info(
        f"[capture] Session {session_id[:8]} received page {page_index} "
        f"({len(contents) / 1024:.0f}KB)"
    )

    return {
        "status": "received",
        "page_index": page_index,
        "file_name": file_name,
    }


@capture_router.get("/page/{session_id}")
async def serve_capture_page(
    session_id: str,
    t: Optional[str] = Query(None),
):
    """Serve the mobile capture HTML page."""
    session = _get_session(session_id, t)

    # Read the static capture page
    static_dir = Path(__file__).parent.parent / "static"
    capture_html_path = static_dir / "capture.html"
    if not capture_html_path.exists():
        raise HTTPException(status_code=500, detail="Capture page not found")

    html = capture_html_path.read_text()

    # Inject session config into the HTML
    config = json.dumps({
        "sessionId": session.session_id,
        "token": session.token,
        "uploadUrl": f"/capture/upload/{session.session_id}",
    })
    html = html.replace("__CAPTURE_CONFIG__", config)

    return HTMLResponse(content=html)


@capture_router.websocket("/ws/{session_id}")
async def capture_websocket(
    websocket: WebSocket,
    session_id: str,
    t: Optional[str] = Query(None),
):
    """WebSocket for pushing OCR results to the Mac frontend."""
    session = _get_session(session_id, t)

    await websocket.accept()
    session.ws_connections.append(websocket)
    logger.info(
        f"[capture] WebSocket connected for session {session_id[:8]} "
        f"({len(session.ws_connections)} subscribers)"
    )

    try:
        # Send any existing results (in case of reconnection)
        if session.queue:
            for result in session.queue.results:
                await websocket.send_json({
                    "type": f"page_{result.status}",
                    "page_index": result.page_index,
                    "content_type": result.content_type,
                    "ocr_text": result.ocr_text,
                    "error": result.error,
                })

        # Keep connection alive, listen for commands
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "session_complete":
                # Phone signals it's done capturing
                msg = {
                    "type": "session_complete",
                    "stats": session.queue.stats if session.queue else {},
                }
                for ws in session.ws_connections:
                    try:
                        await ws.send_json(msg)
                    except Exception:
                        pass
                break
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in session.ws_connections:
            session.ws_connections.remove(websocket)


@capture_router.delete("/session/{session_id}")
async def close_session(
    session_id: str,
    authorization: Optional[str] = Header(None),
):
    """Close a capture session and clean up resources."""
    token = _extract_token(authorization)
    # Allow closing without token from localhost (Mac frontend)
    session = _get_session(session_id, token if token else None)
    _cleanup_session(session_id)
    return {"status": "closed", "session_id": session_id}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_token(authorization: Optional[str]) -> Optional[str]:
    """Extract Bearer token from Authorization header."""
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return None
