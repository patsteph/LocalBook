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
import string
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

# Max dimension for images sent to the vision model. iPhone photos are
# 12MP (4032×3024) which is massive overkill for OCR. Downscaling to
# 2048px max cuts base64 payload from ~7MB to ~600KB and processing
# time from 2+ min to ~30s.
VISION_MAX_DIM = 2048


def _downscale_image(file_path: str) -> str:
    """Downscale an image if it exceeds VISION_MAX_DIM on any side.

    Overwrites the file in-place. Returns the path unchanged.
    """
    try:
        from PIL import Image
        img = Image.open(file_path)
        w, h = img.size
        if max(w, h) <= VISION_MAX_DIM:
            return file_path

        ratio = VISION_MAX_DIM / max(w, h)
        new_w, new_h = int(w * ratio), int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)

        # Preserve EXIF orientation but drop the rest
        img.save(file_path, quality=85, optimize=True)
        logger.info(
            f"[capture] Downscaled {w}×{h} → {new_w}×{new_h} "
            f"({os.path.getsize(file_path) / 1024:.0f}KB)"
        )
    except Exception as e:
        logger.warning(f"[capture] Image downscale failed (proceeding with original): {e}")
    return file_path

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
_short_codes: Dict[str, str] = {}  # short_code → session_id


def _generate_short_code() -> str:
    """Generate a unique 6-character alphanumeric code for QR URLs.

    Uses uppercase + digits only (no ambiguous chars: 0/O, 1/I/L) for
    maximum QR density reduction. 30^6 = ~729M combinations.
    """
    alphabet = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 30 chars, no ambiguous
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        if code not in _short_codes:
            return code


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
        # Clean up short code mapping
        codes_to_remove = [c for c, sid in _short_codes.items() if sid == session_id]
        for c in codes_to_remove:
            _short_codes.pop(c, None)
        logger.info(f"[capture] Session {session_id[:8]} cleaned up")


# ── Process function (OCR via scan_pipeline) ─────────────────────────────────

async def _process_capture(file_path: str) -> tuple[str, str]:
    """OCR a single captured image. Returns (content_type, ocr_text).

    Uses the enhanced document prompt directly (it handles math, tables,
    diagrams, color annotations in a single pass). Skipping the classification
    step cuts processing from 3 LLM calls to 2 per page.
    """
    from services.scan_pipeline import scan_pipeline

    ocr_text = await scan_pipeline._ocr_one_page(file_path, mode="document")
    return ("document", ocr_text)


# ── API Routes ───────────────────────────────────────────────────────────────

@capture_router.post("/session")
async def create_session():
    """Create a new capture session and start the capture server."""
    from services.capture_server import (
        get_capture_url,
        get_short_url,
        start_capture_server,
        is_running,
    )

    session_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    short_code = _generate_short_code()

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
    _short_codes[short_code] = session_id

    # Start capture server if not running
    if not is_running():
        await start_capture_server()

    capture_url = get_capture_url(session_id, token)
    short_url = get_short_url(short_code)

    logger.info(
        f"[capture] Session {session_id[:8]} created — "
        f"short code: {short_code}, URL: {short_url}"
    )

    return {
        "session_id": session_id,
        "token": token,
        "capture_url": capture_url,
        "short_url": short_url,
        "short_code": short_code,
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

    # Downscale large images before OCR (iPhone photos are 12MP → ~7MB base64)
    _downscale_image(file_path)

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

        # Keep connection alive — the Mac frontend only listens, it never
        # sends JSON commands.  We use receive_text() instead of
        # receive_json() so that WebSocket pings/pongs and close frames
        # are handled correctly without raising JSONDecodeError.
        while True:
            try:
                msg = await websocket.receive_text()
                # Try to parse as JSON command (future-proofing)
                try:
                    data = json.loads(msg)
                    if data.get("type") == "session_complete":
                        notify_msg = {
                            "type": "session_complete",
                            "stats": session.queue.stats if session.queue else {},
                        }
                        for ws in session.ws_connections:
                            try:
                                await ws.send_json(notify_msg)
                            except Exception:
                                pass
                        break
                except (json.JSONDecodeError, ValueError):
                    pass  # Non-JSON frame (ping response, etc.)
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"[capture] WS error for {session_id[:8]}: {e}")
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
