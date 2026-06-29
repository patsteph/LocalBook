"""CuratorSynthesisMixin — extracted from the former agents/curator.py (Wave 3 split)."""
from ._models import *  # noqa: F401,F403


class CuratorSynthesisMixin:
    async def synthesize_across_notebooks(
        self,
        query: str,
        notebook_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Answer questions that span multiple notebooks.
        E.g., "What themes appear in both my Pepsi and Coca-Cola research?"
        """
        # Get all notebooks if not specified
        if not notebook_ids:
            notebooks = await notebook_store.list()
            notebook_ids = [n["id"] for n in notebooks]
        
        # Search across all specified notebooks using Curator's cross-notebook access
        all_results = []
        for nb_id in notebook_ids:
            results = await memory_store.search_archival_memory_async(
                query=query,
                namespace=AgentNamespace.CURATOR,
                notebook_id=nb_id,
                cross_notebook=True,
                limit=10
            )
            for r in results:
                all_results.append({
                    "notebook_id": nb_id,
                    "content": r.entry.content,
                    "score": r.combined_score
                })
        
        # Sort by score and take top results
        all_results.sort(key=lambda x: x["score"], reverse=True)
        top_results = all_results[:20]
        
        if not top_results:
            return {
                "synthesis": "No relevant content found across the specified notebooks.",
                "sources": [],
                "notebooks_searched": notebook_ids
            }
        
        # Use LLM to synthesize
        context = "\n".join([
            f"[Notebook {r['notebook_id'][:8]}]: {r['content'][:500]}"
            for r in top_results
        ])
        
        try:
            prompt = f"""You are {self.name}, synthesizing information across multiple research notebooks.

Query: {query}

Content from multiple notebooks:
{context}

Provide a synthesis that:
1. Identifies common themes across notebooks
2. Notes any contradictions or differences
3. Highlights connections the user might not have noticed

Be concise and cite which notebook each insight comes from."""

            response = await ollama_service.generate(
                prompt=prompt,
                system=f"You are {self.name}, a research curator. Personality: {self.personality}",
                model=settings.ollama_model,
                temperature=0.5
            )
            
            synthesis = response.get("response", "Unable to synthesize.")
            
            # Store synthesis in Curator namespace
            entry = ArchivalMemoryEntry(
                content=f"Cross-notebook synthesis for: {query}\n\n{synthesis}",
                content_type="cross_notebook_synthesis",
                source_type=MemorySourceType.SYSTEM,
                topics=["synthesis", "cross_notebook"],
                importance=MemoryImportance.MEDIUM,
            )
            await memory_store.add_archival_memory_async(entry, namespace=AgentNamespace.CURATOR)
            
            return {
                "synthesis": synthesis,
                "sources": [{"notebook_id": r["notebook_id"], "score": r["score"]} for r in top_results[:5]],
                "notebooks_searched": notebook_ids
            }
        except Exception as e:
            logger.error(f"Cross-notebook synthesis failed: {e}")
            return {
                "synthesis": f"Error during synthesis: {str(e)}",
                "sources": [],
                "notebooks_searched": notebook_ids
            }

    async def conversational_reply(
        self,
        message: str,
        notebook_id: Optional[str] = None,
        history: List[Dict[str, str]] = None
    ) -> str:
        """
        Handle a conversational message from the user in the Curator tab.
        The Curator has cross-notebook awareness and can synthesize, advise,
        play devil's advocate, and discuss research strategy.
        """
        history = history or []

        # Intent detection: morning brief recall
        msg_lower = message.lower().strip()

        # 2026-06-07 — direct shortcut for the anticipatory-draft pill.
        # The CuratorPanel sends `'show draft'` / `'discard draft'` straight
        # through this conversational endpoint, which previously fell
        # through to the generic LLM clarifier. Match the keyword and route
        # to the brain directly, mirroring the `_stream_curator` intent
        # handlers in chat.py.
        draft_show_triggers = (
            "show draft", "show me the draft", "open the draft",
            "what did you draft", "show the draft", "view draft",
        )
        draft_discard_triggers = (
            "discard draft", "discard the draft", "trash that draft",
            "don't want that draft", "no thanks on the draft", "reject draft",
        )
        if any(trigger in msg_lower for trigger in draft_show_triggers) and notebook_id:
            try:
                from services.curator_brain import curator_brain
                draft = curator_brain.get_latest_unconsumed_draft(notebook_id)
                if not draft:
                    return (
                        "No pending draft for this notebook. Curator pre-drafts "
                        "Studio content for notebooks with ≥15 sources, a stable "
                        "thesis, and no recent Studio output — yours might not "
                        "qualify yet."
                    )
                curator_brain.mark_draft_consumed(draft["id"])
                return (
                    f"Here's the draft I prepared (**{draft['kind']}**):\n\n"
                    f"---\n\n{draft['content_markdown']}\n\n---\n\n"
                    f"Say *@curator discard draft* if it's not useful — "
                    f"I'll back off on this notebook for a couple weeks."
                )
            except Exception as _e:
                logger.debug(f"[curator.conversational_reply] show_draft shortcut failed: {_e}")
                return f"Couldn't fetch the draft: {_e}"
        if any(trigger in msg_lower for trigger in draft_discard_triggers) and notebook_id:
            try:
                from services.curator_brain import curator_brain
                draft = curator_brain.get_latest_unconsumed_draft(notebook_id) or curator_brain.get_latest_draft(notebook_id)
                if not draft:
                    return "No recent draft for this notebook."
                curator_brain.mark_draft_discarded(draft["id"])
                return (
                    "Discarded. I won't draft for this notebook for the next "
                    "14 days — say *@curator show draft* again after that."
                )
            except Exception as _e:
                logger.debug(f"[curator.conversational_reply] discard_draft shortcut failed: {_e}")
                return f"Couldn't discard the draft: {_e}"

        brief_triggers = [
            "morning brief", "show brief", "show me the brief",
            "today's brief", "todays brief", "daily brief",
            "what did i miss", "what happened", "catch me up",
            "recap", "show the morning brief", "display the morning brief",
            "display morning brief", "recall brief",
        ]
        if any(trigger in msg_lower for trigger in brief_triggers):
            try:
                import json
                from pathlib import Path
                from services.event_logger import event_logger
                
                brief_dir = Path(event_logger.data_dir) / "memory"
                today_str = datetime.utcnow().strftime("%Y-%m-%d")
                brief_file = brief_dir / f"morning_brief_{today_str}.json"
                
                if not brief_file.exists():
                    brief_files = sorted(brief_dir.glob("morning_brief_*.json"), reverse=True)
                    if brief_files:
                        brief_file = brief_files[0]
                
                if brief_file.exists():
                    brief = json.loads(brief_file.read_text())
                    narrative = brief.get("narrative", "")
                    if narrative:
                        brief_date_raw = brief_file.stem.replace("morning_brief_", "")
                        try:
                            from datetime import datetime as _dt
                            brief_date = _dt.strptime(brief_date_raw, "%Y-%m-%d").strftime("%B %d, %Y")
                        except Exception:
                            brief_date = brief_date_raw
                        return f"Here's your brief from **{brief_date}**:\n\n---\n\n{narrative}\n\n---\n*Want me to dig deeper into any of these topics?*"
                    else:
                        # Fallback: reconstruct from notebook data
                        notebooks = brief.get("notebooks", [])
                        if notebooks:
                            try:
                                from datetime import datetime as _dt
                                _fd = _dt.strptime(brief_file.stem.replace('morning_brief_', ''), "%Y-%m-%d").strftime("%B %d, %Y")
                            except Exception:
                                _fd = brief_file.stem.replace('morning_brief_', '')
                            lines = [f"Here's your brief from **{_fd}**:\n"]
                            for nb in notebooks:
                                parts = []
                                added = nb.get('items_added', 0)
                                if added > 0:
                                    parts.append(f"{added} new source{'s' if added != 1 else ''}")
                                interactions = nb.get('interactions_since', 0)
                                if interactions > 0:
                                    parts.append(f"{interactions} interaction{'s' if interactions != 1 else ''}")
                                pending = nb.get('pending_approval', 0)
                                if pending > 0:
                                    parts.append(f"{pending} pending review")
                                summary = ", ".join(parts) if parts else "no recent activity"
                                lines.append(f"**{nb.get('name', 'Notebook')}**: {summary}")
                                for story in (nb.get('recent_stories') or [])[:3]:
                                    lines.append(f"  - \"{story.get('title', '')}\"")
                            return "\n".join(lines) + "\n\n---\n*Want me to dig deeper into any of these topics?*"
                
                return "I don't have a saved morning brief yet. I'll generate one next time you open LocalBook after being away!"
            except Exception as e:
                logger.error(f"Morning brief recall failed: {e}")
        
        # Build context from all notebooks
        notebooks = await notebook_store.list()
        notebook_context = ""
        if notebooks:
            nb_lines = []
            for nb in notebooks[:10]:
                nb_lines.append(f"- {nb.get('name', nb.get('title', 'Untitled'))} (id: {nb['id'][:8]}...)")
            notebook_context = f"Available notebooks:\n" + "\n".join(nb_lines)
        
        # If a specific notebook is referenced, search it for context
        search_context = ""
        if notebook_id:
            try:
                results = await memory_store.search_archival_memory_async(
                    query=message,
                    namespace=AgentNamespace.COLLECTOR,
                    notebook_id=notebook_id,
                    limit=5
                )
                if results:
                    search_context = "\nRelevant content from current notebook:\n" + "\n".join(
                        f"- {r.entry.content[:200]}" for r in results
                    )
            except Exception as _e:
                logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Curator Phase 3a: mental-model context injection. When the
        # current notebook has an inferred mental model with reasonable
        # confidence, surface it so the curator's reply can lean on
        # what we already understand about the user's project. Empty /
        # low-confidence / model-missing cases fall through silently.
        mental_model_context = ""
        if notebook_id:
            try:
                from services.curator_brain import curator_brain as _cb
                _mm = _cb.get_mental_model(notebook_id)
                # Phase 3b hotfix: dropped the >0.3 confidence floor to
                # match the stance scorer. A low-confidence inferred
                # mental model is still useful context for chat replies.
                if (
                    _mm
                    and (_mm.get("thesis") or _mm.get("stage"))
                ):
                    _lines = []
                    if _mm.get("thesis"):
                        _lines.append(f"  - thesis: {_mm['thesis']}")
                    if _mm.get("stage"):
                        _lines.append(f"  - stage: {_mm['stage']}")
                    if _mm.get("blocked_on"):
                        _lines.append(f"  - blocked_on: {_mm['blocked_on']}")
                    if _mm.get("recent_focus"):
                        _lines.append(f"  - recent_focus: {_mm['recent_focus']}")
                    if _mm.get("goals"):
                        _goals = ", ".join(_mm["goals"][:3])
                        _lines.append(f"  - goals: {_goals}")
                    if _lines:
                        mental_model_context = (
                            "\nMental model for this notebook (use as context, do not repeat verbatim):\n"
                            + "\n".join(_lines)
                        )
            except Exception as _e:
                logger.debug(f"[curator] mental_model context fetch: {_e}")

        # Curator Phase 3c: ambient dissent context. Injects top
        # contradicting sources into the system prompt; LLM is
        # instructed to mention them ONLY if relevant to the user's
        # question. Gated by nag budget. The pending overwatch aside
        # (event-bus triggered) is surfaced separately by _stream_curator
        # via curator_aside, not here.
        dissent_context = ""
        if notebook_id:
            try:
                from services.curator_brain import curator_brain as _cb
                # Dissent in chat is medium priority — relevant to user query
                # but not urgent enough to bypass the daily cap.
                if _cb.can_fire_nag("dissent_ambient_in_chat", notebook_id, priority="medium"):
                    dissenters = _cb.get_dissenting_sources(notebook_id, limit=2)
                    if dissenters:
                        # Best-effort: attach source titles for readability.
                        from storage.source_store import source_store as _ss
                        _dlines = []
                        for d in dissenters:
                            title = d.get("source_id")
                            try:
                                src = await _ss.get(d["source_id"])
                                if src:
                                    title = (
                                        src.get("filename") or src.get("title")
                                        or src.get("url") or title
                                    )
                            except Exception:
                                pass
                            _dlines.append(
                                f"  - \"{str(title)[:100]}\": {d.get('rationale', '')[:200]}"
                            )
                        if _dlines:
                            dissent_context = (
                                "\nDissenting evidence in this notebook "
                                "(mention ONLY if the user's question touches the thesis; "
                                "stay silent otherwise — do not force-surface this):\n"
                                + "\n".join(_dlines)
                            )
                            _cb.record_nag("dissent_ambient_in_chat", notebook_id=notebook_id)
            except Exception as _e:
                logger.debug(f"[curator] dissent context fetch: {_e}")

        # Search across ALL notebooks for cross-references (PARALLEL)
        cross_context = ""
        if notebooks and len(notebooks) > 1:
            try:
                import asyncio
                other_nbs = [nb for nb in notebooks[:5] if nb["id"] != notebook_id]
                
                async def _search_nb(nb):
                    return nb, await asyncio.to_thread(
                        memory_store.search_archival_memory,
                        query=message,
                        namespace=AgentNamespace.COLLECTOR,
                        notebook_id=nb["id"],
                        cross_notebook=True,
                        limit=3
                    )
                
                nb_results = await asyncio.gather(
                    *[_search_nb(nb) for nb in other_nbs],
                    return_exceptions=True
                )
                for item in nb_results:
                    if isinstance(item, Exception):
                        continue
                    nb, results = item
                    for r in results:
                        if r.combined_score > 0.3:
                            nb_name = nb.get("name", nb.get("title", "Untitled"))
                            cross_context += f"\n- [{nb_name}]: {r.entry.content[:200]}"
            except Exception as _e:
                logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        if cross_context:
            cross_context = f"\nCross-notebook connections:\n{cross_context}"
        
        # Build conversation history for LLM
        history_text = ""
        if history:
            for msg in history[-6:]:  # Last 6 messages
                role = msg.get("role", "user")
                content = msg.get("content", "")[:500]
                history_text += f"\n{role.upper()}: {content}"
        
        # Get user profile for personalization
        try:
            from api.settings import get_user_profile_sync, build_user_context
            user_profile = get_user_profile_sync()
            user_context = build_user_context(user_profile)
        except Exception:
            user_context = ""
        
        # Pull core memory for deeper user awareness (ReMe integration)
        core_memory_block = ""
        try:
            core_memory = memory_store.load_core_memory()
            core_memory_block = core_memory.to_prompt_block()
        except Exception as _e:
            logger.warning(f"[curator] {type(_e).__name__}: {_e}")
        
        system_prompt = f"""You are {self.name}, the Curator of a research system called LocalBook.
Your personality: {self.personality}

Your role:
- You oversee ALL notebooks and have cross-notebook awareness
- You can synthesize information across research areas
- You can play devil's advocate and find counterarguments
- You advise on research strategy and identify gaps
- You are a guide and advisor, not a search engine

{user_context}

{core_memory_block}

{notebook_context}
{search_context}
{cross_context}
{mental_model_context}
{dissent_context}

Rules:
- Be conversational and concise (2-4 sentences typical)
- Proactively mention cross-notebook connections when relevant
- If asked about something specific, search your knowledge
- If you don't have the information, say so honestly
- Sign off naturally, no forced personality"""

        prompt = message
        if history_text:
            prompt = f"Conversation so far:{history_text}\n\nUSER: {message}"
        
        try:
            response = await ollama_service.generate(
                prompt=prompt,
                system=system_prompt,
                model=settings.ollama_model,
                temperature=0.5
            )
            reply_text = response.get("response", "I'm having trouble processing that right now.")
            # Curator Phase 1: emit observability event so the brain's
            # consumer loop knows the user just talked to the curator.
            try:
                from services.curator_event_bus import event_bus
                event_bus.emit_now(
                    actor="@curator",
                    action="conversational_reply",
                    notebook_id=notebook_id,
                    payload={
                        "message_chars": len(message),
                        "reply_chars": len(reply_text),
                        "had_cross_context": bool(cross_context),
                    },
                    outcome="success",
                )
            except Exception as _e:
                pass
            return reply_text
        except Exception as e:
            logger.error(f"Curator chat failed: {e}")
            try:
                from services.curator_event_bus import event_bus
                event_bus.emit_now(
                    actor="@curator",
                    action="conversational_reply",
                    notebook_id=notebook_id,
                    payload={"error": str(e)[:200]},
                    outcome="failed",
                )
            except Exception:
                pass
            return "I'm experiencing a technical issue. Please try again."
