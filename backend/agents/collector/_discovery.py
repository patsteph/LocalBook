"""DiscoveryMixin — extracted from the former agents/collector.py (Wave 6 split)."""
from ._models import *  # noqa: F401,F403


class DiscoveryMixin:
    AUTO_EXPAND_MAX_DOMAINS_PER_RUN = 3

    AUTO_EXPAND_MAX_RSS_PER_RUN = 3

    AUTO_EXPAND_MIN_DOMAIN_HITS = 3  # Domain must appear in ≥N approved items.

    async def run_first_sweep(self) -> Dict[str, Any]:
        """
        Run immediately after notebook setup to show instant value.
        Uses cached/fast sources only - no slow API calls.
        """
        logger.info(f"Running first sweep for notebook {self.notebook_id}")
        
        results = {
            "items_found": 0,
            "items_queued": 0,
            "sources_checked": 0,
            "duration_ms": 0
        }
        
        start = datetime.utcnow()
        
        # Quick keyword-based collection from configured sources
        # Combine subject with focus areas for targeted searches
        subject = self.config.subject.strip()
        sweep_keywords = []
        if subject and self.config.focus_areas:
            for area in self.config.focus_areas[:3]:
                area_stripped = area.strip()
                if subject.lower() not in area_stripped.lower():
                    sweep_keywords.append(f"{subject} {area_stripped}")
                else:
                    sweep_keywords.append(area_stripped)
        elif self.config.focus_areas:
            sweep_keywords = self.config.focus_areas[:3]
        elif subject:
            sweep_keywords = [subject]
        
        # Enrich sweep with seed domains from existing notebook sources
        try:
            seed_domains = await self._extract_seed_domains()
            if seed_domains:
                # Add site-scoped searches for proven domains
                for domain_kw in seed_domains.get("seed_domains", [])[:4]:
                    seed_query = f"{subject} {domain_kw}" if subject else domain_kw
                    if seed_query not in sweep_keywords:
                        sweep_keywords.append(seed_query)
                print(f"[COLLECTOR] First sweep enriched with {len(seed_domains.get('seed_domains', []))} seed domains")
        except Exception as _e:
            logger.debug(f"[collector] {type(_e).__name__}: {_e}")

        if sweep_keywords:
            items = await self._quick_collect(sweep_keywords)
            results["items_found"] = len(items)
            
            # Process and queue items
            for item in items:
                processed = await self._process_item(item)
                if not processed.is_duplicate:
                    await self._add_to_approval_queue(processed)
                    results["items_queued"] += 1
        
        results["duration_ms"] = (datetime.utcnow() - start).total_seconds() * 1000
        results["sources_checked"] = len(self.config.sources.get("rss_feeds", [])) + len(self.config.sources.get("web_pages", []))
        
        return results

    async def _quick_collect(self, keywords: List[str]) -> List[CollectedItem]:
        """Quick collection: web search + RSS feeds for each keyword."""
        items = []
        
        # 1. Web search via Brave API
        try:
            from services.web_scraper import web_scraper
            for kw in keywords[:5]:  # Cap keywords to limit API calls
                try:
                    results = await web_scraper.search_web(kw, max_results=5)
                    for r in results:
                        url = r.get("url", "")
                        if not url or url in self._known_urls:
                            continue
                        snippet = r.get("snippet", "")
                        title = r.get("title", "Untitled")
                        items.append(CollectedItem(
                            title=title,
                            url=url,
                            content=snippet,
                            preview=snippet[:300],
                            source_name=r.get("source", "web"),
                            source_type="web",
                            collected_at=datetime.utcnow(),
                        ))
                except Exception as e:
                    logger.debug(f"Quick collect search failed for '{kw}': {e}")
        except ImportError as _e:
            logger.debug(f"[collector] {type(_e).__name__}: {_e}")
        
        # 2. RSS feeds
        for feed_url in self.config.sources.get("rss_feeds", [])[:5]:
            try:
                rss_items = await self._collect_from_rss(feed_url, keywords)
                items.extend(rss_items[:3])  # Top 3 per feed
            except Exception as e:
                logger.debug(f"Quick collect RSS failed for {feed_url}: {e}")
        
        # 3. Deep-fetch: scrape full article text for items with thin content
        # This turns search snippets into real content for quality scoring
        try:
            from services.web_scraper import web_scraper
            urls_to_scrape = [it.url for it in items if it.url and len(it.content) < 500][:8]
            if urls_to_scrape:
                scraped = await web_scraper.scrape_urls(urls_to_scrape)
                url_to_text = {s["url"]: s for s in scraped if s.get("success") and s.get("text")}
                for it in items:
                    if it.url in url_to_text:
                        full = url_to_text[it.url]
                        if len(full["text"]) > len(it.content):
                            it.content = full["text"]
                            it.preview = full["text"][:300]
                            if full.get("title") and len(full["title"]) > len(it.title):
                                it.title = full["title"]
        except Exception as e:
            logger.debug(f"Quick collect scrape enrichment failed: {e}")
        
        logger.info(f"Quick collect found {len(items)} items from {len(keywords)} keywords")
        return items

    async def _extract_seed_domains(self) -> Dict[str, List[str]]:
        """
        Analyze existing notebook sources to extract proven-valuable domains and channels.
        Returns additional source config entries derived from what the user already curated.
        """
        from storage.source_store import source_store
        from urllib.parse import urlparse

        sources = await source_store.list(self.notebook_id)
        if not sources:
            return {}

        # Collect all URLs from existing sources
        urls = [s.get("url") for s in sources if s.get("url")]
        if not urls:
            return {}

        # Extract and count domains
        domain_counts: Dict[str, int] = {}
        youtube_channels: List[str] = []
        medium_authors: List[str] = []

        for url in urls:
            try:
                parsed = urlparse(url)
                domain = parsed.netloc.lower().replace("www.", "")

                # YouTube: extract channel/user patterns
                if "youtube.com" in domain:
                    path = parsed.path
                    if "/watch" in path:
                        youtube_channels.append("youtube")  # Can't extract channel from watch URL easily
                    elif "/@" in path or "/c/" in path or "/channel/" in path:
                        channel = path.split("/")[1] if len(path.split("/")) > 1 else ""
                        if channel:
                            youtube_channels.append(channel)
                    continue

                # Medium: extract author/publication
                if "medium.com" in domain:
                    parts = parsed.path.strip("/").split("/")
                    if parts and parts[0].startswith("@"):
                        medium_authors.append(parts[0])
                    elif parts and parts[0] not in ("", "p", "s"):
                        medium_authors.append(parts[0])

                # Skip generic social/platform domains
                skip_domains = {
                    "twitter.com", "x.com", "facebook.com", "linkedin.com",
                    "reddit.com", "github.com", "google.com", "t.co",
                    "bit.ly", "docs.google.com",
                }
                if domain in skip_domains:
                    continue

                domain_counts[domain] = domain_counts.get(domain, 0) + 1
            except Exception:
                continue

        # Build seed sources from domains that appear 2+ times (proven valuable)
        seed_sources: Dict[str, List[str]] = {}

        # Top domains → news keywords (site-scoped searches)
        frequent_domains = sorted(domain_counts.items(), key=lambda x: -x[1])
        site_keywords = []
        for domain, count in frequent_domains[:8]:
            if count >= 2:
                # Use site: scoped search for domains with multiple sources
                site_keywords.append(f"site:{domain}")
            elif count == 1:
                # Single-appearance domains still useful as web pages to monitor
                site_keywords.append(domain)

        if site_keywords:
            seed_sources["seed_domains"] = site_keywords

        # YouTube channels → youtube keywords
        unique_channels = list(set(c for c in youtube_channels if c != "youtube"))
        if unique_channels:
            seed_sources["youtube_channels"] = unique_channels[:5]

        # Medium authors → additional news keywords
        unique_medium = list(set(medium_authors))
        if unique_medium:
            seed_sources["medium_authors"] = unique_medium[:5]

        print(f"[COLLECTOR] Extracted seed domains from {len(urls)} existing sources: "
              f"{len(site_keywords)} domains, {len(unique_channels)} YT channels, {len(unique_medium)} Medium authors")

        return seed_sources

    async def _analyze_coverage_gaps(self) -> List[str]:
        """
        Identify focus areas that are underrepresented in existing sources.
        Returns gap-filling search keywords biased toward underserved topics.
        """
        if not self.config.focus_areas:
            return []

        from storage.source_store import source_store

        sources = await source_store.list(self.notebook_id)
        if not sources:
            return []  # Fresh notebook — no gaps to analyze yet

        subject = self.config.subject.strip()

        # Count how many sources mention each focus area (case-insensitive)
        area_counts: Dict[str, int] = {area: 0 for area in self.config.focus_areas}
        for src in sources:
            text = f"{src.get('filename', '')} {src.get('content', '')[:500]}".lower()
            for area in self.config.focus_areas:
                if area.lower() in text:
                    area_counts[area] += 1

        if not area_counts:
            return []

        avg_count = sum(area_counts.values()) / len(area_counts)
        gap_threshold = max(1, avg_count * 0.4)  # Areas with < 40% of average are gaps

        gap_keywords = []
        for area, count in sorted(area_counts.items(), key=lambda x: x[1]):
            if count < gap_threshold:
                kw = f"{subject} {area}" if subject and subject.lower() not in area.lower() else area
                gap_keywords.append(kw)

        if gap_keywords:
            logger.info(
                f"[COLLECTOR] Coverage gaps detected: {gap_keywords} "
                f"(counts: {area_counts})"
            )
            print(
                f"[COLLECTOR] 🎯 Coverage gaps: {gap_keywords[:5]} — "
                f"will bias collection toward underserved topics"
            )

        return gap_keywords[:5]

    async def auto_discover_sources(self) -> Dict[str, Any]:
        """Smart source auto-discovery — the collector's wander reflex.

        Analyzes recently approved items in this notebook to find:
        1. New domains that consistently produce approved content
        2. RSS feed URLs discovered in approved article content
        3. Outbound links from high-quality approved sources

        Always-on (no plateau gate, no user toggle). Auto-adds the top
        few new domains as site-scoped news_keywords and the top few new
        RSS feeds to the config. Capped per-run so this is a gentle
        wander rather than a flood — the collector finds new territory
        over time, not all at once.

        Skips when the notebook has < 5 sources (not enough signal yet).
        """
        from storage.source_store import source_store
        from urllib.parse import urlparse

        sources = await source_store.list(self.notebook_id)
        if len(sources) < 5:
            return {"discovered": 0, "auto_expanded": False, "reason": "too_few_sources"}

        # Analyze domains of approved sources
        domain_counts: Dict[str, int] = {}
        existing_domains: set = set()
        rss_candidates: List[str] = []

        for src in sources:
            url = src.get("url", "")
            if not url:
                continue
            try:
                parsed = urlparse(url)
                domain = parsed.netloc.lower().replace("www.", "")
                domain_counts[domain] = domain_counts.get(domain, 0) + 1
                existing_domains.add(domain)
            except Exception:
                continue

            # Check content for RSS/feed links (outbound references)
            content = src.get("content", "")[:3000]
            if content:
                import re
                feed_patterns = re.findall(
                    r'https?://[^\s"\'<>]+(?:/rss|/feed|/atom|\.xml)[^\s"\'<>]*',
                    content, re.IGNORECASE
                )
                for feed_url in feed_patterns[:3]:
                    if feed_url not in [f for f in self.config.sources.get("rss_feeds", [])]:
                        rss_candidates.append(feed_url)

        # Find domains that appear ≥N times but aren't in configured sources
        configured_domains = set()
        for page_url in self.config.sources.get("web_pages", []):
            try:
                configured_domains.add(urlparse(page_url).netloc.lower().replace("www.", ""))
            except Exception as _e:
                logger.debug(f"[collector] {type(_e).__name__}: {_e}")

        new_domains = []
        skip = {"twitter.com", "x.com", "facebook.com", "linkedin.com",
                "reddit.com", "google.com", "t.co", "bit.ly", "youtube.com"}
        for domain, count in sorted(domain_counts.items(), key=lambda x: -x[1]):
            if count >= self.AUTO_EXPAND_MIN_DOMAIN_HITS and domain not in configured_domains and domain not in skip:
                new_domains.append({"domain": domain, "count": count})

        unique_rss = list(set(rss_candidates))[:5]

        discovered = {
            "new_domains": new_domains[:8],
            "rss_candidates": unique_rss,
            "discovered": len(new_domains) + len(unique_rss),
            "auto_expanded": False,
        }

        # Auto-add top discoveries to config — the "wander" reflex. Capped
        # per run so the collector explores gradually rather than dumping
        # 20 new sources in one cycle. Stale or low-quality additions
        # naturally drop out over time because they generate fewer
        # approved items than the strong ones.
        if new_domains or unique_rss:
            expanded = False
            config_sources = dict(self.config.sources)
            added_domains: List[str] = []
            added_feeds: List[str] = []

            if new_domains:
                news_kw = list(config_sources.get("news_keywords", []))
                for nd in new_domains[:self.AUTO_EXPAND_MAX_DOMAINS_PER_RUN]:
                    site_kw = f"site:{nd['domain']} {self.config.subject}"
                    if site_kw not in news_kw:
                        news_kw.append(site_kw)
                        added_domains.append(nd['domain'])
                        expanded = True
                config_sources["news_keywords"] = news_kw

            if unique_rss:
                rss_list = list(config_sources.get("rss_feeds", []))
                for feed in unique_rss[:self.AUTO_EXPAND_MAX_RSS_PER_RUN]:
                    if feed not in rss_list:
                        rss_list.append(feed)
                        added_feeds.append(feed)
                        expanded = True
                config_sources["rss_feeds"] = rss_list

            if expanded:
                self.update_config({"sources": config_sources})
                discovered["auto_expanded"] = True
                discovered["added_domains"] = added_domains
                discovered["added_feeds"] = added_feeds
                print(
                    f"[COLLECTOR] 🔍 Auto-expanded sources for {self.notebook_id[:8]}: "
                    f"+{len(added_domains)} domains, +{len(added_feeds)} RSS feeds"
                )
                logger.info(
                    f"[SourceDiscovery] Auto-expanded {self.notebook_id[:8]} — "
                    f"domains: {added_domains}; feeds: {added_feeds}"
                )

        return discovered
