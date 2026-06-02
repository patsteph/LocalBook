"""Auth middleware — verifies X-LocalBook-Token on every request.

Two modes (controlled by the ``enforce`` constructor flag):

- **enforce=False (warn-only):** log a warning when the header is missing
  or invalid, but pass the request through. Kept available for diagnosing
  future regressions.
- **enforce=True:** return 401 on missing/invalid header. Production mode
  after P0.1f Stage 2 shipped 2026-05-21.

Exempt paths bypass auth entirely:
  - ``/`` + ``/health`` → readiness probes (Tauri shell, smoke tests)
  - ``/health/portal`` → static admin HTML; the served HTML monkey-patches
    fetch with a per-launch token (see api/health_portal.py::get_portal)
  - ``/auth/bootstrap`` → extension token-bootstrap (origin-checked
    inside the route; no chicken-and-egg with the token)
  - ``/favicon.ico`` → browser default fetch, no JS context

OPTIONS preflight requests always bypass auth so CORS preflight can
negotiate successfully before the actual (token-bearing) request fires.

Note on middleware ordering: this middleware must sit INSIDE CORS so 401
responses are wrapped with the Access-Control-Allow-Origin header. With
CORS innermost (which we had during P0.1f Stage 2 first attempt) the
browser silently blocked 401 responses and the webview's 401-retry never
fired. See backend/main.py for the correct add_middleware ordering.

P0.1f (2026-05-15).
"""
import hmac
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from utils.token import get_app_token

logger = logging.getLogger(__name__)


# Paths that bypass auth entirely. /auth/bootstrap is origin-checked
# inside its own handler.
EXEMPT_PATHS = frozenset({
    "/",
    "/health",
    "/health/portal",
    "/auth/bootstrap",
    "/favicon.ico",
})

# Path prefixes that bypass auth. Used for media/file endpoints the browser
# fetches via plain <audio>/<img>/<a download> tags — those can't attach
# custom headers, so we accept that they have lower protection. These are
# all UUID-keyed user-owned content; an attacker would need to know the
# specific UUID to fetch anything meaningful.
EXEMPT_PREFIXES = (
    "/audio/download/",
    # Same reasoning as /audio/download/ — the canvas <video> element loads
    # via plain HTML5 src and can't attach the X-LocalBook-Token header.
    # UUID-keyed user content; an attacker would need to know the video_id.
    "/video/stream/",
)

HEADER_NAME = "x-localbook-token"


class AppTokenAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, enforce: bool = False):
        super().__init__(app)
        self.enforce = enforce

    async def dispatch(self, request, call_next):
        # OPTIONS preflight always passes through — CORS middleware handles
        # it (we sit inside CORS in the middleware stack).
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if path in EXEMPT_PATHS or any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)

        expected = get_app_token()
        if expected is None:
            if self.enforce:
                return JSONResponse(
                    {"detail": "auth not initialized"}, status_code=503
                )
            logger.warning("[auth] token not initialized (warn-only)")
            return await call_next(request)

        provided = request.headers.get(HEADER_NAME, "")
        if not provided or not hmac.compare_digest(expected, provided):
            logger.warning(
                f"[auth] {request.method} {path}: "
                f"{'invalid token' if provided else 'missing header'} "
                f"({'enforce' if self.enforce else 'warn-only'})"
            )
            if self.enforce:
                return JSONResponse(
                    {"detail": "missing or invalid app token"},
                    status_code=401,
                )
        return await call_next(request)
