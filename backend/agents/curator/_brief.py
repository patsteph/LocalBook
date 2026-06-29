"""CuratorBriefMixin — extracted from the former agents/curator.py (Wave 3 split)."""
from ._models import *  # noqa: F401,F403


class CuratorBriefMixin:
    async def generate_morning_brief(self, last_seen: datetime) -> MorningBrief:
        """Single-flight wrapper around the morning-brief generator.

        Two concurrent callers (scheduler + user-triggered chat, or UI polling
        + background refresh) would otherwise both spend ~30–90 s building
        narratives and the second completion silently overwrite the first.
        We serialize on a lock and, inside the lock, short-circuit if a
        fresh result is already in memory (<90 s old) so concurrent callers
        all get the same object.
        """
        # Fast path: return cached result without taking the lock at all.
        now_ts = datetime.utcnow()
        if (
            self._morning_brief_cache is not None
            and self._morning_brief_cached_at is not None
            and (now_ts - self._morning_brief_cached_at).total_seconds() < 90
        ):
            return self._morning_brief_cache

        async with self._morning_brief_lock:
            # Re-check under the lock — another coroutine may have finished
            # generating while we were waiting.
            now_ts = datetime.utcnow()
            if (
                self._morning_brief_cache is not None
                and self._morning_brief_cached_at is not None
                and (now_ts - self._morning_brief_cached_at).total_seconds() < 90
            ):
                logger.info(
                    "[curator] Reusing morning brief generated %.1fs ago (single-flight)",
                    (now_ts - self._morning_brief_cached_at).total_seconds(),
                )
                return self._morning_brief_cache

            result = await self._generate_morning_brief_impl(last_seen)
            self._morning_brief_cache = result
            self._morning_brief_cached_at = datetime.utcnow()
            return result

    async def _generate_morning_brief_impl(self, last_seen: datetime) -> MorningBrief:
        """
        Generate a newsletter-quality morning brief summarizing activity since
        the user last opened the app.
        
        Gathers rich data per notebook, then uses LLM to synthesize into a
        narrative the user actually wants to read.
        """
        # Phase 1: Use deterministic temporal context instead of utcnow() guesswork
        from services.temporal import TemporalContext
        temporal = TemporalContext(user_tz=self._get_user_timezone())
        now = temporal.now  # timezone-aware local time
        duration_str = temporal.duration_from(last_seen)
        temporal_block = temporal.for_prompt(last_seen)
        
        notebooks = await notebook_store.list()
        summaries = []
        
        # Pre-load all sources once (not per-notebook) for scalability
        from storage.source_store import source_store
        all_sources_by_nb = await source_store.list_all()
        
        # Gather activity stats for ALL notebooks in PARALLEL (not serial)
        import asyncio
        activity_tasks = [
            self._get_activity_since(nb["id"], last_seen, all_sources_by_nb.get(nb["id"], []))
            for nb in notebooks
        ]
        all_stats = await asyncio.gather(*activity_tasks, return_exceptions=True)
        
        for notebook, stats in zip(notebooks, all_stats):
            if isinstance(stats, Exception):
                logger.error(f"Activity stats failed for {notebook['id']}: {stats}")
                continue
            
            # Include notebook if it has ANY recent activity signal
            studio_created = (
                stats.get("docs_generated", 0) + stats.get("audio_generated", 0) +
                stats.get("visuals_generated", 0) + stats.get("quizzes_generated", 0) +
                stats.get("videos_generated", 0)
            )
            has_activity = (
                stats["items_added"] > 0 or stats["pending_approval"] > 0 or
                stats.get("person_changes") or stats.get("upcoming_key_dates") or
                stats.get("recent_stories") or
                stats.get("collection_runs", 0) > 0 or
                stats.get("sources_this_week", 0) > 0 or
                stats.get("highlights_since", 0) > 0 or
                stats.get("interactions_since", 0) > 0 or
                stats.get("unfinished_threads") or
                studio_created > 0
            )
            if has_activity:
                # Get the collector subject for this notebook
                subject = ""
                try:
                    from agents.collector import get_collector
                    collector = get_collector(notebook["id"])
                    cfg = collector.get_config()
                    subject = cfg.subject if hasattr(cfg, "subject") and cfg.subject else ""
                except Exception as _e:
                    logger.debug(f"[curator] {type(_e).__name__}: {_e}")
                
                # Curator Phase 5: filter + boost stories using
                # suppressions and engagement signal.
                stories_raw = stats.get("recent_stories", [])
                stories_raw = self._filter_and_rank_stories(
                    notebook["id"], stories_raw,
                )
                # Strip 'origin' from story dicts before passing to RecentStory model
                stories = []
                for sr in stories_raw:
                    sr_copy = {k: v for k, v in sr.items() if k != "origin"}
                    stories.append(RecentStory(**sr_copy))

                # Phase 5: record story_offered engagement for each story
                # that survived filtering — the brain uses these to track
                # which topics are repeatedly shown but not clicked.
                self._record_stories_offered(notebook["id"], stories_raw)
                
                summaries.append(NotebookSummary(
                    notebook_id=notebook["id"],
                    name=notebook.get("title", notebook.get("name", "Untitled")),
                    subject=subject,
                    items_added=stats["items_added"],
                    flagged_important=stats.get("flagged", 0),
                    pending_approval=stats.get("pending_approval", 0),
                    top_finding=stats.get("top_item"),
                    recent_stories=stories,
                    person_changes=stats.get("person_changes", []),
                    upcoming_key_dates=stats.get("upcoming_key_dates", []),
                    collection_runs=stats.get("collection_runs", 0),
                    collection_items_found=stats.get("collection_items_found", 0),
                    collection_items_approved=stats.get("collection_items_approved", 0),
                    collection_items_rejected=stats.get("collection_items_rejected", 0),
                    collection_items_pending=stats.get("collection_items_pending", 0),
                    collector_added=stats.get("collector_added", 0),
                    user_added=stats.get("user_added", 0),
                    total_sources=stats.get("total_sources", 0),
                    sources_this_week=stats.get("sources_this_week", 0),
                    sources_last_week=stats.get("sources_last_week", 0),
                    sources_summarized=stats.get("sources_summarized", 0),
                    sources_unread=stats.get("sources_unread", 0),
                    highlights_since=stats.get("highlights_since", 0),
                    recent_highlight_texts=stats.get("recent_highlight_texts", []),
                    interactions_since=stats.get("interactions_since", 0),
                    chat_queries=stats.get("chat_queries", 0),
                    searches=stats.get("searches", 0),
                    docs_read=stats.get("docs_read", 0),
                    docs_generated=stats.get("docs_generated", 0),
                    audio_generated=stats.get("audio_generated", 0),
                    visuals_generated=stats.get("visuals_generated", 0),
                    quizzes_generated=stats.get("quizzes_generated", 0),
                    videos_generated=stats.get("videos_generated", 0),
                    studio_topics=stats.get("studio_topics", []),
                    unfinished_threads=stats.get("unfinished_threads", []),
                    emerging_topics=stats.get("emerging_topics", []),
                    one_week_ago_items=stats.get("one_week_ago_items", []),
                    notes_created=stats.get("notes_created", 0),
                    note_titles=stats.get("note_titles", []),
                    total_notes=stats.get("total_notes", 0),
                ))
        
        # Get any active cross-notebook insight from the brain
        cross_insight = None
        try:
            from services.curator_brain import curator_brain
            _active = curator_brain.get_active_insights(limit=1)
            if _active:
                cross_insight = _active[0]["summary"]
        except Exception as _e:
            logger.debug(f"[curator] get_active_insights (morning brief): {_e}")
        
        # Curator Phase 5 (2026-05-13): build a per-notebook "What's
        # changed in your understanding" diff block. Aggregates across
        # all notebooks. Passed to the synthesizer as a separate input
        # so the LLM can naturally include a "What's new" section.
        understanding_diff_block = ""
        try:
            from services.curator_brain import curator_brain as _cb
            since_iso = last_seen.isoformat() if last_seen else None
            if since_iso:
                diff_lines: List[str] = []
                for nb in notebooks[:10]:
                    diff = _cb.compute_understanding_diff(nb["id"], since_iso)
                    has_signal = (
                        diff.get("new_connections")
                        or diff.get("mental_model_changes")
                        or diff.get("new_dissent_sources")
                    )
                    if not has_signal:
                        continue
                    name = nb.get("title", nb.get("name", "Untitled"))
                    nb_lines = [f"  {name}:"]
                    if diff.get("mental_model_changes"):
                        mm = diff["mental_model_changes"][0]
                        if mm.get("thesis"):
                            nb_lines.append(f"    - thesis refined: {mm['thesis'][:120]}")
                        if mm.get("stage"):
                            nb_lines.append(f"    - stage: {mm['stage']}")
                    if diff.get("new_dissent_sources"):
                        for d in diff["new_dissent_sources"][:2]:
                            nb_lines.append(
                                f"    - new contradicting source: "
                                f"{(d.get('rationale') or '')[:120]}"
                            )
                    if diff.get("new_connections"):
                        for c in diff["new_connections"][:2]:
                            nb_lines.append(
                                f"    - new cross-notebook link: "
                                f"{(c.get('description') or '')[:120]}"
                            )
                    if len(nb_lines) > 1:
                        diff_lines.extend(nb_lines)
                if diff_lines:
                    understanding_diff_block = (
                        "What changed in the curator's understanding since last brief:\n"
                        + "\n".join(diff_lines)
                    )
        except Exception as _e:
            logger.debug(f"[curator] understanding diff fetch failed (non-fatal): {_e}")

        # Generate LLM narrative — turn raw data into a newsletter people look forward to
        narrative = await self._synthesize_brief_narrative(
            summaries, duration_str, cross_insight, temporal_block,
            understanding_diff=understanding_diff_block,
        )

        # Phase 10 — consensus detection + deep-read trigger + HTML dashboard.
        # Always runs after the markdown narrative so the existing path is
        # unaffected if any new piece fails.
        consensus_clusters: List[Dict[str, Any]] = []
        deep_reads_triggered: List[Dict[str, Any]] = []
        narrative_html: Optional[str] = None
        try:
            from services.consensus_detector import detect_consensus
            clusters = await detect_consensus(since_days=3, min_cluster_size=3)
            consensus_clusters = [c.model_dump() for c in clusters]
            deep_reads_triggered = self._fire_deep_reads_for_clusters(clusters)
            total_recent_ingests = sum(c.size for c in clusters) if clusters else 0
            narrative_html = self._compose_brief_html(
                duration_str=duration_str,
                summaries=summaries,
                narrative=narrative,
                cross_insight=cross_insight,
                clusters=clusters,
                deep_reads=deep_reads_triggered,
                total_recent_ingests=total_recent_ingests,
            )
        except Exception as _e:
            logger.debug(f"[curator] Phase 10 dashboard skipped (non-fatal): {_e}")

        return MorningBrief(
            away_duration=duration_str,
            notebook_summaries=summaries,
            cross_notebook_insight=cross_insight,
            narrative=narrative,
            generated_at=now,
            narrative_html=narrative_html,
            consensus_clusters=consensus_clusters,
            deep_reads_triggered=deep_reads_triggered,
        )

    async def generate_weekly_wrap_up(self) -> WeeklyWrapUp:
        """Single-flight wrapper around the weekly-wrap generator.

        The user-reported failure mode: they were reading a wrap with a long
        narrative, then a second (shorter / nearly-empty) wrap appeared and
        replaced it. Root cause: two callers (chat "weekly wrap" intent +
        UI refresh, or scheduler + manual) both invoked this method, each
        wrote its result to `memory/weekly_wrap_YYYY-MM-DD.json`, and the
        second write clobbered the first.

        Fix: serialize concurrent callers on `_weekly_wrap_lock` and reuse
        a cached result for 5 minutes. Narrative generation takes 30–90 s;
        5 minutes comfortably covers any double-click / polling / scheduler
        overlap without ever going stale enough to mislead the user.
        """
        now_ts = datetime.utcnow()
        if (
            self._weekly_wrap_cache is not None
            and self._weekly_wrap_cached_at is not None
            and (now_ts - self._weekly_wrap_cached_at).total_seconds() < 300
        ):
            return self._weekly_wrap_cache

        async with self._weekly_wrap_lock:
            now_ts = datetime.utcnow()
            if (
                self._weekly_wrap_cache is not None
                and self._weekly_wrap_cached_at is not None
                and (now_ts - self._weekly_wrap_cached_at).total_seconds() < 300
            ):
                logger.info(
                    "[curator] Reusing weekly wrap generated %.1fs ago (single-flight)",
                    (now_ts - self._weekly_wrap_cached_at).total_seconds(),
                )
                return self._weekly_wrap_cache

            result = await self._generate_weekly_wrap_up_impl()
            self._weekly_wrap_cache = result
            self._weekly_wrap_cached_at = datetime.utcnow()
            return result

    async def _generate_weekly_wrap_up_impl(self) -> WeeklyWrapUp:
        """Generate a Weekly Wrap Up covering the past 7 days of research activity.
        
        Designed to replace the Monday Morning Brief — gives a broader view of
        what was discovered, debated, and created over the entire week.
        Generated lazily on Monday morning (or on demand).
        """
        from datetime import timedelta
        import asyncio
        
        now = datetime.utcnow()
        # Cover the past 7 days (previous Mon through Sun)
        week_end = now
        week_start = now - timedelta(days=7)
        
        notebooks = await notebook_store.list()
        
        # Pre-load all sources once
        from storage.source_store import source_store
        all_sources_by_nb = await source_store.list_all()
        
        # Gather activity for the full week in parallel
        activity_tasks = [
            self._get_activity_since(nb["id"], week_start, all_sources_by_nb.get(nb["id"], []))
            for nb in notebooks
        ]
        all_stats = await asyncio.gather(*activity_tasks, return_exceptions=True)
        
        summaries = []
        total_sources = 0
        total_collector = 0
        total_user = 0
        total_convos = 0
        
        for notebook, stats in zip(notebooks, all_stats):
            if isinstance(stats, Exception):
                logger.error(f"Weekly stats failed for {notebook['id']}: {stats}")
                continue
            
            has_activity = (
                stats["items_added"] > 0 or stats.get("collection_runs", 0) > 0 or
                stats.get("interactions_since", 0) > 0 or stats.get("highlights_since", 0) > 0
            )
            if not has_activity:
                continue
            
            subject = ""
            try:
                from agents.collector import get_collector
                collector = get_collector(notebook["id"])
                cfg = collector.get_config()
                subject = cfg.subject if hasattr(cfg, "subject") and cfg.subject else ""
            except Exception as _e:
                logger.debug(f"[curator] {type(_e).__name__}: {_e}")
            
            # Curator Phase 5: same filter+boost as the primary path
            stories_raw = stats.get("recent_stories", [])
            stories_raw = self._filter_and_rank_stories(notebook["id"], stories_raw)
            stories = []
            for sr in stories_raw:
                sr_copy = {k: v for k, v in sr.items() if k != "origin"}
                stories.append(RecentStory(**sr_copy))
            self._record_stories_offered(notebook["id"], stories_raw)

            summaries.append(NotebookSummary(
                notebook_id=notebook["id"],
                name=notebook.get("title", notebook.get("name", "Untitled")),
                subject=subject,
                items_added=stats["items_added"],
                flagged_important=stats.get("flagged", 0),
                pending_approval=stats.get("pending_approval", 0),
                top_finding=stats.get("top_item"),
                recent_stories=stories,
                person_changes=stats.get("person_changes", []),
                upcoming_key_dates=stats.get("upcoming_key_dates", []),
                collection_runs=stats.get("collection_runs", 0),
                collection_items_found=stats.get("collection_items_found", 0),
                collection_items_approved=stats.get("collection_items_approved", 0),
                collection_items_rejected=stats.get("collection_items_rejected", 0),
                collection_items_pending=stats.get("collection_items_pending", 0),
                collector_added=stats.get("collector_added", 0),
                user_added=stats.get("user_added", 0),
                total_sources=stats.get("total_sources", 0),
                sources_this_week=stats.get("sources_this_week", 0),
                sources_last_week=stats.get("sources_last_week", 0),
                sources_summarized=stats.get("sources_summarized", 0),
                sources_unread=stats.get("sources_unread", 0),
                highlights_since=stats.get("highlights_since", 0),
                recent_highlight_texts=stats.get("recent_highlight_texts", []),
                interactions_since=stats.get("interactions_since", 0),
                chat_queries=stats.get("chat_queries", 0),
                searches=stats.get("searches", 0),
                docs_read=stats.get("docs_read", 0),
                docs_generated=stats.get("docs_generated", 0),
                audio_generated=stats.get("audio_generated", 0),
                visuals_generated=stats.get("visuals_generated", 0),
                quizzes_generated=stats.get("quizzes_generated", 0),
                videos_generated=stats.get("videos_generated", 0),
                studio_topics=stats.get("studio_topics", []),
                unfinished_threads=stats.get("unfinished_threads", []),
                emerging_topics=stats.get("emerging_topics", []),
                one_week_ago_items=stats.get("one_week_ago_items", []),
            ))
            
            total_sources += stats["items_added"]
            total_collector += stats.get("collector_added", 0)
            total_user += stats.get("user_added", 0)
            total_convos += stats.get("chat_queries", 0)
        
        # Count audio and document generations this week (from event logger, more accurate)
        total_audio = sum(s.audio_generated for s in summaries)
        total_docs = sum(s.docs_generated for s in summaries)
        
        # Cross-notebook insight (from the brain — used to be self._pending_insights)
        cross_insight = None
        try:
            from services.curator_brain import curator_brain
            _active = curator_brain.get_active_insights(limit=1)
            if _active:
                cross_insight = _active[0]["summary"]
        except Exception as _e:
            logger.debug(f"[curator] get_active_insights (weekly): {_e}")
        
        narrative = await self._synthesize_weekly_narrative(
            summaries, cross_insight, total_sources, total_collector,
            total_user, total_convos, total_audio, total_docs
        )
        
        # Phase 14 — compose HTML variant so the wrap can render as a
        # dashboard card via the ```html fence handler (parallels Phase 10
        # morning brief). Non-blocking; falls back to narrative-only on
        # any failure.
        narrative_html: Optional[str] = None
        try:
            narrative_html = self._compose_weekly_wrap_html(
                week_start=week_start.strftime("%Y-%m-%d"),
                week_end=week_end.strftime("%Y-%m-%d"),
                summaries=summaries,
                narrative=narrative,
                cross_insight=cross_insight,
                total_sources=total_sources,
                total_collector=total_collector,
                total_user=total_user,
                total_convos=total_convos,
                total_audio=total_audio,
                total_docs=total_docs,
            )
        except Exception as e:
            logger.debug(f"[curator] weekly wrap HTML composition skipped: {e}")

        return WeeklyWrapUp(
            week_start=week_start.strftime("%Y-%m-%d"),
            week_end=week_end.strftime("%Y-%m-%d"),
            notebook_summaries=summaries,
            cross_notebook_insight=cross_insight,
            narrative=narrative,
            narrative_html=narrative_html,
            generated_at=now,
            total_sources_added=total_sources,
            total_collector_added=total_collector,
            total_user_added=total_user,
            total_conversations=total_convos,
            total_audio_generated=total_audio,
            total_documents_generated=total_docs,
        )

    async def _synthesize_weekly_narrative(
        self,
        summaries: List['NotebookSummary'],
        cross_insight: Optional[str],
        total_sources: int,
        total_collector: int,
        total_user: int,
        total_convos: int,
        total_audio: int,
        total_docs: int,
    ) -> str:
        """Use LLM to generate a Weekly Wrap Up narrative."""
        if not summaries:
            return ""
        
        # Pull memory context for weekly narrative (same pattern as morning brief)
        import asyncio
        memory_context_by_nb = {}
        try:
            from storage.memory_store import memory_store
            from models.memory import AgentNamespace
            
            async def _fetch_memory(nb_id, nb_name):
                results = await asyncio.to_thread(
                    memory_store.search_archival_memory,
                    query=f"weekly progress decisions key findings {nb_name}",
                    namespace=AgentNamespace.CURATOR,
                    notebook_id=nb_id,
                    cross_notebook=True,
                    limit=3
                )
                return nb_id, results
            
            mem_tasks = [_fetch_memory(nb.notebook_id, nb.name) for nb in summaries]
            mem_results = await asyncio.gather(*mem_tasks, return_exceptions=True)
            for item in mem_results:
                if isinstance(item, Exception):
                    continue
                nb_id, results = item
                if results:
                    snippets = [r.entry.content[:250] for r in results if r.combined_score > 0.2]
                    if snippets:
                        memory_context_by_nb[nb_id] = snippets
        except Exception as e:
            logger.debug(f"Memory context for weekly wrap failed (non-fatal): {e}")
        
        # Build structured data (reuse the same format as morning brief)
        notebook_sections = []
        for nb in summaries:
            section = f"Notebook: {nb.name}"
            if nb.subject:
                section += f" (tracking: {nb.subject})"
            details = []
            
            if nb.recent_stories:
                for story in nb.recent_stories[:5]:
                    detail = f"  - \"{story.title}\""
                    if story.source_name:
                        detail += f" ({story.source_name})"
                    if story.summary:
                        detail += f" — {story.summary[:150]}"
                    details.append(detail)
            
            if nb.collector_added > 0 or nb.user_added > 0:
                origin_parts = []
                if nb.collector_added > 0:
                    origin_parts.append(f"{nb.collector_added} auto-collected")
                if nb.user_added > 0:
                    origin_parts.append(f"{nb.user_added} you added")
                details.append(f"  - Sources this week: {'; '.join(origin_parts)}")
            
            if nb.collection_runs > 0:
                details.append(f"  - Collector ran {nb.collection_runs}x: examined {nb.collection_items_found} items, stored {nb.collection_items_approved}, rejected {nb.collection_items_rejected}")
                if nb.collection_items_found > 0 and nb.collection_items_approved == 0:
                    details.append(f"    NOTE: collector found items but NONE passed quality filters — zero new sources added by collector")
            
            if nb.total_sources > 0:
                details.append(f"  - Library: {nb.total_sources} total sources")
            
            if nb.interactions_since > 0:
                activity_parts = []
                if nb.chat_queries > 0:
                    activity_parts.append(f"{nb.chat_queries} conversations")
                if nb.searches > 0:
                    activity_parts.append(f"{nb.searches} searches")
                if activity_parts:
                    details.append(f"  - Your activity: {', '.join(activity_parts)}")
            
            # Studio content creation
            studio_total = nb.docs_generated + nb.audio_generated + nb.visuals_generated + nb.quizzes_generated + nb.videos_generated
            if studio_total > 0:
                studio_parts = []
                if nb.docs_generated > 0:
                    studio_parts.append(f"{nb.docs_generated} document{'s' if nb.docs_generated != 1 else ''}")
                if nb.audio_generated > 0:
                    studio_parts.append(f"{nb.audio_generated} podcast{'s' if nb.audio_generated != 1 else ''}")
                if nb.visuals_generated > 0:
                    studio_parts.append(f"{nb.visuals_generated} visual{'s' if nb.visuals_generated != 1 else ''}")
                if nb.quizzes_generated > 0:
                    studio_parts.append(f"{nb.quizzes_generated} quiz{'zes' if nb.quizzes_generated != 1 else ''}")
                if nb.videos_generated > 0:
                    studio_parts.append(f"{nb.videos_generated} video{'s' if nb.videos_generated != 1 else ''}")
                details.append(f"  - Studio output: created {', '.join(studio_parts)}")
                if nb.studio_topics:
                    details.append(f"    Topics: {', '.join(nb.studio_topics)}")
            
            if nb.highlights_since > 0:
                details.append(f"  - Highlighted {nb.highlights_since} passages")
            
            if nb.unfinished_threads:
                details.append(f"  - Open threads: {'; '.join(nb.unfinished_threads[:2])}")
            
            if nb.emerging_topics:
                details.append(f"  - Emerging topics: {', '.join(nb.emerging_topics)}")
            
            # Memory context — what the user was discussing/deciding this week
            nb_memories = memory_context_by_nb.get(nb.notebook_id, [])
            if nb_memories:
                details.append(f"  - Research context from memory:")
                for mem in nb_memories[:2]:
                    details.append(f"    📝 {mem}")
            
            if details:
                section += "\n" + "\n".join(details)
            notebook_sections.append(section)
        
        raw_data = "\n\n".join(notebook_sections)
        if cross_insight:
            raw_data += f"\n\nCross-notebook insight: {cross_insight}"
        
        # Aggregate stats block
        total_visuals = sum(s.visuals_generated for s in summaries)
        total_quizzes = sum(s.quizzes_generated for s in summaries)
        total_videos = sum(s.videos_generated for s in summaries)
        
        raw_data += f"\n\nWEEKLY TOTALS:"
        raw_data += f"\n  - Total sources added: {total_sources} ({total_collector} by collector, {total_user} by you)"
        raw_data += f"\n  - Conversations: {total_convos}"
        studio_total_week = total_audio + total_docs + total_visuals + total_quizzes + total_videos
        if studio_total_week > 0:
            studio_week_parts = []
            if total_docs > 0:
                studio_week_parts.append(f"{total_docs} document{'s' if total_docs != 1 else ''}")
            if total_audio > 0:
                studio_week_parts.append(f"{total_audio} podcast{'s' if total_audio != 1 else ''}")
            if total_visuals > 0:
                studio_week_parts.append(f"{total_visuals} visual{'s' if total_visuals != 1 else ''}")
            if total_quizzes > 0:
                studio_week_parts.append(f"{total_quizzes} quiz{'zes' if total_quizzes != 1 else ''}")
            if total_videos > 0:
                studio_week_parts.append(f"{total_videos} video{'s' if total_videos != 1 else ''}")
            raw_data += f"\n  - Studio output: {', '.join(studio_week_parts)}"
        
        today_str = datetime.utcnow().strftime("%B %d, %Y")
        prompt = f"""You are a personal research assistant writing a WEEKLY WRAP UP for {today_str}. This covers the ENTIRE past week of the user's research activity — a broader, more reflective view than the daily morning brief.

RAW DATA:
{raw_data}

WEEKLY WRAP UP STRUCTURE:
1. **Opening** — A warm "Here's your week in review" opening. Set a reflective tone.
2. **The Big Picture** — What were the major themes across all notebooks this week? Any patterns emerging?
3. **Per-notebook highlights** — For each active notebook, summarize the week's key additions and discoveries. Use actual titles.
4. **Collector Report** — If the background collector gathered sources, summarize what it found. Distinguish clearly from what the user added themselves.
5. **Your Activity** — How actively did the user engage? Conversations, searches, highlights. Frame it positively.
6. **Threads to Pick Up** — Open questions and unfinished conversations worth revisiting this week.
7. **Looking Ahead** — Based on this week's momentum, what should the user focus on next week? Any upcoming dates?
8. **Weekly Stat Line** — End with a clean summary: "This week: X sources added, Y conversations, Z audio pieces generated."

RULES:
- Use exact numbers from the data. Never invent or round.
- This is a WEEKLY summary — use "this week", "over the past week", "this week's research" framing.
- Distinguish collector-gathered sources from user-added ones.
- Length: 300-500 words. More substantial than the daily brief.

NEWSLETTER FORMATTING (CRITICAL):
- Use markdown extensively for a modern newsletter layout.
- Use `###` headers for each notebook or major section to break up text visually.
- Use **bold** liberally for source titles, key metrics, and important entities.
- Use bullet points (`-`) for lists of items (like newly discovered sources or threads).
- Keep paragraphs very short (1-2 sentences). Absolutely NO dense walls of text. Be highly scannable.
- Insert blank lines between sections to give the text room to breathe.
- Tone: warm, reflective, slightly celebratory of progress. Like a trusted advisor reviewing the week together.

Write the weekly wrap up now:"""
        
        try:
            from services.rag_engine import rag_engine
            from config import settings

            # Routed through rag_engine._call_ollama for two reasons:
            #   1. num_predict=2000 — the original call left this unset, so
            #      Ollama defaulted to ~128 tokens and clipped a 300-500
            #      word newsletter mid-sentence. The clipped tail broke
            #      markdown pairs (**bold**, [link]()) and rendered as
            #      raw chars in the UI — the "markdown leakage" symptom.
            #   2. rag_engine respects the active model's rag_profile,
            #      including use_chat_endpoint=true for Gemma4. Calling
            #      ollama_client.generate directly always hits /api/generate
            #      which uses the wrong template for Gemma and produces
            #      shorter, more fragmented output on memory pressure.
            # voice_modifier=False because the system prompt below already
            # carries the curator's personality and tone instructions.
            narrative = await rag_engine._call_ollama(
                system_prompt="You are a concise, insightful research assistant. Write engaging weekly summaries that help people reflect on their research progress.",
                prompt=prompt,
                model=settings.ollama_model,
                # 2026-06-08: dropped 0.7 → 0.55 for gemma4 (better
                # instruction-following than olmo; CLAUDE.md doc-gen range).
                temperature=0.55,
                num_predict=2000,
                voice_modifier=False,
            )
            narrative = (narrative or "").strip()
            if narrative and not narrative.startswith(("Request timed out", "Error:")):
                return narrative
        except Exception as e:
            logger.error(f"Weekly narrative generation failed: {e}")
        
        # Fallback
        lines = [f"# Weekly Wrap Up — {today_str}\n"]
        for nb in summaries:
            line = f"**{nb.name}**: {nb.items_added} sources added"
            if nb.collector_added > 0:
                line += f" ({nb.collector_added} by collector)"
            lines.append(line)
        lines.append(f"\n**Week totals:** {total_sources} sources, {total_convos} conversations")
        if total_audio > 0:
            lines.append(f", {total_audio} audio pieces")
        return "\n".join(lines)

    def _filter_and_rank_stories(
        self,
        notebook_id: str,
        stories_raw: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Apply Phase 5 brief story filter (suppressions) + boost
        (engagement-based topic click score).

        Suppression is hard — story is dropped if its title contains any
        active suppression keyword.

        Boost is soft — surviving stories get reordered with a small
        positive adjustment for topics the user has clicked recently.
        Falls back to the original order when no engagement signal exists.
        """
        if not stories_raw:
            return stories_raw
        try:
            from services.curator_brain import curator_brain as _cb
            filtered: List[Dict[str, Any]] = []
            for sr in stories_raw:
                title = sr.get("title") or ""
                if _cb.is_topic_suppressed(notebook_id, title):
                    continue
                filtered.append(sr)

            # Apply click-score boost. Topic-key extraction: first 2-3
            # significant words from title. Cheap stopword filter.
            stop = {"the", "a", "an", "of", "in", "on", "for", "to",
                    "and", "or", "is", "are", "with", "from", "by"}

            def _topic_key_for(title: str) -> str:
                words = [w.strip(".,!?:;\"'()[]").lower() for w in (title or "").split()]
                meaningful = [w for w in words if w and w not in stop and len(w) > 2]
                return " ".join(meaningful[:3])

            # Keep stable original order when scores are tied — sort with
            # a (score_desc, idx_asc) key so click-boost re-orders only
            # when there's actual engagement signal.
            # 2026-05-23 (#1 engagement-weighted brief boost): repeatedly
            # ignored topics now get a HARD negative score that pushes them
            # to the bottom. Without this, click-boost only RAISED clicked
            # topics; it didn't suppress the topics the user keeps ignoring.
            # Net effect: brief stops repeating things the user has shown
            # they don't care about.
            scored: List[tuple] = []
            for i, sr in enumerate(filtered):
                key = _topic_key_for(sr.get("title") or "")
                if not key:
                    scored.append((0.0, i, sr))
                    continue
                if _cb.is_topic_repeatedly_ignored(notebook_id, key):
                    # Push to bottom — score very negative, preserves order
                    # within the demoted bucket via the original index.
                    scored.append((1000.0, i, sr))
                    continue
                score = _cb.get_topic_click_score(notebook_id, key)
                scored.append((-score, i, sr))
            scored.sort()
            return [t[2] for t in scored]
        except Exception as e:
            logger.debug(f"[curator] _filter_and_rank_stories failed (non-fatal): {e}")
            return stories_raw

    def _record_stories_offered(
        self,
        notebook_id: str,
        stories: List[Dict[str, Any]],
    ) -> None:
        """Phase 5: record an `offered` engagement event per story that
        ends up in a brief. The brain uses these to compute future
        topic_click_score (offered + clicked → boost weight).

        Best-effort, never raises.
        """
        if not stories:
            return
        try:
            from services.curator_brain import curator_brain as _cb
            stop = {"the", "a", "an", "of", "in", "on", "for", "to",
                    "and", "or", "is", "are", "with", "from", "by"}
            for sr in stories[:20]:  # cap to avoid runaway recording
                title = sr.get("title") or ""
                words = [w.strip(".,!?:;\"'()[]").lower() for w in title.split()]
                meaningful = [w for w in words if w and w not in stop and len(w) > 2]
                topic_key = " ".join(meaningful[:3])
                if not topic_key:
                    continue
                _cb.record_engagement(
                    kind="brief",
                    signal="offered",
                    subject_type="topic",
                    subject_id=topic_key,
                    notebook_id=notebook_id,
                    payload={"title": title[:120]},
                )
        except Exception as e:
            logger.debug(f"[curator] _record_stories_offered failed (non-fatal): {e}")

    async def _get_activity_since(self, notebook_id: str, since: datetime, preloaded_sources: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Get activity stats for a notebook since a given time.

        Pulls from: collector pending queue, archival memory, collection history,
        event logger, and person change detection.
        
        Args:
            preloaded_sources: If provided, skip source_store.list() and use these.
                              Used by generate_morning_brief to avoid N file reads.
        """
        from agents.collector import get_collector
        
        stats = {
            "items_added": 0,
            "flagged": 0,
            "pending_approval": 0,
            "top_item": None,
            "collection_runs": 0,
            "collection_items_found": 0,
            "person_changes": [],
        }
        
        try:
            collector = get_collector(notebook_id)
            
            # Get pending approvals count
            pending = collector.get_pending_approvals()
            stats["pending_approval"] = len(pending)
            
            # Get top finding from pending (highest confidence)
            if pending:
                top = max(pending, key=lambda x: x.get("confidence", 0))
                stats["top_item"] = top.get("title", "")[:100]
                stats["flagged"] = len([p for p in pending if p.get("confidence", 0) >= 0.8])
            
        except Exception as e:
            logger.error(f"Error getting activity for {notebook_id}: {e}")
        
        # Collection history — how many runs happened while user was away
        try:
            from services.collection_history import get_collection_history
            history = get_collection_history(notebook_id, limit=10)
            runs_since = [h for h in history if h.get("timestamp", "") > since.isoformat()]
            stats["collection_runs"] = len(runs_since)
            stats["collection_items_found"] = sum(h.get("items_found", 0) for h in runs_since)
            stats["collection_items_approved"] = sum(h.get("items_approved", 0) for h in runs_since)
            stats["collection_items_rejected"] = sum(h.get("items_rejected", 0) for h in runs_since)
            stats["collection_items_pending"] = sum(h.get("items_pending", 0) for h in runs_since)
            if runs_since and not stats["top_item"]:
                approved = stats["collection_items_approved"]
                stats["top_item"] = f"Collector ran {len(runs_since)} time{'s' if len(runs_since) != 1 else ''}, approved {approved} of {stats['collection_items_found']} items examined"
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Phase 4: Collection quality metrics + recent syntheses for enriched brief
        try:
            from services.collection_history import get_collection_quality_metrics, get_recent_syntheses
            quality = get_collection_quality_metrics(notebook_id)
            stats["collection_health_score"] = quality.get("health_score", 0)
            stats["collection_health_status"] = quality.get("status", "no_data")
            stats["collection_approval_trend"] = quality.get("approval_trend", "stable")
            stats["collection_recommended_actions"] = quality.get("recommended_actions", [])
            
            syntheses = get_recent_syntheses(notebook_id, limit=2)
            if syntheses:
                # Extract approved titles from recent syntheses for the brief
                recent_titles = []
                for s in syntheses:
                    recent_titles.extend(s.get("approved_titles", []))
                stats["recent_approved_titles"] = recent_titles[:5]
                # Extract gap reasons if any runs had zero approvals
                gaps = [s.get("gap_reasons", {}) for s in syntheses if s.get("gap_reasons")]
                if gaps:
                    stats["collection_gap_reasons"] = gaps[0]
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # User interactions — track activity types separately (never conflate with items_added)
        try:
            from services.event_logger import event_logger, EventType
            events = event_logger.get_events_since(since, notebook_id=notebook_id)
            stats["interactions_since"] = len(events)
            stats["chat_queries"] = len([e for e in events if e.event_type == EventType.CHAT_QA.value])
            stats["searches"] = len([e for e in events if e.event_type == EventType.SEARCH_PERFORMED.value])
            stats["docs_read"] = len([e for e in events if e.event_type == EventType.DOCUMENT_READ.value])
            stats["docs_captured"] = len([e for e in events if e.event_type == EventType.DOCUMENT_CAPTURED.value])
            
            # Studio content generation — what the user actively created
            content_events = [e for e in events if e.event_type == EventType.CONTENT_GENERATED.value]
            quiz_events = [e for e in events if e.event_type == EventType.QUIZ_COMPLETED.value]
            studio_topics = set()
            for ce in content_events:
                ctype = ce.data.get("content_type", "")
                topic = ce.data.get("topic", "")
                if ctype == "audio":
                    stats["audio_generated"] = stats.get("audio_generated", 0) + 1
                elif ctype == "visual":
                    stats["visuals_generated"] = stats.get("visuals_generated", 0) + 1
                elif ctype == "video":
                    stats["videos_generated"] = stats.get("videos_generated", 0) + 1
                else:
                    stats["docs_generated"] = stats.get("docs_generated", 0) + 1
                if topic:
                    studio_topics.add(topic[:80])
            stats["quizzes_generated"] = len(quiz_events)
            for qe in quiz_events:
                topic = qe.data.get("topic", "")
                if topic:
                    studio_topics.add(topic[:80])
            stats["studio_topics"] = list(studio_topics)[:5]
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Person changes — surface profile changes for people notebooks
        try:
            from api.people import _load_config
            config = _load_config(notebook_id)
            if config.coaching_enabled and config.members:
                for member in config.members:
                    for change in getattr(member, "recent_changes", []) or []:
                        detected = change.get("detected_at", "")
                        if detected > since.isoformat():
                            stats["person_changes"].append(
                                f"{member.name}: {change.get('description', '')}"
                            )
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Key dates — surface upcoming events within 7 days
        try:
            from agents.collector import get_collector
            collector = get_collector(notebook_id)
            config = collector.get_config()
            subject = config.subject if hasattr(config, "subject") and config.subject else None
            if subject:
                from services.key_dates import get_key_dates
                from datetime import timedelta
                key_dates = await get_key_dates(company_name=subject)
                now_str = datetime.utcnow().strftime("%Y-%m-%d")
                soon_str = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d")
                upcoming = [kd for kd in key_dates if now_str <= kd.get("date", "") <= soon_str]
                if upcoming:
                    stats["upcoming_key_dates"] = [
                        f"{kd['date']}: {kd['event']}" for kd in upcoming[:3]
                    ]
                    if not stats["top_item"]:
                        stats["top_item"] = f"Upcoming: {upcoming[0]['event']} on {upcoming[0]['date']}"
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Use preloaded sources if available, otherwise load (still only once per call)
        all_sources = preloaded_sources if preloaded_sources is not None else []
        if preloaded_sources is None:
            try:
                from storage.source_store import source_store
                all_sources = await source_store.list(notebook_id)
            except Exception as _e:
                logger.warning(f"[curator] {type(_e).__name__}: {_e}")
        
        # Recent sources — pull actual titles of recently added content
        try:
            since_str = since.isoformat()
            recent = [
                s for s in all_sources
                if s.get("created_at", "") > since_str
            ]
            # Sort newest first, take top 5
            recent.sort(key=lambda s: s.get("created_at", ""), reverse=True)
            
            # Determine origin for each source (collector vs user)
            # metadata_json is merged into the source dict by source_store,
            # so collected_by is a top-level key
            def _is_collector_source(src):
                return src.get("collected_by") == "collector"
            
            stats["recent_stories"] = [
                {
                    "title": s.get("title") or s.get("filename", "Untitled"),
                    "source_name": s.get("source_type", s.get("format", "")),
                    "url": s.get("url"),
                    "summary": (s.get("summary") or s.get("description") or "")[:200],
                    "origin": "collector" if _is_collector_source(s) else "user",
                }
                for s in recent[:5]
            ]
            # items_added = actual number of sources created since user was last seen
            # This is the ONLY place items_added is set — no conflation with event logger
            stats["items_added"] = len(recent)
            
            # Split by origin
            stats["collector_added"] = len([s for s in recent if _is_collector_source(s)])
            stats["user_added"] = len(recent) - stats["collector_added"]
            
            # Notes — track separately for "what you've been thinking about"
            recent_notes = [s for s in recent if s.get("type") == "note"]
            stats["notes_created"] = len(recent_notes)
            stats["note_titles"] = [s.get("filename", "Untitled Note") for s in recent_notes[:5]]
            
            # Also count total notes in notebook for context
            all_notes = [s for s in all_sources if s.get("type") == "note"]
            stats["total_notes"] = len(all_notes)
            
            # Research velocity — compare this week vs last week (deltas, not totals)
            from datetime import timedelta
            now = datetime.utcnow()
            week_ago = (now - timedelta(days=7)).isoformat()
            two_weeks_ago = (now - timedelta(days=14)).isoformat()
            added_this_week = len([s for s in all_sources if s.get("created_at", "") > week_ago])
            added_last_week = len([s for s in all_sources if week_ago >= s.get("created_at", "") > two_weeks_ago])
            stats["total_sources"] = len(all_sources)
            stats["sources_this_week"] = added_this_week
            stats["sources_last_week"] = added_last_week
            
            # Reading progress — sources the user has actively engaged with
            # (tagged by user = reviewed; no tags = unreviewed)
            tagged = len([s for s in all_sources if s.get("tags") and len(s.get("tags", [])) > 0])
            stats["sources_summarized"] = tagged
            stats["sources_unread"] = len(all_sources) - tagged
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Recent highlights — what the user explicitly marked as important
        try:
            highlight_count = 0
            recent_highlights = []
            for src in all_sources:
                highlights = src.get("highlights", [])
                for h in highlights:
                    if h.get("created_at", "") > since.isoformat():
                        highlight_count += 1
                        if len(recent_highlights) < 3:
                            recent_highlights.append(h.get("text", "")[:100])
            if highlight_count > 0:
                stats["highlights_since"] = highlight_count
                stats["recent_highlight_texts"] = recent_highlights
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Unfinished threads — conversations where user asked a question
        # but didn't follow up (using existing recall memory SQLite)
        try:
            from storage.memory_store import memory_store
            recent_convos = memory_store.get_recent_conversations(
                limit=50, notebook_id=notebook_id, days=3
            )
            if recent_convos:
                # Group by conversation_id
                convos: Dict[str, list] = {}
                for entry in recent_convos:
                    cid = entry.conversation_id
                    if cid not in convos:
                        convos[cid] = []
                    convos[cid].append(entry)
                
                unfinished = []
                for cid, entries in convos.items():
                    # Sort by timestamp (entries come DESC, reverse for chronological)
                    entries.sort(key=lambda e: e.timestamp)
                    # Check if the user's last message was a question or conversation was short
                    user_msgs = [e for e in entries if e.role == "user"]
                    if user_msgs:
                        last_user_msg = user_msgs[-1].content.strip()
                        is_question = last_user_msg.endswith("?")
                        is_short = len(entries) <= 3  # Single exchange = likely abandoned
                        if is_question or is_short:
                            # Truncate to a readable thread hint
                            hint = last_user_msg[:120]
                            if len(last_user_msg) > 120:
                                hint += "..."
                            unfinished.append(hint)
                
                stats["unfinished_threads"] = unfinished[:3]
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Topic drift — compare recent source topics vs older ones
        try:
            from datetime import timedelta
            now = datetime.utcnow()
            week_ago = (now - timedelta(days=7)).isoformat()
            month_ago = (now - timedelta(days=30)).isoformat()
            
            recent_titles = [
                s.get("title", "").lower()
                for s in all_sources
                if s.get("created_at", "") > week_ago and s.get("title")
            ]
            older_titles = [
                s.get("title", "").lower()
                for s in all_sources
                if month_ago < s.get("created_at", "") <= week_ago and s.get("title")
            ]
            
            if recent_titles and older_titles:
                # Extract simple word-level topics (2+ word phrases would need NLP,
                # but single significant words are a good heuristic)
                import re
                stop_words = {"the","a","an","and","or","but","in","on","at","to","for",
                              "of","with","by","from","is","it","this","that","was","are",
                              "be","has","had","have","will","can","may","not","no","new",
                              "how","what","why","when","who","which","about","after","into"}
                
                def extract_words(titles):
                    words = {}
                    for title in titles:
                        for word in re.findall(r'[a-z]{3,}', title):
                            if word not in stop_words:
                                words[word] = words.get(word, 0) + 1
                    return words
                
                recent_words = extract_words(recent_titles)
                older_words = extract_words(older_titles)
                
                # Find words appearing in recent but not (or rarely) in older
                emerging = []
                for word, count in sorted(recent_words.items(), key=lambda x: -x[1]):
                    if count >= 2 and older_words.get(word, 0) == 0:
                        emerging.append(word)
                    if len(emerging) >= 3:
                        break
                
                stats["emerging_topics"] = emerging
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Temporal lookback — "this day in your research" (7 days ago)
        try:
            from datetime import timedelta
            now = datetime.utcnow()
            # Sources from exactly 6-8 days ago (window around 1 week)
            lookback_start = (now - timedelta(days=8)).isoformat()
            lookback_end = (now - timedelta(days=6)).isoformat()
            
            week_ago_sources = [
                s for s in all_sources
                if lookback_start < s.get("created_at", "") <= lookback_end and s.get("title")
            ]
            if week_ago_sources:
                stats["one_week_ago_items"] = [
                    s.get("title", "")[:100] for s in week_ago_sources[:3]
                ]
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        return stats

    async def _synthesize_brief_narrative(
        self,
        summaries: List['NotebookSummary'],
        duration_str: str,
        cross_insight: Optional[str],
        temporal_block: str = "",
        understanding_diff: str = "",
    ) -> str:
        """
        Use LLM to turn raw notebook activity data into a newsletter-quality
        narrative the user looks forward to reading each morning.

        understanding_diff (Curator Phase 5): optional pre-formatted block
        describing changes in the curator's understanding since the last
        brief. When non-empty, prepended to the LLM prompt so the
        synthesizer naturally weaves a "What's new in your thinking"
        section into the brief.
        """
        if not summaries:
            return ""

        # --- Phase 1C: Quiet Morning Gate ---
        # If nothing substantive happened, skip the LLM and return one sentence.
        # A notebook qualifies as "substantive" when it has new content, user
        # activity, pending items, notes, highlights, or emerging topics.
        # (Collector ran but found nothing does NOT qualify.)
        has_meaningful_activity = any(
            nb.items_added > 0
            or nb.pending_approval > 0
            or nb.collection_items_approved > 0
            or nb.highlights_since > 0
            or nb.notes_created > 0
            or nb.interactions_since > 0
            or nb.emerging_topics
            or nb.recent_stories
            for nb in summaries
        )
        if not has_meaningful_activity:
            # 2026-05-23 (Fix #1): no greeting prefix here. The CuratorPanel
            # frontend already prepends "Good {greeting}! You've been away
            # for {duration}.\n\n{narrative}" — including another "Good X."
            # produced "Good morning! You've been away... Good morning. Quiet
            # since..." (double-greeting). Narrative starts mid-thought.
            return (
                "Quiet since you were last here — nothing I'd flag as worth "
                "your time. Your notebooks are where you left them."
            )

        # --- Phase 2: Inject Curator Brain context (pre-computed understanding) ---
        # If digests exist, the LLM narrates from knowledge, not just activity stats.
        # If brain is empty (first run), brain_context is '' and we fall through to
        # today's stat-only behavior automatically.
        brain_context = ""
        try:
            from services.curator_brain import curator_brain
            brain_context = curator_brain.get_brief_context()
        except Exception as _brain_err:
            logger.debug(f"[curator] Brain context unavailable (non-fatal): {_brain_err}")

        # Pull recent memory context per notebook for richer narrative (ReMe integration)
        # Structured checkpoints from archival memory give the Curator awareness of
        # what the user has been discussing, deciding, and working on
        import asyncio
        memory_context_by_nb = {}
        try:
            from storage.memory_store import memory_store
            from models.memory import AgentNamespace
            
            async def _fetch_memory(nb_id, nb_name):
                results = await asyncio.to_thread(
                    memory_store.search_archival_memory,
                    query=f"recent work progress decisions {nb_name}",
                    namespace=AgentNamespace.CURATOR,
                    notebook_id=nb_id,
                    cross_notebook=True,
                    limit=3
                )
                return nb_id, results
            
            mem_tasks = [_fetch_memory(nb.notebook_id, nb.name) for nb in summaries]
            mem_results = await asyncio.gather(*mem_tasks, return_exceptions=True)
            for item in mem_results:
                if isinstance(item, Exception):
                    continue
                nb_id, results = item
                if results:
                    snippets = [r.entry.content[:250] for r in results if r.combined_score > 0.2]
                    if snippets:
                        memory_context_by_nb[nb_id] = snippets
        except Exception as e:
            logger.debug(f"Memory context for brief failed (non-fatal): {e}")
        
        # Build structured data for the LLM
        notebook_sections = []
        for nb in summaries:
            section = f"Notebook: {nb.name}"
            if nb.subject:
                section += f" (tracking: {nb.subject})"
            details = []
            
            # New content — specific titles
            if nb.recent_stories:
                for story in nb.recent_stories[:3]:
                    detail = f"  - New: \"{story.title}\""
                    if story.source_name:
                        detail += f" ({story.source_name})"
                    if story.summary:
                        detail += f" — {story.summary[:150]}"
                    details.append(detail)
            
            # Source origin breakdown — distinguish collector from user
            if nb.collector_added > 0 or nb.user_added > 0:
                origin_parts = []
                if nb.collector_added > 0:
                    origin_parts.append(f"{nb.collector_added} auto-gathered by your background collector (REVIEW RECOMMENDED — you didn't add these manually)")
                if nb.user_added > 0:
                    origin_parts.append(f"{nb.user_added} added by you")
                details.append(f"  - Source breakdown: {'; '.join(origin_parts)}")
            
            # Collection activity — IMPORTANT: "found" means examined, NOT stored
            if nb.collection_runs > 0:
                details.append(f"  - Background collector ran {nb.collection_runs}x overnight: examined {nb.collection_items_found} potential items, stored {nb.collection_items_approved} into notebook, rejected {nb.collection_items_rejected} (low quality/duplicate)")
                if nb.collection_items_found > 0 and nb.collection_items_approved == 0:
                    details.append(f"    NOTE: The collector found items but NONE passed quality filters — zero new sources were actually added by the collector")
            if nb.pending_approval > 0:
                details.append(f"  - {nb.pending_approval} collector items awaiting your review in the approval queue")
            
            # People updates
            if nb.person_changes:
                for pc in nb.person_changes[:3]:
                    details.append(f"  - People: {pc}")
            
            # Upcoming events
            if nb.upcoming_key_dates:
                for kd in nb.upcoming_key_dates[:2]:
                    details.append(f"  - Coming up: {kd}")
            
            # Research velocity — pre-compute ALL percentages so the LLM
            # never has to do arithmetic (LLMs are bad at math)
            if nb.total_sources > 0:
                if nb.sources_this_week > 0:
                    prior_total = nb.total_sources - nb.sources_this_week
                    lib_growth_pct = int((nb.sources_this_week / prior_total) * 100) if prior_total > 0 else 0
                    velocity_note = (f"  - Research library: {prior_total} → {nb.total_sources} sources "
                                     f"(+{nb.sources_this_week} new this week, {lib_growth_pct}% library growth)")
                    if nb.sources_last_week > 0:
                        if nb.sources_this_week > nb.sources_last_week:
                            pace_pct = int(((nb.sources_this_week - nb.sources_last_week) / nb.sources_last_week) * 100)
                            velocity_note += f". Pace: {nb.sources_this_week} added vs {nb.sources_last_week} last week (+{pace_pct}% faster)"
                        elif nb.sources_this_week < nb.sources_last_week:
                            velocity_note += f". Pace: {nb.sources_this_week} added vs {nb.sources_last_week} last week (slower)"
                        else:
                            velocity_note += f". Pace: same as last week ({nb.sources_last_week})"
                    details.append(velocity_note)
                else:
                    details.append(f"  - Research library: {nb.total_sources} sources (no new additions this week)")
            
            # Review progress — how many sources user has tagged/reviewed
            if nb.sources_unread > 0 and nb.sources_summarized > 0:
                details.append(f"  - Review progress: {nb.sources_summarized} of {nb.total_sources} sources tagged/reviewed, {nb.sources_unread} still unreviewed")
            
            # User activity — how the user has been engaging with this notebook
            if nb.interactions_since > 0:
                activity_parts = []
                if nb.chat_queries > 0:
                    activity_parts.append(f"{nb.chat_queries} chat conversation{'s' if nb.chat_queries != 1 else ''}")
                if nb.searches > 0:
                    activity_parts.append(f"{nb.searches} search{'es' if nb.searches != 1 else ''}")
                if nb.docs_read > 0:
                    activity_parts.append(f"{nb.docs_read} document{'s' if nb.docs_read != 1 else ''} read")
                if activity_parts:
                    details.append(f"  - Your activity: {', '.join(activity_parts)}")
            
            # Studio content creation — what the user actively produced
            studio_total = nb.docs_generated + nb.audio_generated + nb.visuals_generated + nb.quizzes_generated + nb.videos_generated
            if studio_total > 0:
                studio_parts = []
                if nb.docs_generated > 0:
                    studio_parts.append(f"{nb.docs_generated} document{'s' if nb.docs_generated != 1 else ''}")
                if nb.audio_generated > 0:
                    studio_parts.append(f"{nb.audio_generated} podcast{'s' if nb.audio_generated != 1 else ''}")
                if nb.visuals_generated > 0:
                    studio_parts.append(f"{nb.visuals_generated} visual{'s' if nb.visuals_generated != 1 else ''}")
                if nb.quizzes_generated > 0:
                    studio_parts.append(f"{nb.quizzes_generated} quiz{'zes' if nb.quizzes_generated != 1 else ''}")
                if nb.videos_generated > 0:
                    studio_parts.append(f"{nb.videos_generated} video{'s' if nb.videos_generated != 1 else ''}")
                details.append(f"  - Studio output: created {', '.join(studio_parts)}")
                if nb.studio_topics:
                    details.append(f"    Topics: {', '.join(nb.studio_topics)}")
            
            # Highlights — strongest signal of what the user cares about
            if nb.highlights_since > 0:
                details.append(f"  - You highlighted {nb.highlights_since} passages recently")
                for ht in nb.recent_highlight_texts[:2]:
                    details.append(f"    > \"{ht}\"")
            
            # Notes — the user's own thinking and ideas
            if nb.notes_created > 0:
                note_label = f"  - You wrote {nb.notes_created} note{'s' if nb.notes_created != 1 else ''}"
                if nb.note_titles:
                    note_label += f": {', '.join(nb.note_titles[:3])}"
                details.append(note_label)
            if nb.total_notes > 0 and nb.notes_created == 0:
                details.append(f"  - You have {nb.total_notes} note{'s' if nb.total_notes != 1 else ''} in this notebook")
            
            # Unfinished threads — conversations the user might want to continue
            if nb.unfinished_threads:
                details.append(f"  - Unfinished conversations ({len(nb.unfinished_threads)}):")
                for thread in nb.unfinished_threads[:2]:
                    details.append(f"    ? \"{thread}\"")
            
            # Emerging topics — topic drift detection
            if nb.emerging_topics:
                details.append(f"  - Emerging topics (new this week): {', '.join(nb.emerging_topics)}")
            
            # Temporal lookback — "one week ago"
            if nb.one_week_ago_items:
                details.append(f"  - One week ago you were reading:")
                for item in nb.one_week_ago_items[:2]:
                    details.append(f"    ← \"{item}\"")
            
            if nb.top_finding and not nb.recent_stories:
                details.append(f"  - Top finding: {nb.top_finding}")
            
            # Memory context — what the user was discussing/deciding in this notebook
            nb_memories = memory_context_by_nb.get(nb.notebook_id, [])
            if nb_memories:
                details.append(f"  - Recent research context from memory:")
                for mem in nb_memories[:2]:
                    details.append(f"    📝 {mem}")
            
            if details:
                section += "\n" + "\n".join(details)
            notebook_sections.append(section)
        
        raw_data = "\n\n".join(notebook_sections)
        if cross_insight:
            raw_data += f"\n\nCross-notebook insight: {cross_insight}"

        # --- Phase 1A: Temporal block prepended to prompt ---
        # If a temporal_block was provided (from generate_morning_brief), use it.
        # Fallback: build one now so this function remains independently callable.
        from zoneinfo import ZoneInfo
        if not temporal_block:
            from services.temporal import TemporalContext
            temporal_block = TemporalContext(self._get_user_timezone()).for_prompt(
                datetime.utcnow()  # best-effort fallback
            )

        today_str = datetime.now(tz=ZoneInfo(self._get_user_timezone())).strftime("%B %d, %Y")

        # Build the brain context block for the prompt
        brain_section = ""
        if brain_context:
            brain_section = (
                f"\nYOUR UNDERSTANDING OF THE USER'S RESEARCH "
                f"(from your ongoing analysis — use this to narrate from knowledge, not just stats):\n"
                f"{brain_context}\n"
            )

        # Curator Phase 5: prepend "what changed in understanding"
        # block when present. The LLM is instructed to integrate this
        # naturally — typically as a short "What's new in your thinking"
        # section near the top of the brief.
        understanding_section = ""
        if understanding_diff and understanding_diff.strip():
            understanding_section = (
                "\nUNDERSTANDING CHANGES SINCE LAST BRIEF "
                "(include as a 'What's new in your thinking' section "
                "near the top of the brief. Write a short paragraph — 2-4 "
                "sentences — that actually narrates the shifts: name the "
                "specific theses, stages, or contradicting sources that "
                "changed, not just generic phrases. Be substantive; this "
                "is the most novel signal in the brief.):\n"
                f"{understanding_diff}\n"
            )

        # Curator Phase 6a (2026-05-13): voice + observations.
        # Voice block is the FIRST thing the LLM sees so it sets the tone
        # for everything below. Observations payload follows, instructing
        # the LLM to lead the brief with what was actually noticed —
        # NOT with "Good morning, you've been away for Xh".
        voice_block = VOICE_PROMPTS.get(self.narrative_voice, VOICE_PROMPTS[DEFAULT_VOICE])

        # Build observations summary across notebooks. Includes only
        # signal-bearing fields — the rest is omitted to keep the prompt
        # tight.
        observations_section = ""
        try:
            from services.curator_brain import curator_brain as _cb
            since_iso = last_seen.isoformat() if last_seen else None
            obs_lines: List[str] = []
            if since_iso:
                for nb in notebooks[:10]:
                    obs = _cb.compute_brief_observations(nb["id"], since_iso)
                    has_signal = any([
                        obs.get("blocked_on"),
                        obs.get("recent_focus"),
                        obs.get("dissent_count", 0) > 0,
                        obs.get("is_quiet"),
                        obs.get("recent_completed_plans"),
                        obs.get("new_connections"),
                        obs.get("fresh_reclassifications", 0) > 0,
                        obs.get("has_pending_draft"),
                    ])
                    if not has_signal:
                        continue
                    name = nb.get("title", nb.get("name", "Untitled"))
                    # Curator Phase 4: tag the notebook block with mental-model
                    # confidence so the LLM's hedge-rule knows how strongly
                    # to phrase the observations.
                    mm = _cb.get_mental_model(nb["id"])
                    mm_conf = (mm.get("confidence") if mm else None) or 0
                    conf_tag = f" [confidence={mm_conf:.2f}]" if mm_conf else ""
                    nb_lines = [f"  {name}{conf_tag}:"]
                    if obs.get("blocked_on"):
                        nb_lines.append(f"    - BLOCKED ON: {obs['blocked_on'][:140]}")
                    if obs.get("recent_focus"):
                        nb_lines.append(f"    - recent focus: {obs['recent_focus'][:120]}")
                    if obs.get("stage"):
                        nb_lines.append(f"    - stage: {obs['stage']}")
                    if obs.get("dissent_count", 0) > 0:
                        line = f"    - dissent: {obs['dissent_count']} contradicting source(s)"
                        if obs.get("fresh_dissent_rationale"):
                            line += f" — \"{obs['fresh_dissent_rationale'][:120]}\""
                        nb_lines.append(line)
                    if obs.get("is_quiet"):
                        nb_lines.append("    - QUIET: no engagement here in 7+ days")
                    if obs.get("recent_completed_plans"):
                        for p in obs["recent_completed_plans"][:2]:
                            nb_lines.append(f"    - recently completed: {p[:120]}")
                    if obs.get("new_connections"):
                        for c in obs["new_connections"][:2]:
                            nb_lines.append(f"    - new cross-notebook link: {c[:120]}")
                    if obs.get("fresh_reclassifications", 0) > 0:
                        nb_lines.append(
                            f"    - {obs['fresh_reclassifications']} source(s) freshly reclassified"
                        )
                    if obs.get("has_pending_draft"):
                        kind = obs.get("pending_draft_kind") or "document"
                        nb_lines.append(
                            f"    - PENDING DRAFT: a {kind} is ready (mention it; user can run `@curator show draft`)"
                        )
                    if len(nb_lines) > 1:
                        obs_lines.extend(nb_lines)
            if obs_lines:
                observations_section = (
                    "\nOBSERVATIONS (Lead the brief with these — they are the most "
                    "interesting things noticed. Pick the strongest 1-2 to open with. "
                    "Activity stats below are supporting evidence, not the headline.):\n"
                    + "\n".join(obs_lines) + "\n"
                )
        except Exception as _e:
            logger.debug(f"[curator] observations payload failed (non-fatal): {_e}")

        # Fix #1 (2026-05-23): engagement-weighted brief boost — make the
        # LLM AWARE of click patterns so it can actively reference them in
        # the brief ("I noticed you keep coming back to X" / "I'll surface
        # less about Y for now"). The ranker already demotes ignored topics;
        # this is the prose layer the user actually reads.
        engagement_section = ""
        try:
            from services.curator_brain import curator_brain as _cb
            eng_lines: List[str] = []
            for nb in notebooks[:10]:
                summary = _cb.get_topic_engagement_summary(nb["id"])
                if not summary["liked"] and not summary["ignored"]:
                    continue
                name = nb.get("title", nb.get("name", "Untitled"))
                nb_lines = [f"  {name}:"]
                if summary["liked"]:
                    liked_str = ", ".join(
                        f"{t['topic']} ({t['clicked']}×)" for t in summary["liked"][:3]
                    )
                    nb_lines.append(f"    - user has engaged with: {liked_str}")
                if summary["ignored"]:
                    ignored_str = ", ".join(
                        f"{t['topic']} ({t['offered']}× offered)" for t in summary["ignored"][:3]
                    )
                    nb_lines.append(f"    - user has been ignoring: {ignored_str}")
                if len(nb_lines) > 1:
                    eng_lines.extend(nb_lines)
            if eng_lines:
                engagement_section = (
                    "\nUSER ENGAGEMENT PATTERNS (Phase 5 calibration — use these "
                    "to shape what you emphasize. Briefly acknowledge topics the "
                    "user keeps engaging with ('you've been digging into X') and "
                    "do NOT dwell on topics they keep ignoring. Don't list these "
                    "verbatim — internalize them and let them shape your tone "
                    "and selection.):\n"
                    + "\n".join(eng_lines) + "\n"
                )
        except Exception as _e:
            logger.debug(f"[curator] engagement payload failed (non-fatal): {_e}")

        prompt = f"""{voice_block}

{temporal_block}

You are {self.name}, a personal research assistant writing a morning brief for today, {today_str}. The user was away for {duration_str}. Turn the raw activity data below into something worth reading. IMPORTANT: Today's date is {today_str} — use this exact date, do not invent a different date.

WRITE LIKE THE VOICE BLOCK INSTRUCTS — that voice is more important than any pattern below. If the voice says "first-person", use first-person. If the voice says "minimal first-person", don't say "I". The voice block wins.
{brain_section}{observations_section}{understanding_section}{engagement_section}
ACTIVITY DATA:
{raw_data}

CONFIDENCE-AWARE LANGUAGE (Curator Phase 4 — applies to ANY claim from the observations payload that includes a confidence value):
- confidence > 0.85 → DEFINITIVE: "X is the case", "clearly", "established", "confirmed"
- confidence 0.7 – 0.85 → MODERATE: "appears to be", "seems", "looks like", "is shaping up to"
- confidence 0.5 – 0.7 → HEDGED: "I think", "this looks like", "tentatively", "I'm reading this as"
- confidence < 0.5 → SPECULATIVE: "possibly", "not sure but", "worth checking", "wouldn't bet on this yet"
- Apply this to mental-model fields, dissent rationales, insights, and any other observation that carries a confidence score. Do NOT apply to raw numerical facts (source counts, dates) — those are not hedged.

CRITICAL ACCURACY RULES — you MUST follow these:
- ALL percentages are PRE-COMPUTED in the data. Use them VERBATIM. Do NOT calculate your own percentages.
- When data says "X → Y sources (+N new this week, P% library growth)", report: "library grew from X to Y (+N new, P% growth)". The P% is already computed correctly — just copy it.
- When data says "Pace: N added vs M last week (+Q% faster)", report: "pace is up Q% (N this week vs M last)". The Q% is already computed — just copy it.
- NEVER do arithmetic yourself. NEVER compute percentages. NEVER say a number that doesn't appear in the raw data.
- "sources_this_week" is the number of NEW sources added THIS WEEK, not the total count. If data says "+11 new", say "11 new sources" not "90 sources added."
- If something says "no new additions this week," do NOT claim growth occurred.
- "tagged/reviewed" means the user has actively organized those sources with tags. "unreviewed" means no tags yet — not that the sources are unread.

TEMPORAL FRAMING — match the user's actual rhythm:
- The user was away for {duration_str}. Use that to frame your language:
  * If away < 24 hours: say "overnight", "since yesterday", "while you slept" — NOT "this week"
  * If away 1-2 days: say "over the past day" or "since you were last here"
  * If away 3+ days: then "this week" or "over the past few days" is appropriate
- NEVER default to "this week / last week" framing when the user is active daily. It feels disconnected.
- The user works in these notebooks every day. Acknowledge that continuity — "your research is building momentum" not "here's what happened this week."

COLLECTOR vs USER SOURCES — this distinction is CRITICAL:
- "examined N potential items" means the collector LOOKED AT N items from RSS feeds and web pages. This is NOT the same as adding them.
- "stored M into notebook" means M items actually passed quality filters and were ADDED as sources. Only these count as new collector sources.
- If stored = 0, the collector ran but found NOTHING worth adding. Do NOT say it "gathered" or "collected" or "found new" sources. Say it "ran but didn't find anything that passed quality filters" or simply omit the collector section.
- NEVER claim the collector added sources unless "collector_added" or "stored" is > 0 in the data.
- If the data shows "auto-gathered by background collector (REVIEW RECOMMENDED)", ONLY THEN call this out prominently
- Collector-gathered sources arrived WITHOUT the user's involvement — the user needs to know these exist and should examine them
- User-added sources need no special callout — the user already knows about those
- If there are collector sources pending review in the approval queue, make this the most actionable item in the brief
- If there are NO collector sources and NO pending approvals, do NOT write a "Collector Discoveries" section at all

STUDIO CONTENT CREATION — acknowledge what the user is BUILDING, not just reading:
- If "Studio output" data is present, the user actively generated materials (documents, podcasts, visuals, quizzes, videos)
- This is a strong engagement signal — acknowledge it warmly. Use the ACTUAL topic from the data, never write literal brackets like "[topic]" — fill them in or rephrase. Patterns to draw from:
  * "You've been actively creating — 2 podcasts and a visual show you're moving from research to synthesis" (substitute the actual subject matter when known)
  * "The quiz you generated is a great way to solidify your understanding" (mention the real subject if visible in data)
  * "You created 3 documents since your last session — your research is producing tangible output"
- If studio topics overlap with unfinished threads or emerging topics, connect the dots: "You're generating content on the same topics you were exploring in chat — your research is maturing"
- Keep it brief — 1-2 sentences integrated naturally into the per-notebook section, not a separate block
- NEVER emit a literal bracket placeholder ("[topic]", "[note titles]", "[recent topic]") — if you don't know the specific subject, omit the phrase entirely or use a generic word

USER NOTES — the user's own thinking, captured in their own words:
- Notes are first-class content — they represent what the user is ACTIVELY THINKING ABOUT
- If a user wrote notes recently, lead with that or weave it in prominently: name the actual note titles from the data — never emit literal brackets like "[note titles]"
- If a notebook has notes but none were created recently, you can reference them as context: "Your N notes in this notebook form a foundation for..." (use the actual count)
- Connect notes to other activity when possible — name the actual topics from the data, not bracketed placeholders
- Notes signal what the user cares about MORE than sources — sources are inputs, notes are the user's own synthesis

NEWSLETTER FORMATTING (CRITICAL):
- Use markdown extensively for a modern newsletter layout.
- Use `###` headers for each notebook or major section to break up text visually.
- Use **bold** liberally for source titles, key metrics, and important entities.
- Use bullet points (`-`) for lists of items (like newly discovered sources or threads).
- Keep paragraphs very short (1-2 sentences). Absolutely NO dense walls of text. Be highly scannable.
- Insert blank lines between sections to give the text room to breathe.

TONE:
- Warm, professional, like a trusted advisor who knows your research intimately
- Confident and specific — never vague or generic
- Brief — aim for 300-600 words total. ALWAYS finish your last sentence completely.
- The collector callouts, studio output, unfinished threads, emerging interests, and lookback sections are what make this feel MAGICAL — these show the user the system is paying attention. Prioritize them when present.

Write the brief now:"""

        try:
            from services.rag_engine import rag_engine
            from config import settings

            # Routed through rag_engine for the same reasons as the
            # weekly wrap above — respects rag_profile (use_chat_endpoint
            # for Gemma4, think:false to suppress channel tokens), and
            # the higher num_predict prevents truncation that manifests
            # as raw markdown chars in the UI under memory pressure.
            narrative = await rag_engine._call_ollama(
                system_prompt=(
                    f"You are {self.name}, the user's research companion. "
                    f"Personality: {self.personality}. "
                    f"You have been quietly paying attention to their research and have "
                    f"observations to share — not news to report. Use first person. "
                    f"Quote note titles when relevant. If nothing meaningful happened, "
                    f"say so briefly and stop. Never manufacture urgency."
                ),
                prompt=prompt,
                model=settings.ollama_model,
                # 2026-06-08: dropped 0.7 → 0.55 for gemma4 (better
                # instruction-following than olmo; CLAUDE.md doc-gen range).
                temperature=0.55,
                num_predict=1500,
                voice_modifier=False,
            )
            narrative = (narrative or "").strip()
            # Guard against error strings from ollama_client being treated as valid narrative
            if narrative and not narrative.startswith(("Request timed out", "Error:")):
                return narrative
            elif narrative:
                logger.warning(f"Brief LLM returned error: {narrative[:100]}")
                # Fall through to structured fallback
        except Exception as e:
            logger.error(f"Brief narrative generation failed: {e}")
        
        # Fallback: structured but not LLM-generated
        lines = []
        for nb in summaries:
            line = f"**{nb.name}**"
            if nb.subject:
                line += f" ({nb.subject})"
            parts = []
            if nb.recent_stories:
                titles = [f'"{s.title}"' for s in nb.recent_stories[:3]]
                parts.append(f"New: {', '.join(titles)}")
            elif nb.items_added > 0:
                parts.append(f"{nb.items_added} new items")
            if nb.pending_approval > 0:
                parts.append(f"{nb.pending_approval} pending review")
            if nb.person_changes:
                parts.extend(nb.person_changes[:2])
            if nb.upcoming_key_dates:
                parts.extend(nb.upcoming_key_dates[:2])
            if parts:
                line += ": " + " · ".join(parts)
            lines.append(line)
        return "\n".join(lines)
