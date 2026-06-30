"""CollectionMixin — extracted from the former agents/collector.py (Wave 6 split)."""
from ._models import *  # noqa: F401,F403


class CollectionMixin:
    async def cross_reference_validate(self, items: List['CollectedItem']) -> List['CollectedItem']:
        """Phase 3: Cross-reference validation — boost confidence for claims found in multiple sources."""
        if len(items) < 2:
            return items
        
        # Group items by approximate topic (title word overlap)
        for i, item_a in enumerate(items):
            corroborating = 0
            a_words = set(item_a.title.lower().split())
            for j, item_b in enumerate(items):
                if i == j:
                    continue
                b_words = set(item_b.title.lower().split())
                # Significant overlap = corroborating source
                overlap = len(a_words & b_words) / max(len(a_words), 1)
                if overlap > 0.4:
                    corroborating += 1
            
            if corroborating >= 2:
                # Boost confidence for well-corroborated items
                boost = min(0.15, corroborating * 0.05)
                item_a.overall_confidence = min(1.0, item_a.overall_confidence + boost)
                if not hasattr(item_a, 'confidence_reasons') or item_a.confidence_reasons is None:
                    item_a.confidence_reasons = []
                item_a.confidence_reasons.append(f"Corroborated by {corroborating} other sources")
        
        return items

    def _enforce_diversity(
        self,
        items: List['CollectedItem'],
        max_per_domain: int = 3,
        max_total: int = 15,
    ) -> List['CollectedItem']:
        """
        Re-rank collected items to maximize diversity across domains and topics.

        Rules applied in order:
        1. Cap items per domain (default 3) — prevents one site from dominating
        2. Prefer items with low knowledge_overlap (genuinely new content)
        3. Prefer items flagged as is_new_topic
        4. Maintain relevance ordering within each tier

        Returns a re-ranked subset of items.
        """
        from urllib.parse import urlparse

        if not items:
            return items

        # Bucket items by domain
        domain_buckets: Dict[str, List[CollectedItem]] = {}
        for item in items:
            domain = "unknown"
            if item.url:
                try:
                    domain = urlparse(item.url).netloc.lower().replace("www.", "")
                except Exception as _e:
                    logger.debug(f"[collector] {type(_e).__name__}: {_e}")
            domain_buckets.setdefault(domain, []).append(item)

        # Build diversity score for each item:
        #   higher = more valuable for diversity
        #   new_topic bonus + low overlap bonus + domain scarcity bonus
        scored: List[tuple] = []
        domain_selected: Dict[str, int] = {}

        for item in items:
            domain = "unknown"
            if item.url:
                try:
                    domain = urlparse(item.url).netloc.lower().replace("www.", "")
                except Exception as _e:
                    logger.debug(f"[collector] {type(_e).__name__}: {_e}")

            diversity_score = 0.0

            # Prefer genuinely new topics
            if item.is_new_topic:
                diversity_score += 0.3

            # Prefer low knowledge overlap
            diversity_score += (1.0 - item.knowledge_overlap) * 0.3

            # Prefer domains with fewer items already selected
            selected_from_domain = domain_selected.get(domain, 0)
            if selected_from_domain >= max_per_domain:
                diversity_score -= 1.0  # Heavy penalty — over domain cap
            else:
                diversity_score += 0.2 / (1 + selected_from_domain)

            # Keep relevance as tiebreaker
            diversity_score += item.overall_confidence * 0.2

            scored.append((diversity_score, item, domain))

        # Sort by diversity score descending
        scored.sort(key=lambda x: -x[0])

        # Select items respecting domain cap
        selected: List[CollectedItem] = []
        domain_selected = {}

        for _, item, domain in scored:
            if len(selected) >= max_total:
                break
            if domain_selected.get(domain, 0) >= max_per_domain:
                continue
            selected.append(item)
            domain_selected[domain] = domain_selected.get(domain, 0) + 1

        # Log diversity stats
        domains_used = len(set(domain_selected.keys()))
        new_topics = sum(1 for i in selected if i.is_new_topic)
        logger.info(
            f"[COLLECTOR] Diversity filter: {len(items)} → {len(selected)} items, "
            f"{domains_used} domains, {new_topics} new topics"
        )
        print(
            f"[COLLECTOR] 🌐 Diversity: {len(items)} → {len(selected)} items "
            f"({domains_used} domains, {new_topics} new topics)"
        )

        return selected

    async def deep_dive_collect(
        self,
        queries: List[str],
        max_per_query: int = 5,
    ) -> List['CollectedItem']:
        """Use ResearchEngine deep_dive to find high-quality, fully-read articles.
        
        This is a premium strategy: search → scrape full text → quality score.
        More expensive but produces much better content than snippet-only collection.
        """
        from services.research_engine import research_engine, DeepDiveFilters
        
        items: List[CollectedItem] = []
        filters = DeepDiveFilters(
            recency_days=self.config.filters.get("max_age_days", 30),
            min_word_count=300,
            min_outbound_links=1,
            min_quality_score=0.3,
            max_results=max_per_query,
        )
        
        for query in queries[:4]:  # Cap to limit API + LLM cost
            # Mid-flight yield point: a scheduled collection's deep-dive is the
            # longest, heaviest phase (~80 s of web research + scraping). If the
            # user starts a foreground op (visual/chat) after this collection
            # began, pause between queries so we don't saturate Ollama/RAM. No-op
            # for user-triggered "Collect Now" (not a yieldable_background ctx).
            from services.memory_steward import yield_if_background
            await yield_if_background()
            try:
                results = await research_engine.deep_dive(
                    query=query,
                    notebook_id=self.notebook_id,
                    filters=filters,
                )
                for r in results:
                    if r.url in self._known_urls:
                        continue
                    content = r.full_text or r.snippet
                    if len(content) < 200:
                        continue
                    items.append(CollectedItem(
                        title=r.title,
                        url=r.url,
                        content=content,
                        preview=r.snippet[:300] or content[:300],
                        source_name=r.domain or "web",
                        source_type="web",
                        collected_at=datetime.utcnow(),
                        content_hash=self._generate_content_hash(content),
                    ))
            except Exception as e:
                logger.warning(f"Deep dive failed for '{query}': {e}")
        
        logger.info(f"Deep dive collected {len(items)} items from {len(queries)} queries")
        return items

    async def iterative_search_reflect(
        self,
        initial_queries: List[str],
        task: Dict[str, Any],
        max_iterations: int = 3,
    ) -> List['CollectedItem']:
        """IterDRAG-style loop: search → summarize → reflect on gaps → re-search.
        
        Each iteration:
        1. Search + scrape using current queries
        2. Summarize what we found so far (fast model, ~50 tokens)
        3. Reflect: identify knowledge gaps (fast model, ~50 tokens)
        4. If gaps found → generate new queries → loop
        
        Token budget: ~3 fast-model calls per iteration × max_iterations.
        Prompts are kept SHORT to minimize token usage.
        """
        import time as _time
        
        deadline = task.get("_deadline")
        all_items: List[CollectedItem] = []
        findings_so_far: List[str] = []  # Short title summaries, not full text
        queries_used: set = set()
        current_queries = list(initial_queries)
        
        for iteration in range(max_iterations):
            # Mid-flight yield: pause a scheduled collection between search
            # iterations if a foreground op started (no-op for "Collect Now").
            from services.memory_steward import yield_if_background
            await yield_if_background()

            # Budget check
            if deadline and _time.time() > deadline - 90:
                logger.info(f"[IterSearch] Stopping at iteration {iteration} — deadline approaching")
                break
            
            # Skip queries we already used
            fresh_queries = [q for q in current_queries if q.lower() not in queries_used]
            if not fresh_queries:
                logger.info(f"[IterSearch] No fresh queries at iteration {iteration} — stopping")
                break
            
            print(f"[COLLECTOR] 🔄 Iteration {iteration+1}/{max_iterations}: {len(fresh_queries)} queries")
            
            # 1. Search + scrape
            iteration_items = await self.deep_dive_collect(fresh_queries[:4])
            for q in fresh_queries[:4]:
                queries_used.add(q.lower())
            
            if not iteration_items:
                # Try standard web search as fallback
                iteration_items = await self._quick_collect(fresh_queries[:3])
                for q in fresh_queries[:3]:
                    queries_used.add(q.lower())
            
            # Dedup against items already found in previous iterations
            existing_urls = {it.url for it in all_items if it.url}
            new_items = [it for it in iteration_items if not it.url or it.url not in existing_urls]
            all_items.extend(new_items)
            
            # Track what we found (titles only — keep token budget low)
            for it in new_items:
                findings_so_far.append(it.title)
            
            print(f"[COLLECTOR]   Found {len(new_items)} new items (total: {len(all_items)})")
            
            # 2+3. Reflect on gaps and generate new queries (single LLM call)
            if iteration < max_iterations - 1 and len(all_items) < 15:
                new_queries = await self._reflect_and_generate_queries(
                    findings_so_far, list(queries_used), task
                )
                if new_queries:
                    current_queries = new_queries
                    print(f"[COLLECTOR]   🎯 Gap-filling queries: {new_queries}")
                else:
                    logger.info(f"[IterSearch] Reflection found no gaps — stopping")
                    break
            else:
                break
        
        # Store iteration metadata in the task for history recording
        task["_iteration_count"] = min(iteration + 1, max_iterations)
        task["_total_queries_used"] = len(queries_used)
        
        logger.info(
            f"[IterSearch] Complete: {len(all_items)} items from "
            f"{len(queries_used)} queries across {min(iteration+1, max_iterations)} iterations"
        )
        return all_items

    async def _reflect_and_generate_queries(
        self,
        findings_titles: List[str],
        queries_used: List[str],
        task: Dict[str, Any],
    ) -> List[str]:
        """Single fast-model LLM call: assess gaps and generate new queries.
        
        Prompt is intentionally SHORT (~200 tokens input) to minimize cost.
        """
        subject = self.config.subject.strip()
        intent = self.config.intent or ""
        focus = ", ".join(self.config.focus_areas[:5]) if self.config.focus_areas else ""
        
        # Keep findings list short
        findings_text = "\n".join(f"- {t}" for t in findings_titles[-10:])
        used_text = ", ".join(queries_used[-8:])
        
        prompt = f"""Research: {subject or intent}
Focus: {focus}

Found so far:
{findings_text}

Queries already used: {used_text}

What important aspects are MISSING? Generate 3-4 NEW search queries to fill gaps.
Rules: each query 3-8 words, don't repeat used queries, be specific.
Respond ONLY with a JSON array: ["query1", "query2", ...]"""

        try:
            import asyncio as _asyncio
            response = await _asyncio.wait_for(
                ollama_service.generate(
                    prompt=prompt,
                    system="You are a research assistant. Respond only with a JSON array.",
                    model=settings.ollama_fast_model,
                    temperature=0.6,
                ),
                timeout=20,
            )
            text = response.get("response", "")
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                parsed = json.loads(text[start:end])
                if isinstance(parsed, list):
                    return [q.strip() for q in parsed if isinstance(q, str) and len(q.strip()) > 3][:4]
        except Exception as e:
            logger.debug(f"Reflect+generate failed: {e}")
        
        return []

    async def execute_collection_task(self, task: Dict[str, Any]) -> List['CollectedItem']:
        """
        Execute a collection task assigned by the Curator.
        
        The Collector's config serves as guardrails - we combine:
        - task: What Curator wants us to find (directives, specific queries)
        - config: What this notebook cares about (intent, focus areas, sources)
        
        Args:
            task: Dict from Curator containing:
                - notebook_id: Which notebook this is for
                - intent: The notebook's intent (from config)
                - focus_areas: Topics to focus on
                - sources: Where to look
                - curator_directive: Optional specific instruction from Curator
                - specific_query: Optional specific search query
                - avoid_similar_to: Optional list of content to avoid duplicating
        
        Returns:
            List of CollectedItem ready for Curator's judgment
        """
        from services.content_fetcher import unified_fetcher
        import time as _time
        
        deadline = task.get("_deadline") or None
        
        print(f"[COLLECTOR] execute_collection_task starting for {self.notebook_id}")
        logger.info(f"Executing Curator-assigned task for notebook {self.notebook_id}")
        
        # Auto-bootstrap sources on first run if intent exists but sources are empty
        try:
            from agents._bootstrap_sources import auto_bootstrap_sources
            boot = await auto_bootstrap_sources(self)
            if boot.get("bootstrapped") and boot.get("added", 0) > 0:
                # Reload config so the rest of this method sees the new sources
                self.config = self.get_config()
                task["sources"] = self.config.sources
        except Exception as _boot_err:
            logger.debug(f"Bootstrap check failed (non-fatal): {_boot_err}")
        
        collected_items: List[CollectedItem] = []
        
        # ── Build search keywords ──
        # Priority: Curator smart queries > coverage gaps > static config fallback
        keywords = []
        subject = self.config.subject.strip()
        
        # 1. Smart queries from Curator (LLM-generated, specific and targeted)
        smart_queries = task.get("smart_queries", [])
        if smart_queries:
            keywords.extend(smart_queries)
            print(f"[COLLECTOR] Using {len(smart_queries)} Curator smart queries as primary keywords")
        
        # 2. Coverage gap keywords (underserved focus areas)
        try:
            gap_keywords = await self._analyze_coverage_gaps()
            if gap_keywords:
                for gk in gap_keywords:
                    if gk not in keywords:
                        keywords.append(gk)
        except Exception as gap_err:
            print(f"[COLLECTOR] Coverage gap analysis failed (non-fatal): {gap_err}")
        
        # 3. Specific query from Curator (e.g. user-triggered "Collect Now" with a topic)
        if task.get("specific_query"):
            keywords.insert(0, task["specific_query"])
        
        # 4. Fallback: static subject + focus areas (only if nothing better available)
        if not keywords:
            if subject and self.config.focus_areas:
                for area in self.config.focus_areas[:5]:
                    area_stripped = area.strip()
                    if subject.lower() not in area_stripped.lower():
                        keywords.append(f"{subject} {area_stripped}")
                    else:
                        keywords.append(area_stripped)
                keywords.append(subject)
            elif self.config.focus_areas:
                keywords.extend(self.config.focus_areas[:5])
            elif subject:
                keywords.append(subject)
        
        # Always include subject as a catch-all if we have one and it's not already there
        if subject and subject not in keywords:
            keywords.append(subject)
        
        # ── Strategy selection (CBR-informed) ──
        # Check if CBR has a recommendation based on historical success
        strategy = task.get("strategy", "auto")
        if strategy == "auto":
            try:
                from services.collection_history import get_recommended_strategy
                cbr_rec = get_recommended_strategy(self.notebook_id)
                if cbr_rec:
                    strategy = cbr_rec
                    print(f"[COLLECTOR] 📊 CBR recommends '{cbr_rec}' strategy based on past success")
                else:
                    # Always prefer iterative — it uses Brave Search + trafilatura
                    # (the "standard" path uses a weak regex scraper).
                    # With deadline: fewer iterations. Without: full exploration.
                    strategy = "iterative"
            except Exception:
                strategy = "iterative"
        
        if strategy == "iterative" and keywords:
            # Fewer iterations when deadline is tight (manual collect-now)
            max_iter = 2 if deadline else 3
            print(f"[COLLECTOR] 🧪 Using ITERATIVE search-reflect strategy ({len(keywords)} seed queries, {max_iter} iterations)")
            iterative_items = await self.iterative_search_reflect(
                initial_queries=keywords,
                task=task,
                max_iterations=max_iter,
            )
            if iterative_items:
                collected_items.extend(iterative_items)
                print(f"[COLLECTOR] Iterative strategy yielded {len(iterative_items)} items — skipping standard fetch")
                # Skip standard fetcher — go straight to processing
                # But still fetch from RSS/configured sources for breadth
                # Include feed_pages and web_pages so user-added sources are always checked
                try:
                    from services.content_fetcher import unified_fetcher as _uf
                    sources = task.get("sources", self.config.sources)
                    supplement_sources = {
                        "rss_feeds": sources.get("rss_feeds", []),
                        "feed_pages": sources.get("feed_pages", []),
                    }
                    if any(supplement_sources.values()):
                        rss_items = await _uf.fetch_all(supplement_sources, keywords[:3])
                        for fetched in rss_items:
                            item = CollectedItem(
                                title=fetched.title,
                                url=fetched.url,
                                content=fetched.content,
                                preview=fetched.summary or fetched.content[:300],
                                source_name=fetched.source_name,
                                source_type=fetched.source_type,
                                source_url=fetched.source_url,
                                collected_at=fetched.published_date or datetime.utcnow(),
                                content_hash=fetched.content_hash,
                            )
                            collected_items.append(item)
                        print(f"[COLLECTOR] Source supplement (RSS+feed pages): +{len(rss_items)} items")
                except Exception as rss_err:
                    logger.debug(f"RSS supplement failed (non-fatal): {rss_err}")
                
                # Jump to processing (skip the standard fetch block below)
                return await self._process_and_diversify(collected_items, task, deadline)
        
        elif strategy == "deep_dive" and keywords:
            print(f"[COLLECTOR] 🔬 Using DEEP DIVE strategy ({len(keywords)} queries)")
            deep_items = await self.deep_dive_collect(keywords[:4])
            if deep_items:
                collected_items.extend(deep_items)
                return await self._process_and_diversify(collected_items, task, deadline)
        
        # ── Standard strategy (default for manual/deadline runs) ──
        
        # Get sources from task or fall back to config
        sources = task.get("sources", self.config.sources)
        
        # Enrich sources with seed domains from existing notebook content
        try:
            seed_domains = await self._extract_seed_domains()
            if seed_domains:
                # Deep copy to avoid mutating config
                sources = {k: list(v) if isinstance(v, list) else v for k, v in sources.items()}
                
                # Add site-scoped news keywords from proven domains
                existing_news = set(sources.get("news_keywords", []))
                for domain_kw in seed_domains.get("seed_domains", []):
                    # e.g. "site:levelup.gitconnected.com" + subject
                    seed_query = f"{subject} {domain_kw}" if subject else domain_kw
                    if seed_query not in existing_news:
                        sources.setdefault("news_keywords", []).append(seed_query)
                
                # Add YouTube channel keywords
                for channel in seed_domains.get("youtube_channels", []):
                    yt_query = f"{subject} {channel}" if subject else channel
                    if yt_query not in set(sources.get("youtube_keywords", [])):
                        sources.setdefault("youtube_keywords", []).append(yt_query)
                
                # Add Medium author searches
                for author in seed_domains.get("medium_authors", []):
                    medium_query = f"site:medium.com {author} {subject}" if subject else f"site:medium.com {author}"
                    if medium_query not in existing_news:
                        sources.setdefault("news_keywords", []).append(medium_query)
                
                print(f"[COLLECTOR] Enriched sources with seeds: {len(seed_domains.get('seed_domains', []))} domains, "
                      f"{len(seed_domains.get('youtube_channels', []))} YT, {len(seed_domains.get('medium_authors', []))} Medium")
        except Exception as seed_err:
            print(f"[COLLECTOR] Seed domain extraction failed (non-fatal): {seed_err}")
        
        # Use unified fetcher to collect from ALL source types
        # Give fetching at most 60s so processing/judgment still have time
        fetch_timeout = 60
        if deadline:
            fetch_timeout = min(60, max(15, deadline - _time.time() - 60))  # Leave 60s for processing+judgment
        print(f"[COLLECTOR] Fetching from sources: {list(sources.keys())} with {len(keywords)} keywords (timeout: {fetch_timeout:.0f}s)")
        try:
            import asyncio
            fetched_items = await asyncio.wait_for(
                unified_fetcher.fetch_all(sources, keywords),
                timeout=fetch_timeout
            )
            print(f"[COLLECTOR] Unified fetcher returned {len(fetched_items)} items")
            
            # Convert FetchedItem to CollectedItem
            for fetched in fetched_items:
                item = CollectedItem(
                    title=fetched.title,
                    url=fetched.url,
                    content=fetched.content,
                    preview=fetched.summary or fetched.content[:300],
                    source_name=fetched.source_name,
                    source_type=fetched.source_type,
                    source_url=fetched.source_url,
                    collected_at=fetched.published_date or datetime.utcnow(),
                    content_hash=fetched.content_hash
                )
                collected_items.append(item)
                
        except asyncio.TimeoutError:
            # Fetch timed out — continue with whatever items we already collected
            print(f"[COLLECTOR] Fetch timed out after {fetch_timeout:.0f}s — continuing with {len(collected_items)} items already gathered")
        except Exception as e:
            print(f"[COLLECTOR] Unified fetcher error: {type(e).__name__}: {e}")
            logger.error(f"Unified fetcher error: {e}")
            # Fall back to legacy RSS-only collection
            for feed_url in sources.get("rss_feeds", [])[:10]:
                try:
                    items = await self._collect_from_rss(feed_url, keywords)
                    collected_items.extend(items)
                except Exception as e:
                    logger.error(f"RSS collection failed for {feed_url}: {e}")
        
        # Resource list expansion: detect pages that are lists of URLs
        # (e.g., "Top 100 AI RSS Feeds") and fetch the top ~10 individual sites
        # Skip if deadline is tight — this is nice-to-have, not essential
        expanded_items = []
        items_to_remove = []
        skip_expansion = deadline and _time.time() > deadline - 45
        if skip_expansion:
            print(f"[COLLECTOR] Skipping resource list expansion — only {deadline - _time.time():.0f}s left")
        for idx, item in enumerate([] if skip_expansion else collected_items):
            try:
                urls_found = self._detect_resource_list(item)
                if urls_found:
                    print(f"[COLLECTOR] Detected resource list: '{item.title}' — found {len(urls_found)} URLs")
                    items_to_remove.append(idx)  # Always remove the list page itself
                    
                    # Separate RSS feeds from regular web pages
                    rss_urls = []
                    web_urls = []
                    for url in urls_found[:10]:  # Top 10 sites
                        lower = url.lower()
                        if any(hint in lower for hint in ['/rss', '/feed', '/atom', '.xml', 'feeds.']):
                            rss_urls.append(url)
                        else:
                            web_urls.append(url)
                    
                    print(f"[COLLECTOR] List expansion: {len(rss_urls)} RSS feeds, {len(web_urls)} web pages")
                    
                    # Parse RSS feeds to get latest articles from each
                    # Empty keywords = take latest articles from each feed
                    # (the feeds are already topically relevant since they came from a curated list)
                    for feed_url in rss_urls[:8]:
                        try:
                            rss_items = await self._collect_from_rss(feed_url, [])
                            # Take the top 2 articles per feed
                            for rss_item in rss_items[:2]:
                                expanded_items.append(rss_item)
                            if rss_items:
                                print(f"[COLLECTOR]   RSS {feed_url[:60]} → {len(rss_items[:2])} articles")
                        except Exception as rss_err:
                            print(f"[COLLECTOR]   RSS {feed_url[:60]} failed: {rss_err}")
                    
                    # Scrape web pages directly
                    if web_urls:
                        from services.web_scraper import web_scraper
                        scraped = await web_scraper.scrape_urls(web_urls[:8])
                        for result in scraped:
                            if result.get("success") and result.get("text") and len(result["text"]) > 200:
                                expanded_item = CollectedItem(
                                    title=result.get("title", result.get("url", "Untitled")),
                                    url=result.get("url"),
                                    content=result["text"],
                                    preview=result["text"][:300],
                                    source_name=f"via {item.title[:40]}",
                                    source_type="web",
                                    source_url=result.get("url", ""),
                                    collected_at=datetime.utcnow(),
                                )
                                expanded_items.append(expanded_item)
                    
                    print(f"[COLLECTOR] Expanded resource list into {len(expanded_items)} individual sources")
            except Exception as rl_err:
                print(f"[COLLECTOR] Resource list expansion error for '{item.title}': {rl_err}")
                logger.warning(f"Resource list expansion failed for '{item.title}': {rl_err}")
        
        # Always remove list pages (even if expansion yielded nothing — the raw list isn't useful)
        if items_to_remove:
            collected_items = [item for idx, item in enumerate(collected_items) if idx not in items_to_remove]
            collected_items.extend(expanded_items)
        
        # Delegate to shared processing pipeline
        return await self._process_and_diversify(collected_items, task, deadline)

    async def _process_and_diversify(
        self,
        collected_items: List['CollectedItem'],
        task: Dict[str, Any],
        deadline: Optional[float] = None,
    ) -> List['CollectedItem']:
        """Shared pipeline: score, dedup, contextualize, enforce diversity.
        
        Used by all collection strategies (standard, deep_dive, iterative).
        """
        import asyncio
        import time as _time
        
        # Process all items (scoring, duplicate detection)
        print(f"[COLLECTOR] Processing {len(collected_items)} raw items...")
        
        process_semaphore = asyncio.Semaphore(4)
        avoid_similar = task.get("avoid_similar_to", [])
        
        async def _process_bounded(item):
            """Process a single item with bounded concurrency."""
            # If deadline is very tight, skip LLM scoring and use heuristic
            if deadline and _time.time() > deadline - 20:
                return None  # Will be picked up in next auto-collection
            async with process_semaphore:
                try:
                    processed = await self._process_item(item)
                    if processed.is_duplicate:
                        return None
                    # Skip items similar to what Curator said to avoid
                    for avoid_content in avoid_similar:
                        if self._content_similarity(processed.content, avoid_content) > 0.8:
                            return None
                    return processed
                except Exception as proc_err:
                    logger.debug(f"Processing failed for '{item.title}' (non-fatal): {proc_err}")
                    return None
        
        process_results = await asyncio.gather(*[_process_bounded(item) for item in collected_items])
        processed_items = [r for r in process_results if r is not None]
        print(f"[COLLECTOR] {len(processed_items)} items passed processing (from {len(collected_items)} raw)")
        
        # Contextualize items — Temporal Intelligence (Enhancement #6)
        # Adds delta insights: what's new vs what user already knows
        # Skip during manual "Collect Now" (deadline set) — enrichment, not essential
        if deadline and _time.time() > deadline - 60:
            print(f"[COLLECTOR] Skipping contextualization — only {deadline - _time.time():.0f}s left for judgment")
        else:
            ctx_semaphore = asyncio.Semaphore(4)
            
            async def _contextualize_bounded(item):
                async with ctx_semaphore:
                    try:
                        await self.contextualize_item(item)
                    except Exception as ctx_err:
                        logger.debug(f"Contextualization failed for '{item.title}' (non-fatal): {ctx_err}")
            
            await asyncio.gather(*[_contextualize_bounded(item) for item in processed_items])
        
        # Cross-reference validation — boost confidence for corroborated items
        if len(processed_items) >= 3:
            processed_items = await self.cross_reference_validate(processed_items)
        
        # Enforce diversity — cap per-domain, prefer new topics and low-overlap items
        diverse_items = self._enforce_diversity(
            processed_items,
            max_per_domain=3,
            max_total=self.config.schedule.get("max_items_per_run", 15),
        )
        
        logger.info(f"Task execution complete: {len(diverse_items)} diverse items from {len(processed_items)} processed")
        return diverse_items

    def _detect_resource_list(self, item: CollectedItem) -> Optional[List[str]]:
        """Detect if content is a list/directory of URLs rather than actual content.
        
        A resource list page has:
        - Many URLs (>5 unique domains)
        - Short text between URLs (list-like, not article-like)
        - Title often contains "list", "top", "best", "resources", "directory"
        
        Returns list of extracted URLs if it's a resource list, None otherwise.
        """
        import re
        
        content = item.content or ""
        title_lower = (item.title or "").lower()
        
        # Quick check: does the title suggest a list page?
        list_indicators = ['top ', 'best ', 'list of', 'resources', 'directory', 'curated', 
                          'awesome ', 'collection of', 'comprehensive list', 'ultimate list',
                          'rss feed', 'feeds', 'sources for']
        title_is_list = any(indicator in title_lower for indicator in list_indicators)
        
        # Extract all URLs from content
        url_pattern = re.compile(
            r'https?://[^\s<>"\'\)\]\,]+',
            re.IGNORECASE
        )
        urls = url_pattern.findall(content)
        
        # Deduplicate and filter
        seen_domains = set()
        unique_urls = []
        for url in urls:
            # Clean trailing punctuation
            url = url.rstrip('.,;:)]}')
            try:
                from urllib.parse import urlparse
                parsed = urlparse(url)
                domain = parsed.netloc.lower()
                # Skip common non-content domains
                if domain in ('github.com', 'twitter.com', 'x.com', 't.co', 'bit.ly'):
                    continue
                if domain and domain not in seen_domains:
                    seen_domains.add(domain)
                    unique_urls.append(url)
            except Exception:
                continue
        
        # Decision: is this a resource list?
        # Need both high URL density AND list-like title or structure
        words_in_content = len(content.split())
        url_density = len(unique_urls) / max(words_in_content, 1) * 100  # URLs per 100 words
        
        is_resource_list = False
        
        if len(unique_urls) >= 5 and title_is_list:
            is_resource_list = True
        elif len(unique_urls) >= 8 and url_density > 1.5:
            # Many URLs even without list-like title
            is_resource_list = True
        elif len(unique_urls) >= 10:
            # Very many unique domains — almost certainly a list
            is_resource_list = True
        
        if is_resource_list:
            # Filter to keep only URLs that look like content sites (not images, stylesheets, etc.)
            content_urls = []
            for url in unique_urls:
                lower_url = url.lower()
                if any(ext in lower_url for ext in ['.png', '.jpg', '.gif', '.css', '.js', '.svg', '.ico']):
                    continue
                content_urls.append(url)
            
            if len(content_urls) >= 3:
                return content_urls[:15]  # Cap at 15 to avoid overwhelming
        
        return None

    async def _collect_from_rss(
        self, 
        feed_url: str, 
        search_terms: List[str]
    ) -> List[CollectedItem]:
        """Collect items from an RSS feed"""
        import feedparser
        
        items = []
        start_time = datetime.utcnow()
        
        try:
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:20]:  # Limit entries per feed
                # Check if entry matches any search term
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                content = f"{title} {summary}".lower()
                
                # Filter by search terms if provided — but skip filtering for
                # explicitly-subscribed feeds (YouTube channels) where the user
                # wants ALL new content, not just keyword matches.
                is_subscribed_feed = "youtube.com/feeds/" in (feed_url or "")
                if search_terms and not is_subscribed_feed:
                    if not any(term.lower() in content for term in search_terms):
                        continue
                
                item = CollectedItem(
                    title=title,
                    url=entry.get("link"),
                    content=summary,
                    preview=summary[:300] if summary else title,
                    source_name=feed.feed.get("title", feed_url),
                    source_type="rss",
                    source_url=feed_url,
                    collected_at=datetime.utcnow()
                )
                items.append(item)
            
            # Update source health
            response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            self.update_source_health(
                source_id=feed_url,
                source_url=feed_url,
                success=True,
                response_time_ms=response_time,
                items_found=len(items)
            )
            
        except Exception as e:
            logger.error(f"RSS feed error for {feed_url}: {e}")
            self.update_source_health(
                source_id=feed_url,
                source_url=feed_url,
                success=False
            )
        
        return items

    async def _collect_from_webpage(
        self, 
        page_url: str, 
        search_terms: List[str]
    ) -> List[CollectedItem]:
        """Collect items from a webpage via trafilatura scraping."""
        items = []
        start_time = datetime.utcnow()
        
        try:
            from services.web_scraper import web_scraper
            scraped = await web_scraper._scrape_single(page_url)
            
            if not scraped or not scraped.get("success") or not scraped.get("text"):
                self.update_source_health(page_url, page_url, success=False)
                return items
            
            text = scraped["text"]
            title = scraped.get("title", page_url)
            
            # Filter by search terms if provided
            if search_terms:
                text_lower = f"{title} {text}".lower()
                if not any(term.lower() in text_lower for term in search_terms):
                    return items
            
            # Skip shallow pages — under 1000 chars is typically just header/nav noise
            if len(text) < 1000:
                return items
            
            item = CollectedItem(
                title=title,
                url=page_url,
                content=text,
                preview=text[:300],
                source_name=scraped.get("domain", page_url),
                source_type="web",
                source_url=page_url,
                collected_at=datetime.utcnow(),
            )
            items.append(item)
            
            response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            self.update_source_health(page_url, page_url, success=True,
                                      response_time_ms=response_time, items_found=1)
        except Exception as e:
            logger.error(f"Webpage scrape error for {page_url}: {e}")
            self.update_source_health(page_url, page_url, success=False)
        
        return items

    async def _process_item(self, item: CollectedItem) -> CollectedItem:
        """Process a collected item: score, check duplicates, etc."""
        
        # URL-based duplicate detection (survives restarts via _init_dedup_state)
        if item.url and item.url in self._known_urls:
            item.is_duplicate = True
            logger.debug(f"URL duplicate: {item.url}")
            return item
        
        # Generate content hash for duplicate detection
        item.content_hash = self._generate_content_hash(item.content)
        
        # Check for duplicates (Enhancement #4)
        if item.content_hash in self._content_hashes:
            item.is_duplicate = True
            return item
        
        # Check semantic similarity for near-duplicates
        duplicate = await self._find_semantic_duplicate(item)
        if duplicate:
            item.is_duplicate = True
            item.duplicate_of = duplicate
            return item
        
        # Calculate confidence scores (Enhancement #8)
        item = await self._calculate_confidence(item)
        
        # Add to tracking sets
        self._content_hashes.add(item.content_hash)
        if item.url:
            self._known_urls.add(item.url)
        
        return item
