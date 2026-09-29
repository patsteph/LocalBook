"""MCP server at `/mcp` — how agents reach LocalBook.

LB-2 of the v2.5.0 plan. Jocasta (companion 2) is blocked on this: it is how a
Hermes-based agent asks "what did I save about X?" without being handed the
SQLite file.

**Every tool is a thin wrapper.** No retrieval logic lives here. `search_notebooks`
calls `cross_notebook_search`, `ask_notebook` calls `rag_engine.query`. If an
answer is wrong, it is wrong in the app too, which is the only way to keep one
implementation of each capability (the Centralization Rule).

Four things guard the surface:

  * **Loopback only.** The listener is bound locally and the middleware refuses
    anything whose client address is not loopback. An MCP endpoint reachable
    from the LAN is a remote shell into the user's notebooks.
  * **A companion key with scope `mcp`** (LB-0). The meeting recorder holds
    `llm` and gets 403 here, which is the entire point of scopes.
  * **Everything is bounded.** Every tool takes a `k` or `max_chars` with a
    sane default and a hard ceiling, so an agent cannot ask for the whole
    corpus in one call and blow up its own context.
  * **Every call is audited** (`companion_calls`), with arguments HASHED — see
    `companion_audit`.

⚠️ **Integration note, corrected 2026-09-29.** The plan says to mount
`mcp.streamable_http_app()` and start `mcp.session_manager.run()` in the
lifespan. Installed fastmcp (2.14.3) has NEITHER attribute — that recipe is for
the older low-level `mcp` SDK. In fastmcp 2.x, `mcp.http_app()` returns a
Starlette app that carries its OWN lifespan, and that lifespan is what starts
the session manager. It must be chained into `main.py`'s lifespan or the mounted
app fails at runtime — which is the failure the plan warned about, reached by a
different route. `lifespan_context()` below is that chain.
"""

from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MCP_SCOPE = "mcp"

# How long a companion waits for the model before being told to come back.
# The plan's number. Shorter than a user would tolerate, because an agent can
# retry and a user cannot.
BUSY_TIMEOUT_SECONDS = 5.0
BUSY_RETRY_AFTER = 15

# Hard ceilings. A default is a courtesy; a ceiling is what stops one call from
# returning a notebook's entire text.
MAX_K = 50
MAX_CHARS = 50_000
DEFAULT_CHARS = 8_000

# Set by the auth middleware, read by the tools for the audit log.
_caller: contextvars.ContextVar = contextvars.ContextVar("mcp_caller", default=None)


def current_caller():
    return _caller.get()


def _set_caller(identity):
    _caller.set(identity)


# ── bounds ──────────────────────────────────────────────────────────────────


def _bounded_k(k: Any, default: int) -> int:
    try:
        k = int(k)
    except (TypeError, ValueError):
        return default
    return max(1, min(k, MAX_K))


def _bounded_chars(n: Any, default: int = DEFAULT_CHARS) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return default
    return max(200, min(n, MAX_CHARS))


# ── busy handling ───────────────────────────────────────────────────────────


async def _model_is_available(model: str) -> bool:
    """Wait up to BUSY_TIMEOUT_SECONDS for the model lane to have room.

    Acquired at BACKGROUND priority — a companion must never jump ahead of the
    person actually sitting there — then released immediately, because the call
    that follows acquires the lane itself and the main lane has a cap of 1.

    That leaves a small window where the lane is taken between the probe and the
    call. Stated rather than hidden: the cost is that the companion waits inside
    the query instead of getting a clean `busy`, which is the mild failure. The
    alternative — holding the lane across the call — is a deadlock.
    """
    try:
        from services.llm_runtime import PRIORITY_BACKGROUND, _semaphore_for_model

        lane = _semaphore_for_model(model)
        await asyncio.wait_for(lane.acquire(PRIORITY_BACKGROUND), timeout=BUSY_TIMEOUT_SECONDS)
        lane.release()
        return True
    except asyncio.TimeoutError:
        return False
    except Exception as exc:
        # A probe that cannot run must not block the call it was protecting.
        logger.warning("[mcp] could not probe the model lane: %s", exc)
        return True


def _busy(tool: str) -> Dict[str, Any]:
    return {
        "status": "busy",
        "retry_after": BUSY_RETRY_AFTER,
        "detail": f"LocalBook's model is in use; {tool} did not run. Retry shortly.",
    }


# ── auth ────────────────────────────────────────────────────────────────────


def _is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "testclient")


class CompanionAuthMiddleware:
    """Pure-ASGI gate in front of the mounted MCP app.

    ASGI rather than BaseHTTPMiddleware because the MCP transport streams, and
    BaseHTTPMiddleware buffers — it would break SSE-shaped responses.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = (scope.get("client") or (None, None))[0]
        if not _is_loopback(client):
            await _deny(send, 403, "LocalBook's MCP endpoint is reachable from this machine only.")
            return

        token = ""
        for raw_name, raw_value in scope.get("headers") or []:
            if raw_name.lower() == b"authorization":
                value = raw_value.decode("latin-1")
                if value.lower().startswith("bearer "):
                    token = value[7:].strip()
                break

        from services.companions import verify_companion_key

        identity = verify_companion_key(token)
        if identity is None:
            await _deny(
                send, 401,
                "Invalid companion key. Connect this tool from Settings → Companions.",
            )
            return
        if not identity.has(MCP_SCOPE):
            _audit_denied(identity.companion_id, "connect", f"missing scope {MCP_SCOPE}")
            await _deny(
                send, 403,
                f"This companion key does not carry the '{MCP_SCOPE}' scope. "
                f"Reconnect {identity.companion_id} from Settings → Companions.",
            )
            return

        _set_caller(identity)
        await self.app(scope, receive, send)


async def _deny(send, status: int, detail: str) -> None:
    import json

    body = json.dumps({"detail": detail}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})


def _audit_denied(companion_id: str, tool: str, detail: str) -> None:
    try:
        from services import companion_audit

        companion_audit.record(
            companion_id=companion_id, tool=tool,
            outcome=companion_audit.OUTCOME_DENIED, detail=detail,
        )
    except Exception:
        pass


# ── audit wrapper ───────────────────────────────────────────────────────────


@asynccontextmanager
async def _audited(tool: str, args: Dict[str, Any]):
    """One audit row per tool call, whatever the outcome."""
    from services import companion_audit

    identity = current_caller()
    companion_id = getattr(identity, "companion_id", "unknown")
    with companion_audit.CallTimer(companion_id=companion_id, tool=tool, args=args) as timer:
        yield timer


# ── the server ──────────────────────────────────────────────────────────────


def build_server():
    """Construct the FastMCP server and register the tools."""
    from fastmcp import FastMCP

    mcp = FastMCP(
        "LocalBook",
        instructions=(
            "LocalBook is the user's private, offline research library. Search and read "
            "their notebooks here rather than guessing, and cite the sources you are given. "
            "Everything is local to this machine."
        ),
    )

    read_only = {"readOnlyHint": True}

    @mcp.tool(annotations=read_only)
    async def list_notebooks() -> Dict[str, Any]:
        """List the user's notebooks, with how many sources each holds."""
        async with _audited("list_notebooks", {}):
            from storage.notebook_store import notebook_store

            books = await notebook_store.list()
            return {
                "notebooks": [
                    {
                        "id": nb.get("id"),
                        "title": nb.get("title") or nb.get("name"),
                        "description": nb.get("description"),
                        "source_count": nb.get("source_count"),
                    }
                    for nb in books
                ]
            }

    @mcp.tool(annotations=read_only)
    async def search_notebooks(
        query: str,
        notebook_ids: Optional[List[str]] = None,
        k: int = 8,
    ) -> Dict[str, Any]:
        """Search across notebooks and return matching passages with attribution.

        Use this to FIND things. Use ask_notebook when you want a written answer.
        """
        k = _bounded_k(k, 8)
        args = {"query": query, "notebook_ids": notebook_ids, "k": k}
        async with _audited("search_notebooks", args):
            from services.cross_notebook_search import cross_notebook_search

            result = await cross_notebook_search.search(
                query=query, notebook_ids=notebook_ids, top_k=k
            )
            return {
                "results": result.get("results", []),
                "notebooks_searched": result.get("notebooks_searched", 0),
            }

    @mcp.tool(annotations=read_only)
    async def ask_notebook(
        question: str,
        notebook_id: Optional[str] = None,
        top_k: int = 4,
    ) -> Dict[str, Any]:
        """Ask a question and get a written, cited answer from the user's own material.

        Returns {"status": "busy", "retry_after": N} when the model is in use —
        the person sitting at the machine comes first.
        """
        top_k = _bounded_k(top_k, 4)
        args = {"question": question, "notebook_id": notebook_id, "top_k": top_k}
        async with _audited("ask_notebook", args) as timer:
            from config import settings
            from services import companion_audit

            if not await _model_is_available(settings.main_model):
                timer.outcome = companion_audit.OUTCOME_BUSY
                return _busy("ask_notebook")

            target = notebook_id
            if not target:
                from storage.notebook_store import notebook_store

                books = await notebook_store.list()
                if not books:
                    return {"error": "there are no notebooks yet"}
                if len(books) > 1:
                    return {
                        "error": "more than one notebook exists; name one",
                        "notebooks": [
                            {"id": b.get("id"), "title": b.get("title") or b.get("name")}
                            for b in books
                        ],
                    }
                target = books[0].get("id")

            from services.rag_engine import rag_engine

            result = await rag_engine.query(
                notebook_id=target, question=question, top_k=top_k
            )
            return {
                "answer": result.get("answer"),
                "sources": result.get("sources", []),
                "notebook_id": target,
            }

    @mcp.tool(annotations=read_only)
    async def get_source(
        source_id: str,
        offset: int = 0,
        max_chars: int = DEFAULT_CHARS,
    ) -> Dict[str, Any]:
        """Read one source's full text, a page at a time.

        Paginated on purpose: sources are whole documents, and returning one
        entire would consume the agent's context on a single call.
        """
        max_chars = _bounded_chars(max_chars)
        offset = max(0, int(offset or 0))
        args = {"source_id": source_id, "offset": offset, "max_chars": max_chars}
        async with _audited("get_source", args):
            from storage.source_store import source_store

            source = await source_store.get(source_id)
            if not source:
                return {"error": f"no source with id {source_id}"}

            content = source.get("content") or ""
            chunk = content[offset : offset + max_chars]
            next_offset = offset + len(chunk)
            return {
                "source_id": source_id,
                "title": source.get("title"),
                "type": source.get("type"),
                "url": source.get("url"),
                "total_chars": len(content),
                "offset": offset,
                "text": chunk,
                "next_offset": next_offset if next_offset < len(content) else None,
            }

    @mcp.tool(annotations=read_only)
    async def events_since(
        cursor: Optional[str] = None,
        kinds: Optional[List[str]] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """What has happened in LocalBook since you last asked.

        Poll this from your heartbeat. Pass back the `cursor` you were given; the
        first call can omit it. Covers both agent activity (@curator, @collector,
        @research) and notebook activity (sources added, chats, quizzes).

        Ordering is roughly by time and is not a total order — see
        services/event_feed. Call `event_kinds` to discover what `kinds` accepts.
        """
        limit = _bounded_k(limit, 50)
        args = {"cursor": cursor, "kinds": kinds, "limit": limit}
        async with _audited("events_since", args):
            import asyncio as _asyncio

            from services import event_feed

            # SQLite reads across two databases — off the event loop, per the
            # repo's standing rule about sync work on a hot path.
            return await _asyncio.to_thread(
                event_feed.events_since, cursor=cursor, kinds=kinds, limit=limit
            )

    @mcp.tool(annotations=read_only)
    async def event_kinds() -> Dict[str, Any]:
        """The event kinds actually present, for filtering `events_since`."""
        async with _audited("event_kinds", {}):
            import asyncio as _asyncio

            from services import event_feed

            return {"kinds": await _asyncio.to_thread(event_feed.known_kinds)}

    @mcp.tool(annotations=read_only)
    async def web_search(query: str, n: int = 5) -> Dict[str, Any]:
        """Search the web. Use this only when the user's own notebooks do not have it."""
        n = _bounded_k(n, 5)
        async with _audited("web_search", {"query": query, "n": n}):
            from services.web_fallback import web_fallback

            results = await web_fallback.search_web(query, max_results=n)
            return {"results": results or []}

    @mcp.tool(annotations=read_only)
    async def fetch_page(url: str, max_chars: int = DEFAULT_CHARS) -> Dict[str, Any]:
        """Fetch a web page and return its readable text.

        Refuses anything that is not a public http/https address — see
        utils/url_guard.
        """
        max_chars = _bounded_chars(max_chars)
        async with _audited("fetch_page", {"url": url, "max_chars": max_chars}) as timer:
            from services import companion_audit
            from utils.url_guard import check_url

            verdict = check_url(url)
            if not verdict:
                timer.outcome = companion_audit.OUTCOME_DENIED
                timer.detail = verdict.reason
                return {"error": f"refused: {verdict.reason}"}

            # web_scraper is the app's own fetch path — it already handles
            # YouTube, PDFs and arXiv as well as ordinary pages, so a companion
            # gets exactly what the app would get, not a second implementation.
            from services.web_scraper import web_scraper

            scraped = await web_scraper.scrape_urls([url])
            first = (scraped or [{}])[0] or {}
            if first.get("error"):
                timer.outcome = companion_audit.OUTCOME_ERROR
                timer.detail = str(first.get("error"))[:200]
                return {"url": url, "error": first.get("error")}

            text = first.get("content") or ""
            return {
                "url": url,
                "title": first.get("title"),
                "text": text[:max_chars],
                "truncated": len(text) > max_chars,
            }

    return mcp


# ── mounting ────────────────────────────────────────────────────────────────

_app = None
_mcp = None


def get_app():
    """The ASGI app to mount at `/mcp`, built once."""
    global _app, _mcp
    if _app is None:
        _mcp = build_server()
        inner = _mcp.http_app(path="/")
        _app = CompanionAuthMiddleware(inner)
        _app.inner = inner  # kept so the lifespan below can reach it
    return _app


@asynccontextmanager
async def lifespan_context(app):
    """Run the MCP app's own lifespan inside main.py's.

    This is what starts the session manager. Without it the mounted app accepts
    a connection and then fails at runtime — see the module docstring for why
    the plan's `session_manager.run()` recipe does not apply to fastmcp 2.x.
    """
    mounted = get_app()
    async with mounted.inner.router.lifespan_context(mounted.inner):
        yield
