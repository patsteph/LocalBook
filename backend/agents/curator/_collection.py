"""CuratorCollectionMixin — extracted from the former agents/curator.py (Wave 3 split)."""
from ._models import *  # noqa: F401,F403


class CuratorCollectionMixin:
    async def suggest_collector_keywords_from_notes(self, notebook_id: str) -> Dict[str, Any]:
        """Extract themes from a notebook's notes and suggest new collector focus areas.

        Returns dict with:
          - note_themes: list of extracted themes
          - current_focus: existing collector focus_areas
          - suggestions: new keywords/focus_areas not already covered
        """
        from storage.source_store import source_store
        from agents.collector import get_collector

        all_sources = await source_store.list(notebook_id)
        notes = [s for s in all_sources if s.get("type") == "note"]
        if not notes:
            return {"note_themes": [], "current_focus": [], "suggestions": [], "message": "No notes in this notebook"}

        # Build a digest of note titles and content snippets
        note_digest_parts = []
        for n in notes[:15]:
            title = n.get("filename", "Untitled")
            content = (n.get("content") or "")[:500]
            note_digest_parts.append(f"- {title}: {content}")
        note_digest = "\n".join(note_digest_parts)

        # Get current collector config
        try:
            collector = get_collector(notebook_id)
            config = collector.get_config()
            current_focus = config.focus_areas or []
            subject = config.subject or ""
        except Exception:
            current_focus = []
            subject = ""

        # Use LLM to extract themes and suggest keywords
        prompt = f"""Analyze these user notes from a research notebook and extract the key themes and topics the user is thinking about.

NOTES:
{note_digest}

CURRENT COLLECTOR FOCUS AREAS: {', '.join(current_focus) if current_focus else 'None set'}
NOTEBOOK SUBJECT: {subject or 'Not specified'}

Return a JSON object with:
1. "note_themes" — list of 3-7 key themes/topics extracted from the notes (short phrases)
2. "suggestions" — list of 2-5 NEW search keywords or focus areas that the collector should add, based on the note themes but NOT already in the current focus areas. Each should be specific enough to yield good search results.

Return ONLY valid JSON, no explanation."""

        try:
            from services.ollama_service import ollama_service
            from config import settings
            import json

            response = await ollama_service.generate(
                prompt=prompt,
                system="You extract research themes from notes and suggest collector search keywords. Return only valid JSON.",
                model=settings.ollama_fast_model,
                temperature=0.3,
                timeout=30.0,
                num_predict=500,
                extra_options={"keep_alive": "10m"},
            )
            text = response.get("response", "").strip()
            # Parse JSON from response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                return {
                    "note_themes": data.get("note_themes", []),
                    "current_focus": current_focus,
                    "suggestions": data.get("suggestions", []),
                    "note_count": len(notes),
                    "subject": subject,
                }
        except Exception as e:
            logger.error(f"[Curator] Note theme extraction failed: {e}")

        return {"note_themes": [], "current_focus": current_focus, "suggestions": [], "note_count": len(notes)}

    async def apply_note_suggestions_to_collector(self, notebook_id: str, keywords: List[str]) -> Dict[str, Any]:
        """Apply suggested keywords from note analysis to a notebook's collector config.

        Only adds keywords that aren't already in focus_areas.
        """
        from agents.collector import get_collector

        collector = get_collector(notebook_id)
        config = collector.get_config()
        existing = set(a.lower() for a in (config.focus_areas or []))
        new_keywords = [k for k in keywords if k.lower() not in existing]

        if not new_keywords:
            return {"added": [], "message": "All suggested keywords already in focus areas"}

        updated_focus = list(config.focus_areas or []) + new_keywords
        collector.update_config({"focus_areas": updated_focus})

        return {
            "added": new_keywords,
            "total_focus_areas": len(updated_focus),
            "message": f"Added {len(new_keywords)} new focus area(s) to collector",
        }

    async def _build_exploration_context(
        self,
        notebook_id: str,
    ) -> Dict[str, Any]:
        """
        Build a rich context of the user's recent activity for exploration.
        
        Pulls signals from multiple sources to understand what the user
        is currently thinking about, curious about, and engaged with.
        This feeds into adjacent/tangential query generation so the
        collector explores non-linearly — like a research librarian
        who reads adjacent shelves.
        
        Returns dict with:
            recent_questions: Questions the user asked in chat
            recent_highlights: Passages the user highlighted
            recent_searches: Searches the user performed
            recent_additions: Titles of sources the user recently added
            recent_topics: Topics from archival memory
        """
        from datetime import timedelta
        context = {
            "recent_questions": [],
            "recent_highlights": [],
            "recent_searches": [],
            "recent_additions": [],
            "recent_topics": [],
        }
        
        lookback = datetime.utcnow() - timedelta(days=7)
        
        # Pull recent events from the event logger
        try:
            from services.event_logger import event_logger, EventType
            events = event_logger.get_events_since(lookback, notebook_id=notebook_id)
            
            for evt in events:
                if evt.event_type == EventType.CHAT_QA.value:
                    question = evt.data.get("question", "")
                    if question and len(question) > 10:
                        context["recent_questions"].append(question[:200])
                
                elif evt.event_type == EventType.HIGHLIGHT_CREATED.value:
                    text = evt.data.get("text", "")
                    if text and len(text) > 15:
                        context["recent_highlights"].append(text[:300])
                
                elif evt.event_type == EventType.SEARCH_PERFORMED.value:
                    query = evt.data.get("query", "")
                    if query and len(query) > 3:
                        context["recent_searches"].append(query[:150])
                
                elif evt.event_type == EventType.DOCUMENT_CAPTURED.value:
                    title = evt.data.get("title", "")
                    if title:
                        context["recent_additions"].append(title[:150])
                
                elif evt.event_type == EventType.SOURCE_APPROVED.value:
                    src = evt.data.get("source", {})
                    title = src.get("title", src.get("filename", ""))
                    if title:
                        context["recent_additions"].append(title[:150])
        except Exception as e:
            logger.debug(f"Exploration context: event fetch failed (non-fatal): {e}")
        
        # Pull recent source titles (last 7 days by created_at)
        try:
            from storage.source_store import source_store
            all_sources = await source_store.list(notebook_id)
            for s in all_sources:
                created = s.get("created_at", "")
                if created and created > lookback.isoformat():
                    title = s.get("filename", s.get("title", ""))
                    if title and title not in context["recent_additions"]:
                        context["recent_additions"].append(title[:150])
        except Exception as e:
            logger.debug(f"Exploration context: source fetch failed (non-fatal): {e}")
        
        # Pull topic threads from archival memory (cross-notebook for richer signal)
        try:
            from storage.memory_store import memory_store
            from models.memory import AgentNamespace
            results = await memory_store.search_archival_memory_async(
                query="recent research interests topics discussions",
                namespace=AgentNamespace.CURATOR,
                notebook_id=notebook_id,
                cross_notebook=True,
                limit=5
            )
            if results:
                for r in results:
                    if r.combined_score > 0.2:
                        context["recent_topics"].append(r.entry.content[:200])
        except Exception as e:
            logger.debug(f"Exploration context: memory fetch failed (non-fatal): {e}")
        
        # Cap everything to avoid prompt bloat
        context["recent_questions"] = context["recent_questions"][-8:]
        context["recent_highlights"] = context["recent_highlights"][-5:]
        context["recent_searches"] = context["recent_searches"][-6:]
        context["recent_additions"] = context["recent_additions"][-10:]
        context["recent_topics"] = context["recent_topics"][-5:]
        
        return context

    async def _generate_exploration_queries(
        self,
        notebook_id: str,
        config,
        exploration_context: Dict[str, Any],
        recently_used_queries: List[str],
    ) -> List[str]:
        """
        Generate ADJACENT/TANGENTIAL search queries for non-linear discovery.
        
        Unlike smart queries (which target the notebook's direct focus areas),
        exploration queries deliberately push into related but unexplored territory.
        Think: a research librarian who says "based on what you've been reading,
        you might also find this interesting..."
        
        The key insight: the user's research path is linear and intentional.
        The collector's discovery should be non-linear and serendipitous.
        This opens up possibilities the user wouldn't find on their own.
        
        Args:
            config: Notebook collector config (intent, focus_areas, subject)
            exploration_context: Recent user activity from _build_exploration_context
            recently_used_queries: Queries from recent runs to avoid repeating
        
        Returns:
            List of 3-5 adjacent/tangential search queries
        """
        from services.ollama_service import ollama_service
        from config import settings
        
        subject = config.subject.strip() if hasattr(config, 'subject') else ""
        focus_areas_str = ", ".join(config.focus_areas[:8]) if config.focus_areas else "general"
        
        # Build activity signal for the LLM
        activity_lines = []
        
        if exploration_context.get("recent_questions"):
            activity_lines.append("QUESTIONS THE USER ASKED IN CHAT RECENTLY:")
            for q in exploration_context["recent_questions"][-5:]:
                activity_lines.append(f"  ? {q}")
        
        if exploration_context.get("recent_highlights"):
            activity_lines.append("PASSAGES THE USER HIGHLIGHTED (they found these important):")
            for h in exploration_context["recent_highlights"][-4:]:
                activity_lines.append(f"  > {h}")
        
        if exploration_context.get("recent_searches"):
            activity_lines.append("SEARCHES THE USER PERFORMED:")
            for s in exploration_context["recent_searches"][-4:]:
                activity_lines.append(f"  🔍 {s}")
        
        if exploration_context.get("recent_additions"):
            activity_lines.append("SOURCES THE USER RECENTLY ADDED:")
            for a in exploration_context["recent_additions"][-6:]:
                activity_lines.append(f"  + {a}")
        
        if exploration_context.get("recent_topics"):
            activity_lines.append("TOPICS FROM RECENT RESEARCH MEMORY:")
            for t in exploration_context["recent_topics"][-3:]:
                activity_lines.append(f"  📝 {t}")
        
        activity_text = "\n".join(activity_lines) if activity_lines else "(No recent activity signals available)"
        
        # Build recently-used queries block
        avoid_text = ""
        if recently_used_queries:
            avoid_text = f"""
QUERIES ALREADY USED IN RECENT COLLECTION RUNS (do NOT repeat these or close variants):
{chr(10).join(f'  ✗ {q}' for q in recently_used_queries[-15:])}"""
        
        prompt = f"""You are a creative research librarian. Your job is to suggest ADJACENT, TANGENTIAL research directions that the user hasn't thought of yet — based on what they've been reading, asking about, and exploring.

NOTEBOOK SUBJECT: {subject or '(general)'}
FOCUS AREAS: {focus_areas_str}
NOTEBOOK PURPOSE: {config.intent}

{activity_text}
{avoid_text}

Generate 3-5 EXPLORATION queries that are ADJACENT to the user's interests — not the same topics, but related concepts, counterarguments, historical parallels, cross-disciplinary connections, or emerging intersections.

EXPLORATION PRINCIPLES:
- If they're researching "leadership styles" → explore "organizational psychology", "decision fatigue in executives", "military leadership lessons for business"
- If they're studying "machine learning" → explore "cognitive science of pattern recognition", "statistical mechanics and neural networks", "ethics of automated decision making"
- If they highlighted passages about X → find the intellectual NEIGHBORS of X — what scholars in adjacent fields would say about it
- Connect dots across their different interests — if they read about A and asked about B, find where A and B intersect
- Include at least 1 query that a smart colleague would suggest: "have you considered looking at it from THIS angle?"
- Include at least 1 contrarian or counterpoint query: find content that challenges what the user has been reading
- Each query should be 3-8 words, suitable for Google News or web search
- DO NOT repeat recent queries or generate close variants of them

Respond with ONLY a JSON array of strings, no other text:
["query 1", "query 2", ...]"""

        try:
            import asyncio as _asyncio
            response = await _asyncio.wait_for(
                ollama_service.generate(
                    prompt=prompt,
                    system="You are a creative research librarian specializing in cross-disciplinary discovery. Respond only with a valid JSON array of search query strings.",
                    model=settings.ollama_model,
                    temperature=0.9  # Higher creativity for exploration
                ),
                timeout=45
            )
            
            text = response.get("response", "")
            bracket_start = text.find("[")
            bracket_end = text.rfind("]") + 1
            if bracket_start >= 0 and bracket_end > bracket_start:
                parsed = json.loads(text[bracket_start:bracket_end])
                if isinstance(parsed, list):
                    queries = [q.strip() for q in parsed if isinstance(q, str) and len(q.strip()) > 3][:5]
                    if queries:
                        print(f"[CURATOR] 🔭 Generated {len(queries)} exploration queries: {queries}")
                        logger.info(f"Exploration queries for {notebook_id}: {queries}")
                        return queries
        except Exception as e:
            logger.warning(f"Exploration query generation failed (non-fatal): {e}")
            print(f"[CURATOR] Exploration query generation failed: {e}")
        
        return []

    async def _create_collection_task(
        self,
        notebook_id: str,
        config
    ) -> Dict[str, Any]:
        """
        Curator creates a specific collection task for a Collector.
        This is where Curator's intelligence directs what to look for.
        
        Instead of just passing raw config through, the Curator:
        1. Analyzes existing sources to understand what's already covered
        2. Uses LLM to generate specific, targeted search queries
        3. Auto-populates news_keywords so Google News gets searched
        4. Identifies knowledge gaps and emerging subtopics to pursue
        5. Generates EXPLORATION queries for adjacent/tangential discovery
        6. Rotates queries to avoid searching the same things every run
        """
        from storage.source_store import source_store
        
        task = {
            "notebook_id": notebook_id,
            "intent": config.intent,
            "focus_areas": config.focus_areas,
            "sources": config.sources,
            "mode": config.collection_mode.value if hasattr(config.collection_mode, 'value') else str(config.collection_mode),
            "created_by": "curator",
            "created_at": datetime.utcnow().isoformat()
        }
        
        # ── Get recently used queries for rotation/dedup ──
        recently_used_queries = []
        try:
            from services.collection_history import get_recent_queries
            recently_used_queries = get_recent_queries(notebook_id, lookback_runs=5)
            if recently_used_queries:
                print(f"[CURATOR] 🔄 Loaded {len(recently_used_queries)} recently used queries for rotation")
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        cross_notebook_seeds = []

        # ── Build a knowledge snapshot of what we already have ──
        source_titles = []
        source_domains = set()
        try:
            sources = await source_store.list(notebook_id)
            for s in sources[:80]:  # Cap to avoid huge prompts
                title = s.get("filename", s.get("title", ""))
                if title:
                    source_titles.append(title)
                url = s.get("url", "")
                if url:
                    try:
                        from urllib.parse import urlparse
                        domain = urlparse(url).netloc.lower().replace("www.", "")
                        if domain:
                            source_domains.add(domain)
                    except Exception as _e:
                        logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        except Exception as e:
            logger.debug(f"Could not load sources for smart directives: {e}")
        
        # ── Check archival memory for recent coverage ──
        recent_topics_text = ""
        try:
            existing_memories = await memory_store.search_archival_memory_async(
                query=config.intent,
                limit=10,
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=notebook_id
            )
            if existing_memories:
                recent_topics = [m.entry.content[:120] for m in existing_memories[:5]]
                task["avoid_similar_to"] = recent_topics
                recent_topics_text = "\n".join(f"- {t}" for t in recent_topics)
        except Exception as e:
            logger.debug(f"Could not check existing content: {e}")
        
        # ── Use LLM to generate smart, specific search queries ──
        smart_queries = []
        try:
            subject = config.subject.strip() if hasattr(config, 'subject') else ""
            focus_areas_str = ", ".join(config.focus_areas[:10]) if config.focus_areas else "general"
            
            # Build the prompt with existing knowledge context
            existing_context = ""
            if source_titles:
                sample_titles = source_titles[-20:]  # Most recent 20
                existing_context = f"""
The notebook already has {len(source_titles)} sources. Here are the most recent titles:
{chr(10).join(f'- {t}' for t in sample_titles)}

Known domains already collected from: {', '.join(list(source_domains)[:15])}"""
            
            if recent_topics_text:
                existing_context += f"""

Recent content summaries already in the notebook:
{recent_topics_text}"""
            
            # Build recently-used queries block for rotation
            avoid_queries_text = ""
            if recently_used_queries:
                avoid_queries_text = f"""
QUERIES USED IN RECENT RUNS (do NOT repeat these or close variants — generate FRESH queries):
{chr(10).join(f'  ✗ {q}' for q in recently_used_queries[-12:])}"""

            # ── Adaptive query learning: inject successful/failed patterns ──
            adaptive_block = ""
            try:
                from services.collection_history import get_successful_query_patterns, get_failed_query_patterns
                successful = get_successful_query_patterns(notebook_id, min_approval_rate=0.3, limit=5)
                failed = get_failed_query_patterns(notebook_id, limit=5)
                
                if successful:
                    good_examples = [f'  ✓ "{p["query"]}" ({p["approval_rate"]*100:.0f}% approved)' for p in successful]
                    adaptive_block += f"""
QUERY PATTERNS THAT WORKED WELL (generate similar styles):
{chr(10).join(good_examples)}"""
                
                if failed:
                    bad_examples = [f'  ✗ "{q}"' for q in failed]
                    adaptive_block += f"""
QUERY PATTERNS THAT ALWAYS FAILED (avoid these styles):
{chr(10).join(bad_examples)}"""
                
                if adaptive_block:
                    print(f"[CURATOR] 📈 Adaptive learning: {len(successful)} good patterns, {len(failed)} bad patterns")
            except Exception as _e:
                logger.debug(f"[curator] {type(_e).__name__}: {_e}")
            
            prompt = f"""You are a research librarian planning the next collection run for a research notebook.

NOTEBOOK PURPOSE: {config.intent}
SUBJECT: {subject or '(general)'}
FOCUS AREAS: {focus_areas_str}
{existing_context}
{avoid_queries_text}
{adaptive_block}

Generate 6-8 SPECIFIC search queries that would find NEW, valuable content not already covered.

Rules:
- Be SPECIFIC, not generic. "transformer architecture scaling laws 2026" is good. "AI research papers" is bad.
- Target specific researchers, labs, conferences, techniques, or recent developments
- Include at least 1 query targeting a specific research venue (arXiv, conference, journal)
- Include at least 1 query targeting a specific person/lab in this field
- Include at least 1 query about a recent development or trend
- Avoid queries that would return content already in the notebook
- DO NOT repeat or closely paraphrase any recently used queries listed above
- Each query should be 3-8 words, suitable for Google News or web search

Respond with ONLY a JSON array of strings, no other text:
["query 1", "query 2", ...]"""

            import asyncio as _asyncio
            response = await _asyncio.wait_for(
                ollama_service.generate(
                    prompt=prompt,
                    system="You are a research librarian. Respond only with a valid JSON array of search query strings.",
                    model=settings.ollama_model,  # Main model — this is the strategic brain
                    temperature=0.7  # Some creativity in query generation
                ),
                timeout=45  # 45s max for main model query generation — fall back to defaults if slow
            )
            
            text = response.get("response", "")
            # Extract JSON array
            bracket_start = text.find("[")
            bracket_end = text.rfind("]") + 1
            if bracket_start >= 0 and bracket_end > bracket_start:
                parsed = json.loads(text[bracket_start:bracket_end])
                if isinstance(parsed, list):
                    smart_queries = [q.strip() for q in parsed if isinstance(q, str) and len(q.strip()) > 3][:8]
            
            if smart_queries:
                print(f"[CURATOR] 🧠 Generated {len(smart_queries)} smart queries: {smart_queries}")
                logger.info(f"Smart collection queries for {notebook_id}: {smart_queries}")
                
        except Exception as e:
            logger.warning(f"Smart query generation failed (will use defaults): {e}")
            print(f"[CURATOR] Smart query generation failed: {e}")
        
        # ── Generate EXPLORATION queries for adjacent/tangential discovery ──
        exploration_queries = []
        try:
            exploration_context = await self._build_exploration_context(notebook_id)
            has_activity = any(
                exploration_context.get(k)
                for k in ["recent_questions", "recent_highlights", "recent_searches", "recent_additions", "recent_topics"]
            )
            if has_activity:
                exploration_queries = await self._generate_exploration_queries(
                    notebook_id, config, exploration_context,
                    recently_used_queries + smart_queries  # Avoid overlap with smart queries too
                )
            else:
                print(f"[CURATOR] No recent user activity for {notebook_id} — skipping exploration queries")
        except Exception as e:
            logger.warning(f"Exploration query generation failed (non-fatal): {e}")
            print(f"[CURATOR] Exploration queries failed: {e}")
        
        # ── Enrich the task with smart directives + exploration queries ──
        if smart_queries:
            task["smart_queries"] = smart_queries
            task["curator_directive"] = (
                f"Use these targeted queries to find specific, high-quality content: "
                f"{', '.join(smart_queries[:4])}..."
            )
        else:
            task["curator_directive"] = "Find NEW information not covered by existing content"
        
        if exploration_queries:
            task["exploration_queries"] = exploration_queries
            # Blend exploration queries into smart_queries so they get used by the collector
            all_queries = list(smart_queries) + list(exploration_queries)
            task["smart_queries"] = all_queries
            print(f"[CURATOR] 🧭 Task has {len(smart_queries)} targeted + {len(exploration_queries)} exploration queries")
        
        # ── Auto-populate news_keywords if empty ──
        # This ensures Google News actually gets searched
        sources = task.get("sources", {})
        existing_news_kw = sources.get("news_keywords", [])
        
        if not existing_news_kw:
            auto_news_keywords = []
            subject = config.subject.strip() if hasattr(config, 'subject') else ""
            
            # Use smart queries as news keywords (they're already specific)
            if smart_queries:
                auto_news_keywords.extend(smart_queries[:4])
            
            # Include exploration queries in news search for adjacent discovery
            if exploration_queries:
                auto_news_keywords.extend(exploration_queries[:3])
            
            # Also add subject + top focus areas as fallback
            if subject:
                for area in config.focus_areas[:3]:
                    kw = f"{subject} {area}" if subject.lower() not in area.lower() else area
                    if kw not in auto_news_keywords:
                        auto_news_keywords.append(kw)
                if subject not in auto_news_keywords:
                    auto_news_keywords.append(subject)
            
            if auto_news_keywords:
                # Deep copy sources to avoid mutating config
                task["sources"] = {k: list(v) if isinstance(v, list) else v for k, v in sources.items()}
                task["sources"]["news_keywords"] = auto_news_keywords
                print(f"[CURATOR] 📰 Auto-populated {len(auto_news_keywords)} news keywords: {auto_news_keywords}")
        
        # ── Auto-populate arxiv_categories for research-oriented notebooks ──
        arxiv_categories = sources.get("arxiv_categories", [])
        if not arxiv_categories:
            intent_lower = (config.intent or "").lower()
            subject_lower = (config.subject if hasattr(config, 'subject') else "").lower()
            combined = f"{intent_lower} {subject_lower}"
            
            auto_arxiv = []
            # Map common research topics to arXiv categories
            arxiv_hints = {
                "cs.AI": ["artificial intelligence", "ai research", "ai "],
                "cs.LG": ["machine learning", "deep learning", "neural network"],
                "cs.CL": ["natural language", "nlp", "language model", "llm", "gpt", "transformer"],
                "cs.CV": ["computer vision", "image recognition", "object detection"],
                "cs.RO": ["robotics", "robot"],
                "cs.CR": ["cybersecurity", "security", "cryptography"],
                "stat.ML": ["statistical learning", "bayesian"],
                "cs.SE": ["software engineering"],
                "q-fin": ["quantitative finance", "algorithmic trading"],
                "econ": ["economics research"],
            }
            for category, triggers in arxiv_hints.items():
                if any(t in combined for t in triggers):
                    auto_arxiv.append(category)
            
            if auto_arxiv:
                if "sources" not in task or task["sources"] is sources:
                    task["sources"] = {k: list(v) if isinstance(v, list) else v for k, v in sources.items()}
                task["sources"]["arxiv_categories"] = auto_arxiv[:3]
                print(f"[CURATOR] 📚 Auto-added arXiv categories: {auto_arxiv[:3]}")
                
                # Also use smart queries for direct arXiv search (not just browsing categories)
                if smart_queries:
                    task["sources"]["arxiv_queries"] = smart_queries[:4]
                    print(f"[CURATOR] 🔬 Auto-added {len(smart_queries[:4])} arXiv search queries")
        
        return task

    async def assign_immediate_collection(
        self,
        notebook_id: str,
        specific_query: Optional[str] = None,
        deadline_seconds: Optional[int] = 120,
        trigger: str = "manual",
    ) -> Dict[str, Any]:
        """
        Curator assigns an immediate collection task for a specific notebook.
        Called when user clicks "Collect Now" - but Curator still orchestrates.
        
        Master scheduler lock ensures only one notebook collects at a time,
        preventing Ollama contention from parallel collection runs.
        
        Args:
            deadline_seconds: Max seconds for the pipeline. None = no deadline
                              (used by background scheduler for thorough runs).
            trigger: 'manual', 'scheduled', or 'specific' — recorded in history.
        """
        # Acquire collection lock — only one notebook collects at a time
        if self._collection_lock.locked():
            active = self._active_collection or "unknown"
            logger.info(f"[CURATOR] Collection queued for {notebook_id[:8]} — waiting on {active[:8]}")
            print(f"[CURATOR] ⏳ Waiting for {active[:8]}... to finish before collecting {notebook_id[:8]}")
        
        async with self._collection_lock:
            self._active_collection = notebook_id
            try:
                return await self._execute_collection(
                    notebook_id, specific_query, deadline_seconds, trigger
                )
            finally:
                self._active_collection = None

    async def _execute_collection(
        self,
        notebook_id: str,
        specific_query: Optional[str] = None,
        deadline_seconds: Optional[int] = 120,
        trigger: str = "manual",
    ) -> Dict[str, Any]:
        """Inner collection logic — always called under _collection_lock."""
        import time as _time
        deadline = (_time.time() + deadline_seconds) if deadline_seconds else None

        from agents.collector import get_collector

        print(f"[CURATOR] assign_immediate_collection: getting collector for {notebook_id}")
        collector = get_collector(notebook_id)
        config = collector.get_config()

        if not config.intent:
            return {"error": "Collector not configured", "items_collected": 0}

        print(f"[CURATOR] Config loaded. Sources: {list(config.sources.keys()) if config.sources else 'none'}")

        # Curator Phase 2a: register a 3-step plan in the brain so the
        # UI plan card (Phase 2b) and the audit log have visibility into
        # this multi-step action. plan_id is None if brain is offline —
        # everything below tolerates that gracefully.
        # Curator Phase 2b: also register the plan as cancellable so the
        # UI Stop button can signal it via POST /curator/plans/{id}/cancel.
        plan_id: Optional[str] = None
        try:
            from services.curator_brain import curator_brain
            plan_summary = (
                f"Collect for notebook: {config.name or notebook_id[:8]}"
                + (f" — focus: {specific_query}" if specific_query else "")
            )
            plan_id = curator_brain.create_plan(
                intent="assign_immediate_collection",
                summary=plan_summary,
                steps=[
                    {"name": "search", "description": "Gather candidate items from configured sources"},
                    {"name": "judge", "description": "Curator judges each candidate for relevance"},
                    {"name": "store", "description": "Persist approved items + queue ambiguous ones for review"},
                ],
                notebook_id=notebook_id,
                user_visible=True,
            )
            if plan_id:
                curator_brain.register_cancellable(plan_id)
                curator_brain.start_plan(plan_id)
                curator_brain.start_step(plan_id, 1)
        except Exception as _e:
            logger.debug(f"[curator] plan setup failed (non-fatal): {_e}")

        # Create task with optional specific query
        task = await self._create_collection_task(notebook_id, config)
        if specific_query:
            task["specific_query"] = specific_query
            task["curator_directive"] = f"Focus on: {specific_query}"
        
        # Pass deadline to collector so it can manage its time
        task["_deadline"] = deadline
        
        if deadline:
            print(f"[CURATOR] Task created. Executing collection... (budget: {deadline - _time.time():.0f}s remaining)")
        else:
            print(f"[CURATOR] Task created. Executing collection... (no deadline)")
        
        # Execute collection
        collected_items = await collector.execute_collection_task(task)

        print(f"[CURATOR] Collection returned {len(collected_items) if collected_items else 0} items")

        # Plan step 1 (search) — complete with count summary
        if plan_id:
            try:
                from services.curator_brain import curator_brain
                curator_brain.complete_step(
                    plan_id, 1,
                    output_summary=f"{len(collected_items) if collected_items else 0} candidate items"
                )
            except Exception as _e:
                logger.debug(f"[curator] plan step1 complete: {_e}")

        # ── Cancel breakpoint 1: after search, before judge ──────────
        # If the user clicked Stop while step 1 was running, exit before
        # spending compute on step 2. No data loss possible — nothing
        # is persisted yet.
        if plan_id:
            try:
                from services.curator_brain import curator_brain
                if curator_brain.is_cancelled(plan_id):
                    curator_brain.cancel_plan(plan_id, reason="user_requested")
                    curator_brain.unregister_cancellable(plan_id)
                    return {
                        "items_collected": 0,
                        "cancelled": True,
                        "message": "Cancelled by user after search",
                    }
            except Exception as _e:
                logger.debug(f"[curator] cancel check 1: {_e}")

        if not collected_items:
            # Still record history so query rotation works (avoids repeating same queries next run)
            try:
                from services.collection_history import record_collection_run
                record_collection_run(
                    notebook_id=notebook_id,
                    items_found=0, items_approved=0, items_pending=0, items_rejected=0,
                    sources_checked=len(config.sources.get("rss_feeds", [])) + len(config.sources.get("web_pages", [])),
                    trigger=trigger,
                    keywords_used=task.get("focus_areas", [])[:5],
                    queries_used=task.get("smart_queries", []),
                    exploration_queries=task.get("exploration_queries", []),
                )
            except Exception as _e:
                logger.debug(f"[curator] {type(_e).__name__}: {_e}")
            # Plan: no items found — cancel remaining steps (judge + store
            # have nothing to do). Plan ends in cancelled state with reason.
            if plan_id:
                try:
                    from services.curator_brain import curator_brain
                    curator_brain.cancel_plan(plan_id, reason="no_items_found")
                    curator_brain.unregister_cancellable(plan_id)
                except Exception as _e:
                    logger.debug(f"[curator] plan cancel: {_e}")
            return {"items_collected": 0, "message": "No new items found"}

        # Plan step 2 (judge) — start
        if plan_id:
            try:
                from services.curator_brain import curator_brain
                curator_brain.start_step(plan_id, 2)
            except Exception as _e:
                logger.debug(f"[curator] plan step2 start: {_e}")

        # Judge results (pass deadline so judgment can auto-defer if time is tight)
        if deadline:
            remaining = deadline - _time.time()
            print(f"[CURATOR] Judging {len(collected_items)} items... ({remaining:.0f}s remaining)")
        else:
            print(f"[CURATOR] Judging {len(collected_items)} items... (no deadline)")
        judgments = await self.judge_collection(
            collector_id=notebook_id,
            proposed_items=collected_items,
            notebook_intent=config.intent,
            deadline=deadline
        )

        # Plan step 2 (judge) — complete
        if plan_id:
            try:
                from services.curator_brain import curator_brain
                curator_brain.complete_step(
                    plan_id, 2,
                    output_summary=f"{len(judgments)} judgments returned"
                )
            except Exception as _e:
                logger.debug(f"[curator] plan step2 complete: {_e}")

        # ── Cancel breakpoint 2: after judge, before store ───────────
        # The user can still stop here. Judging work is sunk cost, but
        # no items have been persisted to the notebook yet.
        if plan_id:
            try:
                from services.curator_brain import curator_brain
                if curator_brain.is_cancelled(plan_id):
                    curator_brain.cancel_plan(plan_id, reason="user_requested")
                    curator_brain.unregister_cancellable(plan_id)
                    return {
                        "items_collected": 0,
                        "cancelled": True,
                        "message": "Cancelled by user after judging",
                    }
                curator_brain.start_step(plan_id, 3)
            except Exception as _e:
                logger.debug(f"[curator] cancel check 2 / step3 start: {_e}")
        
        approved = 0
        pending = 0
        rejected = 0
        filtered = 0
        approved_titles = []
        filtered_titles = []
        rejection_reasons: Dict[str, int] = {}  # Track why items fail
        CONFIDENCE_FLOOR = 0.50  # Nothing below 50% is ever added
        
        # Pre-fetch existing URLs once for dedup (avoids N × source_store.list() calls)
        from storage.source_store import source_store
        existing_sources = await source_store.list(notebook_id)
        existing_urls = {s.get("url") for s in existing_sources if s.get("url")}

        # Curator Phase 2b: track whether the user cancelled mid-store.
        # If they do, we break out of the loop and report partial counts.
        cancelled_mid_store = False

        for item, judgment in zip(collected_items, judgments):
            # ── Cancel breakpoint 3: per-iteration ──────────────────────
            # User can stop mid-store. Items already processed in earlier
            # iterations stay (they're committed). Future iterations skip.
            if plan_id:
                try:
                    from services.curator_brain import curator_brain
                    if curator_brain.is_cancelled(plan_id):
                        cancelled_mid_store = True
                        break
                except Exception as _e:
                    logger.debug(f"[curator] cancel check 3: {_e}")

            # Hard confidence floor: items below threshold are always filtered
            if item.overall_confidence < CONFIDENCE_FLOOR:
                filtered += 1
                reason = f"below_{int(CONFIDENCE_FLOOR*100)}pct_threshold"
                filtered_titles.append({
                    "title": item.title, "source": item.source_name, 
                    "confidence": item.overall_confidence, 
                    "reason": reason
                })
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                continue
            
            if judgment.decision == JudgmentDecision.APPROVE:
                # Directly store approved items (they aren't in the approval queue)
                try:
                    was_stored = await collector._store_approved_item(item, _existing_urls=existing_urls)
                    if was_stored:
                        approved += 1
                        approved_titles.append({"id": item.id, "title": item.title, "source": item.source_name, "confidence": item.overall_confidence})
                    else:
                        # Item was approved but couldn't be stored (duplicate URL or shallow).
                        # Route to approval queue so the user can still see it, rather
                        # than silently dropping potentially relevant content.
                        queue_result = await collector._add_to_approval_queue(item)
                        if queue_result == 'queued':
                            pending += 1
                        else:
                            filtered += 1
                            filtered_titles.append({"title": item.title, "source": item.source_name, "confidence": item.overall_confidence, "reason": "shallow_or_duplicate"})
                except Exception as e:
                    logger.error(f"Failed to store approved item '{item.title}': {e}")
                    filtered += 1
            elif judgment.decision == JudgmentDecision.REJECT:
                rejected += 1
                reason = getattr(judgment, 'reason', 'curator_rejected') or 'curator_rejected'
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            else:
                # Queue for user review (may auto-approve if high confidence in mixed mode)
                queue_result = await collector._add_to_approval_queue(item)
                if queue_result == 'queued':
                    pending += 1
                elif queue_result == 'stored':
                    approved += 1
                    approved_titles.append({"id": item.id, "title": item.title, "source": item.source_name, "confidence": item.overall_confidence})
                else:
                    filtered += 1
                    filtered_titles.append({"title": item.title, "source": item.source_name, "confidence": item.overall_confidence, "reason": "shallow_or_duplicate"})
        
        print(f"[CURATOR] Done: {approved} approved, {pending} pending, {rejected} rejected, {filtered} filtered (shallow/dup)")

        # Plan step 3 (store) — complete (or mark cancelled if the user
        # stopped mid-iteration). Plan auto-completes from complete_step
        # when this is the final step; cancel_plan overrides to cancelled.
        if plan_id:
            try:
                from services.curator_brain import curator_brain
                if cancelled_mid_store:
                    curator_brain.complete_step(
                        plan_id, 3,
                        output_summary=(
                            f"{approved} approved, {pending} pending, "
                            f"{rejected} rejected before cancel"
                        ),
                    )
                    curator_brain.cancel_plan(plan_id, reason="user_requested")
                else:
                    curator_brain.complete_step(
                        plan_id, 3,
                        output_summary=f"{approved} approved, {pending} pending, {rejected} rejected, {filtered} filtered"
                    )
            except Exception as _e:
                logger.debug(f"[curator] plan step3 complete: {_e}")
            finally:
                # Always unregister so the cancellation registry doesn't
                # accumulate dead entries.
                try:
                    from services.curator_brain import curator_brain as _cb
                    _cb.unregister_cancellable(plan_id)
                except Exception:
                    pass
        
        # Record in collection history
        try:
            from services.collection_history import record_collection_run
            record_collection_run(
                notebook_id=notebook_id,
                items_found=len(collected_items),
                items_approved=approved,
                items_pending=pending,
                items_rejected=rejected,
                sources_checked=len(config.sources.get("rss_feeds", [])) + len(config.sources.get("web_pages", [])) + len(config.sources.get("news_keywords", [])),
                trigger="specific" if specific_query else trigger,
                keywords_used=task.get("focus_areas", [])[:5],
                queries_used=task.get("smart_queries", []),
                exploration_queries=task.get("exploration_queries", []),
                rejection_reasons=rejection_reasons if rejection_reasons else None,
            )
        except Exception as hist_err:
            logger.warning(f"Failed to record collection history (non-fatal): {hist_err}")
        
        # ── Adaptive query learning: record per-query outcomes ──
        try:
            from services.collection_history import record_query_outcomes
            # Build query→outcome map: attribute each item's result to its likely source query
            # Simple heuristic: match item title words to query words
            all_queries = list(task.get("smart_queries", [])) + list(task.get("exploration_queries", []))
            if all_queries:
                query_outcomes: Dict[str, Dict[str, int]] = {}
                for q in all_queries:
                    query_outcomes[q] = {"approved": 0, "rejected": 0, "total": 0}
                
                # Attribute each item to the best-matching query
                for item, judgment in zip(collected_items, judgments):
                    best_query = None
                    best_overlap = 0
                    title_words = set(item.title.lower().split())
                    for q in all_queries:
                        q_words = set(q.lower().split())
                        overlap = len(title_words & q_words)
                        if overlap > best_overlap:
                            best_overlap = overlap
                            best_query = q
                    if not best_query:
                        best_query = all_queries[0]  # Default to first query
                    
                    query_outcomes[best_query]["total"] += 1
                    if judgment.decision == JudgmentDecision.APPROVE:
                        query_outcomes[best_query]["approved"] += 1
                    elif judgment.decision == JudgmentDecision.REJECT:
                        query_outcomes[best_query]["rejected"] += 1
                
                record_query_outcomes(notebook_id, query_outcomes)
                logger.info(f"[Adaptive] Recorded outcomes for {len(query_outcomes)} queries")
        except Exception as aq_err:
            logger.debug(f"Adaptive query recording failed (non-fatal): {aq_err}")
        
        # ── Phase 4: Record collection pattern (CBR) + post-run synthesis ──
        try:
            from services.collection_history import record_collection_pattern, record_run_synthesis
            
            total_judged = approved + pending + rejected + filtered
            approval_rate = approved / max(total_judged, 1)
            strategy_used = task.get("strategy", "auto")
            if strategy_used == "auto":
                strategy_used = "iterative" if not deadline else "standard"
            
            # Record pattern for CBR
            record_collection_pattern(notebook_id, {
                "strategy": strategy_used,
                "queries": task.get("smart_queries", [])[:6],
                "items_found": len(collected_items),
                "items_approved": approved,
                "approval_rate": round(approval_rate, 2),
                "trigger": "specific" if specific_query else trigger,
                "iteration_count": task.get("_iteration_count"),
                "total_queries_used": task.get("_total_queries_used"),
            })
            
            # Record post-run synthesis
            synthesis = {
                "approved_titles": [t["title"] for t in approved_titles[:5]],
                "items_found": len(collected_items),
                "items_approved": approved,
                "items_pending": pending,
                "strategy": strategy_used,
                "trigger": "specific" if specific_query else trigger,
                "top_sources": list(set(t.get("source", "") for t in approved_titles))[:4],
            }
            # Add gap info if nothing was approved
            if approved == 0 and rejection_reasons:
                synthesis["gap_reasons"] = dict(list(rejection_reasons.items())[:3])
            record_run_synthesis(notebook_id, synthesis)
            
        except Exception as p4_err:
            logger.debug(f"Phase 4 recording failed (non-fatal): {p4_err}")

        # ── Auto-expand source discovery (the collector's wander reflex) ──
        # After every sweep, look at recently approved items for patterns —
        # new domains that keep showing up, RSS feeds hiding in article
        # content — and add a capped handful to the config. Always-on so
        # the collector gradually widens its net without nagging the user
        # to manually add sources. Non-fatal: any failure is logged and
        # the sweep result is still returned.
        try:
            discovery = await collector.auto_discover_sources()
            if discovery.get("auto_expanded"):
                logger.info(
                    f"[curator] auto-expand applied to {notebook_id[:8]}: "
                    f"+{len(discovery.get('added_domains', []))} domains, "
                    f"+{len(discovery.get('added_feeds', []))} feeds"
                )
        except Exception as exp_err:
            logger.debug(f"[curator] auto-expand discovery failed (non-fatal): {exp_err}")

        return {
            "items_collected": len(collected_items),
            "items_approved": approved,
            "items_pending": pending,
            "items_rejected": rejected,
            "items_filtered": filtered,
            "auto_approved": approved_titles,
            "filtered": filtered_titles
        }
