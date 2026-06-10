"""articles — Phase 1 Tier 2 REST endpoints.

GET /articles/by-source/{source_id} — list articles for a source with
their character offsets (used by SourceNotesViewer to scroll exactly to
an article boundary).

POST /articles/backfill/{source_id} — extract articles for a source that
was ingested before Phase 1 (lazy migration).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()


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


@router.post("/backfill-all")
async def backfill_all_articles():
    """Batch backfill: iterate every source with format='email'/'forward'
    and no articles yet. Used by the @correspondent backfill articles
    chat intent.

    Returns counts of sources processed + articles created. Sources are
    processed sequentially to keep load light on the embedding model.
    """
    from storage.source_store import source_store
    from storage.article_store import article_store
    from services.article_extractor import extract_articles
    from services.correspondent_processor import _summarize_articles_background
    import asyncio

    all_by_nb = await source_store.list_all() or {}
    sources_processed = 0
    articles_created = 0
    for nb_id, sources in all_by_nb.items():
        for s in sources or []:
            fmt = (s.get("format") or "").lower()
            if fmt not in ("email", "forward"):
                continue
            src_id = s.get("id")
            if not src_id:
                continue
            existing = await article_store.count_by_source(src_id)
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
                logger.debug(f"[articles.backfill-all] extract failed for {src_id}: {e}")
                continue
            if not articles:
                continue
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
            await source_store.update(nb_id, src_id, {"article_count": count})
            if count > 0:
                asyncio.create_task(_summarize_articles_background(src_id))
            sources_processed += 1
            articles_created += count

    return {
        "ok": True,
        "sources_processed": sources_processed,
        "articles_created": articles_created,
    }
