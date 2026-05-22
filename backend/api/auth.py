"""Auth API — token bootstrap for the browser extension.

The Tauri webview reads the per-launch app token directly from the filesystem
via a Tauri command. The extension can't read arbitrary files, so it
calls this endpoint instead.

Security: we only return the token when the Origin header matches the
pinned extension ID (``settings.extension_id``). The Origin header is set
by the browser; JS cannot fake it. A malicious web page that calls
``POST /auth/bootstrap`` from its own context will get its own origin
(e.g. ``https://evil.example``) in the header — which fails the check.

The pinned extension ID is fixed across all installs of OUR extension
(via the manifest ``key`` field — see P0.1d). Another developer can't
publish an extension with the same ID without our private key.

Why POST (not GET): Chrome omits the Origin header on "simple" GETs from
extension service workers, which makes them indistinguishable from a GET
where Origin was never set. POST with ``Content-Type: application/json``
forces a "non-simple" CORS request, which the browser MUST send Origin on.
The body is empty (``{}``) — it's POST purely for the CORS classification.

P0.1e (2026-05-15).
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config import settings
from utils.token import get_app_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class BootstrapResponse(BaseModel):
    token: str


@router.post("/bootstrap", response_model=BootstrapResponse)
async def bootstrap_token(request: Request):
    """Return the app token if the caller is our pinned extension.

    Returns 403 if the Origin header doesn't match the expected
    ``chrome-extension://<extension_id>`` value, or 503 if the token
    hasn't been initialized yet (server still booting).
    """
    expected_origin = f"chrome-extension://{settings.extension_id}"
    origin = request.headers.get("origin", "")
    if origin != expected_origin:
        logger.warning(
            f"[auth] /bootstrap rejected: origin={origin!r} expected={expected_origin!r}"
        )
        raise HTTPException(status_code=403, detail="Origin not permitted")

    token = get_app_token()
    if token is None:
        raise HTTPException(status_code=503, detail="auth not initialized")
    return BootstrapResponse(token=token)
