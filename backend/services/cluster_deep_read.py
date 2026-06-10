"""cluster_deep_read — Phase 5 Tier 2 / C completion (2026-06-10).

Combined "what you already have + what the web adds + what to read next"
flow triggered from the hot-cluster Deep-read CTA.

Per the deferred C design:
  1. Pull the cluster's articles (existing newsletter coverage)
  2. Run research_engine.deep_dive(query=label) for web findings
  3. Synthesize via gemma4 (per user pick): a tight briefing that
     names overlap, novelty, and a next-step recommendation
  4. Stream back the three sections so the user gets context fast

Skips redundant web hits by filtering out domains that match the
cluster's sender list (no point re-scraping Stratechery for an
article we already ingested from Stratechery's newsletter).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Per-section budget so the synthesis prompt stays inside gemma4's
# effective context. Articles + web findings can both grow.
MAX_ARTICLE_CHARS = 1200
MAX_WEB_CHARS = 1200


async def gather_cluster_context(label: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Look up the cluster by label and load its articles.

    Returns (cluster_row, article_list). cluster_row is None if no match.
    """
    from storage.database import get_db
    from storage.article_store import article_store

    if not label:
        return None, []
    row = get_db().get_connection().execute(
        "SELECT * FROM topic_clusters WHERE LOWER(label) LIKE ? LIMIT 1",
        (f"%{label.lower()}%",),
    ).fetchone()
    if not row:
        return None, []
    cluster = dict(row)
    try:
        article_ids = json.loads(cluster.get("article_ids") or "[]")
    except Exception:
        article_ids = []
    articles: List[Dict[str, Any]] = []
    for aid in article_ids[:12]:
        a = await article_store.get(aid)
        if a:
            articles.append(a)
    return cluster, articles


def cluster_sender_domains(articles: List[Dict[str, Any]]) -> List[str]:
    """Extract sender email domains so we can filter web search results
    that would re-scrape content we already have."""
    out: List[str] = []
    for a in articles:
        sender = (a.get("sender") or "")
        if "@" in sender:
            domain = sender.split("@", 1)[1].strip(">").lower()
            if domain and domain not in out:
                out.append(domain)
    return out


async def synthesize(
    *,
    cluster_label: str,
    articles: List[Dict[str, Any]],
    web_results: List[Any],
) -> str:
    """gemma4 call: synthesize the two streams into a tight briefing.

    Returns a markdown string with three sections:
      1. What's covered in your newsletters
      2. What the web adds today
      3. What I'd read next
    """
    from services.ollama_service import ollama_service
    from config import settings

    # Compose article block
    art_lines: List[str] = []
    used = 0
    for a in articles[:8]:
        title = a.get("title") or "(untitled)"
        summary = a.get("summary") or (a.get("body_text") or "")[:200]
        line = f"- **{title}** ({a.get('sender', '?')}): {summary[:200]}"
        if used + len(line) > MAX_ARTICLE_CHARS:
            break
        art_lines.append(line)
        used += len(line)
    article_block = "\n".join(art_lines) or "_(no recent newsletter articles in this cluster)_"

    # Compose web block
    web_lines: List[str] = []
    used = 0
    for r in web_results[:8]:
        try:
            title = getattr(r, "title", None) or getattr(r, "filename", None) or "(untitled)"
            snippet = getattr(r, "snippet", None) or getattr(r, "summary", None) or ""
            url = getattr(r, "url", None) or ""
            line = f"- **{title[:120]}** {('(' + url[:80] + ')') if url else ''}: {snippet[:180]}"
        except Exception:
            continue
        if used + len(line) > MAX_WEB_CHARS:
            break
        web_lines.append(line)
        used += len(line)
    web_block = "\n".join(web_lines) or "_(no notable new web results)_"

    system_prompt = (
        "You are a research briefing writer. Synthesize two streams of "
        "information into a concise three-section briefing. Be specific "
        "and concrete. No filler. No restating the obvious. Quote real "
        "titles and sources by name when it helps the user."
    )

    user_prompt = (
        f"TOPIC: {cluster_label}\n\n"
        f"STREAM A — recent articles from the user's newsletter subscriptions:\n"
        f"{article_block}\n\n"
        f"STREAM B — web search results today:\n"
        f"{web_block}\n\n"
        f"Produce a markdown reply with these three short sections — "
        f"each with a bold heading and 2-4 tight bullet lines:\n\n"
        f"### What your newsletters already cover\n"
        f"### What the web adds today\n"
        f"### What I'd read next\n\n"
        f"For the third section, name the single highest-value article "
        f"or URL the user should open first, with one sentence saying why."
    )

    try:
        result = await ollama_service.generate(
            prompt=user_prompt,
            system=system_prompt,
            # User picked gemma4 (main model) for this synthesis.
            model=settings.ollama_model,
            temperature=0.4,
            num_predict=800,
            timeout=120.0,
        )
        text = (result or {}).get("response", "").strip()
        if not text:
            return _fallback_synthesis(cluster_label, articles, web_results)
        return text
    except Exception as e:
        logger.warning(f"[cluster_deep_read.synthesize] LLM failed: {e}")
        return _fallback_synthesis(cluster_label, articles, web_results)


def _fallback_synthesis(
    cluster_label: str,
    articles: List[Dict[str, Any]],
    web_results: List[Any],
) -> str:
    """When the LLM is unavailable, return the raw two streams so the
    user still gets value."""
    lines = [
        f"### What your newsletters already cover — `{cluster_label}`",
        "",
    ]
    for a in articles[:6]:
        title = a.get("title") or "(untitled)"
        sender = a.get("sender") or "?"
        summary = (a.get("summary") or (a.get("body_text") or "")[:160])
        lines.append(f"- **{title}** ({sender}): {summary[:200]}")
    lines.append("")
    lines.append("### What the web adds today")
    lines.append("")
    for r in web_results[:6]:
        title = getattr(r, "title", None) or "(untitled)"
        url = getattr(r, "url", None) or ""
        lines.append(f"- **{title}** {('· ' + url[:80]) if url else ''}")
    return "\n".join(lines)


async def run(*, label: str, notebook_id: Optional[str] = None) -> Dict[str, Any]:
    """Top-level entry. Returns a dict with the article context,
    web results, and the synthesized briefing — caller streams to chat."""
    from services.research_engine import research_engine, DeepDiveFilters

    cluster, articles = await gather_cluster_context(label)
    if cluster is None:
        return {
            "ok": False,
            "reason": "cluster not found",
            "label": label,
        }

    # Run the deep dive scoped to the topic. Domain-filter sender
    # overlap to avoid redundant scraping of newsletter URLs we already
    # ingested locally.
    skip_domains = cluster_sender_domains(articles)
    filters = DeepDiveFilters()
    try:
        web_results = await research_engine.deep_dive(
            query=label,
            notebook_id=notebook_id or "",
            filters=filters,
            on_status=None,
        )
        if skip_domains:
            web_results = [
                r for r in web_results
                if not any(d in (getattr(r, "url", "") or "").lower() for d in skip_domains)
            ]
    except Exception as e:
        logger.warning(f"[cluster_deep_read.run] web search failed: {e}")
        web_results = []

    briefing = await synthesize(
        cluster_label=cluster["label"],
        articles=articles,
        web_results=web_results,
    )

    return {
        "ok": True,
        "cluster": cluster,
        "articles": articles,
        "web_results": web_results,
        "briefing": briefing,
        "skipped_domains": skip_domains,
    }
