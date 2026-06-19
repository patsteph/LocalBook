"""Capture Server — lightweight HTTP server for phone-based document capture.

Runs on 0.0.0.0:8443 (plain HTTP) to be reachable from the iPhone on the
same Wi-Fi network. Only the /capture/* routes are served on this port;
the main API stays on 127.0.0.1:8000 (never network-exposed).

The server starts on-demand when the first capture session is created and
auto-stops after 30 minutes of no active sessions.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from utils.tasks import safe_create_task

logger = logging.getLogger(__name__)

_capture_app: Optional[FastAPI] = None
_server: Optional[uvicorn.Server] = None
_server_task: Optional[asyncio.Task] = None
_idle_task: Optional[asyncio.Task] = None

CAPTURE_PORT = 8443
IDLE_TIMEOUT_SECS = 30 * 60  # 30 minutes


def get_local_ip() -> str:
    """Get the machine's local network IP address."""
    try:
        # Create a UDP socket and "connect" to a public IP (no data sent).
        # This causes the OS to bind to the correct interface.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _build_capture_app() -> FastAPI:
    """Create a minimal FastAPI app that mounts only capture routes."""
    from api.capture import capture_router

    app = FastAPI(
        title="LocalBook Capture",
        docs_url=None,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(capture_router, prefix="/capture")

    # Short-code redirect: /c/{code} → /capture/page/{session_id}?t={token}
    # Keeps QR code URLs at ~38 chars for compact Version 2 QR codes.
    @app.get("/c/{code}")
    async def short_code_redirect(code: str):
        from api.capture import _short_codes, _sessions
        from fastapi.responses import RedirectResponse

        session_id = _short_codes.get(code.upper())
        if not session_id:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Invalid or expired capture code")
        session = _sessions.get(session_id)
        if not session or session.is_expired:
            from fastapi import HTTPException
            raise HTTPException(status_code=410, detail="Session expired")
        return RedirectResponse(
            url=f"/capture/page/{session.session_id}?t={session.token}",
            status_code=302,
        )

    return app


async def start_capture_server():
    """Start the capture HTTP server (idempotent)."""
    global _capture_app, _server, _server_task

    if _server_task and not _server_task.done():
        logger.debug("[capture-server] Already running")
        return

    _capture_app = _build_capture_app()
    config = uvicorn.Config(
        app=_capture_app,
        host="0.0.0.0",
        port=CAPTURE_PORT,
        log_level="warning",
        # No TLS — using <input capture> instead of getUserMedia
    )
    _server = uvicorn.Server(config)
    _server_task = safe_create_task(_server.serve())

    local_ip = get_local_ip()
    logger.info(
        f"[capture-server] Started on http://{local_ip}:{CAPTURE_PORT}"
    )


async def stop_capture_server():
    """Stop the capture HTTP server."""
    global _server, _server_task, _idle_task

    if _idle_task:
        _idle_task.cancel()
        _idle_task = None

    if _server:
        _server.should_exit = True
        if _server_task:
            try:
                await asyncio.wait_for(_server_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        _server = None
        _server_task = None
        logger.info("[capture-server] Stopped")


def is_running() -> bool:
    """Check if the capture server is currently running."""
    return _server_task is not None and not _server_task.done()


def get_capture_url(session_id: str, token: str) -> str:
    """Generate the full capture URL for a session."""
    local_ip = get_local_ip()
    return f"http://{local_ip}:{CAPTURE_PORT}/capture/page/{session_id}?t={token}"


def get_short_url(short_code: str) -> str:
    """Generate a compact capture URL using the 6-char short code.

    These short URLs produce Version 2 QR codes (25×25 grid) instead of
    Version 5 (37×37), making 80px inline QR codes reliably scannable.
    """
    local_ip = get_local_ip()
    return f"http://{local_ip}:{CAPTURE_PORT}/c/{short_code}"
