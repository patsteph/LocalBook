"""Fetch a URL for a companion with the address pinned at connect time.

utils/url_guard decides whether a URL may be fetched; this makes the decision
hold. `check_url` resolves the host and checks every address, but the HTTP
client used to resolve AGAIN when it connected — a DNS-rebinding window (the
name answers with a public address for the check and 127.0.0.1 for the
connect) — and it followed redirects without checking them at all, so a public
page answering `302 → http://127.0.0.1:8000/…` reached LocalBook's own API.

Here every TCP connection — the first request and every redirect hop — goes
through `_PinnedBackend.connect_tcp`, which resolves the host, refuses the
connection if ANY address is blocked, and connects to the vetted IP itself. TLS
still verifies the certificate against the hostname (httpcore passes the
origin's host as SNI). Proxies from the environment are ignored (`trust_env`
off), since a proxy would resolve on our behalf.

No browser: Chromium cannot be pinned per request, and a page's scripts could
reach the LAN. A page that only renders with JavaScript returns less text.
"""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any, Dict, Optional

import httpcore
import httpx

from utils.url_guard import _address_is_blocked, _resolve, check_url

MAX_BYTES = 20 * 1024 * 1024
MAX_REDIRECTS = 5
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class BlockedAddress(httpcore.ConnectError):
    """The host resolved (now) to an address a companion may not reach."""


def vet_addresses(host: str, addresses) -> str:
    """The address to connect to, or raise. Every address must be public —
    one inward answer is the whole attack, so it is not enough to pick a good one."""
    if not addresses:
        raise BlockedAddress(f"{host} does not resolve")
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        why = _address_is_blocked(ip)
        if why:
            raise BlockedAddress(f"{host} resolves to {addr}, and {why}")
    v4 = [a for a in addresses if ":" not in a]
    return (v4 or list(addresses))[0]


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        from httpcore._backends.auto import AutoBackend

        self._inner = AutoBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        addresses = [str(literal)] if literal else await asyncio.to_thread(_resolve, host)
        ip = vet_addresses(host, addresses)
        return await self._inner.connect_tcp(ip, port, timeout=timeout, local_address=local_address,
                                             socket_options=socket_options)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise BlockedAddress("unix sockets are not fetchable")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def pinned_client(timeout: float = 30.0) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(retries=0)
    transport._pool._network_backend = _PinnedBackend()      # every connection goes through it
    return httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=True,
                             max_redirects=MAX_REDIRECTS, timeout=timeout,
                             headers={"User-Agent": USER_AGENT})


async def fetch(url: str) -> Dict[str, Any]:
    """{url, title, text} or {error}. Pages via trafilatura, documents via the
    app's own document extractor — the same extraction the scraper uses."""
    verdict = check_url(url)
    if not verdict:
        return {"url": url, "error": f"refused: {verdict.reason}"}
    try:
        async with pinned_client() as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    return {"url": url, "error": f"HTTP {resp.status_code}"}
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        return {"url": url, "error": "the page is larger than 20 MB"}
                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                final = str(resp.url)
    except BlockedAddress as exc:
        return {"url": url, "error": f"refused: {exc}"}
    except httpx.TooManyRedirects:
        return {"url": url, "error": "too many redirects"}
    except httpx.HTTPError as exc:
        # httpx re-raises httpcore errors as its own (`raise … from exc`): a refusal
        # from the pinned backend arrives as httpx.ConnectError with ours as the cause.
        if isinstance(exc.__cause__, BlockedAddress):
            return {"url": url, "error": f"refused: {exc.__cause__}"}
        return {"url": url, "error": f"{type(exc).__name__}: {exc}"[:200]}
    return await _extract(url, final, bytes(body), ctype)


async def _extract(url: str, final: str, body: bytes, ctype: str) -> Dict[str, Any]:
    if ctype in ("text/html", "application/xhtml+xml", "") or ctype.startswith("text/"):
        import trafilatura

        html = body.decode("utf-8", errors="replace")
        text = await asyncio.to_thread(trafilatura.extract, html, include_comments=False,
                                       include_tables=True, no_fallback=False)
        meta = await asyncio.to_thread(trafilatura.extract_metadata, html)
        if not text and ctype.startswith("text/plain"):
            text = html
        if not text:
            return {"url": url, "error": "no readable text on the page (it may need JavaScript)"}
        return {"url": url, "final_url": final, "title": getattr(meta, "title", None) or url, "text": text}
    from services.document_processor import document_processor

    name = final.rstrip("/").rsplit("/", 1)[-1] or "document"
    text = await document_processor._extract_text(body, name)
    if not text:
        return {"url": url, "error": f"could not extract text from {ctype or 'the document'}"}
    return {"url": url, "final_url": final, "title": name, "text": text}
