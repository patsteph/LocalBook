"""
Research Engine — powers the @research agent.

Three modes:
  1. web_search   — broad web search, summarise + present for source approval
  2. site_search  — scoped to a single domain (site:example.com)
  3. deep_dive    — multi-hop: search → read → score quality → synthesise top results

All modes deduplicate against existing notebook sources before presenting results.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ─── Quality-filter defaults for Deep Dive ──────────────────────────────────

@dataclass
class DeepDiveFilters:
    """Guardrails that control which results survive a deep dive."""
    recency_days: int = 30               # max article age in days
    min_word_count: int = 500            # ignore thin / stub articles
    min_outbound_links: int = 3          # minimum external references
    min_quality_score: float = 0.6       # 0-1 composite quality score threshold
    max_results: int = 5                 # only return top N
    topic_qualifiers: List[str] = field(default_factory=list)  # extra LLM-evaluated criteria


@dataclass
class ResearchResult:
    """A single research finding."""
    id: str
    title: str
    url: str
    snippet: str
    word_count: int = 0
    quality_score: float = 0.0
    quality_reasons: List[str] = field(default_factory=list)
    already_sourced: bool = False
    full_text: Optional[str] = None      # populated in deep-dive after scrape
    author: Optional[str] = None
    date: Optional[str] = None
    read_time: Optional[str] = None      # e.g. "3 min read"
    domain: Optional[str] = None         # e.g. "arxiv.org"


class ResearchEngine:
    """Orchestrates web/site/deep-dive research workflows."""

    # ── Web Search (mode 1) ──────────────────────────────────────────────

    async def web_search(
        self,
        query: str,
        notebook_id: str,
        max_results: int = 10,
        freshness: Optional[str] = None,
        on_status=None,
    ) -> List[ResearchResult]:
        """Broad web search with deduplication."""
        from services.web_scraper import web_scraper
        from storage.source_store import source_store

        if on_status:
            await on_status("Searching the web...")

        raw = await web_scraper.search_web(query, max_results=max_results, freshness=freshness)
        existing_urls = await self._get_existing_urls(notebook_id)

        results = []
        for i, r in enumerate(raw):
            url = r.get("url", "")
            results.append(ResearchResult(
                id=f"res-{i}",
                title=r.get("title", "Untitled"),
                url=url,
                snippet=r.get("snippet", ""),
                word_count=0,
                quality_score=0.0,
                already_sourced=self._is_duplicate(url, existing_urls),
                read_time=r.get("read_time"),
                domain=self._extract_domain(url),
            ))

        return results

    # ── Site Search (mode 2) ─────────────────────────────────────────────

    async def site_search(
        self,
        query: str,
        site: str,
        notebook_id: str,
        max_results: int = 10,
        on_status=None,
    ) -> List[ResearchResult]:
        """Site-scoped search (prepends site: to query)."""
        scoped_query = f"site:{site} {query}"
        return await self.web_search(
            scoped_query, notebook_id, max_results=max_results, on_status=on_status
        )

    # ── Deep Dive (mode 3) ───────────────────────────────────────────────

    async def deep_dive(
        self,
        query: str,
        notebook_id: str,
        filters: Optional[DeepDiveFilters] = None,
        on_status=None,
    ) -> List[ResearchResult]:
        """
        Multi-hop deep dive:
          1. Search web for candidate URLs
          2. Scrape top candidates in parallel
          3. Score each on quality metrics (word count, links, LLM evaluation)
          4. Filter + rank → return only top results above threshold
        """
        from services.web_scraper import web_scraper
        from storage.source_store import source_store

        if filters is None:
            filters = DeepDiveFilters()

        # Determine Brave freshness param from recency_days
        freshness = None
        if filters.recency_days <= 1:
            freshness = "pd"     # past day
        elif filters.recency_days <= 7:
            freshness = "pw"     # past week
        elif filters.recency_days <= 30:
            freshness = "pm"     # past month

        # ── Step 1: Search ───────────────────────────────────────────────
        if on_status:
            await on_status("Deep dive: searching for candidates...")

        raw = await web_scraper.search_web(query, max_results=20, freshness=freshness)
        existing_urls = await self._get_existing_urls(notebook_id)

        # Pre-filter: skip already-sourced URLs
        candidates = [r for r in raw if not self._is_duplicate(r.get("url", ""), existing_urls)]
        if not candidates:
            return []

        # ── Step 2: Scrape top candidates ────────────────────────────────
        if on_status:
            await on_status(f"Deep dive: reading {min(len(candidates), 10)} articles...")

        urls_to_scrape = [c["url"] for c in candidates[:10]]
        scraped = await web_scraper.scrape_urls(urls_to_scrape)

        # Merge scraped content with search metadata
        scraped_map: Dict[str, Dict] = {}
        for s in scraped:
            if s.get("success") and s.get("text"):
                scraped_map[s["url"]] = s

        # ── Step 3: Score quality ────────────────────────────────────────
        if on_status:
            await on_status("Deep dive: evaluating quality...")

        results: List[ResearchResult] = []
        for i, c in enumerate(candidates[:10]):
            url = c.get("url", "")
            s = scraped_map.get(url)
            if not s:
                continue

            text = s.get("text", "")
            wc = s.get("word_count", len(text.split()))

            # Basic quality signals
            reasons: List[str] = []
            score = 0.0

            # Word count / depth
            if wc >= filters.min_word_count:
                depth_bonus = min(0.25, (wc / 3000) * 0.25)
                score += depth_bonus
                reasons.append(f"{wc:,} words")
            else:
                reasons.append(f"Short ({wc} words)")

            # Outbound link density (count markdown-style or raw http links)
            link_count = len(re.findall(r'https?://', text))
            if link_count >= filters.min_outbound_links:
                score += 0.15
                reasons.append(f"{link_count} references")
            else:
                reasons.append(f"Few references ({link_count})")

            # Recency signal from metadata
            article_date = s.get("date")
            if article_date:
                try:
                    dt = datetime.fromisoformat(article_date.replace("Z", "+00:00"))
                    age_days = (datetime.now(dt.tzinfo) - dt).days if dt.tzinfo else (datetime.utcnow() - dt).days
                    if age_days <= filters.recency_days:
                        score += 0.15
                        reasons.append(f"{age_days}d old")
                    else:
                        reasons.append(f"Older ({age_days}d)")
                except Exception:
                    pass

            # Author presence (signals editorial quality)
            if s.get("author"):
                score += 0.05
                reasons.append(f"By {s['author']}")

            # Compute read_time from actual word count
            dd_minutes = max(1, round(wc / 238))
            if dd_minutes >= 5:
                dd_minutes = 5 * round(dd_minutes / 5)
            dd_read_time = f"{dd_minutes} min read" if dd_minutes < 5 else f"~{dd_minutes} min read"

            results.append(ResearchResult(
                id=f"dd-{i}",
                title=s.get("title", c.get("title", "Untitled")),
                url=url,
                snippet=c.get("snippet", ""),
                word_count=wc,
                quality_score=score,
                quality_reasons=reasons,
                already_sourced=False,
                full_text=text,
                author=s.get("author"),
                date=article_date,
                read_time=dd_read_time,
                domain=self._extract_domain(url),
            ))

        # ── Step 4: LLM quality evaluation (for topic-specific criteria) ─
        if filters.topic_qualifiers and results:
            if on_status:
                await on_status("Deep dive: applying quality criteria...")
            results = await self._llm_evaluate_quality(results, query, filters)

        # ── Step 5: Filter and rank ──────────────────────────────────────
        results = [r for r in results if r.quality_score >= filters.min_quality_score]
        results.sort(key=lambda r: r.quality_score, reverse=True)
        results = results[:filters.max_results]

        return results

    # ── LLM Quality Evaluation ───────────────────────────────────────────

    async def _llm_evaluate_quality(
        self,
        results: List[ResearchResult],
        query: str,
        filters: DeepDiveFilters,
    ) -> List[ResearchResult]:
        """Use LLM to evaluate topic-specific quality criteria on each result."""
        from services.ollama_client import ollama_client

        qualifiers_text = "\n".join(f"- {q}" for q in filters.topic_qualifiers)

        for result in results:
            if not result.full_text:
                continue

            # Use first 2000 chars to keep prompt small
            excerpt = result.full_text[:2000]

            prompt = f"""Evaluate this article for a research query.

Query: "{query}"

Quality criteria the user cares about:
{qualifiers_text}

Article title: {result.title}
Article excerpt:
{excerpt}

Rate the article on a scale of 0.0 to 1.0 for:
1. relevance — how well it matches the query
2. depth — how thorough and well-researched it is
3. criteria_match — how well it satisfies the quality criteria above

Respond with ONLY valid JSON:
{{"relevance": <0-1>, "depth": <0-1>, "criteria_match": <0-1>, "reasoning": "<one sentence>"}}"""

            try:
                resp = await ollama_client.generate(
                    prompt=prompt,
                    system="You are a research quality evaluator. Respond only with JSON.",
                    temperature=0.0,
                    timeout=15.0,
                )
                raw = resp.get("response", "").strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                    if raw.endswith("```"):
                        raw = raw[:-3]
                    raw = raw.strip()

                scores = json.loads(raw)
                llm_score = (
                    scores.get("relevance", 0.5) * 0.4
                    + scores.get("depth", 0.5) * 0.3
                    + scores.get("criteria_match", 0.5) * 0.3
                )
                # Blend LLM score with heuristic score (60/40 LLM-weighted)
                result.quality_score = (result.quality_score * 0.4) + (llm_score * 0.6)

                reasoning = scores.get("reasoning", "")
                if reasoning:
                    result.quality_reasons.append(reasoning)

            except Exception as e:
                logger.warning(f"LLM quality eval failed for {result.url}: {e}")
                # Keep heuristic score as-is

        return results

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _get_existing_urls(self, notebook_id: str) -> set:
        """Get all URLs already in this notebook's sources."""
        from storage.source_store import source_store
        try:
            sources = await source_store.list(notebook_id)
            urls = set()
            for s in sources:
                if s.get("url"):
                    urls.add(s["url"].rstrip("/"))
                if s.get("metadata", {}).get("url"):
                    urls.add(s["metadata"]["url"].rstrip("/"))
            return urls
        except Exception:
            return set()

    def _extract_domain(self, url: str) -> Optional[str]:
        """Extract clean domain from URL (e.g. 'arxiv.org' from 'https://arxiv.org/abs/...')."""
        if not url:
            return None
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.hostname or ""
            if host.startswith("www."):
                host = host[4:]
            return host or None
        except Exception:
            return None

    def _is_duplicate(self, url: str, existing_urls: set) -> bool:
        """Check if a URL (or its canonical form) already exists."""
        if not url:
            return False
        normalised = url.rstrip("/")
        return normalised in existing_urls

    def serialize_results(self, results: List[ResearchResult]) -> List[Dict[str, Any]]:
        """Convert results to JSON-safe dicts (strip full_text for SSE)."""
        out = []
        for r in results:
            d = asdict(r)
            d.pop("full_text", None)  # don't send full text over SSE
            out.append(d)
        return out


# Singleton
research_engine = ResearchEngine()
