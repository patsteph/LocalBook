"""CuratorOverwatchMixin — extracted from the former agents/curator.py (Wave 3 split)."""
from ._models import *  # noqa: F401,F403


class CuratorOverwatchMixin:
    _AUTO_DEEP_READ = True
    _MAX_DEEP_READS_PER_BRIEF = 2

    async def discover_cross_notebook_patterns(self) -> List[ProactiveInsight]:
        """
        Run during consolidation cycle. Finds:
        1. Overlapping entities across notebooks
        2. Contradicting information
        3. Temporal patterns (X happened after Y)
        4. Coverage gaps
        """
        insights = []
        notebooks = await notebook_store.list()
        
        if len(notebooks) < 2:
            return insights  # Need at least 2 notebooks
        
        # Find shared entities across notebooks
        shared_entities = await self._find_shared_entities(notebooks)
        
        for entity, notebook_contexts in shared_entities.items():
            if len(notebook_contexts) >= 2:
                insight = ProactiveInsight(
                    insight_type="cross_reference",
                    entity=entity,
                    notebooks=[ctx["notebook_id"] for ctx in notebook_contexts],
                    summary=f"'{entity}' appears in {len(notebook_contexts)} notebooks. Consider comparing perspectives.",
                    confidence=0.7
                )
                insights.append(insight)

        # Phase 14 (2026-06-08) — temporal_pattern producer. An entity
        # with a recent mention spike (≥3 in last 7d AND ≥2x its prior
        # 14d cadence) is worth surfacing. Cheap: scans existing event
        # payloads, no new LLM calls. Bounded to top 3 to avoid
        # flooding the insights table.
        try:
            from services.curator_brain import curator_brain as _cb
            from datetime import timedelta as _td
            from collections import Counter as _Counter

            now = datetime.utcnow()
            since_recent = (now - _td(days=7)).isoformat()
            since_baseline = (now - _td(days=21)).isoformat()

            # 2026-06-25: recent_events is a SYNC SQLite read and the
            # mention-scan below is a pure-CPU double loop (up to 6000 events ×
            # all shared entities of json.dumps + substring). Run inline with no
            # await, on a mature corpus this blocked the event loop for >2 min →
            # watchdog kill. Bound the entity set, then offload both the reads
            # and the scan to a thread so the loop never stalls.
            recent_ev = await asyncio.to_thread(_cb.recent_events, limit=2000, since_iso=since_recent)
            baseline_ev = await asyncio.to_thread(_cb.recent_events, limit=4000, since_iso=since_baseline)

            # Mention count = case-insensitive substring hit in payload JSON of
            # an entity-string. Bound to the most-shared entities so the scan
            # cost stays O(events × CAP) regardless of corpus growth.
            _ENTITY_CAP = 200
            candidate_entities = sorted(
                (e for e in shared_entities.keys() if e and len(e) >= 3),
                key=lambda e: len(shared_entities.get(e, [])),
                reverse=True,
            )[:_ENTITY_CAP]

            def _scan_mention_spikes(recent_events, baseline_events, entities):
                """Pure-CPU mention-spike scan — runs in a worker thread so it
                cannot block the event loop. Returns a list of (ent, recent_n,
                prior_per_week) spikes."""
                lowered = [(e, e.lower()) for e in entities]
                recent_counts: Dict[str, int] = {}
                baseline_counts: Dict[str, int] = {}
                for ev in recent_events:
                    blob = json.dumps(ev.get("payload") or {}).lower()
                    for ent, ent_l in lowered:
                        if ent_l in blob:
                            recent_counts[ent] = recent_counts.get(ent, 0) + 1
                for ev in baseline_events:
                    blob = json.dumps(ev.get("payload") or {}).lower()
                    for ent, ent_l in lowered:
                        if ent_l in blob:
                            baseline_counts[ent] = baseline_counts.get(ent, 0) + 1
                out: List[tuple] = []
                for ent, recent_n in recent_counts.items():
                    if recent_n < 3:
                        continue
                    # Mentions in days 8-21 (baseline-window minus recent-window).
                    prior_n = max(0, baseline_counts.get(ent, 0) - recent_n)
                    # Spike: recent rate ≥ 2x prior rate (compare per-week).
                    # prior is 14 days, recent is 7 days; multiply prior by 0.5.
                    prior_per_week = prior_n * 0.5
                    if recent_n >= 2 * max(1.0, prior_per_week):
                        out.append((ent, recent_n, prior_per_week))
                return out

            if candidate_entities:
                spikes: List[tuple] = await asyncio.to_thread(
                    _scan_mention_spikes, recent_ev, baseline_ev, candidate_entities
                )

                spikes.sort(key=lambda x: x[1], reverse=True)
                for ent, recent_n, prior_per_week in spikes[:3]:
                    ctxs = shared_entities.get(ent, [])
                    nb_ids = [c["notebook_id"] for c in ctxs] if ctxs else []
                    insights.append(ProactiveInsight(
                        insight_type="temporal_pattern",
                        entity=ent,
                        notebooks=nb_ids,
                        summary=(
                            f"'{ent}' mentions spiked recently — {recent_n} in the "
                            f"last 7 days vs ~{prior_per_week:.1f}/week before. "
                            f"Worth checking what's driving the surge."
                        ),
                        confidence=0.65,
                    ))
        except Exception as _t_e:
            logger.debug(f"[curator] temporal_pattern detection skipped: {_t_e}")

        # Phase 14 (2026-06-08) — coverage_gap producer. Surfaces
        # notebooks where the user's mental model declares a `blocked_on`
        # area (curated data; the user told us what's missing) and the
        # notebook has the thesis but few sources covering that gap.
        try:
            from services.curator_brain import curator_brain as _cb2
            for nb in notebooks:
                nb_id = nb["id"]
                mm = _cb2.get_mental_model(nb_id) or {}
                thesis = (mm.get("thesis") or "").strip()
                blocked = (mm.get("blocked_on") or "").strip()
                if not thesis or not blocked or len(blocked) < 8:
                    continue
                insights.append(ProactiveInsight(
                    insight_type="coverage_gap",
                    entity=thesis[:80],
                    notebooks=[nb_id],
                    summary=(
                        f"Notebook '{nb.get('title', '(unnamed)')}' is light on "
                        f"coverage around: {blocked}. The thesis would be "
                        f"stronger with sources addressing this gap."
                    ),
                    confidence=0.7,
                ))
        except Exception as _c_e:
            logger.debug(f"[curator] coverage_gap detection skipped: {_c_e}")

        # Write the fresh batch to the brain. Brain preserves user signal
        # (thumbs_up / dismissed) when replacing the active set.
        try:
            from services.curator_brain import curator_brain
            curator_brain.add_insights([ins.model_dump() for ins in insights])
        except Exception as e:
            logger.warning(f"Could not persist insights to brain (non-fatal): {e}")
        return insights

    async def _find_shared_entities(self, notebooks: List[Dict]) -> Dict[str, List[Dict]]:
        """Find entities that appear in multiple notebooks"""
        entity_map = {}

        for notebook in notebooks:
            # Search for entities in this notebook's memories.
            # 2026-06-25: search_archival_memory is SYNCHRONOUS (it embeds the
            # query via the blocking `requests` path + does a LanceDB search).
            # Called inline once per notebook with no await between, it froze the
            # event loop across the whole corpus → /health stalled → the Tauri
            # watchdog killed the backend. Offload each blocking search to a
            # thread so the loop stays responsive (and the cycle stays
            # cancellable — there's now an await per notebook).
            results = await asyncio.to_thread(
                memory_store.search_archival_memory,
                query="key entities people companies topics",
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=notebook["id"],
                limit=20,
            )
            
            for r in results:
                for entity in r.entry.entities:
                    if entity not in entity_map:
                        entity_map[entity] = []
                    entity_map[entity].append({
                        "notebook_id": notebook["id"],
                        "context": r.entry.content[:200]
                    })
        
        # Filter to entities in multiple notebooks
        return {k: v for k, v in entity_map.items() if len(v) >= 2}

    async def surface_insight_if_relevant(self, current_query: str) -> Optional[str]:
        """
        Check if any active brain insights relate to the current user query.
        If so, mention it naturally and record the surface event.

        Phase 14 (2026-06-08): the returned string is markdown — it may
        include trailing code-fences (mermaid / json-chart / klein) that
        the frontend ChatMessageBubble routes through
        MarkdownArtifactRenderer for actual visuals. Insight types map to:
          - cross_reference  → Mermaid graph LR (entity ↔ notebooks)
          - temporal_pattern → json-chart line (mentions over time)
          - coverage_gap     → Mermaid mindmap (covered + dashed missing)
        Other types (contradiction etc.) fall back to plain prose.
        """
        try:
            from services.curator_brain import curator_brain
            matches = curator_brain.find_insights_by_entity(current_query)
        except Exception as e:
            logger.warning(f"Could not query brain insights (non-fatal): {e}")
            return None

        if not matches:
            return None

        insight = matches[0]
        try:
            curator_brain.mark_insight_surfaced(insight["id"])
        except Exception as _e:
            logger.debug(f"[curator] mark_insight_surfaced: {_e}")
        # Curator Phase 4: confidence-aware hedging on the surface phrasing.
        # High-conf insights → assertive; low-conf → tentative.
        conf = insight.get("confidence") or 0
        if conf >= 0.85:
            prefix = "💡 Worth noting:"
        elif conf >= 0.5:
            prefix = "💡 By the way:"
        else:
            prefix = "💡 Possibly relevant (low-confidence):"

        base = f"{prefix} {insight['summary']}"
        visual = await self._compose_insight_visual(insight)
        return f"{base}\n\n{visual}" if visual else base

    async def _compose_insight_visual(self, insight: Dict[str, Any]) -> Optional[str]:
        """Build a code-fence visual for an insight based on its type.

        Returns the fence string (including ```), or None on failure.
        Always best-effort — visualizations are additive, never blocking.
        """
        import re as _re
        from datetime import timedelta as _td

        insight_type = insight.get("insight_type") or ""
        entity = (insight.get("entity") or "").strip()
        notebook_ids = insight.get("notebooks") or []

        def _label(s: str, n: int = 40) -> str:
            s = _re.sub(r"[\(\)\[\]\{\}\"`:,]+", " ", str(s or ""))
            s = _re.sub(r"\s+", " ", s).strip()
            return s[:n] or "—"

        async def _nb_names(ids: List[str], limit: int = 6) -> List[str]:
            names: List[str] = []
            for nb_id in (ids or [])[:limit]:
                try:
                    nb = await notebook_store.get(nb_id) or {}
                    names.append(nb.get("title") or nb.get("name") or nb_id[:8])
                except Exception:
                    names.append(str(nb_id)[:8])
            return names

        try:
            if insight_type == "cross_reference":
                if not entity or not notebook_ids:
                    return None
                names = await _nb_names(notebook_ids, limit=6)
                lines = ["graph LR"]
                root = f'root["{_label(entity, 60)}"]'
                lines.append(f"  {root}")
                for i, n in enumerate(names):
                    nid = f"nb{i}"
                    lines.append(f'  {nid}["{_label(n)}"]')
                    lines.append(f"  root --- {nid}")
                lines.append(f"  classDef hub fill:#ede9fe,stroke:#7c3aed,stroke-width:2px;")
                lines.append(f"  classDef leaf fill:#eff6ff,stroke:#3b82f6;")
                lines.append(f"  class root hub;")
                for i in range(len(names)):
                    lines.append(f"  class nb{i} leaf;")
                return "```mermaid\n" + "\n".join(lines) + "\n```"

            if insight_type == "temporal_pattern":
                # Derive a weekly mention series from recent_events. Best-
                # effort: if events are empty or entity didn't appear, skip.
                from services.curator_brain import curator_brain as _cb
                since_iso = (datetime.utcnow() - _td(days=56)).isoformat()
                try:
                    events = _cb.recent_events(limit=2000, since_iso=since_iso)
                except Exception:
                    events = []
                if not events or not entity:
                    return None
                entity_lower = entity.lower()
                # Bucket by ISO week.
                buckets: Dict[str, int] = {}
                for ev in events:
                    payload_blob = json.dumps(ev.get("payload") or {}).lower()
                    if entity_lower in payload_blob:
                        ts = ev.get("ts") or ""
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", ""))
                            iso = dt.strftime("%Y-W%V")
                            buckets[iso] = buckets.get(iso, 0) + 1
                        except Exception:
                            continue
                if len(buckets) < 2:
                    return None
                labels = sorted(buckets.keys())[-8:]
                data = [buckets[w] for w in labels]
                chart = {
                    "kind": "line",
                    "title": f"Mentions of {entity} per week",
                    "labels": labels,
                    "series": [{"label": "mentions", "data": data}],
                }
                return "```json-chart\n" + json.dumps(chart) + "\n```"

            if insight_type == "coverage_gap":
                if not entity:
                    return None
                names = await _nb_names(notebook_ids, limit=4)
                summary = insight.get("summary") or ""
                # Try to lift "missing X" / "gap on X" phrases out of the
                # summary as the dashed branch. Falls back to a generic
                # "underexplored" leaf when extraction fails.
                m = _re.search(r"(?:gap|missing|underexplor|lack(?:s|ing)?)[^.]*?(?:in|on|around)\s+([A-Za-z0-9 ,\-]{4,60})", summary, _re.I)
                missing_label = _label(m.group(1).strip(), 50) if m else "underexplored area"
                lines = ["mindmap", f"  root(({_label(entity, 60)}))"]
                if names:
                    lines.append("    Covered")
                    for n in names:
                        lines.append(f"      {_label(n)}")
                lines.append("    Missing")
                lines.append(f"      {missing_label}")
                lines.append(f"      ::icon(fa fa-question)")
                return "```mermaid\n" + "\n".join(lines) + "\n```"

        except Exception as e:
            logger.debug(f"[curator] _compose_insight_visual({insight_type}) failed: {e}")
            return None

        return None

    async def maybe_fire_anticipatory_draft(
        self,
        notebook_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Pre-draft a Studio output for a mature notebook (Curator Phase 6a).

        Gating (all must be true):
          - Notebook has ≥15 sources (mature enough to draft from)
          - Mental model exists with a thesis (something to write toward)
          - Mental model is stable (last_inferred_at OR last_user_edit_at
            ≥ 3 days ago — thesis isn't churning)
          - No anticipatory draft active for this notebook (existing
            unconsumed draft would just get clobbered)
          - Not in discard cool-off (user hasn't recently rejected one)
          - `can_fire_nag('anticipatory_draft', nb, priority='low')` allows

        Returns the new draft dict on success, None on skip/failure.
        """
        try:
            from services.curator_brain import curator_brain as _cb
            from storage.source_store import source_store
            from datetime import timedelta

            # Gate 1: nag budget (low priority — chatty surface)
            if not _cb.can_fire_nag("anticipatory_draft", notebook_id, priority="low"):
                logger.debug(
                    f"[curator] anticipatory_draft({notebook_id[:8]}): "
                    f"nag budget blocked"
                )
                return None

            # Gate 2: not in discard cool-off
            if _cb.is_drafting_suppressed(notebook_id):
                logger.debug(
                    f"[curator] anticipatory_draft({notebook_id[:8]}): "
                    f"in 14-day discard cool-off"
                )
                return None

            # Gate 3: no existing unconsumed draft
            existing = _cb.get_latest_unconsumed_draft(notebook_id)
            if existing:
                logger.debug(
                    f"[curator] anticipatory_draft({notebook_id[:8]}): "
                    f"existing unconsumed draft #{existing['id']}"
                )
                return None

            # Gate 4: source count ≥ 15
            sources = await source_store.list(notebook_id)
            if len(sources) < 15:
                return None

            # Gate 5: mental model exists with a thesis
            mm = _cb.get_mental_model(notebook_id)
            if not mm:
                return None
            thesis = (mm.get("thesis") or "").strip()
            if not thesis:
                return None

            # Gate 6: mental model is STABLE (≥3 days since last change)
            now = datetime.utcnow()
            three_days_ago = now - timedelta(days=3)
            stable = True
            for ts_field in ("last_user_edit_at", "last_inferred_at"):
                ts_str = mm.get(ts_field)
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if ts > three_days_ago:
                            stable = False
                            break
                    except (ValueError, TypeError):
                        pass
            if not stable:
                logger.debug(
                    f"[curator] anticipatory_draft({notebook_id[:8]}): "
                    f"mental model still settling (changed in last 3d)"
                )
                return None

            # Pick kind based on stage. Default = executive_brief.
            stage = (mm.get("stage") or "").strip().lower()
            if stage in ("exploration", "gathering"):
                kind = "mind_map"
            else:
                kind = "executive_brief"

            # Generate the draft content via the same fast model path
            # the brief synthesizer uses — keeps cost low and matches
            # the curator's voice.
            recent_sources = sources[:15]
            source_titles = "\n".join(
                f"  - {(s.get('filename') or s.get('title') or s.get('url') or 'Untitled')[:120]}"
                for s in recent_sources
            )
            voice_block = VOICE_PROMPTS.get(self.narrative_voice, "")
            prompt = (
                f"{voice_block}\n\n"
                f"You are pre-drafting a {kind} for a researcher who has been working on:\n"
                f"Thesis: {thesis}\n"
                f"Stage: {stage or 'unspecified'}\n"
                f"Recent focus: {mm.get('recent_focus') or 'unspecified'}\n"
                f"Blocked on: {mm.get('blocked_on') or 'nothing in particular'}\n"
                f"\n"
                f"They have {len(sources)} sources collected. Recent ones:\n"
                f"{source_titles}\n"
                f"\n"
                f"Draft a substantive markdown document. For an executive_brief: "
                f"executive summary, key findings, open questions, suggested next step. "
                f"For a mind_map: a structured concept map in markdown with the thesis "
                f"at the root and 5-8 child branches reflecting the sub-themes. "
                f"Keep it tight (under 800 words). Use H2/H3 headings.\n"
                f"\n"
                f"Output ONLY the markdown — no preamble, no commentary."
            )

            try:
                result = await ollama_service.generate(
                    prompt=prompt,
                    system=f"You are {self.name}, drafting pre-emptive Studio content.",
                    model=settings.ollama_model,  # main model — quality matters here
                    temperature=0.5,
                    timeout=120.0,
                )
                content = (result.get("response") or "").strip()
            except Exception as e:
                logger.warning(f"[curator] anticipatory_draft LLM call failed: {e}")
                return None

            if not content or len(content) < 200:
                logger.debug(
                    f"[curator] anticipatory_draft({notebook_id[:8]}): "
                    f"content too short ({len(content)} chars), skipping"
                )
                return None

            # For mind_map kind, prepend an actual Mermaid mindmap visual
            # built from digest data (themes + entities). The frontend
            # MarkdownArtifactRenderer dispatches mermaid code-fences to
            # MermaidRenderer, so the user sees the diagram alongside the
            # prose instead of just prose describing one.
            if kind == "mind_map":
                try:
                    import re
                    digest = _cb.get_digest(notebook_id) or {}
                    themes_raw = digest.get("key_themes") or "[]"
                    entities_raw = digest.get("key_entities") or "[]"
                    themes = json.loads(themes_raw) if isinstance(themes_raw, str) else (themes_raw or [])
                    entities = json.loads(entities_raw) if isinstance(entities_raw, str) else (entities_raw or [])

                    def _mm_label(s: str, n: int = 60) -> str:
                        # Mermaid mindmap node text is line-sensitive and
                        # chokes on parens/brackets. Keep it boring.
                        s = str(s or "").strip()
                        s = re.sub(r"[\(\)\[\]\{\}\"`]+", "", s)
                        s = re.sub(r"\s+", " ", s)
                        return s[:n].strip() or "—"

                    root_label = _mm_label(thesis, 80)
                    lines = ["mindmap", f"  root(({root_label}))"]
                    theme_list = [t for t in (themes or []) if t][:6]
                    entity_list = [e for e in (entities or []) if e][:6]

                    if theme_list:
                        lines.append("    Key themes")
                        for t in theme_list:
                            lines.append(f"      {_mm_label(t)}")
                    if entity_list:
                        lines.append("    Key entities")
                        for e in entity_list:
                            lines.append(f"      {_mm_label(e)}")
                    if not theme_list and not entity_list:
                        # Fall back to recent source titles so the mindmap
                        # is never empty when digest hasn't been built yet.
                        lines.append("    Recent sources")
                        for s in recent_sources[:5]:
                            title = (s.get("filename") or s.get("title") or s.get("url") or "Untitled")
                            lines.append(f"      {_mm_label(title)}")

                    mermaid_block = "```mermaid\n" + "\n".join(lines) + "\n```"
                    content = f"{mermaid_block}\n\n{content}"
                except Exception as e:
                    # Never block the draft on a visualization failure —
                    # the prose is still useful on its own.
                    logger.debug(
                        f"[curator] mind_map mermaid composition failed (non-fatal): {e}"
                    )

            # For executive_brief kind, prepend the Phase 13 notebook
            # dashboard HTML so the user lands on a structured overview
            # (cornerstone summary + themes/entities chips + activity grid
            # + consensus cards) rather than walls of prose. The ```html
            # fence (Phase 14, 2026-06-08) routes through
            # HtmlArtifactRenderer's Shadow DOM + DOMPurify strict.
            if kind == "executive_brief":
                try:
                    dashboard_html = await self.compose_notebook_dashboard_html(notebook_id)
                    if dashboard_html and len(dashboard_html) > 100:
                        content = f"```html\n{dashboard_html}\n```\n\n{content}"
                except Exception as e:
                    logger.debug(
                        f"[curator] executive_brief dashboard composition failed (non-fatal): {e}"
                    )

            # Persist + record nag fire so daily cap counts it
            draft_id = _cb.queue_draft(
                notebook_id=notebook_id,
                kind=kind,
                content_markdown=content,
                source_signal=f"stage={stage}; sources={len(sources)}",
            )
            _cb.record_nag(
                "anticipatory_draft",
                notebook_id=notebook_id,
                subject_id=str(draft_id) if draft_id else None,
            )
            logger.info(
                f"[curator] anticipatory_draft queued for notebook {notebook_id[:8]}: "
                f"kind={kind} chars={len(content)} id={draft_id}"
            )
            return _cb.get_latest_unconsumed_draft(notebook_id)
        except Exception as e:
            logger.warning(f"[curator] maybe_fire_anticipatory_draft failed: {e}")
            return None

    async def maybe_fire_dissent_overwatch(
        self,
        notebook_id: str,
        new_source_id: Optional[str] = None,
    ) -> Optional[str]:
        """Generate a dissent overwatch aside text if conditions are right.

        Curator Phase 3c (2026-05-13). Called by the event-bus consumer
        when a new stance scores as high-confidence contradicts. Returns
        a one-sentence aside text OR None.

        Fires when ALL of:
          - can_fire_nag('dissent_overwatch_aside', notebook_id) is True
          - notebook supporting count ≥ 5 (real consensus to dissent against)
          - at least 1 contradicting source with confidence > 0.6 exists

        On fire: records the nag, queues a pending aside on the brain so
        the next @curator chat reply surfaces it via curator_aside.
        """
        try:
            from services.curator_brain import curator_brain as _cb
            # Nag budget gate first — cheapest check.
            # Contradiction surfacing is HIGH priority — bypasses daily cap.
            # If the curator detects a contradiction in user-supported thesis,
            # that's worth surfacing even on a chatty day. Cool-off still applies.
            if not _cb.can_fire_nag("dissent_overwatch_aside", notebook_id, priority="high"):
                logger.debug(
                    f"[curator] maybe_fire_dissent_overwatch({notebook_id[:8]}): nag budget blocked"
                )
                return None

            counts = _cb.get_notebook_stance_counts(notebook_id)
            if counts.get("supports", 0) < 5:
                return None

            dissenters = _cb.get_dissenting_sources(notebook_id, limit=5)
            top = next((d for d in dissenters if (d.get("confidence") or 0) > 0.6), None)
            if not top:
                return None

            # Best-effort: get the source title for a friendlier aside.
            title = top.get("source_id") or "a source"
            try:
                from storage.source_store import source_store
                src = await source_store.get(top["source_id"])
                if src:
                    title = (
                        src.get("filename") or src.get("title") or src.get("url") or title
                    )
            except Exception:
                pass

            # If the trigger came from a specific newly-scored source, prefer that one.
            if new_source_id:
                new_match = next(
                    (d for d in dissenters if d.get("source_id") == new_source_id),
                    None,
                )
                if new_match and (new_match.get("confidence") or 0) > 0.6:
                    top = new_match
                    try:
                        from storage.source_store import source_store
                        src = await source_store.get(new_source_id)
                        if src:
                            title = (
                                src.get("filename") or src.get("title") or src.get("url") or title
                            )
                    except Exception:
                        pass

            aside = (
                f"Heads up — \"{str(title)[:120]}\" actually contradicts the notebook's thesis: "
                f"{top.get('rationale', '')[:200]}"
            )

            # Phase 14 (2026-06-08) — append a Mermaid quadrantChart so the
            # user sees this dissenter plotted against the notebook's
            # existing stance distribution. Renders via MarkdownArtifact-
            # Renderer's mermaid fence handler. Skipped silently on any
            # failure — prose aside still surfaces.
            try:
                import re as _re

                def _clean(s: str, n: int = 40) -> str:
                    s = _re.sub(r"[\[\]\"`:,]+", " ", str(s or ""))
                    s = _re.sub(r"\s+", " ", s).strip()
                    return s[:n] or "source"

                top_conf = float(top.get("confidence") or 0)
                # Stance is contradicting here; lower-right quadrant.
                # x-axis = confidence (0-1), y-axis = supports vs contradicts.
                new_point = (_clean(title), round(min(0.95, max(0.05, top_conf)), 2), 0.15)
                support_dots: List[tuple] = []
                # Plot up to 3 supporting sources as upper-area dots.
                try:
                    supports = _cb.get_supporting_sources(notebook_id, limit=3)
                except Exception:
                    supports = []
                for i, s in enumerate(supports or []):
                    sc = float(s.get("confidence") or 0.7)
                    sx = round(min(0.95, max(0.05, sc)), 2)
                    sy = round(0.75 + (i * 0.05), 2)
                    label = _clean(s.get("source_id") or f"src{i}", 28)
                    support_dots.append((label, sx, sy))

                lines = [
                    "quadrantChart",
                    "  title Stance vs confidence",
                    "  x-axis Low conf --> High conf",
                    "  y-axis Contradicts --> Supports",
                    "  quadrant-1 Strong support",
                    "  quadrant-2 Weak support",
                    "  quadrant-3 Weak contradiction",
                    "  quadrant-4 Strong contradiction",
                    f"  {new_point[0]}: [{new_point[1]}, {new_point[2]}]",
                ]
                for label, x, y in support_dots:
                    lines.append(f"  {label}: [{x}, {y}]")
                aside = aside + "\n\n```mermaid\n" + "\n".join(lines) + "\n```"
            except Exception as _e:
                logger.debug(f"[curator] dissent quadrant composition failed (non-fatal): {_e}")

            # Record + queue
            nag_id = _cb.record_nag(
                "dissent_overwatch_aside",
                notebook_id=notebook_id,
                subject_id=top.get("source_id"),
            )
            _cb.queue_pending_aside(
                notebook_id=notebook_id,
                kind="dissent",
                aside_text=aside,
                nag_id=nag_id,
            )
            logger.info(
                f"[curator] dissent overwatch queued for notebook {notebook_id[:8]}: {aside[:100]}"
            )
            return aside
        except Exception as e:
            logger.warning(f"[curator] maybe_fire_dissent_overwatch failed: {e}")
            return None

    async def generate_overwatch_aside(
        self,
        query: str,
        answer: str,
        notebook_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Phase D.1 (2026-05-22): trigger-driven only. No probabilistic fallback.

        Returns a dict {aside_text, nag_id, kind} ONLY when an explicit
        upstream trigger has queued one. Otherwise returns None — the user
        typed a regular question; we stay quiet.

        Fix #5 (2026-05-23): now returns the full payload (including nag_id)
        instead of just the aside text, so the UI can wire thumbs feedback
        to POST /curator/asides/{nag_id}/thumbs.

        Surfacing channels (all upstream signals that emit pending_asides
        via the event bus):
          - contradiction (Phase 3b/3c: stance_scored 'contradicts' + high conf)
          - connection (Phase 5: connection_discovered with strength > 0.7)
          - plan_completed (Phase 5: plan_completed with user_visible plans)
          - mental_model_shift (Phase 3a: pending — added when emitted)

        NOT surfaced here (intentionally moved away from chat asides):
          - stagnation: now lives in the Collector panel only (Phase A.1)
          - generic cross-notebook search: required the user to suspect a
            connection exists; if it's genuinely useful, surface it via
            @curator instead

        Pre-D this method ran a brain digest LLM call + a parallel vector
        search across every other notebook + another LLM to decide if any
        of it was useful, on EVERY chat reply. Two LLM hops + N-notebook
        archival scans for a sidebar note the user usually didn't need.
        """
        try:
            from services.curator_brain import curator_brain as _cb
            pending = _cb.consume_pending_aside(notebook_id)
            if pending and pending.get("aside_text"):
                logger.debug(
                    f"[curator] generate_overwatch_aside({notebook_id[:8]}): "
                    f"surfacing pending aside (kind={pending.get('kind')})"
                )
                return {
                    "aside_text": pending["aside_text"],
                    "nag_id": pending.get("nag_id"),
                    "kind": pending.get("kind"),
                }
        except Exception as _e:
            logger.debug(f"[curator] pending aside consume failed: {_e}")

        # No pending aside = nothing event-driven to say. Stay quiet.
        return None

    def _fire_deep_reads_for_clusters(self, clusters: List[Any]) -> List[Dict[str, Any]]:
        """For the top consensus clusters, fire research_engine.deep_dive
        as fire-and-forget. Returns a list of {topic_label, notebook_id,
        query, cluster_id} dicts describing what was triggered.

        Honors `_AUTO_DEEP_READ` switch + max-per-brief cap. Never blocks
        the brief assembly path; any per-cluster failure is debug-logged.
        """
        triggered: List[Dict[str, Any]] = []
        if not self._AUTO_DEEP_READ or not clusters:
            return triggered
        try:
            from services.research_engine import research_engine
            from services.curator_event_bus import event_bus
        except Exception as e:
            logger.debug(f"[curator.fire_deep_reads] import failed: {e}")
            return triggered

        for cl in clusters[: self._MAX_DEEP_READS_PER_BRIEF]:
            try:
                if not cl.primary_notebook_id or not cl.topic_label:
                    continue
                query = cl.topic_label
                safe_create_task(
                    research_engine.deep_dive(
                        query=query,
                        notebook_id=cl.primary_notebook_id,
                    )
                )
                event_bus.emit_now(
                    actor="@curator",
                    action="deep_read_triggered",
                    notebook_id=cl.primary_notebook_id,
                    payload={
                        "cluster_id": cl.cluster_id,
                        "query": query,
                        "cluster_size": cl.size,
                    },
                    outcome="success",
                )
                triggered.append({
                    "cluster_id": cl.cluster_id,
                    "topic_label": cl.topic_label,
                    "notebook_id": cl.primary_notebook_id,
                    "query": query,
                })
            except Exception as e:
                logger.debug(f"[curator.fire_deep_reads] cluster fire failed: {e}")
        return triggered
