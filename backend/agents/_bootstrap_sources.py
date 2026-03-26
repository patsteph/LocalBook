"""
Auto-Bootstrap: Backfill missing intent and discover sources on first collection run.
"""
import logging
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agents.collector import CollectorAgent

logger = logging.getLogger(__name__)

# Template intent patterns — must match CollectorSetupWizard.tsx TEMPLATES
INTENT_TEMPLATES = {
    "company_intel": "Track {subject} news, financials, competitive positioning, and industry developments.",
    "industry_watch": "Monitor the {subject} industry for trends, key players, market data, and regulatory changes.",
    "topic_research": "Deep research on {subject} — papers, key authors, methodologies, and real-world applications.",
    "project_archive": "Archive and track {subject} project documents, updates, and deliverables.",
    "people": "Profile and track {subject} for coaching and development.",
}


def backfill_intent(collector: 'CollectorAgent') -> bool:
    """Generate intent from subject + notebook_purpose if intent is empty.
    Returns True if intent was backfilled.
    """
    config = collector.config
    if config.intent:
        return False
    if not config.subject:
        return False

    purpose = getattr(config, 'notebook_purpose', '') or ''
    template = INTENT_TEMPLATES.get(purpose, "Research on {subject}")
    intent = template.replace("{subject}", config.subject.strip())

    collector.update_config({"intent": intent})
    collector.config.intent = intent
    print(f"[COLLECTOR] Backfilled intent for '{config.subject}': {intent[:80]}...")
    logger.info(f"[Bootstrap] Backfilled intent for {collector.notebook_id[:8]}")
    return True


async def auto_bootstrap_sources(collector: 'CollectorAgent') -> Dict[str, Any]:
    """Auto-discover and add RSS feeds + news keywords when sources are empty."""
    # Step 0: Backfill intent if subject exists but intent is missing
    backfill_intent(collector)

    config = collector.config
    if not config.intent:
        return {"bootstrapped": False, "reason": "no_intent"}

    sources = config.sources or {}
    has_sources = (
        len(sources.get("rss_feeds", [])) > 0 or
        len(sources.get("web_pages", [])) > 0 or
        len(sources.get("news_keywords", [])) > 0
    )
    if has_sources:
        return {"bootstrapped": False, "reason": "already_has_sources"}

    print(f"[COLLECTOR] Auto-bootstrapping sources for '{config.subject}'...")

    try:
        from services.source_discovery import source_discovery

        result = await source_discovery.discover_sources(
            intent=config.intent,
            focus_areas=config.focus_areas or [],
            subject=config.subject or "",
        )

        if not result.sources:
            return {"bootstrapped": False, "reason": "no_sources_found"}

        new_sources: Dict[str, list] = {
            "rss_feeds": list(sources.get("rss_feeds", [])),
            "web_pages": list(sources.get("web_pages", [])),
            "news_keywords": list(sources.get("news_keywords", [])),
        }
        added = 0

        for src in result.sources:
            if src.confidence < 0.6:
                continue
            url = src.url or ""
            rss = src.rss_url or ""
            stype = src.source_type.value if hasattr(src.source_type, 'value') else str(src.source_type)

            if stype in ("rss_feed", "RSS_FEED") and rss:
                if rss not in new_sources["rss_feeds"]:
                    new_sources["rss_feeds"].append(rss)
                    added += 1
            elif stype in ("news_keyword", "NEWS_KEYWORD"):
                kw = (src.metadata or {}).get("keyword", src.name)
                if kw and kw not in new_sources["news_keywords"]:
                    new_sources["news_keywords"].append(kw)
                    added += 1
            elif rss and rss not in new_sources["rss_feeds"]:
                new_sources["rss_feeds"].append(rss)
                added += 1
            elif url and url not in new_sources["web_pages"]:
                new_sources["web_pages"].append(url)
                added += 1

            if added >= 12:
                break

        if added > 0:
            collector.update_config({"sources": new_sources})
            print(f"[COLLECTOR] Bootstrapped {added} sources: "
                  f"{len(new_sources['rss_feeds'])} RSS, "
                  f"{len(new_sources['news_keywords'])} news keywords, "
                  f"{len(new_sources['web_pages'])} web pages")
            logger.info(f"[Bootstrap] Added {added} sources for {collector.notebook_id[:8]}")

        return {"bootstrapped": True, "added": added}

    except Exception as e:
        logger.warning(f"Auto-bootstrap failed (non-fatal): {e}")
        print(f"[COLLECTOR] Bootstrap failed (non-fatal): {e}")
        return {"bootstrapped": False, "reason": str(e)}
