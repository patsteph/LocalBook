"""
Browser Extension Scrape Queue — Phase 3 Fallback

When the backend's Playwright + httpx pipeline fails to scrape a URL
(bot protection, login walls, Cloudflare, etc.), this module queues
the URL for the LocalBook browser extension to scrape instead.

Flow:
  1. Backend calls `request_extension_scrape(url)` → opens URL in default
     browser, creates an asyncio.Event, and waits up to TIMEOUT seconds.
  2. Extension polls `GET /browser/pending-scrapes` every few seconds,
     picks up the request, extracts the page via its content script.
  3. Extension posts result back to `POST /browser/scrape-result/{id}`.
  4. The asyncio.Event fires, `request_extension_scrape` returns the text.

If the extension isn't running or the user doesn't have a browser open,
the request simply times out and returns None — no worse than before.
"""

import asyncio
import logging
import uuid
import webbrowser
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# How long (seconds) the backend waits for the extension to scrape.
SCRAPE_TIMEOUT = 45

# How long a request lingers before being garbage-collected.
REQUEST_TTL = 120


@dataclass
class ScrapeRequest:
    id: str
    url: str
    created_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: Optional[Dict] = None


class BrowserScrapeQueue:
    """In-memory queue for extension-assisted scrape requests."""

    def __init__(self):
        self._pending: Dict[str, ScrapeRequest] = {}

    # ── Backend side ──────────────────────────────────────────────────

    async def request_scrape(self, url: str, open_browser: bool = True) -> Optional[Dict]:
        """Queue a URL for extension scraping. Returns result dict or None on timeout."""
        req_id = str(uuid.uuid4())[:12]
        req = ScrapeRequest(id=req_id, url=url)
        self._pending[req_id] = req

        logger.info(f"[ExtScrape] Queued {req_id} for extension scrape: {url}")

        if open_browser:
            try:
                webbrowser.open(url)
                logger.info(f"[ExtScrape] Opened URL in default browser: {url}")
            except Exception as e:
                logger.warning(f"[ExtScrape] Could not open browser: {e}")

        try:
            await asyncio.wait_for(req.event.wait(), timeout=SCRAPE_TIMEOUT)
        except asyncio.TimeoutError:
            logger.info(f"[ExtScrape] Timeout ({SCRAPE_TIMEOUT}s) waiting for extension to scrape {url}")
            self._pending.pop(req_id, None)
            return None

        result = req.result
        self._pending.pop(req_id, None)
        return result

    # ── Extension side (called by API endpoints) ─────────────────────

    def get_pending(self) -> List[Dict]:
        """Return all pending scrape requests (for extension polling)."""
        self._gc()
        return [
            {"id": r.id, "url": r.url}
            for r in self._pending.values()
            if r.result is None
        ]

    def submit_result(self, request_id: str, content: str, title: str = "", html: str = "") -> bool:
        """Extension submits scraped content. Returns True if request was found."""
        req = self._pending.get(request_id)
        if not req:
            logger.warning(f"[ExtScrape] Result submitted for unknown request {request_id}")
            return False

        word_count = len(content.split()) if content else 0
        char_count = len(content) if content else 0

        req.result = {
            "success": bool(content and char_count > 50),
            "url": req.url,
            "title": title or req.url,
            "text": content,
            "word_count": word_count,
            "char_count": char_count,
            "html": html,
            "scrape_method": "extension",
        }
        if not req.result["success"]:
            req.result["error"] = f"Extension returned insufficient content ({char_count} chars)"

        logger.info(f"[ExtScrape] Result received for {request_id}: {char_count} chars, success={req.result['success']}")
        req.event.set()
        return True

    # ── Housekeeping ─────────────────────────────────────────────────

    def _gc(self):
        """Remove stale requests older than REQUEST_TTL seconds."""
        try:
            now = asyncio.get_event_loop().time()
        except RuntimeError:
            return
        stale = [rid for rid, r in self._pending.items() if now - r.created_at > REQUEST_TTL]
        for rid in stale:
            self._pending.pop(rid, None)

    @property
    def pending_count(self) -> int:
        return sum(1 for r in self._pending.values() if r.result is None)


# Singleton
browser_scrape_queue = BrowserScrapeQueue()
