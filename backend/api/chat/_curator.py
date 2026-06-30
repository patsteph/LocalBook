"""_stream_curator handler — extracted from api/chat.py (Wave 5 split)."""
from ._common import *  # noqa: F401,F403
from ._common import (
    _build_mental_model_block,
    _is_help_request,
    _stream_help,
    _dispatch_multi_intent,
    _quick_intent_for_correspondent,
    _CURATOR_HELP,
    _COLLECTOR_HELP,
    _RESEARCH_HELP,
    _STUDIO_HELP,
)

async def _stream_curator(chat_query: ChatQuery, injected_action: Optional[Dict[str, Any]] = None):
    """Stream a Curator response in SSE format.
    
    LLM-based NLP intent router — anything you can do in the Curator settings
    panel or cross-notebook features, you can do here via natural language.

    If ``injected_action`` is provided, it bypasses the LLM classifier and uses
    the provided {intent, params} directly. This is used by the multi-intent
    dispatcher to execute each classified action in sequence.
    """
    from agents.curator import curator
    from services.cross_notebook_search import cross_notebook_search
    from services.ollama_service import ollama_service
    from services.intent_classifier import classify_intent

    curator_name = curator.name or "Curator"
    q = chat_query.question

    # ── Help shortcut (no LLM call) ──
    if _is_help_request(q):
        for chunk in _stream_help(_CURATOR_HELP, curator_name, "curator"):
            yield chunk
        return

    yield f"data: {json.dumps({'type': 'status', 'message': f'{curator_name} processing...', 'query_type': 'curator'})}\n\n"

    try:
        reply = ""
        results = []
        follow_ups = ['What patterns exist across all notebooks?', 'Compare the key findings', 'What contradictions do you see?']
        cfg = curator.get_config()

        # Helper: stream reply + done
        def _done_event():
            return f"data: {json.dumps({'type': 'done', 'follow_up_questions': follow_ups, 'curator_name': curator_name, 'agent_name': curator_name, 'agent_type': 'curator'})}\n\n"

        # =================================================================
        # LLM-based Intent Classification (bypassed if injected by dispatcher)
        # =================================================================
        if injected_action:
            classified = injected_action
        else:
            classified = await classify_intent(q, "curator")
        intent = classified["intent"]
        params = classified.get("params", {})
        handled = False

        # Curator Phase 2a: emit which intent the user invoked so the
        # brain knows which curator features are getting used. Confidence
        # included so we can later distinguish high-confidence dispatches
        # from low-confidence fallbacks.
        try:
            from services.curator_event_bus import event_bus
            event_bus.emit_now(
                actor="@curator",
                action="curator_intent_dispatched",
                notebook_id=chat_query.notebook_id,
                intent=intent,
                payload={
                    "message_chars": len(q),
                    "confidence": classified.get("confidence", 0.5),
                    "injected": bool(injected_action),
                },
            )
        except Exception as _e:
            logger.debug(f"[chat] curator intent emit failed: {_e}")

        # -----------------------------------------------------------------
        # SET NAME
        # -----------------------------------------------------------------
        if intent == "set_name":
            new_name = (params.get("name") or "").strip()
            if new_name:
                curator.update_config({"name": new_name})
                curator_name = new_name
                reply = f"Done — I'm now **{new_name}**. Nice to meet you!"
                handled = True

        # -----------------------------------------------------------------
        # SET PERSONALITY
        # -----------------------------------------------------------------
        elif intent == "set_personality":
            personality = (params.get("personality") or "").strip().rstrip('.')
            if personality:
                curator.update_config({"personality": personality})
                reply = f"Done. **Personality updated:** {personality}"
                handled = True

        # -----------------------------------------------------------------
        # TOGGLE OVERWATCH
        # -----------------------------------------------------------------
        elif intent == "toggle_overwatch":
            oversight = cfg.get("oversight", {})
            if not isinstance(oversight, dict):
                oversight = {}
            enabled = params.get("enabled", True)
            if isinstance(enabled, str):
                enabled = enabled.lower() not in ("false", "no", "off", "disable")
            oversight["overwatch_enabled"] = bool(enabled)
            curator.update_config({"oversight": oversight})
            if enabled:
                reply = "Done. **Overwatch enabled.** I'll chime in when I spot cross-notebook connections."
            else:
                reply = "Done. **Overwatch disabled.** I won't interject during your regular chats."
            handled = True

        # -----------------------------------------------------------------
        # EXCLUDE NOTEBOOK from cross-NB
        # -----------------------------------------------------------------
        elif intent == "exclude_notebook":
            nb_name = (params.get("notebook_name") or "").strip().strip("'\"")
            if nb_name:
                oversight = cfg.get("oversight", {})
                if not isinstance(oversight, dict): oversight = {}
                excluded = list(oversight.get("excluded_notebook_ids", []))
                excluded.append(f"name:{nb_name}")
                oversight["excluded_notebook_ids"] = excluded
                curator.update_config({"oversight": oversight})
                reply = f"Done — I'll exclude \"{nb_name}\" from cross-notebook operations.\n*(To fully resolve, check the Curator settings panel for notebook IDs.)*"
                handled = True

        # -----------------------------------------------------------------
        # INCLUDE NOTEBOOK back into cross-NB
        # -----------------------------------------------------------------
        elif intent == "include_notebook":
            nb_name = (params.get("notebook_name") or "").strip().strip("'\"")
            if nb_name:
                oversight = cfg.get("oversight", {})
                if not isinstance(oversight, dict): oversight = {}
                excluded = [e for e in oversight.get("excluded_notebook_ids", []) if nb_name.lower() not in e.lower()]
                oversight["excluded_notebook_ids"] = excluded
                curator.update_config({"oversight": oversight})
                reply = f"Done. **\"{nb_name}\"** is now included in cross-notebook operations."
                handled = True

        # -----------------------------------------------------------------
        # MORNING BRIEF
        # -----------------------------------------------------------------
        elif intent == "morning_brief":
            yield f"data: {json.dumps({'type': 'status', 'message': f'{curator_name} preparing your brief...', 'query_type': 'curator'})}\n\n"
            try:
                from datetime import datetime, timedelta
                from pathlib import Path
                from services.event_logger import event_logger
                import json as _json

                # Try to recall today's saved brief first (avoid expensive re-generation)
                brief_dir = Path(event_logger.data_dir) / "memory"
                today_str = datetime.utcnow().strftime("%Y-%m-%d")
                brief_file = brief_dir / f"morning_brief_{today_str}.json"
                saved_brief = None

                if brief_file.exists():
                    try:
                        saved_brief = _json.loads(brief_file.read_text())
                    except Exception as _e:
                        logger.warning(f"[chat] Failed to parse saved brief: {_e}")

                if saved_brief and saved_brief.get("narrative"):
                    parts = [saved_brief["narrative"]]
                    if saved_brief.get("cross_notebook_insight"):
                        parts.append(f"\n**Cross-Notebook Insight:** {saved_brief['cross_notebook_insight']}")
                    reply = "\n\n".join(parts)
                else:
                    brief = await curator.generate_morning_brief(datetime.utcnow() - timedelta(hours=8))
                    parts = []
                    if brief.narrative:
                        parts.append(brief.narrative)
                    if brief.cross_notebook_insight:
                        parts.append(f"\n**Cross-Notebook Insight:** {brief.cross_notebook_insight}")
                    reply = "\n\n".join(parts) if parts else "Nothing notable since your last session."
                follow_ups = ['What patterns exist?', 'Show me details on the first item', 'Compare findings']
            except Exception as be:
                reply = f"Could not generate brief: {be}"
            handled = True

        # -----------------------------------------------------------------
        # WEEKLY WRAP UP
        # -----------------------------------------------------------------
        elif intent == "weekly_wrap_up":
            yield f"data: {json.dumps({'type': 'status', 'message': f'{curator_name} preparing your weekly wrap up...', 'query_type': 'curator'})}\n\n"
            try:
                from datetime import datetime
                from pathlib import Path
                from services.event_logger import event_logger
                import json as _json

                # Try to recall a saved wrap first
                wrap_dir = Path(event_logger.data_dir) / "memory"
                saved_wrap = None
                wrap_files = sorted(wrap_dir.glob("weekly_wrap_*.json"), reverse=True) if wrap_dir.exists() else []
                if wrap_files:
                    try:
                        saved_wrap = _json.loads(wrap_files[0].read_text())
                    except Exception as _e:
                        logger.warning(f"[chat] Failed to parse saved wrap-up: {_e}")

                # Phase 14 (2026-06-08) — prefer the HTML dashboard
                # variant when available; falls back to narrative-only.
                # The frontend MarkdownArtifactRenderer's `html` fence
                # routes this through the strict HtmlArtifactRenderer.
                if saved_wrap and (saved_wrap.get("narrative_html") or saved_wrap.get("narrative")):
                    html_variant = (saved_wrap.get("narrative_html") or "").strip()
                    if html_variant:
                        reply = f"```html\n{html_variant}\n```"
                        if saved_wrap.get("narrative"):
                            reply += "\n\n" + saved_wrap["narrative"]
                    else:
                        reply = saved_wrap["narrative"]
                    if saved_wrap.get("cross_notebook_insight") and "Cross-Notebook Insight" not in reply:
                        reply += f"\n\n**Cross-Notebook Insight:** {saved_wrap['cross_notebook_insight']}"
                else:
                    wrap = await curator.generate_weekly_wrap_up()
                    if wrap.narrative_html:
                        reply = f"```html\n{wrap.narrative_html}\n```"
                        if wrap.narrative:
                            reply += "\n\n" + wrap.narrative
                    else:
                        reply = wrap.narrative if wrap.narrative else "Not enough activity this week for a wrap up."
                    if wrap.cross_notebook_insight and "Cross-Notebook Insight" not in reply:
                        reply += f"\n\n**Cross-Notebook Insight:** {wrap.cross_notebook_insight}"
                follow_ups = ['What were the key themes?', 'Show me collector discoveries', 'Compare to last week']
            except Exception as we:
                reply = f"Could not generate weekly wrap up: {we}"
            handled = True

        # -----------------------------------------------------------------
        # DISCOVER PATTERNS
        # -----------------------------------------------------------------
        elif intent == "discover_patterns":
            yield f"data: {json.dumps({'type': 'status', 'message': f'{curator_name} discovering patterns...', 'query_type': 'curator'})}\n\n"
            try:
                insights = await curator.discover_cross_notebook_patterns()
                if not insights:
                    reply = "No strong cross-notebook patterns detected yet. Add more sources to different notebooks and try again."
                else:
                    # Phase 14 (2026-06-08) — render each insight with the
                    # visual that fits its type (cross_reference → Mermaid
                    # graph; temporal_pattern → json-chart; coverage_gap →
                    # mindmap). Falls back to text bullet on failure so the
                    # reply is never blank.
                    lines = [f"**Cross-Notebook Patterns ({len(insights)} found):**\n"]
                    for ins in insights[:6]:
                        lines.append(
                            f"### {ins.entity}\n"
                            f"_{ins.insight_type.replace('_', ' ')}_ — {ins.summary}"
                        )
                        try:
                            viz = await curator._compose_insight_visual(ins.model_dump())
                            if viz:
                                lines.append(viz)
                        except Exception as _v_e:
                            logger.debug(f"[chat.discover_patterns] viz skipped: {_v_e}")
                        lines.append("")  # spacer
                    reply = "\n".join(lines)
                follow_ups = ['Tell me more about the first pattern', 'Synthesize insights', 'Play devil\'s advocate']
            except Exception as pe:
                reply = f"Pattern discovery failed: {pe}"
            handled = True

        # -----------------------------------------------------------------
        # DEVIL'S ADVOCATE
        # -----------------------------------------------------------------
        elif intent == "devils_advocate":
            yield f"data: {json.dumps({'type': 'status', 'message': f'{curator_name} finding counterarguments...', 'query_type': 'curator'})}\n\n"
            try:
                thesis = (params.get("thesis") or "").strip() or None
                result = await curator.find_counterarguments(
                    notebook_id=chat_query.notebook_id, thesis=thesis
                )
                lines = []
                if result.inferred_thesis:
                    lines.append(f"**Your thesis:** {result.inferred_thesis}\n")
                if result.counterpoints:
                    lines.append("**Counterpoints:**\n")
                    for cp in result.counterpoints:
                        # cp is {query, content, score} — render the content snippet
                        # (whitespace-collapsed for a clean bullet), not the raw dict
                        # repr that was leaking before (2026-06-29 fix).
                        raw = (cp.get("content") or "") if isinstance(cp, dict) else str(cp)
                        snippet = " ".join(raw.split())
                        if snippet:
                            lines.append(f"- {snippet}")
                reply = "\n".join(lines) if lines else "I couldn't find strong counterarguments. Your thesis may be well-supported!"
                follow_ups = ['Strengthen my thesis', 'Find supporting evidence', 'Show related patterns']

                # Phase 14 (2026-06-08) — append a Mermaid quadrant chart
                # plotting the notebook's stance distribution (supports vs
                # contradicts × confidence) so the user sees the shape of
                # the disagreement, not just a counterpoint list.
                try:
                    from services.curator_brain import curator_brain as _cb
                    import re as _re

                    def _q_label(s: str, n: int = 32) -> str:
                        s = _re.sub(r"[\[\]\"`:,]+", " ", str(s or ""))
                        s = _re.sub(r"\s+", " ", s).strip()
                        return s[:n] or "source"

                    supports = _cb.get_supporting_sources(chat_query.notebook_id, limit=4)
                    dissents = _cb.get_dissenting_sources(chat_query.notebook_id, limit=4)
                    if supports or dissents:
                        qlines = [
                            "quadrantChart",
                            "  title Stance vs confidence",
                            "  x-axis Low conf --> High conf",
                            "  y-axis Contradicts --> Supports",
                            "  quadrant-1 Strong support",
                            "  quadrant-2 Weak support",
                            "  quadrant-3 Weak contradiction",
                            "  quadrant-4 Strong contradiction",
                        ]
                        for i, s in enumerate(supports or []):
                            x = round(min(0.95, max(0.05, float(s.get("confidence") or 0.7))), 2)
                            y = round(0.75 + (i * 0.04), 2)
                            qlines.append(f"  {_q_label(s.get('source_id') or f'support{i}')}: [{x}, {y}]")
                        for i, d in enumerate(dissents or []):
                            x = round(min(0.95, max(0.05, float(d.get("confidence") or 0.6))), 2)
                            y = round(0.25 - (i * 0.04), 2)
                            qlines.append(f"  {_q_label(d.get('source_id') or f'dissent{i}')}: [{x}, {y}]")
                        reply = reply + "\n\n```mermaid\n" + "\n".join(qlines) + "\n```"
                except Exception as _v_e:
                    logger.debug(f"[chat.devils_advocate] quadrant skipped: {_v_e}")
            except Exception as de:
                reply = f"Counterargument analysis failed: {de}"
            handled = True

        # -----------------------------------------------------------------
        # SHOW PROFILE / CONFIG
        # -----------------------------------------------------------------
        elif intent == "show_profile":
            oversight = cfg.get("oversight", {})
            synthesis = cfg.get("synthesis", {})
            lines = [f"**{curator_name}'s Profile:**\n"]
            lines.append(f"- **Name:** {curator_name}")
            lines.append(f"- **Personality:** {curator.personality}")
            ow = oversight.get("overwatch_enabled", True) if isinstance(oversight, dict) else True
            lines.append(f"- **Overwatch:** {'enabled' if ow else 'disabled'}")
            excluded = oversight.get("excluded_notebook_ids", []) if isinstance(oversight, dict) else []
            if excluded:
                lines.append(f"- **Excluded notebooks:** {', '.join(str(e) for e in excluded)}")
            freq = synthesis.get("insight_frequency", "daily") if isinstance(synthesis, dict) else "daily"
            lines.append(f"- **Insight frequency:** {freq}")
            reply = "\n".join(lines)
            follow_ups = ['Change your name', 'Change your personality', 'Disable overwatch', 'Brain status']
            handled = True

        # -----------------------------------------------------------------
        # NOTE THEMES → COLLECTOR BRIDGE
        # -----------------------------------------------------------------
        elif intent == "note_themes":
            yield f"data: {json.dumps({'type': 'status', 'message': f'{curator_name} analyzing your notes...', 'query_type': 'curator'})}\n\n"
            try:
                # F7 fix (2026-05-22): _stream_curator never binds a local
                # `notebook_id` — the only available name is chat_query.notebook_id.
                # The bare reference raised NameError on every @curator note themes
                # call, surfaced as "Failed to analyze notes: name 'notebook_id'
                # is not defined" to the user. The downstream method already
                # returns a graceful "No notes in this notebook" message when
                # no sources are present, so we don't need an extra guard.
                result = await curator.suggest_collector_keywords_from_notes(chat_query.notebook_id)
                themes = result.get("note_themes", [])
                suggestions = result.get("suggestions", [])
                current = result.get("current_focus", [])
                note_count = result.get("note_count", 0)

                lines = [f"**Note Analysis** ({note_count} note{'s' if note_count != 1 else ''} scanned)\n"]
                if themes:
                    lines.append("**Themes I found in your notes:**")
                    for t in themes:
                        lines.append(f"- {t}")
                if current:
                    lines.append(f"\n**Current collector focus areas:** {', '.join(current)}")
                if suggestions:
                    lines.append("\n**Suggested new collector keywords** (based on your notes):")
                    for s in suggestions:
                        lines.append(f"- {s}")
                    lines.append("\nSay **\"apply these suggestions\"** or tell me which ones to add.")
                elif themes:
                    lines.append("\nYour collector's focus areas already cover these themes well.")
                else:
                    lines.append("No strong themes found — try adding more notes first.")

                # Phase 14 (2026-06-08) — append a Mermaid mindmap of
                # themes + suggestions so the user sees the structure at
                # a glance instead of reading three bullet lists. Skipped
                # when there's nothing meaningful to visualize.
                if themes or suggestions:
                    try:
                        import re as _re

                        def _mm_label(s: str, n: int = 50) -> str:
                            s = _re.sub(r"[\(\)\[\]\{\}\"`:,]+", " ", str(s or ""))
                            s = _re.sub(r"\s+", " ", s).strip()
                            return s[:n] or "—"

                        mm_lines = ["mindmap", "  root((Notes))"]
                        if themes:
                            mm_lines.append("    Themes")
                            for t in themes[:6]:
                                mm_lines.append(f"      {_mm_label(t)}")
                        if current:
                            mm_lines.append("    Current focus")
                            for c in current[:5]:
                                mm_lines.append(f"      {_mm_label(c)}")
                        if suggestions:
                            mm_lines.append("    Suggested keywords")
                            for s in suggestions[:6]:
                                mm_lines.append(f"      {_mm_label(s)}")
                        lines.append("\n```mermaid\n" + "\n".join(mm_lines) + "\n```")
                    except Exception as _v_e:
                        logger.debug(f"[chat.note_themes] mindmap skipped: {_v_e}")

                reply = "\n".join(lines)
                follow_ups = ['Discover patterns', 'Show your profile', 'What themes connect my notebooks?']
            except Exception as e:
                reply = f"Failed to analyze notes: {e}"
                follow_ups = []
            handled = True

        # -----------------------------------------------------------------
        # COLLECTION SCHEDULE STATUS
        # -----------------------------------------------------------------
        elif intent == "collection_schedule":
            yield f"data: {json.dumps({'type': 'status', 'message': f'{curator_name} checking collection schedule...', 'query_type': 'curator'})}\n\n"
            try:
                from services.collection_scheduler import collection_scheduler
                from services.collection_history import get_collection_history
                from agents.collector import get_collector
                from storage.notebook_store import notebook_store

                sched = collection_scheduler.get_status()
                notebooks = await notebook_store.list()
                nb_names = {nb["id"]: nb.get("title", nb.get("name", nb["id"][:8])) for nb in notebooks}

                lines = [f"**Collection Schedule Dashboard**\n"]
                lines.append(f"- **Scheduler:** {'🟢 Running' if sched.get('running') else '🔴 Stopped'}")
                lines.append(f"- **Notebooks tracked:** {sched.get('notebooks_tracked', 0)}\n")

                details = sched.get("schedule_details", {})
                if details:
                    lines.append("| Notebook | Frequency | Last Run | Next Due | Status |")
                    lines.append("|----------|-----------|----------|----------|--------|")
                    for nb_id, info in details.items():
                        name = nb_names.get(nb_id, nb_id[:12])
                        freq = info.get("frequency", "?")
                        last = info.get("last_run", "never")[:16].replace("T", " ")
                        next_due = info.get("next_due", "?")[:16].replace("T", " ")
                        overdue = info.get("overdue", False)
                        status = "⏰ Overdue" if overdue else "✅ On track"
                        lines.append(f"| {name} | {freq} | {last} | {next_due} | {status} |")

                lines.append("\n**Recent Collection Results:**\n")
                for nb_id in details:
                    name = nb_names.get(nb_id, nb_id[:12])
                    try:
                        runs = get_collection_history(nb_id, limit=3)
                        if runs:
                            for run in runs[:2]:
                                ts = str(run.get("timestamp", "?"))[:16]
                                approved = run.get("items_approved", 0)
                                rejected = run.get("items_rejected", 0)
                                found = run.get("items_found", run.get("items_collected", "?"))
                                lines.append(f"- **{name}** ({ts}): found {found}, approved {approved}, rejected {rejected}")
                        else:
                            lines.append(f"- **{name}**: No recent runs recorded")
                    except Exception:
                        lines.append(f"- **{name}**: History unavailable")

                reply = "\n".join(lines)
                follow_ups = ['Show collection schedule', 'Discover patterns', 'What patterns exist?']
            except Exception as se:
                reply = f"Could not retrieve schedule status: {se}"
            handled = True

        # -----------------------------------------------------------------
        # BRAIN STATUS
        # -----------------------------------------------------------------
        elif intent == "brain_status":
            yield f"data: {json.dumps({'type': 'status', 'message': f'{curator_name} checking brain status...', 'query_type': 'curator'})}\n\n"
            try:
                from services.curator_brain import curator_brain
                stats = curator_brain.get_stats()
                digests = curator_brain.get_all_digests()
                connections = curator_brain.get_active_connections()

                lines = [f"**{curator_name}'s Research Brain**\n"]
                lines.append(f"- **Notebooks with digests:** {stats.get('digests_total', 0)} ({stats.get('digests_dirty', 0)} pending rebuild)")
                lines.append(f"- **Active connections:** {stats.get('connections_active', 0)}")
                lines.append(f"- **Unsurfaced reflections:** {stats.get('reflections_unsurfaced', 0)}")

                if digests:
                    lines.append("\n**What I understand about each notebook:**")
                    for d in digests:
                        summary = d.get("current_summary", "")
                        if summary:
                            lines.append(f"\n**{d['name']}**")
                            lines.append(summary)
                        else:
                            lines.append(f"\n**{d['name']}** — digest not yet built")

                if connections:
                    lines.append("\n**Cross-notebook connections I've detected:**")
                    # Curator Phase 4: tier-aware phrasing by strength.
                    # ≥ 0.7 → "strong" (definitive); 0.4–0.7 → "related" (moderate);
                    # < 0.4 → "possible" (hedged).
                    for i, c in enumerate(connections[:8], 1):
                        s = c["strength"] or 0
                        if s >= 0.7:
                            tier_label = "🟢 strong"
                        elif s >= 0.4:
                            tier_label = "⚪ related"
                        else:
                            tier_label = "🟡 possible"
                        strength_bar = "▓" * int(s * 5) + "░" * (5 - int(s * 5))
                        lines.append(f"{i}. [{strength_bar}] **{tier_label}** — {c['description']}")
                    lines.append("\n*Say **\"dismiss connection 2\"** or **\"connection 3 is useful\"** to give me feedback.*")

                    # Phase 14 (2026-06-08) — append a Mermaid constellation
                    # so users see the cross-notebook structure as a network,
                    # not just a numbered list. Routed via mermaid fence in
                    # MarkdownArtifactRenderer. Strong = solid thick edge,
                    # related = solid, possible = dashed.
                    try:
                        from storage.notebook_store import notebook_store as _nbs
                        import re as _re

                        def _node_label(s: str, n: int = 28) -> str:
                            s = _re.sub(r"[\(\)\[\]\{\}\"`]+", " ", str(s or ""))
                            s = _re.sub(r"\s+", " ", s).strip()
                            return s[:n] or "—"

                        # Build a unique notebook-id → display name map for the
                        # nodes that appear in the top 8 connections.
                        ids_in_play: List[str] = []
                        for c in connections[:8]:
                            for k in ("notebook_a", "notebook_b"):
                                nb_id = c.get(k)
                                if nb_id and nb_id not in ids_in_play:
                                    ids_in_play.append(nb_id)
                        name_by_id: Dict[str, str] = {}
                        for nb_id in ids_in_play:
                            try:
                                nb = await _nbs.get(nb_id) or {}
                                name_by_id[nb_id] = nb.get("title") or nb.get("name") or nb_id[:8]
                            except Exception:
                                name_by_id[nb_id] = str(nb_id)[:8]

                        if len(name_by_id) >= 2:
                            graph_lines = ["graph LR"]
                            # Node declarations with stable short IDs
                            node_id_by_nb: Dict[str, str] = {}
                            for i, (nb_id, name) in enumerate(name_by_id.items()):
                                short = f"n{i}"
                                node_id_by_nb[nb_id] = short
                                graph_lines.append(f'  {short}["{_node_label(name)}"]')
                            # Edges, tier-styled
                            strong_edges: List[str] = []
                            related_edges: List[str] = []
                            possible_edges: List[str] = []
                            for c in connections[:8]:
                                a = node_id_by_nb.get(c.get("notebook_a", ""))
                                b = node_id_by_nb.get(c.get("notebook_b", ""))
                                if not a or not b:
                                    continue
                                s = c.get("strength") or 0
                                if s >= 0.7:
                                    strong_edges.append(f"  {a} === {b}")
                                elif s >= 0.4:
                                    related_edges.append(f"  {a} --- {b}")
                                else:
                                    possible_edges.append(f"  {a} -.-> {b}")
                            graph_lines.extend(strong_edges + related_edges + possible_edges)
                            graph_lines.append("  classDef nb fill:#ede9fe,stroke:#7c3aed,stroke-width:1.5px,color:#4c1d95;")
                            for short in node_id_by_nb.values():
                                graph_lines.append(f"  class {short} nb;")
                            lines.append("\n```mermaid\n" + "\n".join(graph_lines) + "\n```")
                    except Exception as _vis_e:
                        # Visualization failure never blocks the text reply.
                        logger.debug(f"[curator] brain_status constellation skipped: {_vis_e}")
                elif stats.get('digests_total', 0) > 0:
                    lines.append("\n*No cross-notebook connections detected yet. More sources needed across notebooks.*")
                else:
                    lines.append("\n*Brain not yet built — will populate after the next consolidation cycle (within 6 hours of adding sources).*")

                reply = "\n".join(lines)
                follow_ups = ['Find patterns', 'Morning brief', 'Discover connections']
            except Exception as bse:
                reply = f"Could not retrieve brain status: {bse}"
            handled = True

        # -----------------------------------------------------------------
        # DISMISS CONNECTION
        # -----------------------------------------------------------------
        elif intent == "dismiss_connection":
            try:
                from services.curator_brain import curator_brain
                conn_id = params.get("connection_id")

                # If no ID given, show active connections so user can specify
                if conn_id is None:
                    connections = curator_brain.get_active_connections()
                    if not connections:
                        reply = "No active connections to dismiss right now."
                    else:
                        lines = ["Which connection would you like to dismiss? Say the number.\n"]
                        for i, c in enumerate(connections[:8], 1):
                            lines.append(f"{i}. {c['description']}")
                        reply = "\n".join(lines)
                else:
                    # User specified an ID — look it up by position (1-based) or raw ID
                    connections = curator_brain.get_active_connections()
                    target_id = None
                    try:
                        idx = int(conn_id)
                        # Try 1-based list position first
                        if 1 <= idx <= len(connections):
                            target_id = connections[idx - 1]["id"]
                        else:
                            # Fall back to raw DB id
                            target_id = idx
                    except (TypeError, ValueError):
                        pass

                    if target_id is not None and curator_brain.dismiss_connection(target_id):
                        reply = f"Done — I'll stop surfacing that connection. Thanks for the feedback; it helps me calibrate."
                    else:
                        reply = f"Couldn't find connection #{conn_id}. Try **@curator brain status** to see the numbered list."
                follow_ups = ['Brain status', 'Find patterns', 'Show my profile']
            except Exception as dce:
                reply = f"Couldn't dismiss connection: {dce}"
            handled = True

        # -----------------------------------------------------------------
        # APPROVE CONNECTION (thumbs up)
        # -----------------------------------------------------------------
        elif intent == "approve_connection":
            try:
                from services.curator_brain import curator_brain
                conn_id = params.get("connection_id")

                # If no ID given, show active connections so user can specify
                if conn_id is None:
                    connections = curator_brain.get_active_connections()
                    if not connections:
                        reply = "No active connections to confirm right now."
                    else:
                        lines = ["Which connection are you confirming? Say the number.\n"]
                        for i, c in enumerate(connections[:8], 1):
                            lines.append(f"{i}. {c['description']}")
                        reply = "\n".join(lines)
                else:
                    connections = curator_brain.get_active_connections()
                    target_id = None
                    try:
                        idx = int(conn_id)
                        if 1 <= idx <= len(connections):
                            target_id = connections[idx - 1]["id"]
                        else:
                            target_id = idx
                    except (TypeError, ValueError):
                        pass

                    if target_id is not None and curator_brain.thumbs_up_connection(target_id):
                        reply = f"Noted — I'll prioritize that connection in future briefs and overwatch. Good signal, thank you."
                    else:
                        reply = f"Couldn't find connection #{conn_id}. Try **@curator brain status** to see the numbered list."
                follow_ups = ['Brain status', 'Find patterns', 'Morning brief']
            except Exception as ace:
                reply = f"Couldn't confirm connection: {ace}"
            handled = True

        # -----------------------------------------------------------------
        # SHOW WEAKEST HYPOTHESIS (Curator Phase 4 — inverse query)
        # -----------------------------------------------------------------
        elif intent == "show_weakest_hypothesis":
            try:
                from services.curator_brain import curator_brain
                weak = curator_brain.get_weakest_hypothesis(chat_query.notebook_id)
                if not weak:
                    reply = (
                        "I don't have anything I'd flag as weak right now — "
                        "either the brain hasn't formed enough opinions yet, "
                        "or everything's holding up. Try again after more "
                        "sources land in your notebooks."
                    )
                else:
                    kind = weak["kind"]
                    conf_pct = int((weak["confidence"] or 0) * 100)
                    kind_label = {
                        "mental_model": "🧠 The notebook thesis I have",
                        "connection": "🔗 A cross-notebook connection I noticed",
                        "insight": "💡 An insight I flagged",
                    }.get(kind, "something")
                    lines = [
                        f"**{kind_label}** (curator confidence: {conf_pct}%)",
                        "",
                        f"> {weak['content']}",
                        "",
                        "I'm not sure about this one. If you can correct, "
                        "confirm, or dismiss it, that helps me sharpen.",
                    ]
                    # Plain-text suggested next step (compromise per Q3) —
                    # the user has to type the follow-up; no auto-invoke.
                    if kind == "mental_model" and weak.get("notebook_id"):
                        lines.append("")
                        lines.append(
                            "If you want me to dig in: *@research deep dive [topic]* "
                            "for fresh evidence, or *@curator devil's advocate* for counterarguments."
                        )
                    elif kind == "connection":
                        lines.append("")
                        lines.append(
                            f"If the link's wrong, say *dismiss connection {weak['subject_id']}*. "
                            f"If it's interesting, say *connection {weak['subject_id']} is useful*."
                        )
                    elif kind == "insight":
                        lines.append("")
                        lines.append(
                            "Say *@curator dismiss insight* if it's not useful — "
                            "I won't surface it again."
                        )
                    reply = "\n".join(lines)
                follow_ups = ["Brain status", "Devil's advocate", "Find patterns"]
            except Exception as e:
                reply = f"Couldn't find a weak hypothesis: {e}"
            handled = True

        # -----------------------------------------------------------------
        # SET VOICE (Curator Phase 6a — change narrative voice)
        # -----------------------------------------------------------------
        elif intent == "set_voice":
            from agents.curator import VALID_VOICES, VOICE_DESCRIPTIONS
            requested = (params.get("voice") or "").strip().lower().replace(" ", "_").replace("-", "_")
            # Best-effort normalization for the LLM's variations
            normalization = {
                "smart": "smart_colleague",
                "colleague": "smart_colleague",
                "executive": "executive_brief",
                "brief": "executive_brief",
                "analyst": "conversational_analyst",
                "conversational": "conversational_analyst",
                "casual": "conversational_analyst",
            }
            if requested not in VALID_VOICES and requested in normalization:
                requested = normalization[requested]
            if requested in VALID_VOICES:
                curator.update_config({"narrative_voice": requested})
                desc = VOICE_DESCRIPTIONS.get(requested, "")
                reply = f"Voice set to **{requested}** — {desc}. Your next morning brief will use it."
            else:
                opts = ", ".join(f"`{v}`" for v in sorted(VALID_VOICES))
                reply = (
                    f"I don't recognize that voice. Pick one of: {opts}. "
                    f"Say *@curator show voice* to see what each one sounds like."
                )
            follow_ups = ["Show voice options", "Morning brief", "Brain status"]
            handled = True

        # -----------------------------------------------------------------
        # SHOW VOICE (Curator Phase 6a — list current + available voices)
        # -----------------------------------------------------------------
        elif intent == "show_voice":
            from agents.curator import VALID_VOICES, VOICE_DESCRIPTIONS
            current = curator.narrative_voice
            lines = [f"Current voice: **{current}** — {VOICE_DESCRIPTIONS.get(current, '')}"]
            lines.append("")
            lines.append("Available voices:")
            for v in sorted(VALID_VOICES):
                marker = " (current)" if v == current else ""
                lines.append(f"  - **{v}**{marker}: {VOICE_DESCRIPTIONS.get(v, '')}")
            lines.append("")
            lines.append("Switch with: *@curator set voice [name]*")
            reply = "\n".join(lines)
            follow_ups = ["Set voice to smart colleague", "Set voice to executive brief", "Morning brief"]
            handled = True

        # -----------------------------------------------------------------
        # SHOW DRAFT (Curator Phase 6a — view anticipatory draft)
        # -----------------------------------------------------------------
        elif intent == "show_draft":
            try:
                from services.curator_brain import curator_brain
                if not chat_query.notebook_id:
                    reply = "Pick a notebook first — drafts are notebook-scoped."
                else:
                    draft = curator_brain.get_latest_unconsumed_draft(chat_query.notebook_id)
                    if not draft:
                        reply = (
                            "No pending draft for this notebook. Curator pre-drafts "
                            "Studio content for notebooks with ≥15 sources, a stable "
                            "thesis, and no recent Studio output — yours might not "
                            "qualify yet."
                        )
                    else:
                        curator_brain.mark_draft_consumed(draft["id"])
                        reply = (
                            f"Here's the draft I prepared (**{draft['kind']}**):\n\n"
                            f"---\n\n{draft['content_markdown']}\n\n---\n\n"
                            f"Say *@curator discard draft* if it's not useful — "
                            f"I'll back off on this notebook for a couple weeks."
                        )
                follow_ups = ["Morning brief", "Brain status"]
            except Exception as e:
                reply = f"Couldn't fetch the draft: {e}"
            handled = True

        # -----------------------------------------------------------------
        # DISCARD DRAFT (Curator Phase 6a — reject + cool off)
        # -----------------------------------------------------------------
        elif intent == "discard_draft":
            try:
                from services.curator_brain import curator_brain
                if not chat_query.notebook_id:
                    reply = "Pick a notebook first."
                else:
                    # Find the latest unconsumed OR most recently consumed draft
                    # — user might have read it, then decided to discard.
                    draft = curator_brain.get_latest_unconsumed_draft(chat_query.notebook_id)
                    if not draft:
                        draft = curator_brain.get_latest_draft(chat_query.notebook_id)
                    if not draft:
                        reply = "No recent draft for this notebook."
                    else:
                        curator_brain.mark_draft_discarded(draft["id"])
                        reply = (
                            f"Discarded. I won't draft for this notebook for the "
                            f"next 14 days — say *@curator show draft* again after "
                            f"that if you want me to start prepping content again."
                        )
                follow_ups = ["Morning brief"]
            except Exception as e:
                reply = f"Couldn't discard: {e}"
            handled = True

        # -----------------------------------------------------------------
        # SUPPRESS BRIEF TOPIC (Curator Phase 5 — mute a topic keyword)
        # -----------------------------------------------------------------
        elif intent == "suppress_brief_topic":
            topic = (params.get("topic") or "").strip()
            if not topic:
                reply = "What topic should I stop showing you? Try something like *@curator stop showing me crypto stories*."
            else:
                try:
                    from services.curator_brain import curator_brain
                    curator_brain.suppress_topic(topic, notebook_id=chat_query.notebook_id)
                    reply = (
                        f"Got it — won't surface stories about **\"{topic}\"** in your briefs anymore. "
                        f"Say *`@curator unmute {topic}`* to undo."
                    )
                    follow_ups = ["What topics am I muting", "Morning brief", "Brain status"]
                except Exception as e:
                    reply = f"Couldn't mute that topic: {e}"
            handled = True

        # -----------------------------------------------------------------
        # UNSUPPRESS BRIEF TOPIC
        # -----------------------------------------------------------------
        elif intent == "unsuppress_brief_topic":
            topic = (params.get("topic") or "").strip()
            if not topic:
                reply = "Which topic should I unmute? Try *@curator unmute crypto*."
            else:
                try:
                    from services.curator_brain import curator_brain
                    removed = curator_brain.unsuppress_topic(topic, notebook_id=chat_query.notebook_id)
                    if removed:
                        reply = f"Unmuted **\"{topic}\"** — stories about it will appear in briefs again."
                    else:
                        reply = f"I didn't have a mute on **\"{topic}\"** for this notebook."
                    follow_ups = ["What topics am I muting", "Morning brief"]
                except Exception as e:
                    reply = f"Couldn't unmute: {e}"
            handled = True

        # -----------------------------------------------------------------
        # LIST SUPPRESSED TOPICS
        # -----------------------------------------------------------------
        elif intent == "list_suppressed_topics":
            try:
                from services.curator_brain import curator_brain
                rows = curator_brain.list_suppressions(chat_query.notebook_id)
                if not rows:
                    reply = "You haven't muted any topics. Say *@curator stop showing me X* if you want to mute one."
                else:
                    lines = [f"You've muted these topics:"]
                    for r in rows:
                        scope = "(global)" if r["notebook_id"] is None else "(this notebook)"
                        lines.append(f"  - **{r['topic_key']}** {scope}")
                    lines.append(f"\nSay *@curator unmute X* to undo one.")
                    reply = "\n".join(lines)
                follow_ups = ["Morning brief", "Brain status"]
            except Exception as e:
                reply = f"Couldn't fetch your mutes: {e}"
            handled = True

        # -----------------------------------------------------------------
        # FALLBACK: CROSS-NOTEBOOK RAG SEARCH (default behavior)
        # -----------------------------------------------------------------
        if not handled:
            yield f"data: {json.dumps({'type': 'status', 'message': f'{curator_name} searching across notebooks...', 'query_type': 'curator'})}\n\n"

            excluded = []
            try:
                oversight = cfg.get("oversight", {})
                if isinstance(oversight, dict):
                    excluded = [e for e in oversight.get("excluded_notebook_ids", []) if not e.startswith("name:")]
            except Exception as _e:
                logger.warning(f"[chat] Failed to load oversight config: {_e}")

            search_result = await cross_notebook_search.search(
                query=chat_query.question,
                exclude_notebook_ids=excluded or None,
                top_k=10,
                top_k_per_notebook=4,
            )
            results = search_result["results"]
            nb_count = search_result["notebooks_searched"]

            yield f"data: {json.dumps({'type': 'status', 'message': f'{curator_name} found {len(results)} results across {nb_count} notebooks', 'query_type': 'curator'})}\n\n"

            if not results:
                reply = await curator.conversational_reply(
                    message=chat_query.question,
                    notebook_id=chat_query.notebook_id,
                )
            else:
                context = cross_notebook_search.build_context(results, max_chars=8000)

                citations = []
                seen = set()
                for i, r in enumerate(results):
                    key = (r["source_id"], r["chunk_index"])
                    if key in seen: continue
                    seen.add(key)
                    citations.append({
                        "number": len(citations) + 1,
                        "source_id": r["source_id"],
                        "filename": f"{r['notebook_title']} / {r['filename']}",
                        "chunk_index": r["chunk_index"],
                        "text": r["text"][:300],
                        "snippet": r["text"][:120],
                        "confidence": max(0, 1.0 - r.get("_distance", 0.5)),
                        "confidence_level": "high" if r.get("_distance", 1) < 0.4 else "medium",
                    })

                yield f"data: {json.dumps({'type': 'citations', 'citations': citations, 'sources': list(set(r['filename'] for r in results)), 'low_confidence': len(citations) < 2})}\n\n"

                prompt = f"""You are {curator_name}, a cross-notebook research curator.

The user asked: {chat_query.question}

Here is relevant content found across {nb_count} notebooks:

{context}

Synthesize a comprehensive answer that:
1. Draws connections across notebooks
2. Cites sources using [1], [2], etc. matching the citation numbers
3. Notes any contradictions or complementary perspectives
4. Is concise but thorough

Answer:"""

                try:
                    response = await ollama_service.generate(
                        prompt=prompt,
                        system=f"You are {curator_name}, a research curator who synthesizes knowledge across multiple research notebooks. Personality: {curator.personality}",
                        model=settings.ollama_model,
                        temperature=0.5,
                    )
                    reply = response.get("response", "I couldn't generate a synthesis. Please try rephrasing your question.")
                except Exception as gen_err:
                    reply = f"Synthesis generation failed: {gen_err}"

        # Stream reply
        chunk_size = 12
        for i in range(0, len(reply), chunk_size):
            yield f"data: {json.dumps({'type': 'token', 'content': reply[i:i+chunk_size]})}\n\n"

        # Note: pending overwatch asides (Phase 3c) are consumed by
        # generate_overwatch_aside on the regular RAG chat path. The
        # @curator chat path already injects dissent_context into the
        # prompt (see conversational_reply) so the LLM can surface it
        # naturally. Avoiding double-consume here.

        yield _done_event()

        # Log the interaction
        try:
            log_chat_qa(chat_query.notebook_id, f"@curator {chat_query.question}", reply, [r["source_id"] for r in results] if results else [])
        except Exception as _e:
            logger.debug(f"[chat] log_chat_qa failed (non-fatal): {_e}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'error': f'Curator error: {e}'})}\n\n"
