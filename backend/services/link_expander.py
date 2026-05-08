"""
Link Expander — depth+1 outgoing-link follow-up scrape.

Runs as a background job (services/job_queue.py) so the user can submit
"expand these 5 links" and walk away. For each user-selected URL we:

  1. **Politeness gate**     (services/politeness.py) — skip if robots.txt
                              forbids us, sleep per crawl-delay before fetch.
  2. **Depth gate**          — refuse to expand a source whose own depth >= 1.
                              Hard structural cap; depth+1 only, no recursion.
  3. **Dedup gate**           — skip if the URL already exists as a source in
                              any notebook (cross-notebook URL match).
  4. **Fetch**               (services/web_scraper.py) — scrape the article.
  5. **Worthy decision**     (agents/collector._calculate_confidence) —
                              reuse the same LLM relevance scoring the
                              regular collector uses for "is this worth
                              showing to the user?".
  6. **Cross-notebook hint** (agents/curator.score_text_against_notebooks) —
                              flag any other notebooks the article is
                              relevant to. Hint only — never blocks.
  7. **Approval queue**      (agents/collector._approval_queue) — push the
                              item with parent_source_id / discovery_url /
                              cross_notebook_matches stamped on it. The UI
                              renders these as badges.

Resource budget (caps, never exceed):
  MAX_URLS_PER_EXPANSION = 20  — refuse jobs with more selected URLs.
  PER_FETCH_TIMEOUT      = 30s — per-URL HTTP timeout.
  TOTAL_WALL_CLOCK       = 300s — the whole job. Anything older fails.
  FETCH_CONCURRENCY      = 3   — Semaphore inside the job. Lower than
                              web_scraper's own 5 because we run alongside
                              other backend work (RAG, agents, etc.).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlparse

from services.job_queue import job_queue, JobType, JobProgress

logger = logging.getLogger(__name__)


# ── Public limits ─────────────────────────────────────────────────────────

MAX_URLS_PER_EXPANSION = 20
PER_FETCH_TIMEOUT_SECONDS = 30.0
TOTAL_WALL_CLOCK_SECONDS = 300.0
FETCH_CONCURRENCY = 3


# ── Public API ────────────────────────────────────────────────────────────

class LinkExpansionError(Exception):
    """Raised when expand_source_links rejects a request before submitting."""


async def submit_expansion(
    source_id: str,
    notebook_id: str,
    selected_urls: List[str],
) -> str:
    """Submit a depth+1 expansion job. Returns a JobQueue job ID.

    Validates the request structurally BEFORE submitting so misuse fails
    fast (4xx) rather than failing inside a background job (where the
    user only sees a vague error). Raises LinkExpansionError on bad input.
    """
    if not selected_urls:
        raise LinkExpansionError("selected_urls is empty")
    # Dedupe + bound the request — protects the worker from being asked
    # to process 1000 URLs at once.
    cleaned: List[str] = []
    seen_urls: set[str] = set()
    for u in selected_urls:
        if not isinstance(u, str):
            continue
        u = u.strip()
        if not u or u in seen_urls:
            continue
        # Reject anything that isn't http(s) — politeness only handles
        # web URLs; a file:// or javascript: URL would be a security
        # smell.
        if not (u.startswith("http://") or u.startswith("https://")):
            continue
        cleaned.append(u)
        seen_urls.add(u)
        if len(cleaned) >= MAX_URLS_PER_EXPANSION:
            break
    if not cleaned:
        raise LinkExpansionError("no valid http/https URLs in selected_urls")

    # Verify the parent source exists and is depth==0. Done HERE not in
    # the job so a missing source returns a clean error to the API caller.
    from storage.source_store import source_store
    parent = await source_store.get(source_id)
    if not parent:
        raise LinkExpansionError(f"source {source_id} not found")
    if (parent.get("notebook_id") or "") != notebook_id:
        raise LinkExpansionError(f"source {source_id} does not belong to notebook {notebook_id}")
    parent_depth = int(parent.get("depth") or 0)
    if parent_depth >= 1:
        raise LinkExpansionError(
            f"source {source_id} is at depth={parent_depth}; depth+1 expansion only allowed from depth-0 captures"
        )

    # Verify each selected URL is actually in the source's outbound_links —
    # defense against the user submitting arbitrary URLs that bypass the
    # extension's link-extraction context. We don't want this endpoint
    # turning into a generic "scrape any URL I send" tool.
    known_urls = {
        (l.get("url") or "").strip()
        for l in (parent.get("outbound_links") or [])
        if isinstance(l, dict)
    }
    selected_set = set(cleaned)
    unknown = selected_set - known_urls
    if unknown and known_urls:
        raise LinkExpansionError(
            f"selected URLs not in source's outbound_links: {sorted(unknown)[:3]}…"
        )

    # Submit. Handler is registered below at module load.
    job_id = await job_queue.submit(
        job_type=JobType.LINK_EXPANSION,
        params={
            "source_id": source_id,
            "notebook_id": notebook_id,
            "selected_urls": cleaned,
            "parent_title": parent.get("title") or parent.get("filename") or "",
        },
        notebook_id=notebook_id,
    )
    return job_id


# ── Job handler ───────────────────────────────────────────────────────────

async def _handle_expansion(
    params: Dict[str, Any],
    report_progress: Callable[[JobProgress], Awaitable[None]],
    cancel_event: asyncio.Event,
) -> Dict[str, Any]:
    """Body of a link-expansion job. Lazy imports keep cold-start cheap."""
    from agents.collector import (
        get_collector,
        CollectedItem,
        ApprovalQueueItem,
    )
    from agents.curator import curator
    from services import politeness
    from services.web_scraper import web_scraper
    from storage.source_store import source_store

    source_id: str = params["source_id"]
    notebook_id: str = params["notebook_id"]
    selected_urls: List[str] = list(params["selected_urls"])
    parent_title: str = params.get("parent_title", "")

    started_at = time.time()

    def _budget_remaining() -> float:
        return TOTAL_WALL_CLOCK_SECONDS - (time.time() - started_at)

    # Build dedup index ONCE up-front: a set of URL strings that already
    # exist as sources anywhere in any notebook. Cheaper than calling
    # source_store per-URL inside the loop.
    try:
        all_sources = await source_store.list_all()
        existing_urls: set[str] = set()
        for sources in all_sources.values():
            for s in sources:
                u = (s.get("url") or "").strip()
                if u:
                    existing_urls.add(u)
    except Exception as e:
        logger.warning(f"[link-expand] dedup index build failed: {e}")
        existing_urls = set()

    collector_agent = get_collector(notebook_id)

    # Per-URL worker — kept inside the handler so it shares closure state
    # with the politeness lock + dedup index without monkey-patching.
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    results: List[Dict[str, Any]] = []
    queued_count = 0
    skipped_count = 0
    error_count = 0

    async def _process_one(url: str, idx: int) -> None:
        nonlocal queued_count, skipped_count, error_count
        await report_progress(JobProgress(
            percent=int((idx / len(selected_urls)) * 90),
            message=f"Processing {idx + 1}/{len(selected_urls)}: {urlparse(url).netloc or url}",
            stage="processing",
        ))
        if cancel_event.is_set():
            return

        # 1. Politeness — robots.txt
        if not await politeness.is_allowed(url):
            results.append({"url": url, "status": "skipped", "reason": "robots_disallow"})
            skipped_count += 1
            logger.info(f"[link-expand] skipped (robots): {url}")
            return

        # 2. Dedup — already a source somewhere?
        if url in existing_urls:
            results.append({"url": url, "status": "skipped", "reason": "duplicate"})
            skipped_count += 1
            return

        # 3. Per-domain rate limit + fetch
        async with sem:
            await politeness.wait_for_slot(url)
            try:
                fetch_result = await asyncio.wait_for(
                    web_scraper._scrape_single(url),
                    timeout=PER_FETCH_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                results.append({"url": url, "status": "error", "reason": "timeout"})
                error_count += 1
                return
            except Exception as e:
                results.append({"url": url, "status": "error", "reason": f"fetch_failed: {e!s}"[:200]})
                error_count += 1
                return

        if not fetch_result or not fetch_result.get("success") or not fetch_result.get("text"):
            results.append({"url": url, "status": "error", "reason": "empty_content"})
            error_count += 1
            return

        text = fetch_result.get("text") or ""
        title = fetch_result.get("title") or url
        # Truncate VERY long articles before LLM scoring — collector's
        # _score_relevance only inspects the first 1000 chars anyway, but
        # keep the full text on the queue item so the user sees a real
        # preview after approval.
        full_content = text[:200000]  # 200KB hard cap to protect memory
        preview = full_content[:600]

        # 4. Build a CollectedItem and run worthy/cross-notebook scoring
        item = CollectedItem(
            title=title[:300] or url,
            url=url,
            content=full_content,
            preview=preview,
            source_name=urlparse(url).netloc or "web",
            source_type="web",
            source_url=url,
            collected_at=datetime.utcnow(),
            content_hash=_hash_text(full_content),
            # Depth+1 provenance — set BEFORE confidence so any future
            # learned-preference logic that inspects parent sees it.
            parent_source_id=source_id,
            discovery_url=url,
        )
        try:
            item = await collector_agent._calculate_confidence(item)
        except Exception as e:
            logger.warning(f"[link-expand] confidence calc failed for {url}: {e}")
            # Continue anyway — we still queue it, just with default scores.

        # 5. Cross-notebook similarity — hint only, never blocks
        try:
            matches = await curator.score_text_against_notebooks(
                text=full_content,
                exclude_notebook_id=notebook_id,
                max_results=3,
            )
            item.cross_notebook_matches = matches or []
        except Exception as e:
            logger.debug(f"[link-expand] cross-notebook scoring failed for {url}: {e}")
            item.cross_notebook_matches = []

        # 6. Queue for approval. ALWAYS queue — never auto-approve, even
        # at high confidence. The user explicitly chose to scrape but
        # didn't pre-approve the result; they review every item.
        queue_item = ApprovalQueueItem(
            item=item,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        collector_agent._approval_queue.append(queue_item)
        try:
            collector_agent._save_approval_queue()
        except Exception as e:
            logger.warning(f"[link-expand] approval queue save failed: {e}")
        # Track URL so a re-run within the same job won't re-queue it.
        existing_urls.add(url)
        results.append({
            "url": url,
            "status": "queued",
            "title": item.title,
            "relevance_score": item.relevance_score,
            "overall_confidence": item.overall_confidence,
            "cross_notebook_match_count": len(item.cross_notebook_matches),
        })
        queued_count += 1

    # Run all URLs concurrently (capped by FETCH_CONCURRENCY semaphore).
    # gather() with return_exceptions ensures one URL failing doesn't
    # cancel the others.
    tasks = []
    for idx, url in enumerate(selected_urls):
        if cancel_event.is_set():
            break
        if _budget_remaining() <= 0:
            results.append({"url": url, "status": "skipped", "reason": "wall_clock_exceeded"})
            skipped_count += 1
            continue
        tasks.append(asyncio.create_task(_process_one(url, idx)))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.time() - started_at
    await report_progress(JobProgress(
        percent=100,
        message=f"Done — queued {queued_count}, skipped {skipped_count}, errors {error_count} in {elapsed:.1f}s",
        stage="complete",
    ))
    return {
        "queued": queued_count,
        "skipped": skipped_count,
        "errors": error_count,
        "results": results,
        "elapsed_seconds": round(elapsed, 1),
        "parent_source_id": source_id,
        "parent_title": parent_title,
    }


def _hash_text(text: str) -> str:
    """Stable SHA256 over the article body — used by collector dedup."""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Module init: register the handler with the job queue ──────────────────

# Register at import time so the first /sources/{id}/expand-links call
# finds the handler. Idempotent — registering the same handler twice is a
# no-op (the JobQueue stores by JobType key, last-write-wins).
job_queue.register_handler(JobType.LINK_EXPANSION, _handle_expansion)
