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

# Register the HEIF/HEIC opener so PIL can decode iPhone Camera uploads.
# iOS Safari uploads HEIC by default for `<input type=file accept="image/*">`,
# and feeding HEIC bytes straight to Ollama crashes the vision runner
# (granite's libpng-based decoder doesn't recognise HEIC magic) — that
# was the root cause of capture flow getting stuck on "processing"
# while PDF ingestion (which never sees HEIC) worked fine. Best-effort:
# if the package is missing we still register Pillow's built-in formats
# and rely on the JPEG fallback below.
try:
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
except Exception as _e:  # pragma: no cover — pillow-heif missing or broken
    logger.warning(f"[capture] pillow-heif unavailable; HEIC uploads will fail to normalize: {_e}")

# Max dimension for images sent to the vision model. iPhone photos are
# 12MP (4032×3024) which is massive overkill for OCR. 2560 px on the
# longer side is the sweet spot for current vision models — enough
# resolution for dense table cells and footnotes, while keeping the
# base64 payload well under 1 MB.
VISION_MAX_DIM = 2560

# JPEG quality for the persisted vision-ready image. 92 keeps small-text
# detail intact (quality 85 introduces ringing artifacts that blur
# characters and cost OCR accuracy). The file-size delta vs 85 is
# ~30% but accuracy gain on dense documents is much larger.
VISION_JPEG_QUALITY = 92


def _normalize_image_for_vision(file_path: str) -> str:
    """Make an uploaded capture image safe for the Ollama vision runner.

    Steps, in order:
      1. Open with PIL (HEIC/HEIF supported via pillow-heif registration above).
      2. Apply EXIF orientation (iPhone photos are rotation-flagged, not
         physically rotated; the vision model has no EXIF parser).
      3. Convert to RGB (drops alpha, normalises CMYK / palette modes).
      4. Downscale so the longer side ≤ VISION_MAX_DIM.
      5. Re-encode in place as **JPEG** regardless of the source format.
         The on-disk extension (.heic/.png/.jpg) is irrelevant — Ollama
         sniffs magic bytes — so we don't bother renaming.

    Overwriting in-place is intentional: every other downstream consumer
    (capture_queue, scan_pipeline) reads the same path, and we want them
    to see the normalised bytes.

    Returns the (unchanged) file path. On any failure we log and leave
    the original file alone so the queue still produces a typed error
    instead of silently dropping the page.
    """
    try:
        from PIL import Image, ImageOps
        with Image.open(file_path) as src:
            img = ImageOps.exif_transpose(src) or src
            if img.mode != "RGB":
                img = img.convert("RGB")

            w, h = img.size
            if max(w, h) > VISION_MAX_DIM:
                ratio = VISION_MAX_DIM / max(w, h)
                new_w, new_h = max(1, int(w * ratio)), max(1, int(h * ratio))
                img = img.resize((new_w, new_h), Image.LANCZOS)
            else:
                new_w, new_h = w, h

            # Force JPEG output — granite/llava/etc. all decode JPEG via
            # stb_image inside Ollama; HEIC/AVIF/WebP do not work.
            img.save(file_path, format="JPEG", quality=VISION_JPEG_QUALITY, optimize=True)

        logger.info(
            f"[capture] Normalized {w}×{h} → {new_w}×{new_h} JPEG "
            f"({os.path.getsize(file_path) / 1024:.0f}KB) src={file_path}"
        )
    except Exception as e:
        logger.warning(
            f"[capture] Image normalization failed; sending raw bytes "
            f"(vision model may reject): {e}"
        )
    return file_path


# Back-compat alias for any external imports of the prior name.
_downscale_image = _normalize_image_for_vision

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
            from utils.tasks import safe_create_task
            safe_create_task(session.queue.stop(), name=f"capture-queue-stop-{session_id[:8]}")
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

    Uses the scan_pipeline to classify the image (document, math, photo, etc.)
    and then performs OCR using the appropriate prompt.
    """
    logger.info(f"[capture] _process_capture entered for {file_path}")
    # Lazy import — first call may take a few seconds while scan_pipeline
    # and its transitive deps load (granite/ollama clients, embeddings, …).
    # We log on either side so a slow first-import is visible instead of
    # looking like the queue is hung.
    from services.scan_pipeline import scan_pipeline
    logger.info(f"[capture] scan_pipeline imported, calling classify_and_ocr")

    content_type, ocr_text = await scan_pipeline.classify_and_ocr(file_path)
    logger.info(
        f"[capture] classify_and_ocr returned: "
        f"content_type={content_type!r}, ocr_chars={len(ocr_text)}"
    )
    return (content_type, ocr_text)


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
            # Typed-error metadata so the frontend can render targeted
            # guidance ("vision model X failed — pick a different one in
            # Settings") instead of a generic backend error toast.
            "error_type": result.error_type,
            "error_model": result.error_model,
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

    # Normalize: HEIC→JPEG, EXIF rotation, RGB, downscale. Without this an
    # iOS HEIC upload would crash the Ollama vision runner with the unhelpful
    # "model runner has unexpectedly stopped" error and the page would hang
    # in "processing" forever (PDF ingestion was unaffected because PDFs
    # never carry HEIC payloads).
    _normalize_image_for_vision(file_path)

    # Capture metadata (C2 + C4): hash for dedup ledger, QR codes for
    # follow-up suggestions. Both are best-effort — failures don't block
    # the OCR pipeline. Run AFTER normalization so the hash is stable
    # across iOS/Android sources (HEIC normalized to JPEG first).
    from services.capture_metadata import (
        compute_image_hash,
        check_dedup,
        record_capture,
        detect_qr_codes,
    )
    image_hash = compute_image_hash(file_path)
    dedup_record = check_dedup(image_hash) if image_hash else None
    qr_codes = detect_qr_codes(file_path)
    if image_hash:
        record_capture(image_hash, file_path)

    page = CapturePageInfo(path=file_path, status="received")
    session.pages.append(page)

    # Notify WebSocket subscribers about the new page, including any QR
    # results and dedup warnings so the iPhone/Mac client can surface them.
    msg = {
        "type": "page_received",
        "page_index": page_index,
        "file_name": file_name,
        "image_hash": image_hash,
        "qr_codes": qr_codes,
    }
    if dedup_record:
        msg["dedup"] = dedup_record
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
        + (f" qr={len(qr_codes)}" if qr_codes else "")
        + (" [dup]" if dedup_record else "")
    )

    return {
        "status": "received",
        "page_index": page_index,
        "file_name": file_name,
        "image_hash": image_hash,
        "qr_codes": qr_codes,
        "duplicate_of": dedup_record,
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
        # Send any existing results (in case of reconnection). The `replay`
        # flag lets the receiver tell a recovery message apart from a fresh
        # one — important because the side-effect of `page_complete` is
        # "insert OCR text into the note", and replaying that side-effect
        # turns a flapping WS into a runaway insertion loop. Receivers
        # SHOULD use the flag (or their own per-page-index dedup) to update
        # status without re-running side-effects.
        if session.queue:
            for result in session.queue.results:
                await websocket.send_json({
                    "type": f"page_{result.status}",
                    "page_index": result.page_index,
                    "content_type": result.content_type,
                    "ocr_text": result.ocr_text,
                    "error": result.error,
                    "error_type": result.error_type,
                    "error_model": result.error_model,
                    "replay": True,
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
