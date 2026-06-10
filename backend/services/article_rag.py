"""article_rag — independent LanceDB indexing of newsletter articles.

Phase 1C.3 of Tier 2 (2026-06-10). Per A.1 locked decision: articles
should be independently RAG-searchable.

Strategy:
  - Each article gets indexed with a synthetic source_id `art-{uuid}`
    so retrieval can distinguish article chunks from parent-newsletter
    chunks.
  - `resolve_citation_source(synthetic_source_id)` maps an `art-` ID
    back to the parent newsletter's metadata for citation display
    (so citations always point users to the source viewer where they
    can read the article in context).

Indexing is fire-and-forget from `_summarize_articles_background`. Each
article is marked `rag_indexed=1` on success so retries are idempotent.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ARTICLE_SOURCE_PREFIX = "art-"


def is_article_source_id(source_id: str) -> bool:
    return bool(source_id) and source_id.startswith(ARTICLE_SOURCE_PREFIX)


def synthetic_id_for_article(article_id: str) -> str:
    return f"{ARTICLE_SOURCE_PREFIX}{article_id}"


def extract_article_id(synthetic_id: str) -> Optional[str]:
    if not is_article_source_id(synthetic_id):
        return None
    return synthetic_id[len(ARTICLE_SOURCE_PREFIX):]


async def index_article(
    *,
    notebook_id: str,
    article_id: str,
    title: str,
    body_text: str,
) -> bool:
    """Ingest one article into the notebook's LanceDB index under a
    synthetic source_id. Returns True on success.

    Fire-and-forget — caller (background summary task) doesn't await on
    failure; we log at debug since RAG-indexing failures are recoverable.
    """
    if not body_text.strip():
        return False
    try:
        from services.rag_engine import rag_engine
        synthetic_id = synthetic_id_for_article(article_id)
        await rag_engine.ingest_document(
            notebook_id=notebook_id,
            source_id=synthetic_id,
            text=body_text,
            filename=title or "Article",
            source_type="article",
        )
        return True
    except Exception as e:
        logger.debug(f"[article_rag.index_article] failed for {article_id}: {e}")
        return False


async def index_pending_for_source(source_id: str) -> int:
    """Walk articles for `source_id` and index any that aren't yet RAG-
    indexed. Returns count newly indexed. Idempotent — re-running after
    a partial failure picks up where it left off."""
    from storage.article_store import article_store
    from storage.database import get_db
    articles = await article_store.list_by_source(source_id)
    if not articles:
        return 0
    indexed = 0
    conn = get_db().get_connection()
    for a in articles:
        # P14.A (2026-06-10) — skip non-content articles. Sponsors / ads
        # / jobs / navigation chrome should never reach RAG.
        if (a.get("kind") or "content").strip().lower() != "content":
            continue
        # Skip if already indexed
        try:
            row = conn.execute(
                "SELECT rag_indexed FROM articles WHERE id = ?",
                (a["id"],),
            ).fetchone()
            if row and int(row["rag_indexed"] or 0) == 1:
                continue
        except Exception:
            pass
        ok = await index_article(
            notebook_id=a["notebook_id"],
            article_id=a["id"],
            title=a.get("title") or "",
            body_text=a.get("body_text") or "",
        )
        if ok:
            try:
                conn.execute(
                    "UPDATE articles SET rag_indexed = 1 WHERE id = ?",
                    (a["id"],),
                )
                conn.commit()
            except Exception:
                pass
            indexed += 1
    if indexed:
        logger.info(f"[article_rag] indexed {indexed} article(s) for source {source_id[:8]}")
    return indexed


async def resolve_citation_source(synthetic_source_id: str) -> Optional[Dict[str, Any]]:
    """Convert an `art-` source_id into the display info chat citations
    use: parent newsletter's source_id + article title + scroll position.

    Returns None when the synthetic id can't be mapped (article was
    deleted, parent vanished, etc) — caller should fall back to showing
    the raw chunk text without a clickable source.
    """
    article_id = extract_article_id(synthetic_source_id)
    if not article_id:
        return None
    from storage.article_store import article_store
    from storage.source_store import source_store
    article = await article_store.get(article_id)
    if not article:
        return None
    parent_id = article.get("source_id")
    if not parent_id:
        return None
    parent = await source_store.get(parent_id)
    if not parent:
        return None
    return {
        "source_id": parent_id,
        "filename": parent.get("filename") or "Newsletter",
        "article_id": article_id,
        "article_title": article.get("title"),
        "article_position": article.get("position"),
        "article_summary": article.get("summary"),
    }
