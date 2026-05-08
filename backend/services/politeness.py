"""
Politeness — robots.txt + per-domain rate limiting for outgoing-link expansion.

Used by services/link_expander.py before each fetch of a user-selected
outgoing link. Three responsibilities:

1. **robots.txt compliance**: read+cache /robots.txt per domain, refuse to
   fetch URLs the host disallows for our user-agent.
2. **Per-domain rate limit**: respect Crawl-Delay from robots.txt; default
   to 1 request per second per domain when not specified. Implemented as a
   lock+last-request-time table keyed by domain.
3. **Retry-After honor**: when an HTTP fetch returns 429, the caller can
   ask politeness to wait the indicated seconds before allowing another
   request to that domain. Capped at 60s so a misbehaving server can't
   block a queue forever.

Stdlib only (urllib.robotparser, urllib.parse) — no new dependencies per
the Dependency Hygiene rule in CLAUDE.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)


# Single shared User-Agent. Includes a contact URL so site operators who
# spot LocalBook traffic in their logs can find the project. Required by
# many sites' robots.txt rules to identify polite bots.
USER_AGENT = "LocalBook/1.0 (+https://github.com/patsteph/LocalBook)"

# Cache TTL for robots.txt. 1 hour balances freshness against the cost of
# re-fetching for every URL on the same domain in a 20-link expansion.
_ROBOTS_CACHE_TTL_SECONDS = 3600

# Default delay between successive requests to the same domain. Used when
# the site's robots.txt does not specify a Crawl-Delay.
_DEFAULT_CRAWL_DELAY_SECONDS = 1.0

# Maximum delay we'll accept from a Crawl-Delay or Retry-After. A site
# asking for a 10-minute delay would stall the entire expansion job; we
# cap to 60s and log a warning.
_MAX_DELAY_SECONDS = 60.0


# Per-domain robots.txt cache: {domain: (RobotFileParser, fetched_at_ts)}
_robots_cache: Dict[str, Tuple[Optional[RobotFileParser], float]] = {}
_robots_lock = asyncio.Lock()  # protects _robots_cache writes

# Per-domain rate-limit state: {domain: (asyncio.Lock, last_request_ts)}
_rate_state: Dict[str, Tuple[asyncio.Lock, float]] = {}
_rate_state_lock = asyncio.Lock()  # protects _rate_state mutation


def _domain_of(url: str) -> str:
    """Return the lowercased netloc (host[:port]) of a URL.

    Empty string for malformed URLs. Used as the cache + rate-limit key —
    same host with different schemes share a robots.txt by RFC.
    """
    try:
        parsed = urlparse(url)
        return (parsed.netloc or "").lower()
    except Exception:
        return ""


async def _fetch_robots(domain: str) -> Optional[RobotFileParser]:
    """Fetch and parse /robots.txt for a domain. Returns None on any error.

    A None result means "we couldn't determine robots.txt for this site";
    callers treat None as 'permissive' (allow the fetch) rather than
    'forbidden', because a 404 / connection error shouldn't block a
    legitimate fetch attempt. This matches what major crawlers do.
    """
    if not domain:
        return None
    # Try https first (most modern), then http as fallback.
    candidates = [f"https://{domain}/robots.txt", f"http://{domain}/robots.txt"]
    for url in candidates:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                resp = await client.get(url, headers={"User-Agent": USER_AGENT})
                if resp.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
                    return parser
                # 404 = no robots.txt = everything allowed by convention.
                if resp.status_code == 404:
                    return None
        except Exception as e:
            logger.debug(f"[politeness] robots fetch failed for {url}: {e}")
            continue
    return None


async def _get_robots(domain: str) -> Optional[RobotFileParser]:
    """Return a (possibly cached) RobotFileParser for a domain."""
    now = time.time()
    cached = _robots_cache.get(domain)
    if cached is not None:
        parser, fetched_at = cached
        if now - fetched_at < _ROBOTS_CACHE_TTL_SECONDS:
            return parser
    # Cache miss / expired — fetch fresh.
    async with _robots_lock:
        # Double-check after acquiring the lock — another coroutine may
        # have populated the cache while we were waiting.
        cached = _robots_cache.get(domain)
        if cached is not None and now - cached[1] < _ROBOTS_CACHE_TTL_SECONDS:
            return cached[0]
        parser = await _fetch_robots(domain)
        _robots_cache[domain] = (parser, time.time())
        return parser


async def is_allowed(url: str, user_agent: str = USER_AGENT) -> bool:
    """Return True if robots.txt allows our user-agent to fetch `url`.

    Returns True when robots.txt is unreachable or absent (permissive
    fallback — see _fetch_robots). Returns False only when robots.txt
    explicitly disallows our agent for the path. This is the standard
    'fail-open' robots policy major crawlers use.
    """
    domain = _domain_of(url)
    if not domain:
        return False
    parser = await _get_robots(domain)
    if parser is None:
        return True
    try:
        return parser.can_fetch(user_agent, url)
    except Exception as e:
        logger.debug(f"[politeness] can_fetch raised for {url}: {e}")
        return True


async def crawl_delay(url: str, user_agent: str = USER_AGENT) -> float:
    """Return the per-domain crawl delay in seconds.

    Reads Crawl-Delay from robots.txt for the user-agent if specified,
    otherwise falls back to the project default (1.0s). Capped at
    _MAX_DELAY_SECONDS so a misconfigured robots.txt can't stall an
    expansion job.
    """
    domain = _domain_of(url)
    if not domain:
        return _DEFAULT_CRAWL_DELAY_SECONDS
    parser = await _get_robots(domain)
    if parser is None:
        return _DEFAULT_CRAWL_DELAY_SECONDS
    try:
        delay = parser.crawl_delay(user_agent)
        if delay is None:
            return _DEFAULT_CRAWL_DELAY_SECONDS
        # robots.txt parsers may return int or float — coerce to float.
        delay_f = float(delay)
        if delay_f > _MAX_DELAY_SECONDS:
            logger.warning(
                f"[politeness] {domain} requested Crawl-Delay {delay_f}s; capping to {_MAX_DELAY_SECONDS}s"
            )
            return _MAX_DELAY_SECONDS
        return max(0.0, delay_f)
    except Exception as e:
        logger.debug(f"[politeness] crawl_delay raised for {url}: {e}")
        return _DEFAULT_CRAWL_DELAY_SECONDS


async def _get_rate_state(domain: str) -> Tuple[asyncio.Lock, float]:
    """Return (lock, last_request_ts) for a domain, creating it on first use."""
    if domain in _rate_state:
        return _rate_state[domain]
    async with _rate_state_lock:
        if domain not in _rate_state:
            _rate_state[domain] = (asyncio.Lock(), 0.0)
        return _rate_state[domain]


async def wait_for_slot(url: str) -> None:
    """Block until it's polite to fetch `url`.

    Acquires a per-domain async lock so concurrent requests to the same
    domain serialize. Sleeps until at least `crawl_delay(url)` seconds
    have passed since the last request to this domain. Updates the
    last-request-ts on exit.

    Domains are independent — fetching example.com doesn't block
    other.com. Within a domain, requests are strictly serialized.
    """
    domain = _domain_of(url)
    if not domain:
        return
    lock, _ = await _get_rate_state(domain)
    delay = await crawl_delay(url)
    async with lock:
        _, last_ts = _rate_state[domain]
        now = time.time()
        wait = (last_ts + delay) - now
        if wait > 0:
            logger.debug(f"[politeness] sleeping {wait:.2f}s before next {domain} request")
            await asyncio.sleep(min(wait, _MAX_DELAY_SECONDS))
        # Stamp the new last-request-ts so the next caller waits from now.
        _rate_state[domain] = (lock, time.time())


async def honor_retry_after(url: str, retry_after_header: Optional[str]) -> float:
    """Parse a Retry-After header value, sleep that long, return seconds slept.

    Retry-After is either an integer seconds or an HTTP-date string. We
    handle the integer form (the common case for rate-limit responses).
    Capped at _MAX_DELAY_SECONDS — a server asking us to wait an hour
    means we skip the URL instead.

    Returns the number of seconds actually slept (0 if header absent /
    invalid / capped above _MAX_DELAY_SECONDS — caller should treat this
    as "don't retry, skip and log").
    """
    if not retry_after_header:
        return 0.0
    try:
        seconds = float(retry_after_header.strip())
    except (TypeError, ValueError):
        # HTTP-date form is rare for 429 rate-limits; skip rather than
        # implementing date parsing.
        logger.debug(f"[politeness] Retry-After non-numeric for {url!r}: {retry_after_header!r}")
        return 0.0
    if seconds <= 0 or seconds > _MAX_DELAY_SECONDS:
        return 0.0
    await asyncio.sleep(seconds)
    return seconds


# ─── Smoke test ────────────────────────────────────────────────────────────

async def _smoke_test():
    """Manual sanity check — not run automatically. Invoke with:
       python -m asyncio backend/services/politeness.py
    """
    test_urls = [
        "https://en.wikipedia.org/wiki/Main_Page",
        "https://example.com/",
    ]
    for url in test_urls:
        ok = await is_allowed(url)
        delay = await crawl_delay(url)
        print(f"{url}: allowed={ok}, crawl_delay={delay}s")


if __name__ == "__main__":
    asyncio.run(_smoke_test())
