"""Chat API endpoints"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
from services.rag_engine import rag_engine
from services.query_orchestrator import get_orchestrator
from services.event_logger import log_chat_qa
import json

router = APIRouter()


# ─── Agent Help Text (shown for ?, /help, help) ──────────────────────────────

_CURATOR_HELP = """**@curator — Your cross-notebook research advisor**

| Command | What it does |
|---|---|
| `@curator what's new?` | Generate your **Morning Brief** — a summary of activity across all notebooks |
| `@curator find patterns` | Discover **cross-notebook patterns** — shared entities, themes, contradictions |
| `@curator play devil's advocate` | Get **counterarguments** to your thesis or the notebook's main claims |
| `@curator <any question>` | **Cross-notebook search** — synthesizes answers from all your notebooks |
| `@curator change your name to <name>` | Rename the Curator |
| `@curator be more <personality>` | Change the Curator's personality/tone |
| `@curator enable/disable overwatch` | Toggle whether the Curator chimes in during regular chat |
| `@curator exclude <notebook>` | Exclude a notebook from cross-notebook operations |
| `@curator show profile` | Show current Curator configuration |
| `@curator ?` | Show this help |"""

_COLLECTOR_HELP = """**@collector — Your automated content collection agent**

| Command | What it does |
|---|---|
| `@collector add <URL>` | Add a **URL as a monitored source** (RSS feed or web page) |
| `@collector remove <URL>` | Remove a source |
| `@collector add keyword <topic>` | Track a **news keyword** for alerts |
| `@collector set intent <description>` | Set the notebook's **collection intent/purpose** |
| `@collector set subject <name>` | Set the **research subject** |
| `@collector set focus <areas>` | Set or add **focus areas** |
| `@collector exclude <topics>` | Exclude topics from collection |
| `@collector set schedule daily/hourly/weekly` | Set **collection frequency** |
| `@collector set mode auto/manual/hybrid` | Set collection mode |
| `@collector set approval auto/review/mixed` | Set approval mode |
| `@collector collect now` | Trigger an **immediate collection run** |
| `@collector show pending` | Show items **awaiting your approval** |
| `@collector approve all` | Approve all pending items |
| `@collector source health` | Check **source health** — find broken or failing sources |
| `@collector show history` | Show recent **collection run history** |
| `@collector show status` | Overview of Collector configuration and stats |
| `@collector show profile` | Show full Collector profile |
| `@collector ?` | Show this help |"""

_RESEARCH_HELP = """**@research — Your web research agent**

| Command | What it does |
|---|---|
| `@research <query>` | **Web search** — find and summarize results from across the web |
| `@research <query> site:arxiv.org` | **Site search** — search a specific domain |
| `@research deep dive <query>` | **Deep dive** — multi-source, quality-filtered, thorough research |
| `@research ?` | Show this help |

**Deep dive modifiers:** You can add quality criteria like *"peer reviewed"*, *"last 7 days"*, *"minimum 1000 words"* and the agent will apply them as filters."""

_STUDIO_HELP = """**@studio — Create content from your conversation**

| Command | What it does |
|---|---|
| `@studio make a podcast on this` | Generate a **podcast/audio** based on the current conversation |
| `@studio create a study guide` | Generate a **document** (brief, guide, cheat sheet, etc.) |
| `@studio quiz me on this` | Generate a **quiz** to test your understanding |
| `@studio visualize this` | Create a **diagram, flowchart, or mind map** |
| `@studio make a video explainer` | Create a **video** with narration |
| `@studio ?` | Show this help |

**Tips:** Describe what you want naturally — specify format, style, duration, hosts, difficulty, etc. The conversation context is included automatically."""


def _is_help_request(question: str) -> bool:
    """Check if the user is asking for help."""
    q = question.strip().lower()
    return q in ('?', '/help', 'help', '?help', 'commands', '/commands', 'what can you do', 'what can you do?')


def _stream_help(text: str, agent_name: str, agent_type: str):
    """Generator that streams a help message as SSE events."""
    import json as _json
    chunk_size = 40
    for i in range(0, len(text), chunk_size):
        yield f"data: {_json.dumps({'type': 'token', 'content': text[i:i+chunk_size]})}\n\n"
    yield f"data: {_json.dumps({'type': 'done', 'follow_up_questions': [], 'agent_name': agent_name, 'agent_type': agent_type})}\n\n"


class ChatQuery(BaseModel):
    """Chat query request - matches frontend ChatQuery interface"""
    notebook_id: str
    question: str  # Frontend uses 'question', not 'query'
    source_ids: Optional[List[str]] = None
    top_k: Optional[int] = 4  # Reduced from 5 for faster LLM response
    enable_web_search: Optional[bool] = False
    llm_provider: Optional[str] = None
    deep_think: Optional[bool] = False  # Enable Deep Think mode with chain-of-thought reasoning
    use_orchestrator: Optional[bool] = True  # v0.60: Auto-detect complex queries and decompose
    target: Optional[str] = None  # v1.4: @mention routing — 'curator', 'collector', 'studio', or None for default RAG
    chat_context: Optional[str] = None  # v1.5: @studio — recent conversation context for content generation


class WebSource(BaseModel):
    """Web search result source"""
    title: str
    snippet: str
    url: str


class Citation(BaseModel):
    """Citation model - matches frontend Citation interface"""
    number: int
    source_id: str
    filename: str  # Frontend expects 'filename', not 'source_title'
    chunk_index: int
    text: str
    snippet: str  # Short preview of the text
    page: Optional[int] = None
    confidence: float = 0.0
    confidence_level: str = "medium"  # 'high', 'medium', 'low'


class ChatResponse(BaseModel):
    """Chat response - matches frontend ChatResponse interface"""
    answer: str
    citations: List[Citation]
    sources: List[str]
    web_sources: Optional[List[WebSource]] = None
    follow_up_questions: Optional[List[str]] = None
    low_confidence: Optional[bool] = False  # True when < 3 citations found
    memory_used: Optional[List[str]] = None  # Types of memory used: "core_context", "retrieved_memories"
    memory_context_summary: Optional[str] = None  # Brief summary of memory context used


@router.post("/query", response_model=ChatResponse)
async def query(chat_query: ChatQuery):
    """Query the RAG system
    
    v0.60: Automatically detects complex queries and uses orchestrator for decomposition.
    """
    # Clear visual cache when new question is asked
    from services.visual_cache import visual_cache
    await visual_cache.clear_notebook(chat_query.notebook_id)
    
    try:
        # v0.60: Use orchestrator for complex query detection and decomposition
        if chat_query.use_orchestrator:
            orchestrator = get_orchestrator(rag_engine)
            complexity = orchestrator.classify_complexity(chat_query.question)
            
            if complexity == 'complex':
                # Use full orchestration for complex queries
                result = await orchestrator.process(
                    query=chat_query.question,
                    notebook_id=chat_query.notebook_id,
                    llm_provider=chat_query.llm_provider or "ollama"
                )
                return result
        
        # Standard path for simple/moderate queries
        result = await rag_engine.query(
            notebook_id=chat_query.notebook_id,
            question=chat_query.question,
            source_ids=chat_query.source_ids,
            top_k=chat_query.top_k or 4,
            enable_web_search=chat_query.enable_web_search,
            llm_provider=chat_query.llm_provider
        )
        
        # Log Q&A for memory consolidation (fire-and-forget)
        try:
            sources_used = [c.get("source_id", "") for c in (result.get("citations") or [])] if isinstance(result, dict) else [c.source_id for c in getattr(result, 'citations', [])]
            log_chat_qa(chat_query.notebook_id, chat_query.question, result.answer if hasattr(result, 'answer') else result.get("answer", ""), sources_used)
        except Exception:
            pass
        
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query/stream")
async def query_stream(chat_query: ChatQuery):
    """Query the RAG system with streaming response.
    
    Routes to specialized agents when target is set via @mention:
    - target='curator': Cross-notebook synthesis via Curator agent
    - target='collector': Collection status/commands via Collector
    - target=None: Default RAG pipeline
    """
    
    # @mention routing — delegate to specialized agent streams
    if chat_query.target == "curator":
        return StreamingResponse(
            _stream_curator(chat_query),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    if chat_query.target == "collector":
        return StreamingResponse(
            _stream_collector(chat_query),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    if chat_query.target == "research":
        return StreamingResponse(
            _stream_research(chat_query),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    if chat_query.target == "studio":
        return StreamingResponse(
            _stream_studio(chat_query),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    
    # Fallback intent detection: auto-route cross-notebook queries to Curator
    # Uses fast regex first; only invokes LLM classifier if regex is inconclusive
    if chat_query.target is None:
        from agents.supervisor import is_cross_notebook_query
        if is_cross_notebook_query(chat_query.question):
            print(f"[Chat] Auto-routing cross-notebook query to Curator: '{chat_query.question[:60]}...'")
            return StreamingResponse(
                _stream_curator(chat_query),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
    
    # CRITICAL: Clear visual cache for this notebook when new question is asked
    # This prevents stale visuals from a previous question being shown
    from services.visual_cache import visual_cache
    cleared = await visual_cache.clear_notebook(chat_query.notebook_id)
    if cleared > 0:
        print(f"[Chat] Cleared {cleared} stale visual cache entries for notebook {chat_query.notebook_id}")
    
    async def generate():
        answer_parts = []
        sources_used = []
        try:
            async for chunk in rag_engine.query_stream(
                notebook_id=chat_query.notebook_id,
                question=chat_query.question,
                source_ids=chat_query.source_ids,
                top_k=chat_query.top_k or 4,
                llm_provider=chat_query.llm_provider,
                deep_think=chat_query.deep_think or False
            ):
                if chunk.get("type") == "answer_chunk":
                    answer_parts.append(chunk.get("content", ""))
                elif chunk.get("type") == "citations":
                    sources_used = [c.get("source_id", "") for c in chunk.get("citations", [])]
                yield f"data: {json.dumps(chunk)}\n\n"
            # Log the completed Q&A interaction for memory consolidation
            try:
                log_chat_qa(chat_query.notebook_id, chat_query.question, "".join(answer_parts), sources_used)
            except Exception:
                pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


async def _stream_curator(chat_query: ChatQuery):
    """Stream a Curator response in SSE format.
    
    LLM-based NLP intent router — anything you can do in the Curator settings
    panel or cross-notebook features, you can do here via natural language.
    """
    from agents.curator import curator
    from services.cross_notebook_search import cross_notebook_search
    from services.ollama_client import ollama_client
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
        # LLM-based Intent Classification
        # =================================================================
        classified = await classify_intent(q, "curator", ollama_client)
        intent = classified["intent"]
        params = classified.get("params", {})
        handled = False

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
                    except Exception:
                        pass

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
                    except Exception:
                        pass

                if saved_wrap and saved_wrap.get("narrative"):
                    reply = saved_wrap["narrative"]
                    if saved_wrap.get("cross_notebook_insight"):
                        reply += f"\n\n**Cross-Notebook Insight:** {saved_wrap['cross_notebook_insight']}"
                else:
                    wrap = await curator.generate_weekly_wrap_up()
                    reply = wrap.narrative if wrap.narrative else "Not enough activity this week for a wrap up."
                    if wrap.cross_notebook_insight:
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
                    lines = [f"**Cross-Notebook Patterns ({len(insights)} found):**\n"]
                    for ins in insights[:8]:
                        lines.append(f"- **{ins.entity}** ({ins.insight_type}): {ins.summary} — notebooks: {', '.join(ins.notebooks[:3])}")
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
                        lines.append(f"- {cp}")
                reply = "\n".join(lines) if lines else "I couldn't find strong counterarguments. Your thesis may be well-supported!"
                follow_ups = ['Strengthen my thesis', 'Find supporting evidence', 'Show related patterns']
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
            follow_ups = ['Change your name', 'Change your personality', 'Disable overwatch']
            handled = True

        # -----------------------------------------------------------------
        # NOTE THEMES → COLLECTOR BRIDGE
        # -----------------------------------------------------------------
        elif intent == "note_themes":
            yield f"data: {json.dumps({'type': 'status', 'message': f'{curator_name} analyzing your notes...', 'query_type': 'curator'})}\n\n"
            try:
                result = await curator.suggest_collector_keywords_from_notes(notebook_id)
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
                reply = "\n".join(lines)
                follow_ups = ['Apply these suggestions', 'Show collector profile', 'Discover patterns']
            except Exception as e:
                reply = f"Failed to analyze notes: {e}"
                follow_ups = []
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
            except Exception:
                pass

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
                    response = await ollama_client.generate(
                        prompt=prompt,
                        system=f"You are {curator_name}, a research curator who synthesizes knowledge across multiple research notebooks. Personality: {curator.personality}",
                        model=settings.default_model,
                        temperature=0.5,
                    )
                    reply = response.get("response", "I couldn't generate a synthesis. Please try rephrasing your question.")
                except Exception as gen_err:
                    reply = f"Synthesis generation failed: {gen_err}"

        # Stream reply
        chunk_size = 12
        for i in range(0, len(reply), chunk_size):
            yield f"data: {json.dumps({'type': 'token', 'content': reply[i:i+chunk_size]})}\n\n"

        yield _done_event()

        # Log the interaction
        try:
            log_chat_qa(chat_query.notebook_id, f"@curator {chat_query.question}", reply, [r["source_id"] for r in results] if results else [])
        except Exception:
            pass

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'error': f'Curator error: {e}'})}\n\n"


async def _stream_collector(chat_query: ChatQuery):
    """Stream a Collector response in SSE format.
    
    LLM-based NLP intent router — anything you can do in the Collector settings
    panel, you can do here via natural language.
    """
    import re as _re
    from storage.source_store import source_store
    from agents.collector import get_collector, CollectionMode, ApprovalMode
    from services.ollama_client import ollama_client
    from services.intent_classifier import classify_intent

    collector_agent = get_collector(chat_query.notebook_id)
    collector_name = collector_agent.config.name or "Collector"
    notebook_id = chat_query.notebook_id
    q = chat_query.question

    # ── Help shortcut (no LLM call) ──
    if _is_help_request(q):
        for chunk in _stream_help(_COLLECTOR_HELP, collector_name, "collector"):
            yield chunk
        return

    yield f"data: {json.dumps({'type': 'status', 'message': f'{collector_name} processing...', 'query_type': 'collector'})}\n\n"

    try:
        reply = ""
        follow_ups = ['Show my collection status', 'Find new sources about this topic', 'What sources need attention?']
        config = collector_agent.get_config()

        # Helper: parse frequency from text
        def _parse_freq(text: str) -> str:
            t = str(text).lower()
            if "hour" in t: return "hourly"
            if "day" in t or "daily" in t: return "daily"
            if "week" in t or "weekly" in t: return "weekly"
            return "daily"

        # Helper: notify curator
        def _notify_curator(msg: str):
            try:
                from storage.memory_store import memory_store, AgentNamespace
                from models.memory import ArchivalMemoryEntry, MemorySourceType, MemoryImportance
                memory_store.add_archival_memory(ArchivalMemoryEntry(
                    content=msg, source_type=MemorySourceType.AGENT_GENERATED,
                    importance=MemoryImportance.MEDIUM, notebook_id=notebook_id,
                ), namespace=AgentNamespace.CURATOR)
            except Exception:
                pass

        # Helper: extract URL from message (simple, reliable)
        url_match = _re.search(r'(https?://[^\s,]+)', q)

        # =================================================================
        # LLM-based Intent Classification
        # =================================================================
        classified = await classify_intent(q, "collector", ollama_client)
        intent = classified["intent"]
        params = classified.get("params", {})

        # -----------------------------------------------------------------
        # ADD URL (with optional schedule)
        # -----------------------------------------------------------------
        if intent == "add_url":
            url = (params.get("url") or "").strip().rstrip('.,;:)')
            # Fallback: extract URL from message if LLM missed it
            if not url and url_match:
                url = url_match.group(1).rstrip('.,;:)')
            if url:
                is_rss = params.get("is_rss", False)
                if isinstance(is_rss, str):
                    is_rss = is_rss.lower() in ("true", "yes")
                if not is_rss:
                    is_rss = url.endswith(('.rss', '.xml', '/feed', '/atom'))

                if is_rss:
                    rss_feeds = list(config.sources.get("rss_feeds", []))
                    if url in rss_feeds:
                        reply = f"RSS feed already tracked: {url}"
                    else:
                        rss_feeds.append(url)
                        collector_agent.update_config({"sources": {**config.sources, "rss_feeds": rss_feeds}})
                        reply = f"Done. **RSS feed added:** {url}\n- Will be checked on the next collection run."
                        _notify_curator(f"Collector added RSS feed: {url}")
                    follow_ups = ['Collect now', 'Show my sources', 'Set schedule to daily']
                else:
                    yield f"data: {json.dumps({'type': 'status', 'message': f'{collector_name} fetching {url}...', 'query_type': 'collector'})}\n\n"
                    schedule_raw = params.get("schedule", "manual")
                    freq = _parse_freq(schedule_raw) if schedule_raw and schedule_raw != "manual" else "manual"

                    from services.web_scraper import web_scraper

                    # Use scrape_with_html so we can check for index pages
                    scraped = await web_scraper.scrape_with_html(url)
                    raw_html = scraped.get("html")

                    # ── Index / feed page detection ──────────────────────
                    is_index = False
                    if scraped.get("success") and raw_html:
                        try:
                            is_index = web_scraper.is_index_page(url, raw_html, scraped.get("text", ""))
                        except Exception:
                            is_index = False

                    if is_index and raw_html:
                        # ── FEED PAGE FLOW ───────────────────────────────
                        yield f"data: {json.dumps({'type': 'status', 'message': 'Index page detected — extracting articles...', 'query_type': 'collector'})}\n\n"

                        article_links = web_scraper.extract_article_links(url, raw_html, max_links=10)

                        if not article_links:
                            reply = f"**Index page detected** at {url} but could not find article links.\nTry linking to a specific article instead."
                        else:
                            # Store index URL as a feed_page for recurring collection
                            feed_pages = list(config.sources.get("feed_pages", []))
                            if url not in feed_pages:
                                feed_pages.append(url)
                            collector_agent.update_config({
                                "sources": {**config.sources, "feed_pages": feed_pages},
                                "schedule": {**config.schedule, "frequency": freq if freq != "manual" else "weekly"},
                            })

                            # Preview the articles found (but do NOT ingest — collection pipeline handles that)
                            sched_label = freq if freq != "manual" else "weekly"
                            lines = [f"**Feed page registered:** [{scraped.get('title', url)}]({url})",
                                     f"- **Schedule:** {sched_label} checks for new articles",
                                     f"- **{len(article_links)} articles detected** on this page\n"]
                            for a in article_links[:8]:
                                lines.append(f"- [{a['title']}]({a['url']})")
                            if len(article_links) > 8:
                                lines.append(f"- *...and {len(article_links) - 8} more*")
                            lines.append(f"\nThese will be scraped, scored for relevance, and processed on the next **{sched_label}** collection run.")
                            lines.append("Articles that pass my quality filters will appear in your notebook sources.")
                            reply = "\n".join(lines)
                            _notify_curator(f"Collector registered feed page: {url}. {len(article_links)} articles detected.")

                        follow_ups = ['Collect now', 'Show my collection status', 'Add another source']

                    elif scraped.get("success") and scraped.get("text"):
                        # ── SINGLE URL FLOW ──────────────────────────────
                        # Register in collector profile only — collection pipeline handles ingestion
                        title = scraped.get("title", url)
                        wc = scraped.get("word_count", len(scraped["text"].split()))

                        web_pages = list(config.sources.get("web_pages", []))
                        if url not in web_pages:
                            web_pages.append(url)
                        collector_agent.update_config({
                            "sources": {**config.sources, "web_pages": web_pages},
                            "schedule": {**config.schedule, "frequency": freq},
                        })
                        lines = [f"Done. **Source registered:** [{title}]({url})",
                                 f"- **{wc}** words detected on page"]
                        if freq != "manual":
                            lines.append(f"- **Schedule set:** {freq} checks")
                        else:
                            lines.append(f"- **Schedule:** manual (say \"check daily\" to automate)")
                        lines.append(f"\nThis will be processed through the collection pipeline on the next run.")
                        lines.append("Say **\"collect now\"** to trigger an immediate collection.")
                        reply = "\n".join(lines)
                        _notify_curator(f"Collector registered web source: {title} ({url}). Schedule: {freq}.")
                    else:
                        error = scraped.get("error", "Could not extract content")
                        reply = f"**Could not fetch:** {url}\n- Reason: {error}\n\nTry a different URL, or add content manually via the Sources panel."
                    follow_ups = ['Show my collection status', 'Collect now', 'Add another source']

        # -----------------------------------------------------------------
        # REMOVE / DISABLE SOURCE
        # -----------------------------------------------------------------
        elif intent == "remove_source":
            url = (params.get("url") or "").strip().rstrip('.,;:)')
            if not url and url_match:
                url = url_match.group(1).rstrip('.,;:)')
            if url:
                disabled = list(config.disabled_sources)
                web_pages = list(config.sources.get("web_pages", []))
                rss_feeds = list(config.sources.get("rss_feeds", []))
                removed = False
                if url in web_pages:
                    web_pages.remove(url)
                    collector_agent.update_config({"sources": {**config.sources, "web_pages": web_pages}})
                    removed = True
                if url in rss_feeds:
                    rss_feeds.remove(url)
                    collector_agent.update_config({"sources": {**config.sources, "rss_feeds": rss_feeds}})
                    removed = True
                if not removed and url not in disabled:
                    disabled.append(url)
                    collector_agent.update_config({"disabled_sources": disabled})
                reply = f"Done. **Source removed:** {url}" if removed else f"Done. **Source disabled:** {url}"

        # -----------------------------------------------------------------
        # ADD NEWS KEYWORD
        # -----------------------------------------------------------------
        elif intent == "add_keyword":
            keyword = (params.get("keyword") or "").strip().strip("'\"")
            if keyword:
                keywords = list(config.sources.get("news_keywords", []))
                if keyword.lower() in [k.lower() for k in keywords]:
                    reply = f"Already tracking news keyword: **{keyword}**"
                else:
                    keywords.append(keyword)
                    collector_agent.update_config({"sources": {**config.sources, "news_keywords": keywords}})
                    reply = f"Done. **News keyword added:** {keyword}\n- Will be searched on the next collection run."
                    _notify_curator(f"Collector added news keyword: {keyword}")

        # -----------------------------------------------------------------
        # SET INTENT
        # -----------------------------------------------------------------
        elif intent == "set_intent":
            val = (params.get("intent") or "").strip().rstrip('.')
            if val:
                collector_agent.update_config({"intent": val})
                reply = f"Done. **Intent updated:** {val}"

        # -----------------------------------------------------------------
        # SET SUBJECT
        # -----------------------------------------------------------------
        elif intent == "set_subject":
            subject = (params.get("subject") or "").strip().rstrip('.')
            if subject:
                collector_agent.update_config({"subject": subject})
                reply = f"Done. **Subject updated:** {subject}"

        # -----------------------------------------------------------------
        # SET / ADD FOCUS AREAS
        # -----------------------------------------------------------------
        elif intent == "set_focus":
            raw_areas = params.get("areas", [])
            if isinstance(raw_areas, str):
                raw_areas = [a.strip().strip('"\'') for a in _re.split(r'[,;\n]', raw_areas) if a.strip()]
            areas = [a for a in raw_areas if a]
            if areas:
                add_to = params.get("add_to_existing", False)
                if isinstance(add_to, str):
                    add_to = add_to.lower() in ("true", "yes")
                if add_to:
                    existing = list(config.focus_areas)
                    areas = existing + [a for a in areas if a.lower() not in [e.lower() for e in existing]]
                collector_agent.update_config({"focus_areas": areas})
                reply = f"Done. **Focus areas updated:** {', '.join(areas)}"

        # -----------------------------------------------------------------
        # SET / ADD EXCLUDED TOPICS
        # -----------------------------------------------------------------
        elif intent == "set_excluded":
            raw_topics = params.get("topics", [])
            if isinstance(raw_topics, str):
                raw_topics = [t.strip().strip('"\'') for t in _re.split(r'[,;\n]', raw_topics) if t.strip()]
            topics = [t for t in raw_topics if t]
            if topics:
                add_to = params.get("add_to_existing", False)
                if isinstance(add_to, str):
                    add_to = add_to.lower() in ("true", "yes")
                if add_to:
                    existing = list(config.excluded_topics)
                    topics = existing + [t for t in topics if t.lower() not in [e.lower() for e in existing]]
                collector_agent.update_config({"excluded_topics": topics})
                reply = f"Done. **Excluded topics updated:** {', '.join(topics)}"

        # -----------------------------------------------------------------
        # SET NAME
        # -----------------------------------------------------------------
        elif intent == "set_name":
            new_name = (params.get("name") or "").strip()
            if new_name:
                collector_agent.update_config({"name": new_name})
                collector_name = new_name
                reply = f"Done — I'm now **{new_name}**. Nice to meet you!"

        # -----------------------------------------------------------------
        # SET COLLECTION MODE
        # -----------------------------------------------------------------
        elif intent == "set_mode":
            mode_str = (params.get("mode") or "").lower()
            if mode_str in ("manual", "automatic", "hybrid"):
                mode = CollectionMode(mode_str)
                collector_agent.update_config({"collection_mode": mode})
                reply = f"Done. **Collection mode set to:** {mode.value}"

        # -----------------------------------------------------------------
        # SET APPROVAL MODE
        # -----------------------------------------------------------------
        elif intent == "set_approval":
            mode_str = (params.get("mode") or "").lower().replace(" ", "_")
            if "trust" in mode_str or "auto" in mode_str:
                collector_agent.update_config({"approval_mode": ApprovalMode.TRUST_ME})
                reply = "Done. **Approval mode:** trust me (auto-approve all)"
            elif "show" in mode_str:
                collector_agent.update_config({"approval_mode": ApprovalMode.SHOW_ME})
                reply = "Done. **Approval mode:** show me first (queue all for review)"
            else:
                collector_agent.update_config({"approval_mode": ApprovalMode.MIXED})
                reply = "Done. **Approval mode:** mixed (auto-approve high confidence, queue uncertain)"

        # -----------------------------------------------------------------
        # SET SCHEDULE (standalone, no URL)
        # -----------------------------------------------------------------
        elif intent == "set_schedule":
            freq_raw = params.get("frequency", "daily")
            freq = _parse_freq(freq_raw)
            collector_agent.update_config({"schedule": {**config.schedule, "frequency": freq}})
            reply = f"Done. **Schedule updated:** {freq}"

        # -----------------------------------------------------------------
        # SET FILTERS (max age, min relevance)
        # -----------------------------------------------------------------
        elif intent == "set_filters":
            updates = {}
            parts = []
            if params.get("max_age_days"):
                try:
                    updates["max_age_days"] = int(params["max_age_days"])
                    parts.append(f"max age: {updates['max_age_days']} days")
                except (ValueError, TypeError):
                    pass
            if params.get("min_relevance"):
                try:
                    updates["min_relevance"] = float(params["min_relevance"])
                    parts.append(f"min relevance: {updates['min_relevance']}")
                except (ValueError, TypeError):
                    pass
            if updates:
                collector_agent.update_config({"filters": {**config.filters, **updates}})
                reply = f"Done. **Filters updated:** {', '.join(parts)}"
            else:
                reply = f"Current filters: max age {config.filters.get('max_age_days', 30)} days, min relevance {config.filters.get('min_relevance', 0.5)}"

        # -----------------------------------------------------------------
        # COLLECT NOW
        # -----------------------------------------------------------------
        elif intent == "collect_now":
            yield f"data: {json.dumps({'type': 'status', 'message': f'{collector_name} running collection...', 'query_type': 'collector'})}\n\n"
            try:
                from agents.curator import curator
                result = await curator.assign_immediate_collection(notebook_id=notebook_id)
                found = result.get("items_found", 0)
                queued = result.get("items_queued", 0)
                reply = f"**Collection complete.**\n- **{found}** items found\n- **{queued}** items queued for review"
                if queued > 0:
                    follow_ups = ['Show pending items', 'Approve all pending', 'Show collection status']
            except Exception as ce:
                reply = f"Collection failed: {ce}"

        # -----------------------------------------------------------------
        # SHOW PENDING APPROVALS
        # -----------------------------------------------------------------
        elif intent == "show_pending":
            pending = collector_agent.get_pending_approvals()
            if not pending:
                reply = "No items pending approval."
            else:
                lines = [f"**{len(pending)} items pending approval:**\n"]
                for item in pending[:10]:
                    title = item.get("title", "Untitled")
                    conf = item.get("confidence", 0)
                    lines.append(f"- **{title}** (confidence: {conf:.0%})")
                if len(pending) > 10:
                    lines.append(f"- ...and {len(pending) - 10} more")
                lines.append(f"\nSay *\"approve all\"* or review in the Collector panel.")
                reply = "\n".join(lines)
                follow_ups = ['Approve all pending', 'Reject all pending', 'Show collection status']

        # -----------------------------------------------------------------
        # APPROVE ALL PENDING
        # -----------------------------------------------------------------
        elif intent == "approve_all":
            pending = collector_agent.get_pending_approvals()
            if not pending:
                reply = "No items to approve."
            else:
                ids = [p.get("item_id", p.get("id", "")) for p in pending]
                approved = await collector_agent.approve_batch(ids)
                reply = f"Done. **Approved {approved} items.**"
                _notify_curator(f"User approved {approved} pending items in notebook {notebook_id}")

        # -----------------------------------------------------------------
        # SOURCE HEALTH
        # -----------------------------------------------------------------
        elif intent == "source_health":
            report = collector_agent.get_source_health_report()
            if not report:
                reply = "No source health data available yet. Run a collection first."
            else:
                lines = [f"**Source Health Report ({len(report)} sources):**\n"]
                for s in report:
                    icon = {"healthy": "[ok]", "degraded": "[warn]", "failing": "[err]", "dead": "[dead]"}.get(s.get("health", ""), "[?]")
                    lines.append(f"{icon} {s.get('url', 'unknown')[:60]} — {s.get('health', 'unknown')} ({s.get('items_collected', 0)} items)")
                reply = "\n".join(lines)

        # -----------------------------------------------------------------
        # SHOW PROFILE / CONFIG
        # -----------------------------------------------------------------
        elif intent == "show_profile":
            lines = [f"**{collector_name}'s Profile:**\n"]
            if config.subject: lines.append(f"- **Subject:** {config.subject}")
            if config.intent: lines.append(f"- **Intent:** {config.intent}")
            if config.focus_areas: lines.append(f"- **Focus areas:** {', '.join(config.focus_areas)}")
            if config.excluded_topics: lines.append(f"- **Excluded:** {', '.join(config.excluded_topics)}")
            lines.append(f"- **Mode:** {config.collection_mode.value if hasattr(config.collection_mode, 'value') else config.collection_mode}")
            lines.append(f"- **Approval:** {config.approval_mode.value if hasattr(config.approval_mode, 'value') else config.approval_mode}")
            lines.append(f"- **Schedule:** {config.schedule.get('frequency', 'manual')}")
            lines.append(f"- **Filters:** max age {config.filters.get('max_age_days', 30)}d, min relevance {config.filters.get('min_relevance', 0.5)}")
            web_ct = len(config.sources.get("web_pages", []))
            rss_ct = len(config.sources.get("rss_feeds", []))
            kw_ct = len(config.sources.get("news_keywords", []))
            lines.append(f"- **Sources:** {web_ct} web pages, {rss_ct} RSS feeds, {kw_ct} keywords")
            reply = "\n".join(lines)

        # -----------------------------------------------------------------
        # SHOW HISTORY
        # -----------------------------------------------------------------
        elif intent == "show_history":
            from services.collection_history import get_collection_history
            history = get_collection_history(notebook_id, limit=5)
            if not history:
                reply = "No collection history yet. Say *\"collect now\"* to run a sweep."
            else:
                lines = ["**Recent collection runs:**\n"]
                for h in history:
                    lines.append(f"- {h.get('started_at', '?')}: {h.get('items_found', 0)} found, {h.get('items_queued', 0)} queued")
                reply = "\n".join(lines)

        # -----------------------------------------------------------------
        # FALLBACK: STATUS
        # -----------------------------------------------------------------
        else:
            sources = await source_store.list(notebook_id)
            source_count = len(sources)
            recent = sorted(sources, key=lambda s: s.get("created_at", ""), reverse=True)[:5]

            lines = [f"Here's your collection status for this notebook:\n"]
            lines.append(f"- **{source_count}** total sources indexed")
            web_pages = config.sources.get("web_pages", [])
            if web_pages: lines.append(f"- **{len(web_pages)}** monitored web pages")
            rss_feeds = config.sources.get("rss_feeds", [])
            if rss_feeds: lines.append(f"- **{len(rss_feeds)}** RSS feeds")
            lines.append(f"- **Schedule:** {config.schedule.get('frequency', 'manual')}")
            if recent:
                lines.append(f"\n**Recent sources:**")
                for s in recent:
                    lines.append(f"- {s.get('filename', 'Unknown')} ({s.get('format', 'file').upper()})")
            lines.append(f"\n*I can do a lot! Try:*")
            lines.append(f"- *\"add https://example.com, check daily\"*")
            lines.append(f"- *\"focus on earnings, M&A\"*")
            lines.append(f"- *\"ignore crypto\"*")
            lines.append(f"- *\"collect now\"*")
            lines.append(f"- *\"show pending approvals\"*")
            lines.append(f"- *\"show your profile\"*")
            reply = "\n".join(lines)

        # Stream the reply
        chunk_size = 12
        for i in range(0, len(reply), chunk_size):
            yield f"data: {json.dumps({'type': 'token', 'content': reply[i:i+chunk_size]})}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'follow_up_questions': follow_ups, 'agent_name': collector_name, 'agent_type': 'collector'})}\n\n"

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'error': f'Collector error: {e}'})}\n\n"


async def _stream_research(chat_query: ChatQuery):
    """Stream a Research agent response in SSE format.

    LLM-based NLP intent router with three modes:
      - web_search:  broad web search
      - site_search: domain-scoped search
      - deep_dive:   multi-hop search → scrape → quality-score → synthesise
    Results are streamed as a narrative summary followed by a structured
    'research_results' event so the frontend can render approval cards.
    """
    from services.ollama_client import ollama_client
    from services.intent_classifier import classify_intent
    from services.research_engine import research_engine, DeepDiveFilters

    q = chat_query.question
    notebook_id = chat_query.notebook_id

    # ── Help shortcut (no LLM call) ──
    if _is_help_request(q):
        for chunk in _stream_help(_RESEARCH_HELP, "Research", "research"):
            yield chunk
        return

    yield f"data: {json.dumps({'type': 'status', 'message': 'Research agent analysing your request...', 'query_type': 'research'})}\n\n"

    try:
        # ── Intent classification ────────────────────────────────────────
        classified = await classify_intent(q, "research", ollama_client)
        intent = classified["intent"]
        params = classified.get("params", {})

        # Async status helper (for research_engine callbacks)
        async def _emit_status(msg: str):
            pass  # status is yielded inline below; engine uses sync callback

        results = []
        mode_label = "Web Search"

        # ── WEB SEARCH ───────────────────────────────────────────────────
        if intent == "web_search":
            search_query = params.get("query", q)
            max_results = int(params.get("max_results", 10))
            yield f"data: {json.dumps({'type': 'status', 'message': f'Searching the web for: {search_query}', 'query_type': 'research'})}\n\n"
            results = await research_engine.web_search(
                query=search_query,
                notebook_id=notebook_id,
                max_results=max_results,
            )
            mode_label = "Web Search"

        # ── SITE SEARCH ──────────────────────────────────────────────────
        elif intent == "site_search":
            search_query = params.get("query", q)
            site = params.get("site", "")
            if not site:
                # Try to extract domain from the message
                import re as _re
                dm = _re.search(r'(?:site[: ]+|on\s+|from\s+)([\w.-]+\.\w{2,})', q, _re.IGNORECASE)
                site = dm.group(1) if dm else ""
            yield f"data: {json.dumps({'type': 'status', 'message': f'Searching {site or 'the web'} for: {search_query}', 'query_type': 'research'})}\n\n"
            if site:
                results = await research_engine.site_search(
                    query=search_query, site=site, notebook_id=notebook_id,
                )
            else:
                results = await research_engine.web_search(
                    query=search_query, notebook_id=notebook_id,
                )
            mode_label = f"Site Search ({site})" if site else "Web Search"

        # ── DEEP DIVE ────────────────────────────────────────────────────
        elif intent == "deep_dive":
            search_query = params.get("query", q)
            recency = int(params.get("recency_days", 30))
            min_wc = int(params.get("min_word_count", 500))
            topic_quals = params.get("topic_qualifiers", [])
            if isinstance(topic_quals, str):
                topic_quals = [t.strip() for t in topic_quals.split(",") if t.strip()]

            filters = DeepDiveFilters(
                recency_days=recency,
                min_word_count=min_wc,
                topic_qualifiers=topic_quals,
            )

            yield f"data: {json.dumps({'type': 'status', 'message': f'Deep dive: searching for candidates...', 'query_type': 'research'})}\n\n"

            # Wrap status updates as SSE — deep_dive is multi-step
            status_messages = []

            async def _dd_status(msg):
                status_messages.append(msg)

            results = await research_engine.deep_dive(
                query=search_query,
                notebook_id=notebook_id,
                filters=filters,
                on_status=_dd_status,
            )

            # Emit accumulated status messages
            for sm in status_messages:
                yield f"data: {json.dumps({'type': 'status', 'message': sm, 'query_type': 'research'})}\n\n"

            mode_label = "Deep Dive"

        # ── Build clean header (no verbose narrative) ───────────────────
        new_results = [r for r in results if not r.already_sourced]
        dupes = [r for r in results if r.already_sourced]

        if not new_results:
            reply = "No new results found."
            if dupes:
                reply += f" ({len(dupes)} results were already in your sources.)"
            else:
                reply += " Try broadening your query or adjusting filters."
        else:
            reply = f"**{mode_label} — {len(new_results)} results found**"
            if dupes:
                reply += f"  ·  *{len(dupes)} already in sources*"

        # ── Stream header as tokens ───────────────────────────────────
        chunk_size = 12
        for i in range(0, len(reply), chunk_size):
            yield f"data: {json.dumps({'type': 'token', 'content': reply[i:i+chunk_size]})}\n\n"

        # ── Emit structured results for card UI ───────────────────────
        if new_results:
            yield f"data: {json.dumps({'type': 'research_results', 'results': research_engine.serialize_results(new_results)})}\n\n"

        follow_ups = []
        if new_results:
            follow_ups = ['Add all results as sources', 'Deep dive into the top result', 'Narrow the search']
        else:
            follow_ups = ['Try a broader search', 'Search a specific site', 'Deep dive with filters']

        yield f"data: {json.dumps({'type': 'done', 'follow_up_questions': follow_ups, 'agent_name': 'Research', 'agent_type': 'research'})}\n\n"

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'error': f'Research error: {e}'})}\n\n"


async def _stream_studio(chat_query: ChatQuery):
    """Stream a Studio agent response in SSE format.

    LLM-based intent router — lets the user create Studio content (audio,
    documents, quizzes, visuals, videos) directly from the chat, using the
    current conversation as context.
    """
    from services.ollama_client import ollama_client
    from services.intent_classifier import classify_intent
    from services.event_logger import log_content_generated

    q = chat_query.question
    notebook_id = chat_query.notebook_id
    chat_context = chat_query.chat_context or ""

    # ── Help shortcut ──
    if _is_help_request(q):
        for chunk in _stream_help(_STUDIO_HELP, "Studio", "studio"):
            yield chunk
        return

    yield f"data: {json.dumps({'type': 'status', 'message': 'Studio interpreting your request...', 'query_type': 'studio'})}\n\n"

    try:
        # ── Intent classification ────────────────────────────────────────
        classified = await classify_intent(q, "studio", ollama_client)
        intent = classified["intent"]
        params = classified.get("params", {})
        topic = (params.get("topic") or "").strip() or None
        reply = ""
        follow_ups = ['Make a podcast on this', 'Create a study guide', 'Quiz me on this topic']

        # -----------------------------------------------------------------
        # GENERATE AUDIO (podcast, interview, etc.)
        # -----------------------------------------------------------------
        if intent == "generate_audio":
            yield f"data: {json.dumps({'type': 'status', 'message': 'Studio generating podcast...', 'query_type': 'studio'})}\n\n"
            try:
                from services.audio_generator import audio_service

                skill_id = (params.get("skill_id") or "podcast").strip()
                host1 = (params.get("host1_gender") or "male").strip().lower()
                host2 = (params.get("host2_gender") or "female").strip().lower()
                duration = int(params.get("duration_minutes", 10))
                if duration < 5: duration = 5
                if duration > 45: duration = 45

                result = await audio_service.generate(
                    notebook_id=notebook_id,
                    topic=topic or "the current discussion",
                    duration_minutes=duration,
                    skill_id=skill_id,
                    host1_gender=host1,
                    host2_gender=host2,
                    accent="us",
                    chat_context=chat_context,
                )
                audio_id = result.get("audio_id", "")
                status = result.get("status", "pending")
                log_content_generated(notebook_id, "audio", skill_id, topic or "chat-context")

                lines = [
                    f"**Podcast generation started!** 🎙️",
                    f"",
                    f"- **Style:** {skill_id.replace('_', ' ').title()}",
                    f"- **Duration:** ~{duration} min",
                    f"- **Hosts:** {host1.title()} & {host2.title()}",
                    f"- **Status:** {status}",
                    f"",
                    f"The podcast is being generated in the background. You'll find it in **Studio → Audio** when it's ready.",
                ]
                reply = "\n".join(lines)
                follow_ups = ['Create a study guide too', 'Make a quiz on this', 'Show me a visual']

            except Exception as ae:
                reply = f"Podcast generation failed: {ae}"

        # -----------------------------------------------------------------
        # GENERATE DOCUMENT (brief, guide, summary, etc.)
        # -----------------------------------------------------------------
        elif intent == "generate_document":
            yield f"data: {json.dumps({'type': 'status', 'message': 'Studio generating document...', 'query_type': 'studio'})}\n\n"
            try:
                from api.content import generate_content as _gen_content, ContentGenerateRequest

                skill_id = (params.get("skill_id") or "research_summary").strip()
                style = (params.get("style") or "professional").strip()

                result = await _gen_content(ContentGenerateRequest(
                    notebook_id=notebook_id,
                    skill_id=skill_id,
                    topic=topic,
                    style=style,
                    chat_context=chat_context,
                ))
                content = result.content
                skill_name = result.skill_name
                log_content_generated(notebook_id, "document", skill_id, topic or "chat-context")

                lines = [
                    f"**{skill_name} generated!** 📄",
                    f"",
                    f"---",
                    f"",
                    content[:3000] if len(content) > 3000 else content,
                ]
                if len(content) > 3000:
                    lines.append(f"\n\n*...truncated. Full document available in Studio → Documents.*")
                reply = "\n".join(lines)
                follow_ups = ['Make a podcast on this', 'Quiz me on this', 'Create a visual']

            except Exception as de:
                reply = f"Document generation failed: {de}"

        # -----------------------------------------------------------------
        # GENERATE QUIZ
        # -----------------------------------------------------------------
        elif intent == "generate_quiz":
            yield f"data: {json.dumps({'type': 'status', 'message': 'Studio generating quiz...', 'query_type': 'studio'})}\n\n"
            try:
                from api.quiz import generate_quiz as _gen_quiz, GenerateQuizRequest

                num_q = int(params.get("num_questions", 5))
                if num_q < 3: num_q = 3
                if num_q > 10: num_q = 10
                difficulty = (params.get("difficulty") or "medium").strip().lower()
                if difficulty not in ("easy", "medium", "hard"):
                    difficulty = "medium"

                result = await _gen_quiz(GenerateQuizRequest(
                    notebook_id=notebook_id,
                    num_questions=num_q,
                    difficulty=difficulty,
                    topic=topic,
                    chat_context=chat_context,
                ))
                questions = result.questions
                log_content_generated(notebook_id, "quiz", "quiz", topic or "chat-context")

                lines = [
                    f"**Quiz generated!** 🎯  ({len(questions)} questions, {difficulty})",
                    f"",
                    f"Head to **Studio → Quiz** to take it interactively, or preview below:",
                    f"",
                ]
                for i, q_item in enumerate(questions[:5]):
                    lines.append(f"**Q{i+1}.** {q_item.question}")
                    for opt in (q_item.options or []):
                        lines.append(f"  - {opt}")
                    lines.append("")
                if len(questions) > 5:
                    lines.append(f"*...plus {len(questions) - 5} more questions*")
                reply = "\n".join(lines)
                follow_ups = ['Make it harder', 'Create a study guide', 'Podcast on this topic']

            except Exception as qe:
                reply = f"Quiz generation failed: {qe}"

        # -----------------------------------------------------------------
        # GENERATE VISUAL (diagram, chart, etc.)
        # -----------------------------------------------------------------
        elif intent == "generate_visual":
            yield f"data: {json.dumps({'type': 'status', 'message': 'Studio generating visual...', 'query_type': 'studio'})}\n\n"
            try:
                from api.visual import generate_visual_summary as _gen_visual, GenerateVisualRequest

                result = await _gen_visual(GenerateVisualRequest(
                    notebook_id=notebook_id,
                    diagram_types=["mindmap", "flowchart"],
                    focus_topic=topic or q,
                ))
                diagrams = result.diagrams
                log_content_generated(notebook_id, "visual", "visual", topic or "chat-context")

                if diagrams:
                    lines = []
                    for d in diagrams:
                        lines.append(f"**{d.title}** ({d.diagram_type}) 📊")
                        lines.append(f"")
                        lines.append(f"```mermaid")
                        lines.append(d.code)
                        lines.append(f"```")
                        lines.append(f"")
                    lines.append(f"*Open in Studio → Visual for an interactive view.*")
                    reply = "\n".join(lines)
                else:
                    reply = "Could not generate a visual from the current context. Try providing more specific content."
                follow_ups = ['Make a flowchart instead', 'Create a document', 'Make a podcast']

            except Exception as ve:
                reply = f"Visual generation failed: {ve}"

        # -----------------------------------------------------------------
        # GENERATE VIDEO
        # -----------------------------------------------------------------
        elif intent == "generate_video":
            yield f"data: {json.dumps({'type': 'status', 'message': 'Studio generating video...', 'query_type': 'studio'})}\n\n"
            try:
                from services.video_generator import video_generator

                duration = int(params.get("duration_minutes", 5))
                if duration < 1: duration = 1
                if duration > 10: duration = 10
                visual_style = (params.get("visual_style") or "classic").strip()
                narrator_gender = (params.get("narrator_gender") or "female").strip()
                accent = (params.get("accent") or "us").strip()

                result = await video_generator.generate(
                    notebook_id=notebook_id,
                    topic=topic or "the current discussion",
                    duration_minutes=duration,
                    visual_style=visual_style,
                    narrator_gender=narrator_gender,
                    accent=accent,
                    format_type="explainer",
                    chat_context=chat_context,
                )
                video_id = result.get("video_id", "")
                status = result.get("status", "pending")
                log_content_generated(notebook_id, "video", "explainer", topic or "chat-context")

                lines = [
                    f"**Video generation started!** 🎬",
                    f"",
                    f"- **Duration:** ~{duration} min",
                    f"- **Style:** {visual_style}",
                    f"- **Status:** {status}",
                    f"",
                    f"You'll find the video in **Studio → Video** when it's ready.",
                ]
                reply = "\n".join(lines)
                follow_ups = ['Create a podcast too', 'Make a study guide', 'Quiz me']

            except Exception as vie:
                reply = f"Video generation failed: {vie}"

        # -----------------------------------------------------------------
        # FALLBACK
        # -----------------------------------------------------------------
        else:
            reply = (
                "I'm not sure what type of content you'd like me to create. "
                "Try something like:\n\n"
                "- *\"Make a podcast on this topic\"*\n"
                "- *\"Create a study guide\"*\n"
                "- *\"Quiz me on what we discussed\"*\n"
                "- *\"Visualize this as a flowchart\"*\n"
                "- *\"Make a video explainer\"*\n\n"
                "Type **@studio ?** for full help."
            )

        # Stream the reply
        chunk_size = 12
        for i in range(0, len(reply), chunk_size):
            yield f"data: {json.dumps({'type': 'token', 'content': reply[i:i+chunk_size]})}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'follow_up_questions': follow_ups, 'agent_name': 'Studio', 'agent_type': 'studio'})}\n\n"

        # Log interaction
        try:
            log_chat_qa(notebook_id, f"@studio {q}", reply[:500], [])
        except Exception:
            pass

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'error': f'Studio error: {e}'})}\n\n"


class ChatHistoryMessage(BaseModel):
    """Single message in chat history"""
    role: str  # "user" or "assistant"
    content: str


class ContextChatQuery(BaseModel):
    """Chat query with injected page context - for extension use"""
    notebook_id: str
    question: str
    page_context: Optional[dict] = None  # {title, summary, key_points, key_concepts}
    chat_history: Optional[List[ChatHistoryMessage]] = None  # Previous messages for context
    enable_web_search: Optional[bool] = False


@router.post("/query-with-context")
async def query_with_context(request: ContextChatQuery):
    """Chat endpoint that accepts injected page context.
    
    Best practices implemented:
    1. Context injection - page summary/content injected directly
    2. Conversation history - previous messages included for continuity
    3. Fallback handling - graceful responses when context is limited
    4. Query understanding - LLM understands the browsing context
    """
    try:
        # Build context-enriched prompt
        context_parts = []
        
        # Add page context if provided
        if request.page_context:
            pc = request.page_context
            context_parts.append(f"[PAGE TITLE: {pc.get('title', 'Unknown')}]")
            
            # Include raw content if available (for detailed Q&A)
            if pc.get('raw_content'):
                context_parts.append(f"\n[FULL ARTICLE CONTENT]\n{pc['raw_content']}\n[END ARTICLE CONTENT]")
            elif pc.get('summary'):
                # Fallback to summary if no raw content
                context_parts.append(f"\n[ARTICLE SUMMARY]\n{pc['summary']}\n[END SUMMARY]")
            
            if pc.get('key_points'):
                points = pc['key_points']
                if isinstance(points, list) and points:
                    context_parts.append("\n[KEY POINTS]")
                    for p in points:
                        context_parts.append(f"• {p}")
                    context_parts.append("[END KEY POINTS]")
            
            if pc.get('key_concepts'):
                concepts = pc['key_concepts']
                if isinstance(concepts, list) and concepts:
                    context_parts.append(f"\n[KEY CONCEPTS: {', '.join(concepts)}]")
        
        page_context_text = "\n".join(context_parts) if context_parts else ""
        
        # Build conversation history (keep last 12 messages = 6 exchanges)
        history_text = ""
        if request.chat_history and len(request.chat_history) > 0:
            recent_history = request.chat_history[-12:]
            history_parts = []
            for msg in recent_history:
                role_label = "User" if msg.role == "user" else "Assistant"
                history_parts.append(f"{role_label}: {msg.content}")
            if history_parts:
                history_text = "\n\n=== CONVERSATION HISTORY ===\n" + "\n".join(history_parts) + "\n" + "="*50
        
        # Combine all context
        full_context = page_context_text + history_text
        
        # Determine response strategy based on available context
        has_page_context = bool(request.page_context and (request.page_context.get('raw_content') or request.page_context.get('summary')))
        
        # Use LLM to answer with full context
        if has_page_context:
            system_prompt = """You are a helpful research assistant. The user is reading a web article and asking questions about it.

INSTRUCTIONS:
1. Answer ONLY based on the article content provided below
2. Be specific and cite details from the article
3. If the information isn't in the article, say so briefly - don't speculate
4. Give direct, focused answers without preamble
5. NEVER repeat the question or include any markup from the context in your response"""
        else:
            system_prompt = """You are a helpful research assistant. The user is browsing the web and has a question.

Since there's no page content available, provide a helpful general response.
If the question seems to be about specific page content, suggest the user first summarize the page."""

        # Clean prompt structure - question is clearly separated
        user_prompt = f"""ARTICLE CONTEXT:
{full_context}

USER QUESTION: {request.question}

Provide a direct answer based on the article content above."""
        
        answer = await rag_engine._call_ollama(system_prompt, user_prompt)
        
        # Generate follow-up questions based on context
        follow_ups = None
        if has_page_context and request.page_context.get('key_concepts'):
            concepts = request.page_context['key_concepts'][:3]
            follow_ups = [f"Tell me more about {c}" for c in concepts]
        
        return ChatResponse(
            answer=answer,
            citations=[],
            sources=[],
            web_sources=None,
            follow_up_questions=follow_ups,
            low_confidence=not has_page_context
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query-with-context/stream")
async def query_with_context_stream(request: ContextChatQuery):
    """Streaming version of query-with-context for the browser extension."""
    try:
        # Build context (same logic as non-streaming)
        context_parts = []
        if request.page_context:
            pc = request.page_context
            context_parts.append(f"[PAGE TITLE: {pc.get('title', 'Unknown')}]")
            if pc.get('raw_content'):
                context_parts.append(f"\n[FULL ARTICLE CONTENT]\n{pc['raw_content']}\n[END ARTICLE CONTENT]")
            elif pc.get('summary'):
                context_parts.append(f"\n[ARTICLE SUMMARY]\n{pc['summary']}\n[END SUMMARY]")
            if pc.get('key_points'):
                points = pc['key_points']
                if isinstance(points, list) and points:
                    context_parts.append("\n[KEY POINTS]")
                    for p in points:
                        context_parts.append(f"• {p}")
                    context_parts.append("[END KEY POINTS]")
            if pc.get('key_concepts'):
                concepts = pc['key_concepts']
                if isinstance(concepts, list) and concepts:
                    context_parts.append(f"\n[KEY CONCEPTS: {', '.join(concepts)}]")

        page_context_text = "\n".join(context_parts) if context_parts else ""

        history_text = ""
        if request.chat_history and len(request.chat_history) > 0:
            recent_history = request.chat_history[-12:]
            history_parts = []
            for msg in recent_history:
                role_label = "User" if msg.role == "user" else "Assistant"
                history_parts.append(f"{role_label}: {msg.content}")
            if history_parts:
                history_text = "\n\n=== CONVERSATION HISTORY ===\n" + "\n".join(history_parts) + "\n" + "=" * 50

        full_context = page_context_text + history_text
        has_page_context = bool(request.page_context and (request.page_context.get('raw_content') or request.page_context.get('summary')))

        if has_page_context:
            system_prompt = """You are a helpful research assistant. The user is reading a web article and asking questions about it.

INSTRUCTIONS:
1. Answer ONLY based on the article content provided below
2. Be specific and cite details from the article
3. If the information isn't in the article, say so briefly - don't speculate
4. Give direct, focused answers without preamble
5. NEVER repeat the question or include any markup from the context in your response"""
        else:
            system_prompt = """You are a helpful research assistant. The user is browsing the web and has a question.

Since there's no page content available, provide a helpful general response.
If the question seems to be about specific page content, suggest the user first summarize the page."""

        user_prompt = f"""ARTICLE CONTEXT:
{full_context}

USER QUESTION: {request.question}

Provide a direct answer based on the article content above."""

        async def generate():
            try:
                async for token in rag_engine._stream_ollama(system_prompt, user_prompt, use_fast_model=True):
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/suggested-questions/{notebook_id}")
async def get_suggested_questions(notebook_id: str):
    """Get suggested questions for a notebook"""
    try:
        questions = await rag_engine.get_suggested_questions(notebook_id)
        return {"questions": questions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/insights/{notebook_id}")
async def get_proactive_insights(notebook_id: str, limit: int = 3):
    """Phase 4.1: Get proactive insights for a notebook.
    
    Analyzes document content to suggest interesting questions
    or observations the user might want to explore.
    """
    try:
        insights = await rag_engine.generate_proactive_insights(notebook_id, limit=limit)
        return {"insights": insights}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
