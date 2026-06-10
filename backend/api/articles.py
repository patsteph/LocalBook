"""articles — Phase 1 Tier 2 REST endpoints.

GET /articles/by-source/{source_id} — list articles for a source with
their character offsets (used by SourceNotesViewer to scroll exactly to
an article boundary).

POST /articles/backfill/{source_id} — extract articles for a source that
was ingested before Phase 1 (lazy migration).

POST /articles/backfill-all — queue a background batch backfill.
GET /articles/backfill/status — peek at the running batch.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()


# Global single-instance backfill state. Prevents two backfills running
# concurrently (each would queue dozens of summary/embed/RAG tasks and
# overwhelm Ollama / LanceDB → backend crash).
_BACKFILL_LOCK = asyncio.Lock()
_BACKFILL_STATUS: Dict[str, Any] = {
    "running": False,
    "queued": 0,
    "processed": 0,
    "articles_created": 0,
    "started_at": None,
    "finished_at": None,
    "last_error": None,
}
# Tiny sleep between source-level work so the event loop can service the
# normal IMAP poller, user chats, etc. Keeps the backfill in the background
# instead of monopolizing Ollama.
_PER_SOURCE_SLEEP_SECONDS = 1.0


@router.get("/by-source/{source_id}")
async def list_articles_for_source(source_id: str):
    """Return the article rows for a parent newsletter source. Caller can
    use `body_text_offset` to scroll exactly to an article boundary."""
    from storage.article_store import article_store
    articles = await article_store.list_by_source(source_id)
    return {
        "source_id": source_id,
        "count": len(articles),
        "articles": [
            {
                "id": a.get("id"),
                "position": a.get("position"),
                "title": a.get("title"),
                "summary": a.get("summary"),
                "topic_tags": a.get("topic_tags"),
                "body_text_offset": a.get("body_text_offset", -1),
            }
            for a in articles
        ],
    }


@router.post("/backfill/{source_id}")
async def backfill_articles(source_id: str):
    """Lazy migration — extract articles for a newsletter that was
    ingested before Phase 1. Triggered by SourceNotesViewer when a user
    opens a newsletter that has no article rows yet.

    No-op if articles already exist for this source.
    """
    from storage.source_store import source_store
    from storage.article_store import article_store
    from services.article_extractor import extract_articles
    from services.correspondent_processor import _summarize_articles_background
    import asyncio

    source = await source_store.get(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    fmt = (source.get("format") or "").lower()
    if fmt not in ("email", "forward"):
        raise HTTPException(status_code=400, detail=f"Source format '{fmt}' is not extractable")

    existing = await article_store.count_by_source(source_id)
    if existing > 0:
        return {"ok": True, "skipped": True, "reason": "articles already exist", "count": existing}

    text_body = source.get("content") or ""
    html_body = (source.get("metadata") or {}).get("content_html") if isinstance(source.get("metadata"), dict) else source.get("content_html")
    if not (text_body or html_body):
        raise HTTPException(status_code=400, detail="Source has no extractable body")

    articles = extract_articles(
        html_body=html_body or "",
        text_body=text_body,
        fallback_title=source.get("filename") or "(untitled)",
    )
    if not articles:
        return {"ok": True, "count": 0}

    count = await article_store.create_batch(
        source_id=source_id,
        notebook_id=source.get("notebook_id", ""),
        sender=source.get("sender") or source.get("original_sender"),
        articles=[
            {
                "position": a.position,
                "title": a.title,
                "body_text": a.body_text,
                "body_html": a.body_html,
                "body_text_offset": a.body_text_offset,
            }
            for a in articles
        ],
    )
    await source_store.update(source.get("notebook_id", ""), source_id, {"article_count": count})

    # Kick off summary + embedding in the background — same pattern as
    # live ingest.
    if count > 0:
        asyncio.create_task(_summarize_articles_background(source_id))

    return {"ok": True, "count": count, "backfilled": True}


async def _backfill_worker():
    """Single-instance background worker. Iterates every email/forward
    source missing articles, extracts + persists, and runs the summary +
    embed + RAG-index work INLINE (sequential). No parallel task fan-out.

    Why sequential: the previous parallel implementation kicked off one
    asyncio.create_task per source × dozens of sources × per-article LLM
    + embed + RAG-write — Ollama queues collapsed and LanceDB write
    contention crashed the backend. Sequential keeps the system stable
    at the cost of total runtime; user can navigate away and check back."""
    from storage.source_store import source_store
    from storage.article_store import article_store
    from services.article_extractor import extract_articles
    from services.correspondent_processor import _summarize_articles_background

    async with _BACKFILL_LOCK:
        _BACKFILL_STATUS["running"] = True
        _BACKFILL_STATUS["started_at"] = datetime.utcnow().isoformat()
        _BACKFILL_STATUS["finished_at"] = None
        _BACKFILL_STATUS["last_error"] = None
        _BACKFILL_STATUS["processed"] = 0
        _BACKFILL_STATUS["articles_created"] = 0
        try:
            all_by_nb = await source_store.list_all() or {}
            for nb_id, sources in all_by_nb.items():
                for s in sources or []:
                    fmt = (s.get("format") or "").lower()
                    if fmt not in ("email", "forward"):
                        continue
                    src_id = s.get("id")
                    if not src_id:
                        continue
                    try:
                        existing = await article_store.count_by_source(src_id)
                    except Exception:
                        existing = 0
                    if existing > 0:
                        continue
                    text_body = s.get("content") or ""
                    meta = s.get("metadata") or {}
                    html_body = meta.get("content_html") if isinstance(meta, dict) else s.get("content_html")
                    if not (text_body or html_body):
                        continue
                    try:
                        articles = extract_articles(
                            html_body=html_body or "",
                            text_body=text_body,
                            fallback_title=s.get("filename") or "(untitled)",
                        )
                    except Exception as e:
                        logger.debug(f"[articles.backfill_worker] extract failed for {src_id}: {e}")
                        continue
                    if not articles:
                        continue
                    try:
                        count = await article_store.create_batch(
                            source_id=src_id,
                            notebook_id=nb_id,
                            sender=s.get("sender") or s.get("original_sender"),
                            articles=[
                                {
                                    "position": a.position,
                                    "title": a.title,
                                    "body_text": a.body_text,
                                    "body_html": a.body_html,
                                    "body_text_offset": a.body_text_offset,
                                }
                                for a in articles
                            ],
                        )
                    except Exception as e:
                        logger.warning(f"[articles.backfill_worker] persist failed for {src_id}: {e}")
                        continue

                    try:
                        await source_store.update(nb_id, src_id, {"article_count": count})
                    except Exception:
                        pass

                    # CRITICAL CHANGE — run summary + embed + RAG INLINE
                    # (await) instead of asyncio.create_task. This keeps
                    # Ollama / LanceDB load to one source at a time and
                    # prevents the cascade that crashed the backend.
                    if count > 0:
                        try:
                            await _summarize_articles_background(src_id)
                        except Exception as e:
                            logger.debug(f"[articles.backfill_worker] post-extract pass failed for {src_id}: {e}")

                    _BACKFILL_STATUS["processed"] += 1
                    _BACKFILL_STATUS["articles_created"] += count

                    # Yield to the event loop so the IMAP poller, user
                    # chats, etc. can interleave.
                    await asyncio.sleep(_PER_SOURCE_SLEEP_SECONDS)
        except Exception as e:
            _BACKFILL_STATUS["last_error"] = str(e)[:300]
            logger.error(f"[articles.backfill_worker] aborted: {e}")
        finally:
            _BACKFILL_STATUS["running"] = False
            _BACKFILL_STATUS["finished_at"] = datetime.utcnow().isoformat()
            logger.info(
                f"[articles.backfill_worker] done — processed {_BACKFILL_STATUS['processed']} "
                f"source(s), {_BACKFILL_STATUS['articles_created']} article(s)"
            )


@router.post("/backfill-all")
async def backfill_all_articles():
    """Queue a background batch backfill of every email/forward source
    that doesn't have articles yet. Returns immediately.

    Single-instance: returns `already_running` if a backfill is in flight.
    Status is queryable via GET /articles/backfill/status.
    """
    from storage.source_store import source_store
    from storage.article_store import article_store

    if _BACKFILL_STATUS["running"]:
        return {
            "ok": True,
            "already_running": True,
            "status": dict(_BACKFILL_STATUS),
        }

    # Pre-count how many sources need work so the user sees a real ETA.
    queued = 0
    try:
        all_by_nb = await source_store.list_all() or {}
        for nb_id, sources in all_by_nb.items():
            for s in sources or []:
                fmt = (s.get("format") or "").lower()
                if fmt not in ("email", "forward"):
                    continue
                src_id = s.get("id")
                if not src_id:
                    continue
                if await article_store.count_by_source(src_id) > 0:
                    continue
                queued += 1
    except Exception as e:
        logger.warning(f"[articles.backfill-all] pre-count failed: {e}")

    _BACKFILL_STATUS["queued"] = queued
    asyncio.create_task(_backfill_worker())
    return {
        "ok": True,
        "queued": queued,
        "message": f"Backfill started in the background for {queued} source(s).",
    }


@router.get("/backfill/status")
async def backfill_status():
    """Peek at the running backfill, if any."""
    return dict(_BACKFILL_STATUS)
