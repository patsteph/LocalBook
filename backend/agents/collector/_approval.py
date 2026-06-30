"""ApprovalMixin — extracted from the former agents/collector.py (Wave 6 split)."""
from ._models import *  # noqa: F401,F403


class ApprovalMixin:
    async def _add_to_approval_queue(self, item: CollectedItem) -> str:
        """Add item to approval queue based on approval mode.

        Returns:
            'queued' if item was queued for user review,
            'stored' if auto-approved and stored successfully,
            'skipped' if auto-approved but storage failed (dedup, shallow, error),
            'rejected' if curator pre-triage rejected the item outright (Phase 1).
        """
        # Dedup: skip if URL already in queue or known sources
        if item.url:
            if item.url in self._known_urls:
                logger.info(f"Skipping queue add (URL known): {item.url}")
                return 'skipped'
            for q in self._approval_queue:
                if q.item.url == item.url:
                    logger.info(f"Skipping queue add (already queued): {item.url}")
                    return 'skipped'

        # Curator pre-triage (Curator Phase 1, 2026-05-12). Runs before
        # the approval-mode branches so a curator REJECT can short-circuit
        # even TRUST_ME, and a high-confidence curator APPROVE can store
        # without queuing. Falls through gracefully on any failure — the
        # collector behaves exactly as before when the curator isn't
        # available or pre-triage is disabled.
        curator_judgment = None
        if settings.curator_pre_triage_enabled:
            try:
                from agents.curator import curator, JudgmentDecision
                # Phase C.1 (2026-05-22): use the public `judge_collected_item`
                # contract instead of reaching into the curator's private
                # `_judge_single_item`. The public wrapper additionally emits
                # a `collector_item_pre_triaged` observability event so every
                # pre-triage decision lands in the brain's event log.
                curator_judgment = await curator.judge_collected_item(
                    item,
                    self.config.intent or "",
                    self.config.name or "Collector",
                )
                item.curator_decision = curator_judgment.decision.value
                logger.info(
                    f"[curator] judged '{item.title[:60]}': "
                    f"{curator_judgment.decision.value} "
                    f"(confidence {curator_judgment.confidence:.2f}) — {curator_judgment.reason}"
                )
                # Reject: stamp the reason and short-circuit. The item
                # never enters the queue.
                if curator_judgment.decision == JudgmentDecision.REJECT:
                    item.status = "rejected"
                    item.rejection_reason = curator_judgment.reason
                    self._emit_event(
                        "source_rejected",
                        item,
                        outcome="success",
                        extra={"by": "curator", "reason": curator_judgment.reason},
                    )
                    return 'rejected'
                # Approve with high confidence: store immediately, bypass queue.
                if (
                    curator_judgment.decision == JudgmentDecision.APPROVE
                    and curator_judgment.confidence >= 0.8
                ):
                    item.status = "approved"
                    was_stored = await self._store_approved_item(item)
                    self._emit_event(
                        "source_added" if was_stored else "source_store_skipped",
                        item,
                        outcome="success" if was_stored else "deferred",
                        extra={"by": "curator_auto_approve"},
                    )
                    return 'stored' if was_stored else 'skipped'
                # Otherwise (MODIFY / DEFER_TO_USER / low-confidence APPROVE)
                # fall through to the existing approval-mode logic, with
                # the curator decision stamped on the item for UI display.
            except Exception as e:
                logger.warning(f"Curator pre-triage failed (non-fatal): {e}")

        if self.config.approval_mode == ApprovalMode.TRUST_ME:
            # Auto-approve
            item.status = "approved"
            was_stored = await self._store_approved_item(item)
            return 'stored' if was_stored else 'skipped'

        if self.config.approval_mode == ApprovalMode.MIXED:
            # Auto-approve high confidence
            if item.overall_confidence >= 0.85:
                item.status = "approved"
                was_stored = await self._store_approved_item(item)
                return 'stored' if was_stored else 'skipped'

        # Queue for approval
        queue_item = ApprovalQueueItem(
            item=item,
            expires_at=datetime.utcnow() + timedelta(days=self.APPROVAL_EXPIRY_DAYS)
        )
        self._approval_queue.append(queue_item)
        if item.url:
            self._known_urls.add(item.url)
        self._save_approval_queue()
        self._emit_event(
            "source_pending_review",
            item,
            outcome="deferred",
            extra={
                "curator_decision": item.curator_decision,
                "approval_mode": str(self.config.approval_mode),
            },
        )
        return 'queued'

    def _emit_event(
        self,
        action: str,
        item: CollectedItem,
        outcome: str = "success",
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Best-effort emit to the curator event bus. Never raises.

        Curator Phase 1 (2026-05-12). Centralized so we have ONE spot to
        update if the payload shape evolves.
        """
        try:
            from services.curator_event_bus import event_bus
            payload: Dict[str, Any] = {
                "item_id": item.id,
                "title": item.title[:120] if item.title else "",
                "url": item.url,
                "source_name": item.source_name,
                "overall_confidence": item.overall_confidence,
            }
            if extra:
                payload.update(extra)
            event_bus.emit_now(
                actor="@collector",
                action=action,
                notebook_id=self.notebook_id,
                payload=payload,
                outcome=outcome,
            )
        except Exception as _e:
            # event bus is observability; failures must not break the agent
            pass

    async def _store_approved_item(self, item: CollectedItem, _existing_urls: set = None) -> bool:
        """Store an approved item as a notebook source AND in Collector memory.
        
        Returns True if the item was actually stored, False if skipped (dedup, shallow, error).
        Pass _existing_urls to avoid repeated source_store.list() calls in batch operations.
        """
        from storage.source_store import source_store
        from services.rag_engine import rag_engine
        
        # Final dedup guard: check if this URL already exists in stored sources
        print(f"[STORE] Attempting to store: '{item.title}' ({len(item.content)} chars, URL: {item.url})")
        if item.url:
            if _existing_urls is None:
                existing = await source_store.list(self.notebook_id)
                _existing_urls = {src.get("url") for src in existing if src.get("url")}
            if item.url in _existing_urls:
                print(f"[STORE] ✗ SKIPPED (duplicate URL): {item.url}")
                logger.info(f"Skipping duplicate store (URL exists): {item.url}")
                self._known_urls.add(item.url)
                return False
        
        # Enrich thin content by scraping full article (RSS feeds only have summaries)
        # Minimum content threshold — sources under 1000 chars are shallow scrapes
        # (just headers/meta/snippets) and pollute the RAG index with noise.
        # The enrichment block below tries to upgrade thin content before this gate.
        MIN_CONTENT_CHARS = 1000
        
        content = item.content
        if item.url and len(content) < 1000:
            try:
                # SEC filings need special handling — SEC.gov requires specific User-Agent
                if item.source_type == "sec":
                    content = await self._deep_fetch_sec_filing(item, content)
                else:
                    from services.web_scraper import web_scraper
                    scraped = await web_scraper._scrape_single(item.url)
                    if scraped and scraped.get("success") and scraped.get("text"):
                        full_text = scraped["text"]
                        if len(full_text) > len(content):
                            logger.info(f"Enriched '{item.title}': {len(content)} -> {len(full_text)} chars")
                            content = full_text
                            item.content = full_text
                            if scraped.get("title") and len(scraped["title"]) > len(item.title):
                                item.title = scraped["title"]
            except Exception as enrich_err:
                print(f"[STORE] Content enrichment failed for '{item.title}': {enrich_err}")
                logger.debug(f"Content enrichment failed (using original): {enrich_err}")
        
        # Gate: reject sources that are still too shallow after enrichment
        if len(content) < MIN_CONTENT_CHARS:
            logger.warning(
                f"[COLLECTOR] Rejecting shallow source '{item.title}' "
                f"({len(content)} chars < {MIN_CONTENT_CHARS} minimum). "
                f"Type: {item.source_type}, URL: {item.url}"
            )
            print(
                f"[STORE] ✗ SKIPPED (shallow): '{item.title}' "
                f"({len(content)} chars < {MIN_CONTENT_CHARS} minimum)"
            )
            return False
        
        # 1. Create actual notebook source so it shows up in the UI
        source_data = {
            "id": item.id,
            "notebook_id": self.notebook_id,
            "type": item.source_type or "web",
            "format": item.source_type or "web",
            "url": item.url,
            "title": item.title,
            "filename": item.title,
            "content": content,
            "summary": item.preview or content[:300],
            "word_count": len(content.split()),
            "char_count": len(content),
            "status": "processing",
            "collected_by": "collector",
            "confidence_score": item.overall_confidence,
            "confidence_reasons": item.confidence_reasons,
            "created_at": datetime.utcnow().isoformat()
        }
        
        try:
            await source_store.create(
                notebook_id=self.notebook_id,
                filename=item.title,
                metadata=source_data
            )
            
            # 2. Index in RAG for searchability
            rag_result = await rag_engine.ingest_document(
                notebook_id=self.notebook_id,
                source_id=item.id,
                text=item.content,
                filename=item.title,
                source_type=item.source_type or "web"
            )
            
            chunks = rag_result.get("chunks", 0) if rag_result else 0
            await source_store.update(self.notebook_id, item.id, {
                "chunks": chunks,
                "status": "completed"
            })
            
            print(f"[STORE] ✓ STORED: '{item.title}' → {chunks} chunks, status=completed")
            logger.info(f"Approved item stored as source: {item.title} ({chunks} chunks)")
            
            # Track URL in the dedup set for batch operations
            if item.url and _existing_urls is not None:
                _existing_urls.add(item.url)
            
            # Notify Constellation/frontend that a new source was added
            try:
                from api.constellation_ws import notify_source_updated
                await notify_source_updated({
                    "notebook_id": self.notebook_id,
                    "source_id": item.id,
                    "status": "completed",
                    "title": item.title[:200],
                    "chunks": chunks
                })
            except Exception as ws_err:
                logger.debug(f"WebSocket notification failed (non-fatal): {ws_err}")
            
        except Exception as e:
            print(f"[STORE] ✗ FAILED to store '{item.title}': {type(e).__name__}: {e}")
            logger.error(f"Failed to store approved item as source: {e}")
            return False
        
        # 3. Auto-tag the source using LLM (fire-and-forget, non-blocking)
        async def _tag_in_background():
            try:
                from services.auto_tagger import auto_tagger
                tags = await auto_tagger.generate_tags(
                    title=item.title,
                    content=item.content[:3000],
                    notebook_subject=self.config.subject,
                    focus_areas=self.config.focus_areas,
                )
                if tags:
                    from storage.source_store import source_store as _ss
                    await _ss.set_tags(self.notebook_id, item.id, tags)
                    logger.info(f"Auto-tagged source '{item.title[:50]}' with: {tags}")
            except Exception as tag_err:
                logger.debug(f"Auto-tagging failed (non-fatal): {tag_err}")
        
        from utils.tasks import safe_create_task
        safe_create_task(_tag_in_background(), name="collector-auto-tag")
        
        # 4. Also store in Collector memory for pattern tracking (non-fatal)
        try:
            entry = ArchivalMemoryEntry(
                content=f"{item.title}\n\n{item.content}",
                content_type="collected_item",
                source_type=MemorySourceType.WEB if item.url else MemorySourceType.MANUAL,
                source_id=item.url or item.id,
                source_notebook_id=self.notebook_id,
                topics=self.config.focus_areas[:5],
                importance=MemoryImportance.MEDIUM if item.overall_confidence >= 0.7 else MemoryImportance.LOW,
            )
            
            await memory_store.add_archival_memory_async(
                entry,
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=self.notebook_id
            )
        except Exception as mem_err:
            logger.warning(f"Failed to store item in archival memory (non-fatal): {mem_err}")
        
        # 4. Record approval signal for learning
        memory_store.record_user_signal(
            notebook_id=self.notebook_id,
            signal_type="item_approved",
            item_id=item.id,
            metadata={
                "title": item.title[:200],
                "source_name": item.source_name,
                "confidence": item.overall_confidence,
                "source_type": item.source_type
            }
        )
        
        return True

    async def _deep_fetch_sec_filing(self, item: 'CollectedItem', fallback_content: str) -> str:
        """
        Attempt to fetch full SEC filing content from the filing URL.
        
        SEC.gov requires a specific User-Agent header format and blocks generic scrapers.
        Tries multiple strategies:
        1. trafilatura with proper SEC headers
        2. Direct HTTP fetch + HTML text extraction
        
        Returns the best content found, or the original fallback_content if all strategies fail.
        """
        import aiohttp
        
        if not item.url:
            return fallback_content
        
        best_content = fallback_content
        sec_headers = {
            "User-Agent": "LocalBook Research Assistant research@localbook.app",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        
        # Strategy 1: trafilatura with proper headers (handles most HTML filings)
        try:
            import trafilatura
            import asyncio
            loop = asyncio.get_event_loop()
            
            # trafilatura.fetch_url doesn't accept custom headers easily,
            # so we download first, then extract
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers=sec_headers
            ) as session:
                async with session.get(item.url) as response:
                    if response.status == 200:
                        html = await response.text()
                        
                        # Extract with trafilatura (run in thread pool — blocking)
                        def _extract(h):
                            return trafilatura.extract(
                                h,
                                include_comments=False,
                                include_tables=True,
                                no_fallback=False,
                            )
                        
                        text = await loop.run_in_executor(None, _extract, html)
                        if text and len(text) > len(best_content):
                            logger.info(
                                f"SEC deep fetch (trafilatura): '{item.title}' "
                                f"{len(best_content)} -> {len(text)} chars"
                            )
                            best_content = text
                            item.content = text
        except Exception as e:
            logger.debug(f"SEC deep fetch strategy 1 failed: {e}")
        
        # Strategy 2: If trafilatura didn't yield much, try raw text extraction
        # (SEC filings are often plain-ish HTML with lots of text in <p>, <span>, <td>)
        if len(best_content) < 500:
            try:
                import re
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers=sec_headers
                ) as session:
                    async with session.get(item.url) as response:
                        if response.status == 200:
                            html = await response.text()
                            # Strip scripts, styles, then extract all text
                            html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
                            html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
                            html = re.sub(r'<[^>]+>', ' ', html)
                            text = re.sub(r'\s+', ' ', html).strip()
                            # Trim to reasonable size for a filing
                            text = text[:50000]
                            if len(text) > len(best_content):
                                logger.info(
                                    f"SEC deep fetch (raw extract): '{item.title}' "
                                    f"{len(best_content)} -> {len(text)} chars"
                                )
                                best_content = text
                                item.content = text
            except Exception as e:
                logger.debug(f"SEC deep fetch strategy 2 failed: {e}")
        
        return best_content

    def get_pending_approvals(self) -> List[Dict[str, Any]]:
        """Get items pending approval"""
        now = datetime.utcnow()
        
        # Filter out expired items
        valid = [q for q in self._approval_queue if q.expires_at > now]
        if len(valid) != len(self._approval_queue):
            self._approval_queue = valid
            self._save_approval_queue()
        
        return [
            {
                "item_id": q.item.id,
                "title": q.item.title,
                "preview": q.item.preview or q.item.content[:200],
                "source": q.item.source_name,
                "url": q.item.url,
                "confidence": q.item.overall_confidence,
                "confidence_reasons": q.item.confidence_reasons,
                "queued_at": q.queued_at.isoformat(),
                "expires_at": q.expires_at.isoformat(),
                "days_until_expiry": (q.expires_at - now).days,
                # Temporal Intelligence (Enhancement #6)
                "delta_summary": q.item.delta_summary,
                "is_new_topic": q.item.is_new_topic,
                "temporal_context": q.item.temporal_context,
                "knowledge_overlap": q.item.knowledge_overlap,
                "related_titles": q.item.related_titles,
                # Depth+1 expansion provenance — None for regular collector items.
                "parent_source_id": q.item.parent_source_id,
                "discovery_url": q.item.discovery_url,
                "cross_notebook_matches": q.item.cross_notebook_matches,
            }
            for q in valid
        ]

    def get_expiring_soon(self, days: int = 3) -> List[Dict[str, Any]]:
        """Get items expiring within N days"""
        cutoff = datetime.utcnow() + timedelta(days=days)
        return [
            a for a in self.get_pending_approvals()
            if datetime.fromisoformat(a["expires_at"]) <= cutoff
        ]

    async def approve_item(self, item_id: str, curator_approved: bool = False) -> bool:
        """Approve a queued item"""
        for i, q in enumerate(self._approval_queue):
            if q.item.id == item_id:
                q.item.status = "approved"
                await self._store_approved_item(q.item)
                self._approval_queue.pop(i)
                self._save_approval_queue()
                self._emit_event(
                    "source_added",
                    q.item,
                    outcome="success",
                    extra={"by": "curator" if curator_approved else "user"},
                )
                return True
        return False

    async def approve_batch(self, item_ids: List[str]) -> int:
        """Approve multiple items (batch operation)"""
        approved = 0
        for item_id in item_ids:
            if await self.approve_item(item_id):
                approved += 1
        return approved

    async def approve_all_from_source(self, source_name: str) -> int:
        """Approve all items from a specific source"""
        item_ids = [
            q.item.id for q in self._approval_queue
            if q.item.source_name == source_name
        ]
        return await self.approve_batch(item_ids)

    async def reject_item(
        self,
        item_id: str,
        reason: str,
        feedback_type: Optional[str] = None
    ) -> bool:
        """
        Reject an item with feedback for learning.
        
        feedback_type: wrong_topic, too_old, bad_source, already_knew, other
        """
        for i, q in enumerate(self._approval_queue):
            if q.item.id == item_id:
                q.item.status = "rejected"
                q.item.rejection_reason = reason
                
                # Record signal for learning
                memory_store.record_user_signal(
                    notebook_id=self.notebook_id,
                    signal_type="reject",
                    item_id=item_id,
                    metadata={
                        "reason": reason,
                        "feedback_type": feedback_type,
                        "source": q.item.source_name,
                        "confidence": q.item.overall_confidence
                    }
                )
                
                # Learn from rejection
                await self._learn_from_rejection(q.item, reason, feedback_type)

                self._approval_queue.pop(i)
                self._save_approval_queue()
                self._emit_event(
                    "source_rejected",
                    q.item,
                    outcome="success",
                    extra={"by": "user", "reason": reason, "feedback_type": feedback_type},
                )
                return True
        return False

    async def _learn_from_rejection(
        self,
        item: CollectedItem,
        reason: str,
        feedback_type: Optional[str]
    ) -> None:
        """Adapt Collector behavior based on rejection"""
        if feedback_type == "wrong_topic":
            # Add to excluded topics (placeholder - would extract topics from item)
            pass
        elif feedback_type == "bad_source":
            # Reduce trust for this source
            if item.source_name in self._source_health:
                self._source_health[item.source_name].health = SourceHealth.DEGRADED
        elif feedback_type == "too_old":
            # Tighten freshness filter
            current_max = self.config.filters.get("max_age_days", 30)
            if current_max > 7:
                self.config.filters["max_age_days"] = current_max - 7
                self._save_config()
