"""CuratorJudgmentMixin — extracted from the former agents/curator.py (Wave 3 split)."""
from ._models import *  # noqa: F401,F403


class CuratorJudgmentMixin:
    async def judge_collection(
        self, 
        collector_id: str,
        proposed_items: List[CollectedItem],
        notebook_intent: str,
        deadline: float = 0
    ) -> List[JudgmentResult]:
        """
        Review items a Collector wants to add.
        Returns judgment for each item (parallel with bounded concurrency).
        If deadline is set and approaching, auto-defers remaining items to user review.
        """
        import asyncio
        import time as _time
        sem = asyncio.Semaphore(4)
        
        async def _judge_bounded(item):
            # Mid-flight yield: pause scheduled-collection judging if a
            # foreground op is active (no-op for "Collect Now").
            from services.memory_steward import yield_if_background
            await yield_if_background()
            # If less than 10s left, skip LLM and auto-defer
            if deadline and _time.time() > deadline - 10:
                return JudgmentResult(
                    decision=JudgmentDecision.DEFER_TO_USER,
                    reason="Deferred to keep collection fast. Will review in background.",
                    confidence=item.overall_confidence
                )
            async with sem:
                return await self._judge_single_item(item, notebook_intent, collector_id)

        results = await asyncio.gather(*[_judge_bounded(item) for item in proposed_items])
        return list(results)

    async def judge_collected_item(
        self,
        item: CollectedItem,
        intent: str,
        collector_id: str,
    ) -> JudgmentResult:
        """Public contract for collector pre-triage (Phase C.1).

        Synchronous quality-gate decision the Collector calls on each
        proposed item before queueing. The Collector treats Curator as a
        verdict source via this method; that's the only entry point.

        Internally delegates to `_judge_single_item` (the implementation
        also used by the batched `judge_proposed_items` path).
        """
        result = await self._judge_single_item(item, intent, collector_id)
        # Phase C.1 (2026-05-22): emit an observability event so the brain's
        # event log captures every pre-triage decision. The collector still
        # gets the verdict synchronously above; this is purely additive.
        try:
            from services.curator_event_bus import event_bus
            event_bus.emit_now(
                actor="@curator",
                action="collector_item_pre_triaged",
                notebook_id=getattr(item, "notebook_id", None),
                payload={
                    "decision": result.decision.value,
                    "confidence": float(result.confidence or 0.0),
                    "item_title": (item.title or "")[:120],
                    "url": (item.url or "")[:240],
                    "collector_id": collector_id,
                },
                outcome="success",
            )
        except Exception as _bus_err:
            logger.debug(f"[curator] pre-triage event emit failed (non-fatal): {_bus_err}")
        return result

    async def _judge_single_item(
        self,
        item: CollectedItem,
        intent: str,
        collector_id: str
    ) -> JudgmentResult:
        """Judge a single collected item.

        Implementation detail of `judge_collected_item`. External callers
        should use the public method — this is kept as an internal name so
        the batched `_judge_bounded` helper and any back-compat tooling
        don't break.
        """
        auto_threshold = self.config.get("oversight", {}).get("auto_approve_threshold", 0.85)
        
        # High confidence items get auto-approved
        if item.overall_confidence >= auto_threshold:
            return JudgmentResult(
                decision=JudgmentDecision.APPROVE,
                reason=f"High confidence match ({item.overall_confidence:.0%})",
                confidence=item.overall_confidence
            )
        
        # Low confidence items get deferred to user
        if item.overall_confidence < 0.5:
            return JudgmentResult(
                decision=JudgmentDecision.DEFER_TO_USER,
                reason=f"Low confidence ({item.overall_confidence:.0%}). Needs human review.",
                confidence=item.overall_confidence
            )
        
        # Temporal Intelligence: reject high-overlap items with no new information
        if hasattr(item, 'knowledge_overlap') and item.knowledge_overlap > 0.8:
            delta = getattr(item, 'delta_summary', None) or ""
            no_new_info = not delta or "no new" in delta.lower() or "no significant" in delta.lower() or "already" in delta.lower()
            if no_new_info:
                return JudgmentResult(
                    decision=JudgmentDecision.REJECT,
                    reason=f"High overlap ({item.knowledge_overlap:.0%}) with existing knowledge. No significant new information.",
                    confidence=item.knowledge_overlap
                )
        
        # Medium confidence - use LLM to evaluate
        try:
            prompt = f"""You are {self.name}, the Curator of a research system. Your personality: {self.personality}

A Collector wants to add this item to a notebook with intent: "{intent}"

Item to evaluate:
- Title: {item.title}
- Source: {item.source_name}
- Preview: {item.preview[:500]}
- Relevance Score: {item.relevance_score:.0%}

Evaluate if this item matches the notebook's intent. Consider:
1. Does it directly relate to the stated intent?
2. Is it from a trustworthy source?
3. Is this information fresh/relevant?

Respond with JSON only:
{{
    "decision": "approve" | "reject" | "modify" | "defer_to_user",
    "reason": "brief explanation",
    "confidence": 0.0-1.0,
    "modifications": null or ["suggestion1", "suggestion2"]
}}"""

            response = await ollama_service.generate(
                prompt=prompt,
                system="You are an editorial judgment system. Respond only with valid JSON.",
                model=settings.ollama_fast_model,  # Fast model — sufficient for approve/reject JSON
                temperature=0.3
            )
            
            # Parse JSON response
            result_text = response.get("response", "")
            # Extract JSON from response
            json_start = result_text.find("{")
            json_end = result_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                result_json = json.loads(result_text[json_start:json_end])
                return JudgmentResult(
                    decision=JudgmentDecision(result_json.get("decision", "defer_to_user")),
                    reason=result_json.get("reason", "LLM evaluation"),
                    confidence=float(result_json.get("confidence", 0.5)),
                    modifications=result_json.get("modifications")
                )
        except Exception as e:
            logger.error(f"LLM judgment failed: {e}")
        
        # Fallback: defer to user
        return JudgmentResult(
            decision=JudgmentDecision.DEFER_TO_USER,
            reason="Unable to automatically evaluate. Human review recommended.",
            confidence=item.overall_confidence
        )

    async def score_user_item(
        self,
        notebook_id: str,
        title: str,
        content: str,
        url: Optional[str] = None,
        source_type: str = "web",
        user_weight_bonus: float = 1.5
    ) -> Dict[str, Any]:
        """
        Score and learn from user-provided content.
        
        When a user manually adds/captures content, this is a STRONG signal
        of what they find important. We:
        1. Score the content for relevance to notebook intent
        2. Extract topics and entities
        3. Record as a positive learning signal with bonus weight
        4. Return scoring info for storage
        
        Args:
            notebook_id: Which notebook the content is being added to
            title: Content title
            content: Content text
            url: Optional source URL
            source_type: Type of source (web, pdf, manual, etc.)
            user_weight_bonus: Multiplier for user-provided content (default 1.5x)
            
        Returns:
            Dict with scoring results and extracted metadata
        """
        from agents.collector import get_collector
        
        result = {
            "relevance_score": 0.5,
            "topics": [],
            "entities": [],
            "importance": "medium",
            "user_provided": True,
            "user_weight": user_weight_bonus,
            "effective_score": 0.5
        }
        
        try:
            # Get notebook intent from Collector config
            collector = get_collector(notebook_id)
            config = collector.get_config()
            intent = config.intent or ""
            focus_areas = config.focus_areas or []
            
            # Score relevance using LLM
            if intent or focus_areas:
                prompt = f"""Analyze this user-provided content for a research notebook.

Notebook intent: {intent}
Focus areas: {', '.join(focus_areas) if focus_areas else 'Not specified'}

Content title: {title}
Content preview: {content[:1000]}

Respond with JSON only:
{{
    "relevance_score": 0.0-1.0,
    "topics": ["topic1", "topic2"],
    "entities": ["entity1", "entity2"],
    "importance": "low" | "medium" | "high" | "critical",
    "reasoning": "brief explanation"
}}"""

                response = await ollama_service.generate(
                    prompt=prompt,
                    model=settings.ollama_fast_model,
                    temperature=0.2
                )
                
                text = response.get("response", "")
                json_start = text.find("{")
                json_end = text.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    parsed = json.loads(text[json_start:json_end])
                    result["relevance_score"] = float(parsed.get("relevance_score", 0.5))
                    result["topics"] = parsed.get("topics", [])
                    result["entities"] = parsed.get("entities", [])
                    result["importance"] = parsed.get("importance", "medium")
            
            # Apply user weight bonus - user explicitly added this, so it matters
            result["effective_score"] = min(1.0, result["relevance_score"] * user_weight_bonus)
            
            # Record as strong positive signal for learning
            memory_store.record_user_signal(
                notebook_id=notebook_id,
                signal_type="user_capture",
                metadata={
                    "title": title[:200],
                    "url": url,
                    "source_type": source_type,
                    "topics": result["topics"],
                    "entities": result["entities"],
                    "relevance_score": result["relevance_score"],
                    "importance": result["importance"]
                }
            )
            
            # Also record topic preferences for pattern learning
            for topic in result["topics"][:5]:
                memory_store.record_user_signal(
                    notebook_id=notebook_id,
                    signal_type="topic_interest",
                    metadata={"topic": topic, "source": "user_capture"}
                )
            
            logger.info(f"Scored user item for {notebook_id}: {result['relevance_score']:.2f} -> {result['effective_score']:.2f}")
            
        except Exception as e:
            logger.error(f"Error scoring user item: {e}")
        
        return result

    async def get_learned_preferences(self, notebook_id: str) -> Dict[str, Any]:
        """
        Retrieve learned preferences from user signals for a notebook.
        
        Returns aggregated patterns from:
        - User captures (what they manually add)
        - Approvals/rejections
        - Topic interests
        """
        preferences = {
            "preferred_topics": [],
            "preferred_sources": [],
            "rejected_patterns": [],
            "capture_count": 0,
            "approval_rate": 0.0
        }
        
        try:
            # Get all signals for this notebook (signal_type=None gets all types)
            signals = memory_store.get_user_signals(
                notebook_id=notebook_id,
                signal_type=None,  # Get all signal types
                since_days=90,  # Look back 90 days
                limit=200
            )
            
            # Filter to relevant signal types (includes highlights as strongest signal)
            relevant_types = {"user_capture", "topic_interest", "item_approved", "item_rejected", "source_approved", "source_rejected", "content_highlighted"}
            signals = [s for s in signals if s.get("signal_type") in relevant_types]
            
            topic_counts: Dict[str, int] = {}
            source_counts: Dict[str, int] = {}
            rejected_sources: set = set()
            approvals = 0
            rejections = 0
            highlight_count = 0
            
            for signal in signals:
                meta = signal.get("metadata", {})
                
                if signal["signal_type"] == "content_highlighted":
                    # HIGHEST weight - user explicitly marked this as important
                    highlight_count += 1
                    for topic in meta.get("topics", []):
                        topic_counts[topic] = topic_counts.get(topic, 0) + 3  # Triple weight for highlights
                    for entity in meta.get("entities", []):
                        topic_counts[entity] = topic_counts.get(entity, 0) + 2
                
                elif signal["signal_type"] == "user_capture":
                    preferences["capture_count"] += 1
                    for topic in meta.get("topics", []):
                        topic_counts[topic] = topic_counts.get(topic, 0) + 2  # Double weight for captures
                
                elif signal["signal_type"] == "topic_interest":
                    topic = meta.get("topic")
                    if topic:
                        topic_counts[topic] = topic_counts.get(topic, 0) + 1
                
                elif signal["signal_type"] == "item_approved":
                    approvals += 1
                    source = meta.get("source_name")
                    if source:
                        source_counts[source] = source_counts.get(source, 0) + 1
                
                elif signal["signal_type"] == "item_rejected":
                    rejections += 1
                
                elif signal["signal_type"] == "source_rejected":
                    rejected_sources.add(meta.get("source_url", ""))
            
            preferences["highlight_count"] = highlight_count
            
            # Sort topics by frequency
            sorted_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)
            preferences["preferred_topics"] = [t[0] for t in sorted_topics[:10]]
            
            # Sort sources by approval count
            sorted_sources = sorted(source_counts.items(), key=lambda x: x[1], reverse=True)
            preferences["preferred_sources"] = [s[0] for s in sorted_sources[:10]]
            
            preferences["rejected_patterns"] = list(rejected_sources)[:10]
            
            if approvals + rejections > 0:
                preferences["approval_rate"] = approvals / (approvals + rejections)
            
        except Exception as e:
            logger.error(f"Error getting learned preferences: {e}")
        
        return preferences

    async def score_text_against_notebooks(
        self,
        text: str,
        exclude_notebook_id: Optional[str] = None,
        notebook_ids: Optional[List[str]] = None,
        per_notebook_limit: int = 5,
        max_results: int = 3,
    ) -> List[Dict[str, Any]]:
        """Cross-notebook similarity for a candidate text blob (depth+1 expansion).

        Adapter on top of the same memory_store search the existing
        synthesize_across_notebooks() helper uses. Skips the LLM synthesis
        step — the link expander only needs the relevance signal per
        notebook (so it can render a "📌 Also relevant: NotebookX" hint
        in the approval queue), not a paragraph of synthesized prose.

        Args:
            text: Candidate article text (or summary). The first ~1500 chars
                  are used as the search query — long enough to surface
                  thematic overlap, short enough to keep search cheap.
            exclude_notebook_id: Notebook the article is being added to.
                  Excluded from the cross-notebook hint because "this is
                  also relevant to its own notebook" is noise.
            notebook_ids: Restrict search to these notebooks. None = all.
            per_notebook_limit: Memory matches fetched per notebook. The
                  highest-scoring match wins per notebook.
            max_results: Cap the returned list. Default 3 — UI shows at
                  most 3 chips per queue item to stay readable.

        Returns:
            [{notebook_id, notebook_name, score, snippet}] sorted by score
            descending, capped at max_results. Empty list if nothing
            crosses the relevance threshold.
        """
        if not text or not text.strip():
            return []

        # Resolve notebook IDs + names once so we can stamp human-readable
        # labels on the results (the queue UI shows the name, not the ID).
        all_notebooks = await notebook_store.list()
        nb_name_by_id = {n["id"]: n.get("name", "") or n.get("title", "") or n["id"][:8] for n in all_notebooks}
        if not notebook_ids:
            notebook_ids = [n["id"] for n in all_notebooks]
        notebook_ids = [nid for nid in notebook_ids if nid != exclude_notebook_id]
        if not notebook_ids:
            return []

        # Use a leading slice as the search query — full text would slow
        # the embedding step without adding signal beyond the first
        # paragraph or two.
        query = text[:1500]

        # Aggregate the BEST score per notebook. A notebook with one strong
        # hit beats a notebook with many weak hits — that's what the user
        # wants when seeing "📌 Also relevant: …".
        best_by_notebook: Dict[str, Dict[str, Any]] = {}
        for nb_id in notebook_ids:
            try:
                results = await memory_store.search_archival_memory_async(
                    query=query,
                    namespace=AgentNamespace.CURATOR,
                    notebook_id=nb_id,
                    cross_notebook=True,
                    limit=per_notebook_limit,
                )
            except Exception as e:
                logger.debug(f"[curator] cross-notebook search failed for {nb_id}: {e}")
                continue
            for r in results:
                score = float(getattr(r, "combined_score", 0.0) or 0.0)
                if score <= 0:
                    continue
                cur = best_by_notebook.get(nb_id)
                if cur is None or score > cur["score"]:
                    best_by_notebook[nb_id] = {
                        "notebook_id": nb_id,
                        "notebook_name": nb_name_by_id.get(nb_id, nb_id[:8]),
                        "score": round(score, 3),
                        "snippet": (r.entry.content or "")[:240],
                    }

        # Threshold: only surface meaningful matches. The exact value is
        # tuned to memory_store's combined_score scale (0-1 with semantic
        # similarity). 0.45 is "noticeable thematic overlap" — below that,
        # showing a chip is just noise.
        ranked = sorted(
            (m for m in best_by_notebook.values() if m["score"] >= 0.45),
            key=lambda m: m["score"],
            reverse=True,
        )
        return ranked[:max_results]

    async def find_counterarguments(
        self,
        notebook_id: str,
        thesis: Optional[str] = None
    ) -> CounterargumentResult:
        """
        If thesis provided, find evidence against it.
        If not, infer thesis from notebook content and find counters.

        Curator Phase 3b (2026-05-13): when source_stances has ≥3
        contradicting rows for this notebook AND no override thesis
        is supplied, prefer the cached stances over re-running an
        LLM search (faster + already curator-evaluated). Falls back
        to the original semantic-search path when stances are sparse
        or absent.
        """
        # Phase 3b: check the stance table for cached counter-evidence.
        # Only use it when the caller didn't override the thesis — if
        # they did, the cached stances may have been scored against a
        # different thesis and would be misleading.
        if thesis is None:
            try:
                from services.curator_brain import curator_brain
                mm = curator_brain.get_mental_model(notebook_id)
                if mm and mm.get("thesis"):
                    dissent_rows = curator_brain.get_dissenting_sources(notebook_id, limit=5)
                    if len(dissent_rows) >= 3:
                        # Attach source titles best-effort
                        from storage.source_store import source_store
                        counterpoints: List[Dict[str, Any]] = []
                        for d in dissent_rows:
                            title = d["source_id"]
                            try:
                                src = await source_store.get(d["source_id"])
                                if src:
                                    title = (
                                        src.get("filename")
                                        or src.get("title")
                                        or src.get("url")
                                        or d["source_id"]
                                    )
                            except Exception:
                                pass
                            counterpoints.append({
                                "query": "(cached stance)",
                                "content": f"[{title[:120]}] {d['rationale']}"[:300],
                                "score": d["confidence"],
                                "source_id": d["source_id"],
                            })
                        avg_conf = sum(d["confidence"] for d in dissent_rows) / max(1, len(dissent_rows))
                        return CounterargumentResult(
                            inferred_thesis=mm["thesis"],
                            counterpoints=counterpoints,
                            confidence=min(1.0, max(0.3, avg_conf)),
                        )
            except Exception as _e:
                logger.debug(f"[curator] find_counterarguments stance path skipped: {_e}")
                # Fall through to the legacy semantic-search path.

        # Legacy path — infer thesis if not provided, run counter-query
        # semantic search. Still used when (a) caller overrides thesis,
        # or (b) stance table is sparse for this notebook.
        if not thesis:
            thesis = await self._infer_thesis(notebook_id)

        # Generate counter-queries
        counter_queries = await self._generate_counter_queries(thesis)

        counterpoints = []

        for query in counter_queries:
            # Search notebook for contradicting evidence
            results = await memory_store.search_archival_memory_async(
                query=query,
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=notebook_id,
                limit=5
            )

            for r in results:
                counterpoints.append({
                    "query": query,
                    "content": r.entry.content[:300],
                    "score": r.combined_score
                })

        # Rank and dedupe
        counterpoints.sort(key=lambda x: x["score"], reverse=True)

        return CounterargumentResult(
            inferred_thesis=thesis,
            counterpoints=counterpoints[:5],
            confidence=0.6 if counterpoints else 0.3
        )

    async def _infer_thesis(self, notebook_id: str) -> str:
        """Infer the main thesis/hypothesis from notebook content"""
        results = await memory_store.search_archival_memory_async(
            query="main thesis hypothesis conclusion argument",
            namespace=AgentNamespace.COLLECTOR,
            notebook_id=notebook_id,
            limit=10
        )
        
        if not results:
            return "Unable to infer thesis from notebook content."
        
        context = "\n".join([r.entry.content[:300] for r in results])
        
        try:
            prompt = f"""Based on this research content, what is the main thesis or hypothesis being explored?

Content:
{context}

State the thesis in one clear sentence."""

            response = await ollama_service.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.3
            )
            return response.get("response", "Unable to infer thesis.")
        except Exception as e:
            logger.error(f"Thesis inference failed: {e}")
            return "Unable to infer thesis."

    async def _generate_counter_queries(self, thesis: str) -> List[str]:
        """Generate search queries to find contradicting evidence"""
        try:
            prompt = f"""Given this thesis: "{thesis}"

Generate 3 search queries that would find contradicting evidence or alternative perspectives.
Return only the queries, one per line."""

            response = await ollama_service.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.5
            )
            
            queries = response.get("response", "").strip().split("\n")
            return [q.strip() for q in queries if q.strip()][:3]
        except Exception as e:
            logger.error(f"Counter-query generation failed: {e}")
            return [f"evidence against {thesis}", f"criticism of {thesis}"]

    async def validate_discovered_sources(
        self,
        notebook_id: str,
        intent: str,
        discovered_sources: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Curator reviews discovered sources and provides recommendations.
        
        For each source:
        - Validates relevance to intent
        - Assigns recommendation (auto_approve, suggest, skip)
        - Provides reasoning for user review
        
        Args:
            notebook_id: The notebook these sources are for
            intent: The user's stated intent
            discovered_sources: List of sources from SourceDiscoveryService
            
        Returns:
            List of sources with curator recommendations added
        """
        logger.info(f"Curator validating {len(discovered_sources)} discovered sources for notebook {notebook_id}")
        
        validated_sources = []
        
        for source in discovered_sources:
            validated = await self._validate_single_source(source, intent)
            validated_sources.append(validated)
        
        # Sort by recommendation priority: auto_approve first, then suggest, then skip
        priority_order = {"auto_approve": 0, "suggest": 1, "skip": 2}
        validated_sources.sort(
            key=lambda s: priority_order.get(s.get("curator_recommendation", "skip"), 3)
        )
        
        # Store validation in memory for learning (non-fatal if it fails)
        try:
            entry = ArchivalMemoryEntry(
                content=f"Source discovery for: {intent}\nValidated {len(validated_sources)} sources",
                content_type="source_discovery_validation",
                source_type=MemorySourceType.SYSTEM,
                source_notebook_id=notebook_id,
                topics=["source_discovery", "validation"],
                importance=MemoryImportance.LOW,
            )
            await memory_store.add_archival_memory_async(entry, namespace=AgentNamespace.CURATOR)
        except Exception as mem_err:
            logger.warning(f"Failed to store discovery validation in memory (non-fatal): {mem_err}")
        
        return validated_sources

    async def _validate_single_source(
        self,
        source: Dict[str, Any],
        intent: str
    ) -> Dict[str, Any]:
        """Validate a single discovered source against intent"""
        source_name = source.get("name", "Unknown")
        source_type = source.get("source_type", "unknown")
        source_confidence = source.get("confidence", 0.5)
        source_desc = source.get("description", "")
        
        # High confidence sources from discovery engine get auto-approved
        if source_confidence >= 0.85 and source.get("auto_approve", False):
            source["curator_recommendation"] = "auto_approve"
            source["curator_reason"] = "High relevance source for your research"
            return source
        
        # Use LLM for medium confidence sources
        if source_confidence >= 0.5:
            try:
                prompt = f"""You are {self.name}, validating a source for research.

Research Intent: {intent}

Source to evaluate:
- Name: {source_name}
- Type: {source_type}
- Description: {source_desc}

Should this source be included? Consider:
1. Is it directly relevant to the research intent?
2. Is it a reputable/useful source type?

Respond with JSON only:
{{
    "recommendation": "suggest" or "skip",
    "reason": "one sentence explanation"
}}"""

                response = await ollama_service.generate(
                    prompt=prompt,
                    model=settings.ollama_fast_model,
                    temperature=0.3
                )
                
                text = response.get("response", "")
                json_start = text.find("{")
                json_end = text.rfind("}") + 1
                
                if json_start >= 0 and json_end > json_start:
                    result = json.loads(text[json_start:json_end])
                    source["curator_recommendation"] = result.get("recommendation", "suggest")
                    source["curator_reason"] = result.get("reason", "Potentially relevant source")
                    return source
            except Exception as e:
                logger.error(f"Source validation LLM failed: {e}")
        
        # Low confidence or validation failed - suggest with caveat
        source["curator_recommendation"] = "suggest" if source_confidence >= 0.4 else "skip"
        source["curator_reason"] = "Lower confidence - review before including"
        return source

    async def learn_from_source_decisions(
        self,
        notebook_id: str,
        approved_sources: List[Dict[str, Any]],
        rejected_sources: List[Dict[str, Any]]
    ) -> None:
        """
        Learn from user's source approval decisions.
        Improves future discovery recommendations.
        """
        # Store approval patterns in memory
        if approved_sources:
            approved_types = [s.get("source_type") for s in approved_sources]
            memory_store.record_user_signal(
                notebook_id=notebook_id,
                signal_type="source_approval",
                metadata={
                    "approved_count": len(approved_sources),
                    "source_types": approved_types
                }
            )
        
        if rejected_sources:
            rejected_types = [s.get("source_type") for s in rejected_sources]
            memory_store.record_user_signal(
                notebook_id=notebook_id,
                signal_type="source_rejection",
                metadata={
                    "rejected_count": len(rejected_sources),
                    "source_types": rejected_types
                }
            )
