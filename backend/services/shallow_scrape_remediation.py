"""
Shallow Scrape Remediation — ISS-002

Scans collected sources (collected_by="collector") whose stored content
falls in the 300–600 char range — the signature of a shallow scrape where
only the page header/meta was captured.  Re-scrapes each candidate URL
using the current web_scraper (trafilatura-based, fixed), updates the
source content, and re-indexes it in LanceDB.

Runs once in the background at startup, after the HTTP server is ready.
Skips:
  - Sources without a URL
  - Sources not marked as collected_by="collector"
  - Sources whose format/type is 'youtube', 'note', 'document', 'pdf', 'upload'
  - Sources already marked remediated_shallow_scrape=True
  - Sources where re-scrape yields less content than what was stored
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# Char range that indicates a shallow scrape (header/snippet only).
SHALLOW_MIN = 300
SHALLOW_MAX = 600

# Source types that are NOT web-scraped — skip these entirely.
SKIP_FORMATS = {"youtube", "note", "document", "pdf", "upload", "docx", "pptx", "xlsx", "csv"}

# Semaphore: re-scrape at most N URLs concurrently to avoid hammering sites.
MAX_CONCURRENT = 3

# Minimum improvement to bother updating — new content must be meaningfully longer.
MIN_IMPROVEMENT_CHARS = 200


async def _remediate_source(notebook_id: str, source: dict) -> bool:
    """Re-scrape a single shallow source. Returns True if updated."""
    from storage.source_store import source_store
    from services.web_scraper import web_scraper
    from services.rag_engine import rag_engine

    source_id = source.get("id")
    url = source.get("url", "")
    title = source.get("filename") or source.get("title") or source_id
    source_type = source.get("type") or source.get("format") or "web"
    old_content = source.get("content") or ""

    if not url:
        return False

    try:
        scraped = await web_scraper._scrape_single(url)
    except Exception as e:
        logger.warning(f"[ShallowRemedy] Scrape failed for '{title}': {e}")
        # Mark as remediated so we don't retry sources that crash
        await source_store.update(notebook_id, source_id, {"remediated_shallow_scrape": True})
        return False

    if not scraped or not scraped.get("success"):
        err = scraped.get("error", "unknown") if scraped else "no response"
        logger.debug(f"[ShallowRemedy] Scrape unsuccessful for '{title}': {err}")
        # Mark as remediated so we don't retry sources that can't be scraped
        await source_store.update(notebook_id, source_id, {"remediated_shallow_scrape": True})
        return False

    new_content = scraped.get("text", "")
    improvement = len(new_content) - len(old_content)

    if improvement < MIN_IMPROVEMENT_CHARS:
        logger.debug(
            f"[ShallowRemedy] No meaningful improvement for '{title}': "
            f"{len(old_content)} -> {len(new_content)} chars (delta {improvement})"
        )
        # Still mark as remediated so we don't retry on next startup
        await source_store.update(notebook_id, source_id, {"remediated_shallow_scrape": True})
        return False

    logger.info(
        f"[ShallowRemedy] Enriched '{title}': {len(old_content)} -> {len(new_content)} chars"
    )

    updates: dict = {
        "content": new_content,
        "char_count": len(new_content),
        "word_count": len(new_content.split()),
        "remediated_shallow_scrape": True,
    }
    # Update title if the scraper found a better one
    scraped_title = scraped.get("title", "")
    if scraped_title and len(scraped_title) > len(title):
        updates["filename"] = scraped_title

    await source_store.update(notebook_id, source_id, updates)

    # Re-index into LanceDB: delete old shallow chunks first to prevent duplicates,
    # then ingest the full content.
    try:
        await rag_engine.delete_source(notebook_id, source_id)
        await rag_engine.ingest_document(
            notebook_id=notebook_id,
            source_id=source_id,
            text=new_content,
            filename=updates.get("filename", title),
            source_type=source_type,
        )
        logger.info(f"[ShallowRemedy] Re-indexed '{title}'")
    except Exception as idx_err:
        logger.warning(f"[ShallowRemedy] Re-index failed for '{title}': {idx_err}")

    return True


def _sentinel_path():
    from config import settings
    return settings.data_dir / ".shallow_scrape_remediation_done"


async def run_shallow_scrape_remediation():
    """
    Entry point called at startup.  Runs ONCE — a sentinel file is written
    to the data directory on completion so subsequent startups skip entirely.

    Scans ALL notebooks for collected sources in the shallow-scrape char
    range and re-scrapes them with the fixed scraper.  Runs with a bounded
    semaphore so it doesn't spike memory or hit rate limits.
    """
    sentinel = _sentinel_path()
    if sentinel.exists():
        logger.debug("[ShallowRemedy] Already completed — skipping.")
        return

    # Query database directly — don't use source_store which may read from stale JSON cache
    try:
        from storage.database import get_db
        conn = get_db().get_connection()
        rows = conn.execute("SELECT * FROM sources").fetchall()
        print(f"[ShallowRemedy] Loaded {len(rows)} source rows from database")
        all_sources: Dict[str, List[Dict]] = {}
        collector_count = 0
        for row in rows:
            src = dict(row)
            # Unpack metadata_json
            meta = src.pop('metadata_json', None)
            if meta:
                try:
                    import json as _json
                    extra = _json.loads(meta) if isinstance(meta, str) else meta
                    src.update(extra)
                except Exception as _e:
                    logger.warning(f"[shallow-scrape-remediation] {type(_e).__name__}: {_e}")
            if src.get("collected_by") == "collector":
                collector_count += 1
            nb_id = src.get("notebook_id")
            if nb_id:
                all_sources.setdefault(nb_id, []).append(src)
        print(f"[ShallowRemedy] {collector_count} collector-owned sources found")
    except Exception as e:
        print(f"[ShallowRemedy] ERROR: Could not query database: {e}")
        logger.error(f"[ShallowRemedy] Could not query database: {e}")
        return

    candidates = []
    skipped_collector = 0
    skipped_remediated = 0
    skipped_format = 0
    skipped_no_url = 0
    skipped_too_long = 0
    for notebook_id, sources in all_sources.items():
        for src in sources:
            if src.get("collected_by") != "collector":
                skipped_collector += 1
                continue
            if src.get("remediated_shallow_scrape"):
                skipped_remediated += 1
                continue
            fmt = (src.get("format") or src.get("type") or "").lower()
            if fmt in SKIP_FORMATS:
                skipped_format += 1
                continue
            if not src.get("url"):
                skipped_no_url += 1
                continue
            content = src.get("content") or ""
            char_count = len(content)
            if not (SHALLOW_MIN <= char_count <= SHALLOW_MAX):
                skipped_too_long += 1
                continue
            candidates.append((notebook_id, src))
    print(f"[ShallowRemedy] Filter: {skipped_collector} non-collector, {skipped_remediated} already-fixed, {skipped_format} wrong-format, {skipped_no_url} no-url, {skipped_too_long} size-outside-range")

    if not candidates:
        logger.info("[ShallowRemedy] No shallow-scraped collected sources found — nothing to do.")
        try:
            sentinel.write_text("completed — 0 candidates found")
        except Exception as _e:
            logger.warning(f"[shallow-scrape-remediation] {type(_e).__name__}: {_e}")
        return

    logger.info(f"[ShallowRemedy] Found {len(candidates)} shallow collected source(s) to re-scrape.")
    print(f"🔧 ShallowRemedy: re-scraping {len(candidates)} shallow collected source(s)...")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    fixed = 0
    skipped = 0

    async def bounded_remediate(notebook_id: str, src: dict):
        nonlocal fixed, skipped
        async with semaphore:
            result = await _remediate_source(notebook_id, src)
            if result:
                fixed += 1
            else:
                skipped += 1

    tasks = [bounded_remediate(nb_id, src) for nb_id, src in candidates]
    await asyncio.gather(*tasks, return_exceptions=True)

    print(f"✅ ShallowRemedy: {fixed} source(s) enriched, {skipped} skipped (no improvement or scrape failed).")
    logger.info(f"[ShallowRemedy] Complete — {fixed} enriched, {skipped} skipped.")

    # Write sentinel so this job never runs again on subsequent startups
    try:
        sentinel.write_text(f"completed — {fixed} enriched, {skipped} skipped")
    except Exception as e:
        logger.warning(f"[ShallowRemedy] Could not write sentinel file: {e}")
