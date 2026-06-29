"""Chat API endpoints"""
import asyncio
import json
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from services.rag_engine import rag_engine
from services.query_orchestrator import get_orchestrator
from services.event_logger import log_chat_qa
from services.voice_engine import voice_engine
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Phase 14 article-pipeline single-instance lock (2026-06-11) ─────────────
#
# Both `reprocess articles` and `re-extract all` invoke
# `_summarize_articles_background` which fires multiple LLM calls per
# content article (classifier + summary + sectioner + entity extractor).
# If two of these run concurrently — or even just one is firing while the
# user kicks off another — the Ollama queue collapses and the backend
# OOMs. This shared lock + status dict ensures only one batch runs at a
# time. Second invocations get a clean "already running" reply with
# progress.
_ARTICLE_PIPELINE_LOCK = asyncio.Lock()
_ARTICLE_PIPELINE_STATUS: Dict[str, Any] = {
    "running": False,
    "operation": "",          # "reprocess" or "reextract"
    "sources_total": 0,
    "sources_processed": 0,
    "articles_touched": 0,
    "started_at": None,
    "finished_at": None,
    "last_error": None,
}


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
| `@curator brain status` | Show what I understand about your notebooks and detected connections |
| `@curator dismiss that connection` | Tell me a cross-notebook connection is wrong — I'll never show it again |
| `@curator that connection is useful` | Confirm a connection is valuable — I'll prioritize it in future briefs |
| `@curator ?` | Show this help |"""

_COLLECTOR_HELP = """**@collector — Your automated content collection agent**

| Command | What it does |
|---|---|
| `@collector subscribe <URL>` | **Subscribe** to a source — scrapes now + auto-checks for new content on schedule (YouTube channels, blogs, RSS) |
| `@collector add <URL>` | Add a **URL as a monitored source** (RSS feed or web page) |
| `@collector remove <URL>` | Remove a source |
| `@collector add keyword <topic>` | Track a **news keyword** for alerts |
| `@collector add note <content>` | Save a **user note** as a searchable source (e.g. *"note: the key insight is…"*) |
| `@collector note what we discussed above` | **Capture the current chat** as a synthesized markdown note-source |
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
| `@studio make flash cards on this` | Drop an interactive **Flash Cards** deck (3–50) onto the canvas — answer by click, type, or voice; the tutor reads feedback aloud |
| `@studio visualize this` | Create a **diagram, flowchart, or mind map** |
| `@studio make a video explainer` | Create a **video** with narration |
| `@studio ?` | Show this help |

**Tips:** Describe what you want naturally — specify format, style, duration, hosts, difficulty, etc. The conversation context is included automatically."""


def _build_mental_model_block(notebook_id: Optional[str]) -> Optional[str]:
    """Build a terse mental-model context block for RAG prompt injection.

    Curator Phase 3.5 (2026-05-13). Format intentionally short — 1 line
    per populated field — so RAG context bloat stays under ~100 tokens
    on the high-traffic chat path. Returns None when no notebook, no
    mental model, or no thesis (RAG should fall through to current
    behaviour silently).

    Failure-safe: ANY error (brain unavailable, DB lock, etc.) returns
    None so chat continues to work even if the curator brain is broken.
    """
    if not notebook_id:
        return None
    try:
        from services.curator_brain import curator_brain
        mm = curator_brain.get_mental_model(notebook_id)
        if not mm:
            return None
        thesis = (mm.get("thesis") or "").strip()
        if not thesis:
            return None
        lines = [f"Notebook thesis: {thesis}"]
        stage = (mm.get("stage") or "").strip()
        if stage:
            lines.append(f"Stage: {stage}")
        return "\n".join(lines)
    except Exception as _e:
        logger.debug(f"[chat] mental-model fetch for RAG injection failed (non-fatal): {_e}")
        return None


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
    chat_context: Optional[str] = None  # v1.5: @studio / @collector — recent conversation context for content generation and note synthesis


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
    
    if chat_query.question and chat_query.question.strip():
        voice_engine.add_observation(
            text_sample=chat_query.question,
            source_type="chat",
            voice_weight=0.5,
            notebook_id=chat_query.notebook_id
        )
    
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
        
        # Curator Phase 3.5 (2026-05-13): inject the notebook's mental
        # model (thesis + stage) into the RAG system prompt so every
        # chat reply is notebook-context aware, not just @curator chats.
        # Terse format, fail-silent on any error.
        extra_ctx = _build_mental_model_block(chat_query.notebook_id)

        # Standard path for simple/moderate queries
        result = await rag_engine.query(
            notebook_id=chat_query.notebook_id,
            question=chat_query.question,
            source_ids=chat_query.source_ids,
            top_k=chat_query.top_k or 4,
            enable_web_search=chat_query.enable_web_search,
            llm_provider=chat_query.llm_provider,
            extra_system_context=extra_ctx,
        )
        
        # Log Q&A for memory consolidation (fire-and-forget)
        try:
            sources_used = [c.get("source_id", "") for c in (result.get("citations") or [])] if isinstance(result, dict) else [c.source_id for c in getattr(result, 'citations', [])]
            log_chat_qa(chat_query.notebook_id, chat_query.question, result.answer if hasattr(result, 'answer') else result.get("answer", ""), sources_used)
        except Exception as _e:
            logger.debug(f"[chat] log_chat_qa failed (non-fatal): {_e}")

        # Curator Phase 2a: emit rag_query event for engagement tracking +
        # brain awareness of what the user is asking. Fire-and-forget.
        try:
            from services.curator_event_bus import event_bus
            _sources_used = sources_used if 'sources_used' in dir() else []
            event_bus.emit_now(
                actor="user",
                action="rag_query",
                notebook_id=chat_query.notebook_id,
                payload={
                    "question_chars": len(chat_query.question or ""),
                    "source_count": len(_sources_used),
                    "top_k": chat_query.top_k or 4,
                    "streaming": False,
                },
                outcome="success",
            )
        except Exception as _e:
            logger.debug(f"[chat] rag_query event emit failed: {_e}")

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
    if chat_query.question.strip():
        voice_engine.add_observation(
            text_sample=chat_query.question,
            source_type="chat",
            voice_weight=0.5,
            notebook_id=chat_query.notebook_id
        )

    # @mention routing — delegate to specialized agent streams via the
    # multi-intent dispatcher (supports compound messages like
    # "add this URL and set my focus to X").
    if chat_query.target == "curator":
        return StreamingResponse(
            _dispatch_multi_intent(chat_query, "curator", _stream_curator),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    if chat_query.target == "collector":
        return StreamingResponse(
            _dispatch_multi_intent(chat_query, "collector", _stream_collector),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    if chat_query.target == "research":
        return StreamingResponse(
            _dispatch_multi_intent(chat_query, "research", _stream_research),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    if chat_query.target == "studio":
        return StreamingResponse(
            _dispatch_multi_intent(chat_query, "studio", _stream_studio),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    if chat_query.target == "correspondent":
        return StreamingResponse(
            _dispatch_multi_intent(chat_query, "correspondent", _stream_correspondent),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    
    # Fallback intent detection: auto-route cross-notebook queries to Curator
    # Uses fast regex first; only invokes LLM classifier if regex is inconclusive
    if chat_query.target is None:
        from agents.curator import is_cross_notebook_query
        if is_cross_notebook_query(chat_query.question):
            print(f"[Chat] Auto-routing cross-notebook query to Curator: '{chat_query.question[:60]}...'")

            async def _wrap_auto_routed():
                # Phase A.3 (2026-05-22, F5): emit auto_routed signal BEFORE
                # the dispatcher so the frontend can render an "auto-routed"
                # badge in the curator agent header. Without this, the user
                # types a plain question, sees curator styling, and has no
                # idea why.
                yield f"data: {json.dumps({'type': 'auto_routed', 'to': 'curator', 'reason': 'cross_notebook_keywords'})}\n\n"
                async for chunk in _dispatch_multi_intent(chat_query, "curator", _stream_curator):
                    yield chunk

            return StreamingResponse(
                _wrap_auto_routed(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
    
    # CRITICAL: Clear visual cache for this notebook when new question is asked
    # This prevents stale visuals from a previous question being shown
    from services.visual_cache import visual_cache
    cleared = await visual_cache.clear_notebook(chat_query.notebook_id)
    if cleared > 0:
        print(f"[Chat] Cleared {cleared} stale visual cache entries for notebook {chat_query.notebook_id}")
    
    # Curator Phase 3.5: mental-model context for the streaming path.
    # Fetched once outside the generator so it's not re-fetched on retries.
    extra_ctx = _build_mental_model_block(chat_query.notebook_id)

    async def generate():
        answer_parts = []
        sources_used = []
        try:
            # Hold the foreground guard for the WHOLE RAG answer so every
            # background AI flood — community detection, curator digest
            # rebuilds, memory consolidation, HyDE, image description — PAUSES
            # while the user's chat runs, giving gemma the RAM + GPU to answer
            # fast on an 18 GB box. This is the symmetry the chat path was
            # missing: visual generation already holds the guard (and works),
            # but chat never did, so a post-upload background storm starved the
            # gemma query analysis into a timeout and thrashed the box into
            # swap → SIGTERM (observed 2026-06-23). Deadlock-proof: the query
            # path's OWN Ollama calls run inside this task tree, so their
            # await_background_clearance passes straight through (contextvar).
            from services.memory_steward import foreground_guard
            async with foreground_guard("chat"):
                # Free RAM for the answer model before it loads: evict anything not
                # in the chat working set (main + fast + embed) so gemma stays
                # resident without swap-thrash on the 18 GB box. No-op when nothing
                # extra is loaded; reclaims RAM after a visual/ingest left heavy
                # models hot (2026-06-29: chat hung for minutes waiting on gemma
                # under memory pressure during a newsletter ingest).
                try:
                    from services.memory_steward import free_for_pipeline
                    from config import settings as _settings
                    await free_for_pipeline(
                        keep=[_settings.ollama_model, _settings.ollama_fast_model, _settings.embedding_model],
                        reason="chat",
                    )
                except Exception as _ev_err:
                    logger.debug(f"[chat] pre-answer eviction skipped: {_ev_err}")
                async for chunk in rag_engine.query_stream(
                    notebook_id=chat_query.notebook_id,
                    question=chat_query.question,
                    source_ids=chat_query.source_ids,
                    top_k=chat_query.top_k or 4,
                    llm_provider=chat_query.llm_provider,
                    deep_think=chat_query.deep_think or False,
                    extra_system_context=extra_ctx,
                ):
                    if chunk.get("type") == "answer_chunk":
                        answer_parts.append(chunk.get("content", ""))
                    elif chunk.get("type") == "citations":
                        sources_used = [c.get("source_id", "") for c in chunk.get("citations", [])]
                    yield f"data: {json.dumps(chunk)}\n\n"
            # Log the completed Q&A interaction for memory consolidation
            try:
                log_chat_qa(chat_query.notebook_id, chat_query.question, "".join(answer_parts), sources_used)
            except Exception as _e:
                logger.debug(f"[chat] log_chat_qa failed (non-fatal): {_e}")
            # Curator Phase 2a: emit rag_query event for engagement tracking.
            try:
                from services.curator_event_bus import event_bus
                event_bus.emit_now(
                    actor="user",
                    action="rag_query",
                    notebook_id=chat_query.notebook_id,
                    payload={
                        "question_chars": len(chat_query.question or ""),
                        "answer_chars": sum(len(p) for p in answer_parts),
                        "source_count": len(sources_used),
                        "top_k": chat_query.top_k or 4,
                        "streaming": True,
                    },
                    outcome="success",
                )
            except Exception as _e:
                logger.debug(f"[chat] rag_query stream emit failed: {_e}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            try:
                from services.curator_event_bus import event_bus
                event_bus.emit_now(
                    actor="user",
                    action="rag_query",
                    notebook_id=chat_query.notebook_id,
                    payload={"error": str(e)[:200], "streaming": True},
                    outcome="failed",
                )
            except Exception:
                pass
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


async def _dispatch_multi_intent(chat_query: "ChatQuery", agent_type: str, handler_fn):
    """Multi-intent dispatcher: classify once, run the handler per classified action.

    Single-intent messages are handled exactly as before — one classifier call,
    one handler invocation with the classified action injected. Compound messages
    (classifier returns ``actions`` with multiple entries) run the handler once
    per action in sequence, swallowing intermediate ``done`` events so the SSE
    stream stays open until the final action completes.

    If classification fails, falls through to the handler with no injected
    action so the handler can do its own classification — preserving the
    pre-multi-intent error recovery path.
    """
    from services.intent_classifier import classify_intent
    from services.ollama_service import ollama_service

    q = chat_query.question

    # Help requests short-circuit inside each handler; let them handle it natively.
    if _is_help_request(q):
        async for chunk in handler_fn(chat_query):
            yield chunk
        return

    # 2026-06-10 — agent-specific keyword shortcuts BEFORE the LLM classifier.
    # The LLM gets confused at 27+ intents and collapses simple verb-noun
    # queries ("backfill articles", "whats hot") into the default fallback.
    # When the shortcut matches, we skip the LLM entirely and inject the
    # resolved action so the handler treats it like any other classified call.
    quick_action = None
    if agent_type == "correspondent":
        try:
            qi_intent, qi_params = _quick_intent_for_correspondent(q)
            if qi_intent:
                quick_action = {"intent": qi_intent, "params": qi_params or {}, "confidence": 1.0}
                logger.info(f"[multi-intent] correspondent quick-intent matched: {qi_intent}")
        except Exception as _qi_e:
            logger.debug(f"[multi-intent] quick-intent check failed: {_qi_e}")
    elif agent_type == "curator":
        # Deterministic routing for the canonical report commands so they can never
        # fall to the LLM's default-intent fallback (2026-06-29: classifier timeouts
        # were misrouting "Morning brief" → cross_notebook_search). Tight exact-phrase
        # match — broad questions still go to the classifier.
        _qc = q.strip().lower().rstrip("!.?")
        if _qc in ("morning brief", "morning briefing", "brief me", "catch me up", "what did i miss"):
            quick_action = {"intent": "morning_brief", "params": {}, "confidence": 1.0}
        elif _qc in ("weekly wrap", "weekly wrap up", "weekly wrap-up", "weekly wrapup", "week in review", "weekly recap"):
            quick_action = {"intent": "weekly_wrap_up", "params": {}, "confidence": 1.0}
        if quick_action:
            logger.info(f"[multi-intent] curator quick-intent matched: {quick_action['intent']}")

    if quick_action:
        async for chunk in handler_fn(chat_query, injected_action=quick_action):
            yield chunk
        return

    # Try to classify the message into one or more actions.
    try:
        classified = await classify_intent(q, agent_type)
    except Exception as e:
        logger.warning(f"[multi-intent] Classification failed for {agent_type}: {e}; delegating to handler")
        async for chunk in handler_fn(chat_query):
            yield chunk
        return

    actions = classified.get("actions") or []
    if not actions:
        # Defensive: classifier should always return at least one action, but
        # if something pathological happens, let the handler do its own thing.
        async for chunk in handler_fn(chat_query):
            yield chunk
        return

    if len(actions) == 1:
        # Fast path — behaviour is identical to pre-multi-intent code. The
        # handler still sees classified["intent"]/classified["params"] unchanged.
        async for chunk in handler_fn(chat_query, injected_action=classified):
            yield chunk
        return

    # Compound message — run each action in sequence.
    intent_list = ", ".join(a["intent"] for a in actions)
    logger.info(f"[multi-intent] {agent_type}: {len(actions)} actions → {intent_list}")

    for idx, action in enumerate(actions):
        is_last = (idx == len(actions) - 1)

        # Visible separator so the user sees the agent moving between actions.
        if idx > 0:
            sep_msg = f"Action {idx + 1}/{len(actions)}: {action['intent']}"
            sep_payload = json.dumps({
                "type": "status",
                "message": sep_msg,
                "query_type": agent_type,
            })
            yield f"data: {sep_payload}\n\n"

        # Each handler call sees a single-intent classified dict, so its
        # internal if/elif chain fires exactly as it does in single-intent mode.
        single_classified = {
            "intent": action["intent"],
            "params": action.get("params", {}) or {},
            "confidence": action.get("confidence", 0.5),
            "actions": [action],
        }

        async for chunk in handler_fn(chat_query, injected_action=single_classified):
            # Swallow the handler's "done" event for every action except the
            # last one — otherwise the UI closes the stream after the first
            # action completes and subsequent actions are never shown.
            if not is_last and '"type": "done"' in chunk:
                continue
            yield chunk


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
                        lines.append(f"- {cp}")
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


async def _ingest_source_background(notebook_id: str, source_id: str, text: str, filename: str, source_type: str):
    """Background task: run the heavy RAG ingest pipeline (chunk → embed → entities).

    Creates the source record upfront so the UI shows it immediately as
    "processing", then this task updates it to "completed" when done.
    Runs outside the chat SSE stream to avoid blocking the response and
    to let Ollama unload the main model before embedding starts.

    Mirrors services/source_ingestion.create_and_ingest_source:
    1. Extract content_date + update source
    2. RAG ingest (chunk + embed)
    3. Update source with chunks/status
    4. Auto-tag (non-fatal)
    5. Log document_captured event
    6. Notify via websocket
    """
    from services.rag_engine import rag_engine
    from storage.source_store import source_store as _ss
    from api.constellation_ws import notify_source_updated
    try:
        # 1. Content date extraction (non-fatal)
        try:
            from services.content_date_extractor import extract_content_date
            content_date = extract_content_date(filename, text[:800] if text else "")
            if content_date:
                await _ss.update(notebook_id, source_id, {"content_date": content_date})
        except Exception as _dt_err:
            logger.debug(f"[ingest] content_date extraction failed (non-fatal): {_dt_err}")

        # 2. RAG ingestion
        result = await rag_engine.ingest_document(
            notebook_id=notebook_id,
            source_id=source_id,
            text=text,
            filename=filename,
            source_type=source_type,
        )
        chunks = result.get("chunks", 0)
        characters = result.get("characters", len(text))

        # 3. Update source
        await _ss.update(notebook_id, source_id, {
            "chunks": chunks,
            "characters": characters,
            "status": "completed",
            "content": text,
        })
        logger.info(f"[ingest] Background ingest done: {filename} — {chunks} chunks, {characters} chars")

        # 4. Auto-tag (non-fatal) — matches services/source_ingestion.py
        try:
            from services.auto_tagger import auto_tagger
            await auto_tagger.tag_source_in_notebook(
                notebook_id, source_id, filename, text[:3000]
            )
        except Exception as _tag_err:
            logger.debug(f"[ingest] Auto-tagging failed (non-fatal): {_tag_err}")

        # 5. Log event (non-fatal)
        try:
            from services.event_logger import log_document_captured
            log_document_captured(notebook_id, filename, filename, source_type)
        except Exception as _ev_err:
            logger.debug(f"[ingest] Event log failed (non-fatal): {_ev_err}")

        # 6. Notify websocket
        await notify_source_updated({
            "notebook_id": notebook_id,
            "source_id": source_id,
            "status": "completed",
            "title": filename,
            "chunks": chunks,
            "characters": characters,
        })
    except Exception as e:
        logger.error(f"[ingest] Background ingest FAILED for {filename}: {e}")
        await _ss.update(notebook_id, source_id, {
            "status": "failed",
            "error": str(e)[:200],
        })
        try:
            await notify_source_updated({
                "notebook_id": notebook_id,
                "source_id": source_id,
                "status": "failed",
                "title": filename,
                "error": str(e)[:100],
            })
        except Exception:
            pass


async def _ingest_youtube_batch_background(
    notebook_id: str,
    videos: list,
    skip_url: str = "",
    max_concurrent: int = 2,
):
    """Background task: scrape + ingest a batch of YouTube video transcripts.

    Each video is scraped for its transcript, a source record is created, and
    the heavy RAG pipeline runs in the background.  A semaphore limits
    concurrent transcript fetches to *max_concurrent* to stay gentle on RAM
    and network.

    *skip_url* is the video that was already scraped in Step 1 — we skip it
    to avoid duplicates.
    """
    from services.web_scraper import web_scraper
    from storage.source_store import source_store as _ss
    from utils.tasks import safe_create_task

    logger.info(f"[YT-batch] === STARTED === notebook={notebook_id[:8]}, {len(videos)} videos, skip_url={skip_url}")

    # Normalise the skip URL to a video ID for robust comparison
    skip_vid = web_scraper._extract_youtube_id(skip_url) if skip_url else None
    logger.info(f"[YT-batch] skip_vid={skip_vid}")
    sem = asyncio.Semaphore(max_concurrent)
    ingested = 0
    skipped = 0
    failed = 0

    async def _process_one(video: dict):
        nonlocal ingested, skipped, failed
        vid = video.get("video_id", "")
        if vid == skip_vid:
            logger.info(f"[YT-batch] SKIP (already scraped): {vid}")
            skipped += 1
            return
        video_url = video.get("url") or f"https://www.youtube.com/watch?v={vid}"
        logger.info(f"[YT-batch] Processing: {vid} — {video.get('title', '?')[:50]}")
        async with sem:
            try:
                MIN_SOURCE_CHARS = 1000  # Reject shallow transcripts
                yt_result = await web_scraper._scrape_youtube(video_url)
                if not (yt_result.get("success") and yt_result.get("text")):
                    err = yt_result.get('error', 'unknown')
                    logger.info(f"[YT-batch] No transcript for {vid}: {err}")
                    failed += 1
                    return
                title = yt_result.get("title", video.get("title", f"Video {vid}"))
                text = f"Title: {title}\n\nTranscript:\n{yt_result['text']}"
                wc = len(yt_result["text"].split())
                if len(text) < MIN_SOURCE_CHARS:
                    logger.info(f"[YT-batch] SHALLOW transcript for {vid}: {len(text)} chars < {MIN_SOURCE_CHARS} — skipped")
                    failed += 1
                    return
                logger.info(f"[YT-batch] Transcript OK: {vid} — {wc:,} words")
                src_meta = {
                    "type": "youtube", "format": "youtube",
                    "url": video_url, "status": "processing",
                    "chunks": 0, "characters": 0,
                    "capture_type": "youtube", "user_provided": True,
                    "word_count": wc,
                }
                rec = await _ss.create(notebook_id=notebook_id, filename=title, metadata=src_meta)
                sid = rec["id"]
                logger.info(f"[YT-batch] Source created: {sid[:8]} — firing ingest task")
                safe_create_task(
                    _ingest_source_background(notebook_id, sid, text, title, "youtube"),
                    name=f"yt-batch-{sid[:8]}",
                )
                ingested += 1
            except Exception as e:
                import traceback
                logger.warning(f"[YT-batch] EXCEPTION for {vid}: {e}")
                traceback.print_exc()
                failed += 1

    tasks = [_process_one(v) for v in videos]
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info(f"[YT-batch] === DONE === {ingested} ingested, {skipped} skipped (dup), {failed} failed out of {len(videos)} total")


async def _ingest_feed_articles_background(
    notebook_id: str,
    articles: list,
    max_concurrent: int = 2,
):
    """Background task: scrape + ingest articles found on a feed/index page.

    Each article URL is scraped, a source record is created, and the heavy
    RAG pipeline runs in the background.  A semaphore limits concurrency.
    """
    from services.web_scraper import web_scraper
    from storage.source_store import source_store as _ss
    from api.constellation_ws import notify_source_updated

    MIN_SOURCE_CHARS = 1000
    logger.info(f"[feed-ingest] === STARTED === notebook={notebook_id[:8]}, {len(articles)} articles")

    sem = asyncio.Semaphore(max_concurrent)
    ingested = 0
    failed = 0

    async def _process_one(article):
        nonlocal ingested, failed
        art_url = article.get("url", "")
        art_title = article.get("title", art_url)
        async with sem:
            try:
                scraped = await web_scraper.scrape_with_html(art_url, extension_fallback=True)
                if not scraped.get("success") or not scraped.get("text"):
                    logger.warning(f"[feed-ingest] SKIP (no content): {art_url}")
                    failed += 1
                    return
                text = scraped["text"]
                if len(text) < MIN_SOURCE_CHARS:
                    logger.info(f"[feed-ingest] SKIP (shallow {len(text)} chars): {art_url}")
                    failed += 1
                    return

                title = scraped.get("title", art_title)
                wc = scraped.get("word_count", len(text.split()))

                # Check for duplicate URL
                existing = await _ss.list(notebook_id)
                existing_urls = {s.get("url") or s.get("metadata", {}).get("url", "") for s in existing}
                if art_url in existing_urls:
                    logger.info(f"[feed-ingest] SKIP (dup): {art_url}")
                    return

                # Detect correct source type — a feed page may link to
                # YouTube videos, arxiv papers, or regular web articles.
                if web_scraper._is_youtube_url(art_url):
                    art_src_type = "youtube"
                elif web_scraper._is_arxiv_url(art_url):
                    art_src_type = "arxiv"
                else:
                    art_src_type = "web"

                src_meta = {
                    "type": art_src_type, "format": art_src_type,
                    "url": art_url, "status": "processing",
                    "chunks": 0, "characters": 0,
                    "capture_type": art_src_type, "user_provided": True,
                    "word_count": wc,
                }
                source_rec = await _ss.create(
                    notebook_id=notebook_id, filename=title, metadata=src_meta
                )
                _sid = source_rec["id"]
                await _ingest_source_background(notebook_id, _sid, text, title, art_src_type)
                ingested += 1
                logger.info(f"[feed-ingest] OK: {title} ({wc} words)")
            except Exception as e:
                logger.error(f"[feed-ingest] FAILED {art_url}: {e}")
                failed += 1

    tasks = [_process_one(a) for a in articles]
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info(f"[feed-ingest] === DONE === {ingested} ingested, {failed} failed out of {len(articles)} total")


async def _stream_collector(chat_query: ChatQuery, injected_action: Optional[Dict[str, Any]] = None):
    """Stream a Collector response in SSE format.
    
    LLM-based NLP intent router — anything you can do in the Collector settings
    panel, you can do here via natural language.

    If ``injected_action`` is provided, it bypasses the LLM classifier and uses
    the provided {intent, params} directly. This is used by the multi-intent
    dispatcher to execute each classified action in sequence.
    """
    import re as _re
    from storage.source_store import source_store
    from agents.collector import get_collector, CollectionMode, ApprovalMode
    from services.ollama_service import ollama_service
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
        follow_ups = ['Show my collection status', 'Check source health', 'Collect now']
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
                entry = ArchivalMemoryEntry(
                    content=msg, source_type=MemorySourceType.AGENT_GENERATED,
                    importance=MemoryImportance.MEDIUM, notebook_id=notebook_id,
                )
                # P0.5 (2026-05-15): offload sync embedding+write to loop executor
                # so the streaming event loop is not blocked by Ollama HTTP.
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    # No running loop (rare — tests / non-streaming) — fall back to sync.
                    memory_store.add_archival_memory(entry, namespace=AgentNamespace.CURATOR)
                    return
                future = loop.run_in_executor(
                    None,
                    lambda e=entry: memory_store.add_archival_memory(e, namespace=AgentNamespace.CURATOR),
                )
                def _log_failure(fut):
                    exc = fut.exception()
                    if exc:
                        logger.warning(f"[chat] Memory store failed (async): {exc}")
                future.add_done_callback(_log_failure)
            except Exception as _e:
                logger.warning(f"[chat] Memory store failed: {_e}")

        # Helper: extract URL from message (simple, reliable)
        url_match = _re.search(r'(https?://[^\s,]+)', q)

        # =================================================================
        # LLM-based Intent Classification (bypassed if injected by dispatcher)
        # =================================================================
        if injected_action:
            classified = injected_action
        else:
            classified = await classify_intent(q, "collector")
        intent = classified["intent"]
        params = classified.get("params", {})

        # Curator Phase 2a: emit collector intent dispatch event.
        try:
            from services.curator_event_bus import event_bus
            event_bus.emit_now(
                actor="@collector",
                action="collector_intent_dispatched",
                notebook_id=notebook_id,
                intent=intent,
                payload={
                    "message_chars": len(q),
                    "confidence": classified.get("confidence", 0.5),
                    "injected": bool(injected_action),
                },
            )
        except Exception as _e:
            logger.debug(f"[chat] collector intent emit failed: {_e}")

        # Safety net: if message contains a URL but intent fell through to
        # show_status (fallback), the user almost certainly wants add_url.
        if intent == "show_status" and url_match:
            logger.info(f"[collector] Intent override: show_status → add_url (URL detected in message)")
            intent = "add_url"
            if not params.get("url"):
                params["url"] = url_match.group(1).rstrip('.,;:)')

        # Safety net: detect explicit note-creation phrases and force add_note.
        # Small fast-models sometimes mis-route these to show_status / add_keyword.
        # We only override when the user isn't also pasting a URL (add_url wins then).
        if intent in ("show_status", "add_keyword", "set_intent", "set_subject") and not url_match:
            _q_strip = q.strip()
            _q_low = _q_strip.lower()
            _note_triggers = (
                "add a note", "add this note", "add the note", "save a note",
                "save this note", "save the note", "jot this down", "jot down",
                "remember this", "take a note", "capture this note",
                "note to my sources", "note to sources", "here's a note",
                "heres a note", "log this note",
            )
            # Also catch leading "note:" or "note -"
            starts_with_note = bool(_re.match(r'^\s*note\s*[:\-—]\s*', _q_strip, _re.IGNORECASE))
            if any(t in _q_low for t in _note_triggers) or starts_with_note:
                logger.info(f"[collector] Intent override: {intent} → add_note (note phrase detected)")
                intent = "add_note"
                # Strip the trigger phrase to recover the note body
                body = _q_strip
                if starts_with_note:
                    body = _re.sub(r'^\s*note\s*[:\-—]\s*', '', body, count=1, flags=_re.IGNORECASE)
                else:
                    # Remove the matched trigger + common connectors ("to my sources", "for me", etc.)
                    body = _re.sub(
                        r'^(?:please\s+)?'
                        r'(?:add|save|jot(?:\s+down)?|take|log|capture|remember)\s+'
                        r'(?:a|this|the)?\s*note\s*'
                        r'(?:down)?\s*'
                        r'(?:to\s+(?:my\s+)?sources?)?\s*'
                        r'(?:for\s+me)?\s*'
                        r'[:.\-—]?\s*',
                        '',
                        body,
                        count=1,
                        flags=_re.IGNORECASE,
                    )
                body = body.strip().lstrip(':.-—').strip()
                # Preserve whatever the classifier already extracted, otherwise
                # hand the cleaned body to the handler.
                if not params.get("content") and body:
                    params["content"] = body
                # Empty body + chat_context available → treat as from_chat
                if not body and (getattr(chat_query, "chat_context", None) or "").strip():
                    params["from_chat"] = True

        # Safety net: if classified as add_url but message mentions channel/subscribe/follow,
        # user likely wants recurring subscription, not one-off URL add.
        if intent == "add_url" and url_match:
            _q_low = q.lower()
            if any(kw in _q_low for kw in (
                "add the channel", "channel to sources", "subscribe", "follow", "monitor",
                "keep checking", "watch for new",
            )):
                logger.info(f"[collector] Intent override: add_url → subscribe (subscription keyword detected)")
                intent = "subscribe"
                if not params.get("url"):
                    params["url"] = url_match.group(1).rstrip('.,;:)')

        # -----------------------------------------------------------------
        # SUBSCRIBE (resolve → scrape now → register recurring feed)
        # -----------------------------------------------------------------
        if intent == "subscribe":
            url = (params.get("url") or "").strip().rstrip('.,;:)')
            if not url and url_match:
                url = url_match.group(1).rstrip('.,;:)')
            if url:
                from services.web_scraper import web_scraper
                import time as _time
                _op_start = _time.time()
                _trace = [f"[Subscribe] START url={url}"]

                yield f"data: {json.dumps({'type': 'status', 'message': f'{collector_name} resolving subscription target...', 'query_type': 'collector'})}\n\n"

                try:
                    sub = await web_scraper.resolve_subscription_target(url)
                    _trace.append(f"  resolve: type={sub.get('source_type')}, feed={sub.get('feed_url')}, channel={sub.get('channel_name')}")
                except Exception as e:
                    logger.warning(f"[Subscribe] resolve_subscription_target failed for {url}: {e}")
                    _trace.append(f"  resolve: FAILED — {e}")
                    reply = f"**Could not resolve subscription target:** {url}\n- Error: {e}"
                    follow_ups = ['Show my collection status', 'Show my sources']
                    sub = None

                if sub:
                    src_type = sub["source_type"]
                    feed_url = sub.get("feed_url")
                    channel_name = sub.get("channel_name") or url
                    schedule_raw = params.get("schedule")
                    freq = _parse_freq(schedule_raw) if schedule_raw else sub.get("default_schedule", "weekly")
                    # Message-level fallback: catch schedule keywords in compound
                    # messages that the classifier may have missed
                    _q_low = q.lower()
                    if not schedule_raw:
                        if "hourly" in _q_low or "every hour" in _q_low:
                            freq = "hourly"
                        elif "daily" in _q_low or "every day" in _q_low or "each day" in _q_low:
                            freq = "daily"
                        elif "weekly" in _q_low or "every week" in _q_low:
                            freq = "weekly"

                    from storage.source_store import source_store as _src_store
                    from utils.tasks import safe_create_task
                    notebook_id = chat_query.notebook_id
                    lines = []

                    # ── Step 1: Immediate scrape + ingest as source ──
                    immediate_url = sub.get("immediate_url", url)
                    yield f"data: {json.dumps({'type': 'status', 'message': f'{collector_name} scraping content...', 'query_type': 'collector'})}\n\n"

                    immediate_ok = False
                    is_yt_video = web_scraper._is_youtube_url(immediate_url) and ("/watch" in immediate_url or "youtu.be/" in immediate_url or "/shorts/" in immediate_url)
                    MIN_SOURCE_CHARS = 1000  # Reject shallow scrapes

                    if src_type == "youtube_channel" and is_yt_video:
                        # Scrape the specific video transcript
                        try:
                            yt_result = await web_scraper._scrape_youtube(immediate_url)
                            if yt_result.get("success") and yt_result.get("text"):
                                wc = len(yt_result["text"].split())
                                yt_title = yt_result.get("title", "Video")
                                content = f"Title: {yt_title}\n\nTranscript:\n{yt_result['text']}"
                                if len(content) < MIN_SOURCE_CHARS:
                                    _trace.append(f"  transcript: SHALLOW ({len(content)} chars < {MIN_SOURCE_CHARS}) — skipped")
                                    lines.append(f"**Immediate:** Transcript too short ({len(content)} chars) — skipped")
                                else:
                                    # Create source record immediately (shows in UI as "processing")
                                    try:
                                        src_meta = {
                                            "type": "youtube", "format": "youtube",
                                            "url": immediate_url, "status": "processing",
                                            "chunks": 0, "characters": 0,
                                            "capture_type": "youtube", "user_provided": True,
                                            "word_count": wc,
                                        }
                                        source_rec = await _src_store.create(
                                            notebook_id=notebook_id, filename=yt_title, metadata=src_meta
                                        )
                                        _sid = source_rec["id"]
                                        # Fire-and-forget: heavy RAG pipeline runs in background
                                        safe_create_task(
                                            _ingest_source_background(notebook_id, _sid, content, yt_title, "youtube"),
                                            name=f"subscribe-ingest-{_sid[:8]}"
                                        )
                                        _trace.append(f"  source: CREATED id={_sid}, ingest queued in background")
                                        lines.append(f"**Immediate:** Scraped transcript from \"{yt_title}\" ({wc:,} words)")
                                        immediate_ok = True
                                    except Exception as _ingest_err:
                                        _trace.append(f"  source: FAILED — {_ingest_err}")
                                        logger.warning(f"[Subscribe] Source creation failed for {yt_title}: {_ingest_err}")
                                        lines.append(f"**Immediate:** Scraped transcript but failed to save: {_ingest_err}")
                                    _trace.append(f"  transcript: OK {wc} words — {yt_title}")
                            else:
                                _trace.append(f"  transcript: FAILED — {yt_result.get('error', 'no text')}")
                        except Exception as _yt_err:
                            _trace.append(f"  transcript: EXCEPTION — {_yt_err}")
                            logger.warning(f"[Subscribe] YouTube transcript exception: {_yt_err}")
                    
                    if not immediate_ok:
                        try:
                            # Use cached scrape from resolve_subscription_target if available
                            cached = sub.get("_scraped")
                            if cached and cached.get("success") and cached.get("text"):
                                scraped = cached
                                _trace.append("  html_scrape: used cached result")
                            else:
                                scraped = await web_scraper.scrape_with_html(immediate_url, extension_fallback=True)
                            if scraped.get("success") and scraped.get("text"):
                                wc = len(scraped["text"].split())
                                pg_title = scraped.get("title", "Page")
                                if len(scraped["text"]) < MIN_SOURCE_CHARS:
                                    _trace.append(f"  html_scrape: SHALLOW ({len(scraped['text'])} chars < {MIN_SOURCE_CHARS}) — skipped")
                                    lines.append(f"**Immediate:** Page too shallow ({len(scraped['text'])} chars) — skipped")
                                else:
                                    # Create source record immediately (shows in UI as "processing")
                                    try:
                                        src_meta = {
                                            "type": "web", "format": "web",
                                            "url": immediate_url, "status": "processing",
                                            "chunks": 0, "characters": 0,
                                            "user_provided": True, "word_count": wc,
                                        }
                                        source_rec = await _src_store.create(
                                            notebook_id=notebook_id, filename=pg_title, metadata=src_meta
                                        )
                                        _sid = source_rec["id"]
                                        # Fire-and-forget: heavy RAG pipeline runs in background
                                        safe_create_task(
                                            _ingest_source_background(notebook_id, _sid, scraped["text"], pg_title, "web"),
                                            name=f"subscribe-ingest-{_sid[:8]}"
                                        )
                                        _trace.append(f"  source: CREATED id={_sid}, ingest queued in background")
                                        lines.append(f"**Immediate:** Scraped \"{pg_title}\" ({wc:,} words)")
                                        immediate_ok = True
                                    except Exception as _ingest_err:
                                        _trace.append(f"  source: FAILED — {_ingest_err}")
                                        logger.warning(f"[Subscribe] Source creation failed for {pg_title}: {_ingest_err}")
                                        lines.append(f"**Immediate:** Scraped but failed to save: {_ingest_err}")
                                    _trace.append(f"  html_scrape: OK {wc} words — {pg_title}")
                            else:
                                _trace.append(f"  html_scrape: FAILED — {scraped.get('error', 'no text')}")
                        except Exception as _scrape_err:
                            _trace.append(f"  html_scrape: EXCEPTION — {_scrape_err}")
                            logger.warning(f"[Subscribe] HTML scrape exception: {_scrape_err}")
                    
                    if not immediate_ok:
                        lines.append(f"**Immediate:** Could not scrape content from {immediate_url} (will still subscribe)")
                        _trace.append(f"  scrape: ALL METHODS FAILED for {immediate_url}")

                    # ── Step 2: Register subscription ──
                    yield f"data: {json.dumps({'type': 'status', 'message': f'{collector_name} setting up subscription...', 'query_type': 'collector'})}\n\n"

                    if src_type == "youtube_channel" and feed_url:
                        # Register YouTube channel as RSS feed
                        rss_feeds = list(config.sources.get("rss_feeds", []))
                        if feed_url not in rss_feeds:
                            rss_feeds.append(feed_url)
                            collector_agent.update_config({
                                "sources": {**config.sources, "rss_feeds": rss_feeds},
                                "schedule": {**config.schedule, "frequency": freq},
                            })
                        lines.append(f"\n**Subscribed:** {channel_name}")
                        lines.append(f"- YouTube channel RSS feed registered")
                        lines.append(f"- Schedule: **{freq}** checks for new uploads")
                        lines.append(f"- New videos will be auto-scraped for transcripts")
                        _trace.append(f"  rss: registered feed={feed_url}, schedule={freq}")
                        _notify_curator(f"Collector subscribed to YouTube channel: {channel_name} ({feed_url}). Schedule: {freq}.")

                    elif src_type == "rss_feed" and feed_url:
                        rss_feeds = list(config.sources.get("rss_feeds", []))
                        if feed_url not in rss_feeds:
                            rss_feeds.append(feed_url)
                            collector_agent.update_config({
                                "sources": {**config.sources, "rss_feeds": rss_feeds},
                                "schedule": {**config.schedule, "frequency": freq},
                            })
                        lines.append(f"\n**Subscribed:** {channel_name}")
                        lines.append(f"- RSS feed registered: {feed_url}")
                        lines.append(f"- Schedule: **{freq}** checks for new content")
                        _notify_curator(f"Collector subscribed to RSS feed: {channel_name} ({feed_url}). Schedule: {freq}.")

                    elif src_type == "feed_page":
                        feed_pages = list(config.sources.get("feed_pages", []))
                        if url not in feed_pages:
                            feed_pages.append(url)
                            collector_agent.update_config({
                                "sources": {**config.sources, "feed_pages": feed_pages},
                                "schedule": {**config.schedule, "frequency": freq},
                            })
                        lines.append(f"\n**Subscribed:** {channel_name}")
                        lines.append(f"- Feed page registered for article monitoring")
                        lines.append(f"- Schedule: **{freq}** checks for new articles")
                        _notify_curator(f"Collector subscribed to feed page: {channel_name} ({url}). Schedule: {freq}.")

                    else:
                        # Couldn't find a feed — register as web page with schedule
                        web_pages = list(config.sources.get("web_pages", []))
                        if url not in web_pages:
                            web_pages.append(url)
                            collector_agent.update_config({
                                "sources": {**config.sources, "web_pages": web_pages},
                                "schedule": {**config.schedule, "frequency": freq},
                            })
                        lines.append(f"\n**Registered:** {channel_name}")
                        lines.append(f"- No RSS feed found — registered as monitored web page")
                        lines.append(f"- Schedule: **{freq}** checks for changes")
                        _notify_curator(f"Collector registered web source (no feed found): {channel_name} ({url}). Schedule: {freq}.")

                    # ── Step 3: Batch-scrape playlist videos and/or channel feed ──
                    batch_videos = []  # videos to scrape in background
                    logger.info(f"[Subscribe] Step 3: sub keys={list(sub.keys())}")
                    
                    # 3a: Playlist videos (from URL with &list= or /playlist?list=)
                    playlist_videos = sub.get("playlist_videos", [])
                    playlist_id = sub.get("playlist_id")
                    logger.info(f"[Subscribe] Step 3a: playlist_id={playlist_id}, playlist_videos={len(playlist_videos)}")
                    if playlist_videos:
                        batch_videos = list(playlist_videos)  # copy
                        lines.append(f"\n**Playlist** (`{playlist_id}`): {len(playlist_videos)} videos found — scraping transcripts in background")
                        _trace.append(f"  playlist: {len(playlist_videos)} videos from {playlist_id}")
                    
                    # 3b: Recent channel feed videos (if no playlist, or in addition to)
                    if src_type == "youtube_channel" and feed_url:
                        logger.info(f"[Subscribe] Step 3b: fetching feed {feed_url}")
                        try:
                            import feedparser
                            feed = feedparser.parse(feed_url)
                            logger.info(f"[Subscribe] Step 3b: feedparser returned {len(feed.entries)} entries, bozo={feed.bozo}")
                            if feed.bozo:
                                logger.warning(f"[Subscribe] Feed parse warning: {feed.bozo_exception}")
                            feed_video_ids = set(v.get("video_id") for v in batch_videos)
                            added_from_feed = 0
                            if feed.entries:
                                for entry in feed.entries[:10]:
                                    # YouTube Atom feeds have yt:videoId as a dedicated field
                                    vid = entry.get("yt_videoid")
                                    if not vid:
                                        # Fallback: parse from link
                                        link = entry.get("link", "")
                                        if "watch?v=" in link:
                                            vid = link.split("watch?v=")[-1].split("&")[0]
                                        elif "youtu.be/" in link:
                                            vid = link.split("/")[-1].split("?")[0]
                                    entry_title = entry.get("title", f"Video {vid}")
                                    logger.info(f"[Subscribe] Feed entry: vid={vid}  title={entry_title[:50]}")
                                    if vid and vid not in feed_video_ids:
                                        batch_videos.append({
                                            "video_id": vid,
                                            "title": entry_title,
                                            "url": f"https://www.youtube.com/watch?v={vid}",
                                        })
                                        feed_video_ids.add(vid)
                                        added_from_feed += 1
                                if not playlist_videos:
                                    lines.append(f"\n**Recent uploads:** {added_from_feed} videos found — scraping transcripts in background")
                                elif added_from_feed > 0:
                                    lines.append(f"**Channel feed:** {added_from_feed} additional recent videos queued")
                                _trace.append(f"  feed: {len(feed.entries)} entries, {added_from_feed} added to batch")
                            else:
                                logger.warning(f"[Subscribe] Feed returned 0 entries for {feed_url}")
                        except Exception as _feed_err:
                            import traceback
                            _trace.append(f"  feed: FAILED — {_feed_err}")
                            logger.warning(f"[Subscribe] Feed parse failed: {_feed_err}")
                            traceback.print_exc()
                    else:
                        logger.info(f"[Subscribe] Step 3b skipped: src_type={src_type}, feed_url={feed_url}")
                    
                    # Fire background batch scrape
                    logger.info(f"[Subscribe] Step 3 TOTAL: {len(batch_videos)} videos in batch, skip_url={immediate_url}")
                    if batch_videos:
                        for i, bv in enumerate(batch_videos):
                            logger.info(f"[Subscribe]   batch[{i}]: {bv.get('video_id')} — {bv.get('title', '?')[:40]}")
                        safe_create_task(
                            _ingest_youtube_batch_background(
                                notebook_id=notebook_id,
                                videos=batch_videos,
                                skip_url=immediate_url,
                                max_concurrent=2,
                            ),
                            name=f"yt-batch-{notebook_id[:8]}",
                        )
                        _trace.append(f"  batch: {len(batch_videos)} videos queued for background scrape")
                    else:
                        logger.warning(f"[Subscribe] Step 3: NO videos to batch-scrape!")

                    _elapsed = _time.time() - _op_start
                    _trace.append(f"  DONE in {_elapsed:.1f}s")
                    logger.info("\n".join(_trace))
                    reply = "\n".join(lines)
                    follow_ups = ['Collect now', 'Show my subscription sources', 'Subscribe to another channel']
            else:
                reply = "Please provide a URL to subscribe to. Example: *\"subscribe to https://youtube.com/@stanfordgsb\"*"
                follow_ups = ['Show my sources', 'Show my collection status']

        # -----------------------------------------------------------------
        # ADD URL (with optional schedule)
        # -----------------------------------------------------------------
        elif intent == "add_url":
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
                    # Message-level fallback: catch schedule keywords the LLM may have
                    # missed when multiple commands are bundled in one message
                    # (e.g. "scrape this video AND collect daily")
                    if freq == "manual":
                        _q_low = q.lower()
                        if "hourly" in _q_low or "every hour" in _q_low:
                            freq = "hourly"
                        elif "daily" in _q_low or "every day" in _q_low or "each day" in _q_low:
                            freq = "daily"
                        elif "weekly" in _q_low or "every week" in _q_low:
                            freq = "weekly"
                        if freq != "manual":
                            logger.info(f"[add_url] Message-level schedule detected: {freq}")

                    from services.web_scraper import web_scraper

                    # Use scrape_with_html so we can check for index pages
                    scraped = await web_scraper.scrape_with_html(url, extension_fallback=True)
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

                            sched_label = freq if freq != "manual" else "weekly"
                            lines = [f"**Feed page registered:** [{scraped.get('title', url)}]({url})",
                                     f"- **Schedule:** {sched_label} checks for new articles",
                                     f"- **{len(article_links)} articles detected** on this page\n"]
                            for a in article_links[:8]:
                                lines.append(f"- [{a['title']}]({a['url']})")
                            if len(article_links) > 8:
                                lines.append(f"- *...and {len(article_links) - 8} more*")

                            # Immediately scrape + ingest top articles in background
                            from utils.tasks import safe_create_task
                            safe_create_task(
                                _ingest_feed_articles_background(
                                    notebook_id=notebook_id,
                                    articles=article_links,
                                    max_concurrent=2,
                                ),
                                name=f"feed-ingest-{notebook_id[:8]}",
                            )
                            lines.append(f"\nScraping {len(article_links)} articles now — they'll appear in your sources shortly.")
                            reply = "\n".join(lines)
                            _notify_curator(f"Collector registered feed page: {url}. {len(article_links)} articles being ingested.")

                        follow_ups = ['Collect now', 'Show my collection status', 'Add another source']

                    elif scraped.get("success") and scraped.get("text"):
                        # ── SINGLE URL FLOW ──────────────────────────────
                        # Register in collector AND immediately ingest the
                        # already-scraped content so it appears in sources now.
                        title = scraped.get("title", url)
                        text = scraped["text"]
                        wc = scraped.get("word_count", len(text.split()))

                        # Detect the correct source type (youtube / arxiv / web)
                        # so the UI labels match what the user actually added.
                        if web_scraper._is_youtube_url(url):
                            src_type = "youtube"
                        elif web_scraper._is_arxiv_url(url):
                            src_type = "arxiv"
                        else:
                            src_type = "web"

                        web_pages = list(config.sources.get("web_pages", []))
                        if url not in web_pages:
                            web_pages.append(url)
                        collector_agent.update_config({
                            "sources": {**config.sources, "web_pages": web_pages},
                            "schedule": {**config.schedule, "frequency": freq},
                        })

                        # Create source record + fire background ingest
                        from utils.tasks import safe_create_task
                        MIN_SOURCE_CHARS = 1000
                        handled = False  # True if a branch built full reply + notified curator
                        lines = []

                        if len(text) >= MIN_SOURCE_CHARS:
                            try:
                                src_meta = {
                                    "type": src_type, "format": src_type,
                                    "url": url, "status": "processing",
                                    "chunks": 0, "characters": 0,
                                    "capture_type": src_type, "user_provided": True,
                                    "word_count": wc,
                                }
                                source_rec = await source_store.create(
                                    notebook_id=notebook_id, filename=title, metadata=src_meta
                                )
                                _sid = source_rec["id"]
                                safe_create_task(
                                    _ingest_source_background(notebook_id, _sid, text, title, src_type),
                                    name=f"add-url-ingest-{_sid[:8]}"
                                )
                                lines = [f"Done. **Source added:** [{title}]({url})",
                                         f"- **{wc:,}** words scraped and ingesting now"]
                            except Exception as _ie:
                                logger.warning(f"[add_url] Source creation failed: {_ie}")
                                lines = [f"Done. **Source registered:** [{title}]({url})",
                                         f"- **{wc:,}** words detected (ingest failed: {_ie})"]
                        else:
                            # Content too short — try article extraction as fallback
                            # (the page may be a blog/index that is_index_page missed)
                            fallback_articles = []
                            if raw_html:
                                fallback_articles = web_scraper.extract_article_links(url, raw_html, max_links=10)
                            if fallback_articles:
                                # It IS an index page — switch to feed page flow
                                feed_pages = list(config.sources.get("feed_pages", []))
                                if url not in feed_pages:
                                    feed_pages.append(url)
                                sched_label = freq if freq != "manual" else "weekly"
                                collector_agent.update_config({
                                    "sources": {**config.sources, "feed_pages": feed_pages},
                                    "schedule": {**config.schedule, "frequency": sched_label},
                                })
                                lines = [f"**Blog/index page detected:** [{title}]({url})",
                                         f"- **{len(fallback_articles)} articles found** — scraping now\n"]
                                for a in fallback_articles[:6]:
                                    lines.append(f"- [{a['title']}]({a['url']})")
                                if len(fallback_articles) > 6:
                                    lines.append(f"- *...and {len(fallback_articles) - 6} more*")
                                safe_create_task(
                                    _ingest_feed_articles_background(
                                        notebook_id=notebook_id,
                                        articles=fallback_articles,
                                        max_concurrent=2,
                                    ),
                                    name=f"feed-ingest-{notebook_id[:8]}",
                                )
                                lines.append(f"\n- **Schedule set:** {sched_label} checks for new articles")
                                reply = "\n".join(lines)
                                _notify_curator(f"Collector registered blog: {title} ({url}). {len(fallback_articles)} articles being ingested.")
                                handled = True
                            else:
                                lines = [f"Done. **Source registered:** [{title}]({url})",
                                         f"- Content too short ({len(text)} chars) for immediate ingest"]

                        # Unified schedule + notify for all non-fallback paths
                        if not handled:
                            if freq != "manual":
                                lines.append(f"- **Schedule set:** {freq} checks")
                            else:
                                lines.append(f"- **Schedule:** manual (say \"check daily\" to automate)")
                            reply = "\n".join(lines)
                            _notify_curator(f"Collector added web source: {title} ({url}). Schedule: {freq}.")
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
        # ADD NOTE (save a user note as a searchable source)
        # -----------------------------------------------------------------
        elif intent == "add_note":
            from services.rag_engine import rag_engine
            from datetime import datetime as _dt

            # Normalize params
            raw_title = (params.get("title") or "").strip().strip('"\'')
            raw_content = (params.get("content") or "").strip()
            from_chat = params.get("from_chat")
            if isinstance(from_chat, str):
                from_chat = from_chat.lower() in ("true", "yes", "1")
            from_chat = bool(from_chat)

            # Heuristic fallback: a user request like "add a note about the
            # research above" has little/no explicit content AND chat_context
            # is available → treat as from_chat even if classifier missed it.
            chat_ctx = (chat_query.chat_context or "").strip() if hasattr(chat_query, "chat_context") else ""
            if not from_chat and chat_ctx and len(raw_content) < 40:
                _q_low = q.lower()
                if any(kw in _q_low for kw in (
                    "we discussed", "we were discussing", "we are discussing",
                    "above", "this chat", "this conversation", "the research",
                    "what we just", "the thread", "the discussion",
                )):
                    from_chat = True

            if from_chat and chat_ctx:
                yield f"data: {json.dumps({'type': 'status', 'message': f'{collector_name} summarizing the conversation into a note...', 'query_type': 'collector'})}\n\n"
                # Ask the model to synthesize a crisp markdown note from the
                # recent chat. Keep the prompt tight — we want a saveable note,
                # not a full essay.
                focus_hint = raw_content or q  # what the user wants the note to focus on
                synth_prompt = (
                    "You are helping a researcher save a note from their recent chat. "
                    "Write a concise, self-contained Markdown note capturing the key "
                    "findings, claims, and open questions discussed. Prefer bullet "
                    "points. Include a '# Title' on the first line. Do NOT add a "
                    "preamble like 'Here is your note'. Keep it under 400 words.\n\n"
                    f"User request: {q}\n\n"
                    f"Focus (if given): {focus_hint}\n\n"
                    "Recent conversation:\n"
                    "------\n"
                    f"{chat_ctx}\n"
                    "------\n\n"
                    "Now write the note (start with the `# Title` heading):"
                )
                synthesized = ""
                try:
                    resp = await ollama_service.generate(
                        prompt=synth_prompt,
                        model=getattr(settings, "ollama_fast_model", None) or settings.ollama_model,
                        temperature=0.3,
                        num_predict=600,
                        timeout=45.0,
                    )
                    synthesized = (resp or {}).get("response", "").strip()
                except Exception as _synth_err:
                    logger.warning(f"[add_note] chat synthesis failed (non-fatal): {_synth_err}")

                if synthesized:
                    # Extract title from first H1 if the model included one
                    first_line = synthesized.splitlines()[0].lstrip("# ").strip() if synthesized.splitlines() else ""
                    note_title = raw_title or (first_line[:80] if first_line else f"Chat note — {_dt.utcnow().strftime('%Y-%m-%d %H:%M')}")
                    note_body = synthesized
                else:
                    # Synthesis unavailable — save raw chat as fallback so the
                    # user never loses the intent to capture this thread.
                    note_title = raw_title or f"Chat capture — {_dt.utcnow().strftime('%Y-%m-%d %H:%M')}"
                    note_body = (
                        f"# {note_title}\n\n"
                        f"_User asked to save this conversation._\n\n"
                        f"**Request:** {q}\n\n"
                        f"## Conversation\n\n{chat_ctx}"
                    )
            else:
                # Pure dictation path — user gave the note body directly
                note_body = raw_content
                if not note_body:
                    # Nothing to save — nudge the user
                    reply = (
                        "**I can save a note for you as a searchable source.** Try:\n"
                        "- *\"@collector add a note titled 'Meeting Thoughts': the research team agreed…\"*\n"
                        "- *\"@collector note what we just discussed above\"* (captures the recent chat)\n"
                        "- *\"@collector save this: <your content>\"*"
                    )
                    follow_ups = ["Show my sources", "Add a note about the research above", "Show my collection status"]
                    # Skip the rest of the handler
                    note_body = None

                if note_body is not None:
                    note_title = raw_title or (
                        note_body.splitlines()[0].lstrip("# ").strip()[:80]
                        if note_body.splitlines() else f"Note — {_dt.utcnow().strftime('%Y-%m-%d %H:%M')}"
                    )

            # Create + ingest the note-source (mirrors POST /{notebook_id}/note)
            if note_body:
                yield f"data: {json.dumps({'type': 'status', 'message': f'{collector_name} saving your note as a source...', 'query_type': 'collector'})}\n\n"
                try:
                    src = await source_store.create(
                        notebook_id=notebook_id,
                        filename=note_title,
                        metadata={
                            "type": "note",
                            "format": "markdown",
                            "size": len(note_body.encode("utf-8")),
                            "chunks": 0,
                            "characters": 0,
                            "status": "processing",
                            "origin": "collector_chat",   # provenance tag
                        },
                    )
                    src_id = src["id"]
                    ingest_result = await rag_engine.ingest_document(
                        notebook_id=notebook_id,
                        source_id=src_id,
                        text=note_body,
                        filename=note_title,
                        source_type="note",
                    )
                    await source_store.update(notebook_id, src_id, {
                        "chunks": ingest_result.get("chunks", 0),
                        "characters": ingest_result.get("characters", len(note_body)),
                        "status": "completed",
                        "content": note_body,
                    })
                    # Best-effort auto-tag (same as /note endpoint)
                    try:
                        from services.auto_tagger import auto_tagger
                        await auto_tagger.tag_source_in_notebook(
                            notebook_id, src_id, note_title, note_body[:3000]
                        )
                    except Exception as _tag_err:
                        logger.debug(f"[add_note] auto-tag failed (non-fatal): {_tag_err}")

                    word_count = len(note_body.split())
                    preview = note_body.strip().splitlines()[0] if note_body.strip() else ""
                    if preview.startswith("#"):
                        preview = preview.lstrip("# ").strip()
                    reply = (
                        f"Done. **Note saved as source:** {note_title}\n"
                        f"- **{word_count:,}** words indexed into the notebook ({ingest_result.get('chunks', 0)} chunks)\n"
                        f"- Source type: **note** — searchable in chat and shows in the Sources panel\n"
                        + (f"- *{preview[:140]}*" if preview and preview != note_title else "")
                    )
                    follow_ups = ["Show my sources", "Edit this note", "Add another note"]
                    _notify_curator(
                        f"Collector saved a user note: \"{note_title}\" "
                        f"({word_count} words, from_chat={from_chat})."
                    )
                except Exception as _create_err:
                    logger.exception(f"[add_note] failed to save note: {_create_err}")
                    reply = f"**Could not save the note:** {_create_err}"
                    follow_ups = ["Show my sources", "Show my collection status"]

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
                except (ValueError, TypeError) as _e:
                    logger.debug(f"[chat] Invalid max_age_days param: {_e}")
            if params.get("min_relevance"):
                try:
                    updates["min_relevance"] = float(params["min_relevance"])
                    parts.append(f"min relevance: {updates['min_relevance']}")
                except (ValueError, TypeError) as _e:
                    logger.debug(f"[chat] Invalid min_relevance param: {_e}")
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
                from datetime import datetime as _dt
                # Curator Phase 2b: kick collection off in a background
                # task so we can detect the plan_id (created inside
                # _execute_collection) and stream it to the client.
                #
                # IMPORTANT (2026-05-13 fix): the polling below filters
                # plans by created_at >= collection_start to avoid
                # picking up a PRIOR run's plan from the same notebook.
                # Without this filter, the card showed the old plan's
                # results while the final message reflected the new
                # run — confusing and wrong.
                collection_start_iso = _dt.utcnow().isoformat()
                collection_task = asyncio.create_task(
                    curator.assign_immediate_collection(notebook_id=notebook_id)
                )

                def _pick_this_runs_plan(plans):
                    """Filter to plans for THIS collection: same notebook,
                    correct intent, created at or after we kicked off."""
                    return [
                        p for p in plans
                        if p.get("created_at", "") >= collection_start_iso
                        and p.get("intent") == "assign_immediate_collection"
                    ]

                plan_id_streamed = False
                try:
                    from services.curator_brain import curator_brain
                    for _ in range(20):  # up to ~2s
                        if collection_task.done():
                            break
                        candidates = curator_brain.recent_plans(
                            limit=5, notebook_id=notebook_id, status="running"
                        )
                        candidates = _pick_this_runs_plan(candidates)
                        if not candidates:
                            # Newly-completed plan path: the collection
                            # finished fast enough that there's no
                            # running plan, but still must be THIS run
                            # (filter by start timestamp).
                            all_recent = curator_brain.recent_plans(
                                limit=5, notebook_id=notebook_id
                            )
                            candidates = _pick_this_runs_plan(all_recent)
                        if candidates:
                            plan_id = candidates[0]["plan_id"]
                            yield f"data: {json.dumps({'type': 'plan_attached', 'plan_id': plan_id})}\n\n"
                            plan_id_streamed = True
                            break
                        await asyncio.sleep(0.1)
                except Exception as _e:
                    logger.debug(f"[chat] plan_attached probe failed (non-fatal): {_e}")

                result = await collection_task

                # If the task finished too fast for the poll loop to
                # catch, do one last check — same timestamp filter so
                # we never attach a prior run's plan to this message.
                if not plan_id_streamed:
                    try:
                        from services.curator_brain import curator_brain
                        all_recent = curator_brain.recent_plans(limit=5, notebook_id=notebook_id)
                        candidates = _pick_this_runs_plan(all_recent)
                        if candidates:
                            yield f"data: {json.dumps({'type': 'plan_attached', 'plan_id': candidates[0]['plan_id']})}\n\n"
                    except Exception:
                        pass

                found = result.get("items_collected", 0)
                approved = result.get("items_approved", 0)
                queued = result.get("items_pending", 0)
                if result.get("cancelled"):
                    reply = f"**Collection cancelled.** {result.get('message', '')}"
                else:
                    reply = f"**Collection complete.**\n- **{found}** items found\n- **{approved}** auto-approved\n- **{queued}** items queued for review"
                    if queued > 0:
                        follow_ups = ['Show pending items', 'Approve all pending', 'Check source health']
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
                follow_ups = ['Approve all pending', 'Show my collection status', 'Collect now']

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
            feed_ct = len(config.sources.get("feed_pages", []))
            kw_ct = len(config.sources.get("news_keywords", []))
            parts = []
            if web_ct: parts.append(f"{web_ct} web pages")
            if rss_ct: parts.append(f"{rss_ct} RSS/channel feeds")
            if feed_ct: parts.append(f"{feed_ct} feed pages")
            if kw_ct: parts.append(f"{kw_ct} keywords")
            lines.append(f"- **Sources:** {', '.join(parts) if parts else 'none configured'}")
            # List subscription feeds
            rss_feeds = config.sources.get("rss_feeds", [])
            if rss_feeds:
                lines.append(f"\n**Subscriptions ({len(rss_feeds)}):**")
                for feed in rss_feeds:
                    if "youtube.com/feeds" in feed:
                        lines.append(f"- 📺 YouTube channel: {feed}")
                    else:
                        lines.append(f"- 📡 {feed}")
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
                    ts = str(h.get('timestamp', '?'))[:16].replace('T', ' ')
                    lines.append(f"- {ts}: {h.get('items_found', 0)} found, {h.get('items_approved', 0)} approved, {h.get('items_pending', 0)} pending")
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


def _quick_intent_for_correspondent(query: str) -> tuple:
    """2026-06-10 — fast-path keyword override before LLM classification.

    With 27+ Correspondent intents, the LLM classifier was collapsing
    simple queries ("show articles", "whats hot", "show subscriptions")
    into the default show_status. This regex table catches the obvious
    verb-noun pairs deterministically. Returns (intent, params) on match
    or (None, {}) to fall through to the LLM classifier.

    Order matters — most specific patterns first. Each pattern is a
    (regex, intent, optional_param_extractor) tuple.
    """
    import re as _re_q
    q = (query or "").strip().lower()
    if not q:
        return (None, {})

    # Discovery + browse (no params)
    simple_patterns = [
        (r"^(show|list)\s+articles?$|show me articles", "show_articles"),
        (r"^backfill\s+status$|^backfill\s+progress$|how.?s?\s+(?:the\s+)?backfill", "backfill_status"),
        (r"^backfill(\s+articles?)?$|^extract\s+articles?\s+for\s+old\b|rebuild\s+article\s+index", "backfill_articles"),
        (r"^refresh\s+titles?$|^fix\s+(article\s+)?titles?$|^rebuild\s+titles?$", "refresh_titles"),
        (r"^reprocess\s+articles?$|^reclassify\s+articles?$|^re-?run\s+phase\s*14$|^refresh\s+articles?$|^refresh\s+article\s+data$", "reprocess_articles"),
        (r"^re-?extract(\s+all)?(\s+articles?)?$|^re-?split(\s+all)?(\s+newsletters?)?$", "reextract_articles"),
        (r"^(article\s+)?pipeline\s+status$|^reprocess\s+status$|^re-?extract\s+status$", "article_pipeline_status"),
        (r"^diagnose\s+extraction$|^extraction\s+diagnostic$|^article\s+extraction\s+diagnostic$", "diagnose_extraction"),
        (r"^(show|list)\s+subscriptions?$|show.*proposals?$", "show_subscriptions"),
        (r"^(show|list)\s+(approval\s+)?queue$|^(show\s+)?pending$|^show\s+approvals?$", "show_queue"),
        (r"^(show|list)\s+accounts?$|^(show|list)\s+inboxes?$", "show_accounts"),
        (r"^(show|list)\s+senders?$", "show_senders"),
        (r"^(show|list)\s+entities?$", "show_entities"),
        (r"^(show|list)\s+scorecards?$|^show\s+grades?$|^rank\s+(my\s+)?newsletters?$", "show_scorecards"),
        (r"^(show|list)\s+recent$|^what.?s?\s+recent$|^show\s+recent\s+newsletters?$", "show_recent"),
        (r"^sync(\s+now)?$|^poll(\s+now)?$|^refresh$", "sync_now"),
        (r"^(show\s+)?status$", "show_status"),
        (r"^summari[sz]e\s+recent$|^(weekly\s+)?summary$", "summarize_recent"),
        (r"^quiet\s+senders?$|^silent\s+senders?$", "quiet_senders"),
        (r"^(show|list|suggest)\s+unsubscribe(\s+candidates?)?$|^which\s+newsletters?\s+should\s+i\s+drop", "show_unsubscribe_candidates"),
        (r"^(show|list)\s+blocklist$|^(show|list)\s+blocked\s+senders?$", "show_blocklist"),
        (r"^(show\s+)?routing(\s+histogram)?$|^(show\s+)?confidence\s+distribution$|^show\s+thresholds?$", "show_routing"),
        (r"^(show|list)\s+digest\s+mode$|^which\s+senders?\s+(?:are\s+)?in\s+digest", "show_digest_mode"),
        (r"^(show|list)?\s*effectiveness$|^how\s+effective(\s+is\s+correspondent)?$|^score$|^show\s+score$", "show_score"),
    ]
    for pat, intent in simple_patterns:
        if _re_q.search(pat, q):
            return (intent, {})

    # Hot/cold (may carry deep flag)
    if _re_q.search(r"what.?s?\s+hot|hot\s+topics?|trending(\s+up)?", q):
        params = {}
        if _re_q.search(r"\bdeep\b|\bcluster|\btheme", q):
            params["deep"] = True
        return ("whats_hot", params)
    if _re_q.search(r"what.?s?\s+cold|cooling(\s+off)?|trending\s+down|going\s+quiet", q):
        params = {}
        if _re_q.search(r"\bdeep\b|\bcluster|\btheme", q):
            params["deep"] = True
        return ("whats_cold", params)

    # Cluster articles
    m = _re_q.search(r"(?:show|list)\s+cluster\s+(.+)", q)
    if m:
        return ("show_cluster_articles", {"label": m.group(1).strip()})
    m = _re_q.search(r"articles?\s+in\s+cluster\s+(.+)", q)
    if m:
        return ("show_cluster_articles", {"label": m.group(1).strip()})

    # Cluster deep-read (P5.3) — combined newsletter context + web briefing
    m = _re_q.search(r"^deep[\s-]read\s+(.+)", q)
    if m:
        return ("cluster_deep_read", {"label": m.group(1).strip()})

    # P5.5 — RFC 2369 unsubscribe with two-step confirmation
    m = _re_q.search(r"^(?:try|really|force)\s+unsubscribe\s+(.+)", q)
    if m:
        return ("try_unsubscribe", {"email_or_name": m.group(1).strip()})
    m = _re_q.search(r"^(?:confirm(?:\s+unsubscribe)?|yes\s+execute)\s+([a-f0-9]{8,16})", q)
    if m:
        return ("confirm_unsubscribe", {"token": m.group(1).strip()})
    if _re_q.search(r"^(show|list)\s+unsub(?:scribe)?\s+(log|history|attempts?)$", q):
        return ("show_unsubscribe_log", {})

    # Articles-from-sender pattern (preserve sender)
    m = _re_q.search(r"(?:show|list)\s+articles?\s+from\s+(.+)", q)
    if m:
        return ("show_articles_from_sender", {"email_or_name": m.group(1).strip()})

    # Entities-for-sender
    m = _re_q.search(r"(?:show|list)\s+entit(?:y|ies)\s+(?:for|from)\s+(.+)", q)
    if m:
        return ("show_entities_for_sender", {"email_or_name": m.group(1).strip()})

    # Score sender
    m = _re_q.search(r"^(?:score|grade|rate)\s+(.+)", q)
    if m:
        return ("score_sender", {"email_or_name": m.group(1).strip()})

    # Deep dive on one sender
    m = _re_q.search(r"(?:show|tell)\s+(?:me\s+)?(?:about|sender)\s+(.+)", q)
    if m:
        return ("show_sender", {"email_or_name": m.group(1).strip()})

    # Forget sender
    m = _re_q.search(r"forget\s+(.+)", q)
    if m:
        return ("forget_sender", {"email": m.group(1).strip()})

    # Unsubscribe / block / unblock
    m = _re_q.search(r"^(?:unsubscribe|stop\s+ingesting|block|drop)\s+(.+)", q)
    if m:
        return ("unsubscribe_sender", {"email_or_name": m.group(1).strip()})
    m = _re_q.search(r"^(?:unblock|resume\s+ingesting)\s+(.+)", q)
    if m:
        return ("unblock_sender", {"email_or_name": m.group(1).strip()})

    # Frequency tuner (G)
    m = _re_q.search(r"^(?:digest\s+mode|weekly\s+digest(?:\s+for)?|bundle)\s+(.+?)(?:\s+(?:into\s+)?weekly)?$", q)
    if m:
        return ("digest_mode", {"email_or_name": m.group(1).strip()})
    m = _re_q.search(r"^(?:live\s+mode|live\s+ingest(?:\s+for)?)\s+(.+)", q)
    if m:
        return ("live_mode", {"email_or_name": m.group(1).strip()})

    return (None, {})


async def _stream_correspondent(chat_query: ChatQuery, injected_action: Optional[Dict[str, Any]] = None):
    """Stream a Correspondent (IMAP) agent response in SSE format.

    Surface every Correspondent capability via natural language: status,
    queue actions, subscription proposals, sender learning, hot/cold
    trends, summaries. See READFIRST/CORRESPONDENT_CAPABILITIES.md for
    the canonical function matrix.
    """
    from services.intent_classifier import classify_intent
    from services.ollama_service import ollama_service
    from services.credential_locker import list_imap_accounts, update_imap_state
    from agents.correspondent import (
        correspondent_agent, _load_sender_routing, _save_sender_routing,
        _normalize_sender,
    )
    import re as _re

    name = "Correspondent"
    q = chat_query.question

    yield f"data: {json.dumps({'type': 'status', 'message': f'{name} processing...', 'query_type': 'correspondent'})}\n\n"

    follow_ups = [
        "Show the approval queue",
        "What's hot this week?",
        "Show learned sender routings",
    ]

    def _done():
        return f"data: {json.dumps({'type': 'done', 'follow_up_questions': follow_ups, 'agent_name': name, 'agent_type': 'correspondent'})}\n\n"

    def _reply(text: str):
        # K1 fix (2026-06-09) — emit a single `token` event matching the
        # pattern every other agent stream uses (curator, collector,
        # research). The frontend chat consumer doesn't handle
        # `reply_chunk`; using it silently dropped the reply and left
        # the stream stuck at "processing" forever. Single-shot token
        # is fine — chunking is only useful for word-by-word streaming
        # which Correspondent doesn't do.
        return f"data: {json.dumps({'type': 'token', 'content': text})}\n\n"

    def _mm_label(s: str, n: int = 40) -> str:
        s = _re.sub(r"[\(\)\[\]\{\}\"`:,]+", " ", str(s or ""))
        s = _re.sub(r"\s+", " ", s).strip()
        return s[:n] or "—"

    try:
        if injected_action:
            intent = injected_action.get("intent") or "show_status"
            params = injected_action.get("params") or {}
        else:
            # 2026-06-10 — fast-path keyword override for unambiguous
            # phrasings. With 27+ intents the LLM classifier was
            # collapsing simple queries like "show articles" / "whats
            # hot" into the default show_status. This pre-LLM regex
            # match handles the common verb-noun pairs deterministically
            # and falls through to the classifier for anything else.
            intent, params = _quick_intent_for_correspondent(q)
            if not intent:
                cls = await classify_intent(q, "correspondent")
                intent = (cls or {}).get("intent") or "show_status"
                params = (cls or {}).get("params") or {}

        # M2 (2026-06-09) — graceful fallback when action intents land
        # without their required parameter. Better to show the queue
        # than crash with "Couldn't find item undefined." This catches
        # both classifier misroutes and ambiguous phrasings like
        # "show approval queue" (which should be show_queue but
        # occasionally gets approve_queued).
        if intent in ("approve_queued", "reroute_queued", "dismiss_queued") and not params.get("index"):
            logger.info(
                f"[correspondent.chat] intent={intent} missing required index — "
                f"falling back to show_queue for query: {q[:80]!r}"
            )
            intent = "show_queue"
            params = {}
        elif intent in ("approve_subscription", "dismiss_subscription") and not params.get("index"):
            logger.info(
                f"[correspondent.chat] intent={intent} missing required index — "
                f"falling back to show_subscriptions for query: {q[:80]!r}"
            )
            intent = "show_subscriptions"
            params = {}

        # M3 (2026-06-09) — log the chosen intent so debugging mis-routes
        # is easy. INFO level so it shows in production logs.
        logger.info(
            f"[correspondent.chat] resolved intent={intent} params={params} "
            f"for query: {q[:80]!r}"
        )

        # ─────────────────────────────────────────────────────────────
        # SYNC + STATUS
        # ─────────────────────────────────────────────────────────────
        if intent == "sync_now":
            summary = await correspondent_agent.poll_all()
            totals = summary.get("totals", {})
            yield _reply(
                f"📬 Synced **{len(summary.get('accounts', {}))}** inbox(es). "
                f"**{totals.get('ingested', 0)}** ingested, "
                f"**{totals.get('queued', 0)}** queued, "
                f"**{totals.get('personal', 0)}** personal, "
                f"**{totals.get('transactional', 0)}** transactional."
            )

        elif intent in ("pause", "resume"):
            email = params.get("email")
            accounts = await list_imap_accounts()
            target = next((a for a in accounts if not email or a.email == email), None)
            if not target:
                yield _reply("Couldn't find an account to update.")
            else:
                await update_imap_state(email=target.email, enabled=(intent == "resume"))
                yield _reply(f"{intent.title()}d **{target.email}**.")

        elif intent == "show_accounts":
            accounts = await list_imap_accounts()
            if not accounts:
                yield _reply("No inboxes connected. Add one in Settings → Correspondent.")
            else:
                lines = [f"**{len(accounts)} connected inbox(es):**"]
                for a in accounts:
                    state = "enabled" if a.enabled else "paused"
                    lines.append(f"- {a.email} ({a.imap_host}, {state}, last polled {a.last_polled_at or 'never'})")
                yield _reply("\n".join(lines))

        # ─────────────────────────────────────────────────────────────
        # APPROVAL QUEUE
        # ─────────────────────────────────────────────────────────────
        elif intent == "show_queue":
            items = correspondent_agent.list_queue()
            sub_count = len(correspondent_agent.list_subscription_queue())
            # Resolve notebook list once so the inline picker has options
            from storage.notebook_store import notebook_store
            nbs_full = await notebook_store.list() or []
            notebooks_payload = [{"id": nb["id"], "title": nb.get("title", "(unnamed)")} for nb in nbs_full]
            empty_msg = "Approval queue is empty."
            if sub_count:
                empty_msg += f" {sub_count} subscription proposal(s) still waiting — say `show subscriptions`."
            payload = {
                "items": items,
                "notebooks": notebooks_payload,
                "empty_message": empty_msg,
            }
            yield _reply("```json-correspondent-queue\n" + json.dumps(payload) + "\n```")

        elif intent == "approve_queued":
            try:
                idx = int(params.get("index", 0)) - 1
            except (TypeError, ValueError):
                idx = -1
            items = correspondent_agent.list_queue()
            if idx < 0 or idx >= len(items):
                yield _reply(f"Couldn't find item {params.get('index')}. Say `show queue` to see what's pending.")
            else:
                item = items[idx]
                result = await correspondent_agent.approve_queued(item["item_id"], notebook_id=None)
                if result.get("ok"):
                    deleted = result.get("imap_deleted")
                    tail = " · removed from inbox" if deleted else (" · couldn't delete from inbox" if deleted is False else "")
                    yield _reply(
                        f"✓ Approved *{item.get('subject', '(no subject)')[:80]}* → "
                        f"`{(item.get('top_candidate') or {}).get('notebook_name', 'notebook')}`{tail}."
                    )
                else:
                    yield _reply(f"⚠ Approve failed: {result.get('reason', 'unknown')}")

        elif intent == "reroute_queued":
            try:
                idx = int(params.get("index", 0)) - 1
            except (TypeError, ValueError):
                idx = -1
            notebook_hint = (params.get("notebook") or "").strip().lower()
            items = correspondent_agent.list_queue()
            if idx < 0 or idx >= len(items):
                yield _reply(f"Couldn't find item {params.get('index')}. Say `show queue` to see what's pending.")
            elif not notebook_hint:
                yield _reply("Tell me which notebook — e.g. `reroute 2 to AI Research`.")
            else:
                from storage.notebook_store import notebook_store
                nbs = await notebook_store.list()
                target = next((nb for nb in nbs if notebook_hint in (nb.get("title") or "").lower()), None)
                if not target:
                    yield _reply(f"Couldn't match a notebook for `{notebook_hint}`.")
                else:
                    item = items[idx]
                    result = await correspondent_agent.approve_queued(item["item_id"], notebook_id=target["id"])
                    if result.get("ok"):
                        yield _reply(
                            f"✓ Rerouted to **{target['title']}**. Sender `{item.get('sender', '?')[:50]}` learned this preference — "
                            f"future emails from them will bias toward this notebook."
                        )
                    else:
                        yield _reply(f"⚠ Reroute failed: {result.get('reason', 'unknown')}")

        elif intent == "dismiss_queued":
            try:
                idx = int(params.get("index", 0)) - 1
            except (TypeError, ValueError):
                idx = -1
            items = correspondent_agent.list_queue()
            if idx < 0 or idx >= len(items):
                yield _reply(f"Couldn't find item {params.get('index')}.")
            else:
                item = items[idx]
                result = await correspondent_agent.dismiss_queued(item["item_id"])
                if result.get("ok"):
                    yield _reply(f"🗑 Dismissed *{item.get('subject', '(no subject)')[:80]}*.")
                else:
                    yield _reply(f"⚠ Dismiss failed: {result.get('reason', 'unknown')}")

        # ─────────────────────────────────────────────────────────────
        # SUBSCRIPTION + ENTITY PROPOSALS
        # ─────────────────────────────────────────────────────────────
        elif intent == "show_subscriptions":
            subs = correspondent_agent.list_subscription_queue()
            payload = {
                "items": subs,
                "empty_message": "No subscription or entity-watch proposals waiting.",
            }
            yield _reply("```json-correspondent-subscriptions\n" + json.dumps(payload) + "\n```")

        elif intent == "approve_subscription":
            try:
                idx = int(params.get("index", 0)) - 1
            except (TypeError, ValueError):
                idx = -1
            subs = correspondent_agent.list_subscription_queue()
            if idx < 0 or idx >= len(subs):
                yield _reply(f"Couldn't find proposal {params.get('index')}.")
            else:
                s = subs[idx]
                result = await correspondent_agent.approve_subscription(s["id"])
                if result.get("ok"):
                    if s.get("kind") == "entity":
                        yield _reply(f"✓ Watching `{s.get('entity_name', s.get('title', '?'))}`. Source created.")
                    else:
                        yield _reply(f"✓ Subscribed to *{s.get('title', '?')[:80]}*. Feed added to the Collector.")
                else:
                    yield _reply(f"⚠ Subscribe failed: {result.get('reason', 'unknown')}")

        elif intent == "dismiss_subscription":
            try:
                idx = int(params.get("index", 0)) - 1
            except (TypeError, ValueError):
                idx = -1
            subs = correspondent_agent.list_subscription_queue()
            if idx < 0 or idx >= len(subs):
                yield _reply(f"Couldn't find proposal {params.get('index')}.")
            else:
                s = subs[idx]
                result = await correspondent_agent.dismiss_subscription(s["id"])
                if result.get("ok"):
                    yield _reply(f"🗑 Dropped proposal *{s.get('title', '?')[:80]}*.")
                else:
                    yield _reply(f"⚠ Dismiss failed: {result.get('reason', 'unknown')}")

        # ─────────────────────────────────────────────────────────────
        # SENDER LEARNING
        # ─────────────────────────────────────────────────────────────
        elif intent == "show_senders":
            routing = _load_sender_routing()
            if not routing:
                yield _reply("No sender learnings yet — every routing is by similarity. Approve a few queued items and I'll start learning.")
            else:
                from storage.notebook_store import notebook_store
                nbs = {nb["id"]: nb.get("title") or "(unnamed)" for nb in (await notebook_store.list() or [])}
                lines = [f"**📚 Learned sender routings ({len(routing)} sender(s)):**\n"]
                # Mermaid graph: senders → notebooks, edge weight = correction count
                mm = ["graph LR"]
                sender_idx = 0
                edges = []
                for sender, bucket in list(routing.items())[:8]:
                    sid = f"s{sender_idx}"
                    sender_idx += 1
                    mm.append(f'  {sid}["{_mm_label(sender, 28)}"]')
                    for nb_id, count in bucket.items():
                        nb_name = nbs.get(nb_id, nb_id[:8])
                        nid = f"n_{nb_id[:8]}"
                        mm.append(f'  {nid}["{_mm_label(nb_name, 24)}"]')
                        weight = "===" if count >= 2 else "---"
                        edges.append(f"  {sid} {weight}|{count}| {nid}")
                        lines.append(f"- `{sender}` → **{nb_name}** ({count} correction{'s' if count != 1 else ''})")
                mm.extend(edges)
                mm.append("  classDef sender fill:#fef3c7,stroke:#d97706,color:#78350f;")
                mm.append("  classDef nb fill:#ede9fe,stroke:#7c3aed,color:#4c1d95;")
                for i in range(sender_idx):
                    mm.append(f"  class s{i} sender;")
                lines.append("\n```mermaid\n" + "\n".join(mm) + "\n```")
                yield _reply("\n".join(lines))

        elif intent == "forget_sender":
            email = (params.get("email") or "").strip().lower()
            if not email:
                yield _reply("Tell me which sender — e.g. `forget alice@news.io`.")
            else:
                routing = _load_sender_routing()
                norm = _normalize_sender(email)
                if norm in routing:
                    del routing[norm]
                    _save_sender_routing(routing)
                    yield _reply(f"🧹 Forgot what I learned about `{email}`. Next email from them routes by similarity again.")
                else:
                    yield _reply(f"I had no learnings for `{email}` to forget.")

        # ─────────────────────────────────────────────────────────────
        # DISCOVERY + AUDIT (J4 — 2026-06-09)
        # ─────────────────────────────────────────────────────────────
        elif intent == "show_recent":
            try:
                limit = int(params.get("limit") or 10)
            except (TypeError, ValueError):
                limit = 10
            from services.correspondent_trends import _gather_newsletter_sources
            from datetime import timedelta as _td
            from storage.notebook_store import notebook_store
            sources = await _gather_newsletter_sources(datetime.utcnow() - _td(days=30))
            sources.sort(key=lambda s: s.get("created_at") or "", reverse=True)
            sources = sources[:limit]
            if not sources:
                yield _reply("📭 No newsletters ingested in the last 30 days.")
            else:
                nbs = {nb["id"]: nb.get("title") or "(unnamed)" for nb in (await notebook_store.list() or [])}
                lines = [f"**📬 Last {len(sources)} newsletter(s) ingested:**\n"]
                for s in sources:
                    nb_name = nbs.get(s.get("notebook_id"), "?")
                    when = (s.get("created_at") or "")[:10]
                    lines.append(
                        f"- **{when}** — *{(s.get('subject') or '(no subject)')[:70]}* "
                        f"from `{(s.get('sender') or '?')[:50]}` → **{nb_name}**"
                    )
                yield _reply("\n".join(lines))

        elif intent == "show_sender":
            query = (params.get("email_or_name") or "").strip().lower()
            if not query:
                yield _reply("Tell me which sender — e.g. `show me Stratechery` or `tell me about alice@news.io`.")
            else:
                from services.correspondent_trends import _gather_newsletter_sources
                from agents.correspondent import _load_sender_routing, _normalize_sender
                from datetime import timedelta as _td
                from storage.notebook_store import notebook_store
                sources = await _gather_newsletter_sources(datetime.utcnow() - _td(days=90))
                matches = [s for s in sources if query in (s.get("sender") or "").lower()]
                if not matches:
                    yield _reply(f"No newsletters from `{query}` in the last 90 days.")
                else:
                    matches.sort(key=lambda s: s.get("created_at") or "", reverse=True)
                    nbs = {nb["id"]: nb.get("title") or "(unnamed)" for nb in (await notebook_store.list() or [])}
                    # Routing distribution
                    nb_counts: Dict[str, int] = {}
                    for s in matches:
                        nb_id = s.get("notebook_id") or ""
                        nb_counts[nb_id] = nb_counts.get(nb_id, 0) + 1
                    # Learning state
                    routing = _load_sender_routing()
                    learned: Dict[str, int] = {}
                    sender_addr = matches[0].get("sender") or ""
                    norm = _normalize_sender(sender_addr)
                    if norm in routing:
                        learned = routing[norm]

                    lines = [f"**📬 Deep dive: `{matches[0].get('sender', query)}`**\n"]
                    lines.append(f"- **Volume:** {len(matches)} newsletter(s) in last 90 days")
                    lines.append(f"- **Most recent:** {(matches[0].get('created_at') or '?')[:10]}")
                    if nb_counts:
                        nb_str = ", ".join(f"`{nbs.get(nb_id, nb_id[:8])}` ({n})" for nb_id, n in sorted(nb_counts.items(), key=lambda x: -x[1]))
                        lines.append(f"- **Where it lands:** {nb_str}")
                    if learned:
                        learn_str = ", ".join(f"`{nbs.get(nb_id, nb_id[:8])}` (+{n})" for nb_id, n in learned.items())
                        lines.append(f"- **Learned routing:** {learn_str}")
                    else:
                        lines.append(f"- **Learned routing:** _none yet — approvals will teach the router_")
                    # Recent subjects
                    lines.append("\n**Recent subjects:**")
                    for s in matches[:5]:
                        when = (s.get("created_at") or "")[:10]
                        lines.append(f"- {when} — *{(s.get('subject') or '(no subject)')[:80]}*")
                    yield _reply("\n".join(lines))

        elif intent == "quiet_senders":
            try:
                days_silent = int(params.get("days") or 21)
            except (TypeError, ValueError):
                days_silent = 21
            from services.correspondent_trends import _gather_newsletter_sources
            from datetime import timedelta as _td
            sources = await _gather_newsletter_sources(datetime.utcnow() - _td(days=180))
            if not sources:
                yield _reply("Not enough ingest history to compute quiet senders yet.")
            else:
                # Group by sender, find last_seen
                last_seen: Dict[str, str] = {}
                counts: Dict[str, int] = {}
                for s in sources:
                    sender = s.get("sender") or ""
                    if not sender:
                        continue
                    created = s.get("created_at") or ""
                    if created > last_seen.get(sender, ""):
                        last_seen[sender] = created
                    counts[sender] = counts.get(sender, 0) + 1
                threshold = (datetime.utcnow() - _td(days=days_silent)).isoformat()
                quiet = [
                    (sender, last_seen[sender], counts[sender])
                    for sender in last_seen
                    if last_seen[sender] < threshold and counts[sender] >= 2  # ignore one-offs
                ]
                quiet.sort(key=lambda x: x[1])
                if not quiet:
                    yield _reply(f"📭 No senders quiet for {days_silent}+ days. Everyone you've heard from recently is still active.")
                else:
                    lines = [f"**🌙 {len(quiet)} sender(s) silent for {days_silent}+ days:**\n"]
                    for sender, last, n in quiet[:12]:
                        lines.append(
                            f"- `{sender[:60]}` — last heard {last[:10]} ({n} email{'s' if n != 1 else ''} total). "
                            f"Consider unsubscribing if you no longer need them."
                        )
                    yield _reply("\n".join(lines))

        elif intent == "move_source":
            source_query = (params.get("source_query") or "").strip().lower()
            notebook_hint = (params.get("notebook") or "").strip().lower()
            if not source_query or not notebook_hint:
                yield _reply("Tell me which source and which notebook — e.g. `move the McKinsey newsletter to AI Research`.")
            else:
                from services.correspondent_trends import _gather_newsletter_sources
                from datetime import timedelta as _td
                from storage.notebook_store import notebook_store
                from storage.source_store import source_store
                sources = await _gather_newsletter_sources(datetime.utcnow() - _td(days=60))
                matches = [
                    s for s in sources
                    if source_query in (s.get("subject") or "").lower()
                    or source_query in (s.get("sender") or "").lower()
                ]
                if not matches:
                    yield _reply(f"Couldn't find a recent newsletter matching `{source_query}`.")
                else:
                    nbs = await notebook_store.list() or []
                    target = next((nb for nb in nbs if notebook_hint in (nb.get("title") or "").lower()), None)
                    if not target:
                        yield _reply(f"Couldn't match a notebook for `{notebook_hint}`.")
                    else:
                        src = matches[0]
                        src_id = src.get("id")
                        old_nb_id = src.get("notebook_id")
                        # Source store doesn't have a direct move; we update notebook_id
                        try:
                            ok = await source_store.update(old_nb_id, src_id, {"notebook_id": target["id"]})
                            if ok:
                                # Record the sender→notebook learning
                                from agents.correspondent import _record_sender_routing
                                _record_sender_routing(sender=src.get("sender") or "", notebook_id=target["id"])
                                yield _reply(
                                    f"✓ Moved *{(src.get('subject') or '(no subject)')[:80]}* → **{target['title']}**. "
                                    f"Recorded sender preference so future emails from `{(src.get('sender') or '?')[:50]}` favor this notebook."
                                )
                            else:
                                yield _reply("⚠ Source move failed.")
                        except Exception as _move_e:
                            yield _reply(f"⚠ Source move failed: {_move_e}")

        # ─────────────────────────────────────────────────────────────
        # ARTICLES + ENTITIES (Phase 1 Tier 2 — 2026-06-09)
        # ─────────────────────────────────────────────────────────────
        elif intent == "backfill_articles":
            try:
                from api.articles import backfill_all_articles, backfill_status
                result = await backfill_all_articles()
                if result.get("already_running"):
                    st = result.get("status") or {}
                    yield _reply(
                        f"🔄 A backfill is already running. **{st.get('processed', 0)}**/"
                        f"**{st.get('queued', 0)}** sources processed so far, "
                        f"**{st.get('articles_created', 0)}** article(s) extracted. "
                        f"Try `@correspondent backfill status` in a few minutes."
                    )
                else:
                    queued = result.get("queued", 0)
                    if queued == 0:
                        yield _reply("✓ Nothing to backfill — every newsletter already has articles.")
                    else:
                        yield _reply(
                            f"🔄 **Started background backfill of {queued} source(s).** "
                            f"This runs sequentially with summary + embedding + RAG-index per article — "
                            f"figure ~30–60s per source. The app stays responsive; you can keep using it. "
                            f"Try `@correspondent show articles` periodically to watch them appear, or "
                            f"`@correspondent backfill status` to peek at progress."
                        )
            except Exception as _bf_e:
                yield _reply(f"⚠ Backfill kickoff failed: {_bf_e}")

        elif intent == "refresh_titles":
            import re as _re_rt
            from storage.article_store import article_store
            from services.article_extractor import _extract_title_from_segment, _looks_like_title
            yield _reply("🔄 Re-extracting titles for existing articles…")
            try:
                articles = await article_store.list_all_with_text(limit=5000)
                fixed = 0
                skipped = 0
                from_summary = 0
                for a in articles:
                    old = (a.get("title") or "").strip()
                    # Q1.h (2026-06-10) — prefer the LLM summary's first
                    # sentence as the title whenever summary is clean
                    # prose. The body's first line will never beat the
                    # engineered summary; fall back to body extraction
                    # only when summary is missing or HTML/chrome echo.
                    summary = (a.get("summary") or "").strip()
                    candidate: Optional[str] = None
                    if summary and len(summary) >= 8 and not summary.startswith("<") and not summary.lower().startswith(("view online", "sign up", "subscribe", "unsubscribe")):
                        first_sent = _re_rt.split(r"(?<=[.!?])\s+", summary, maxsplit=1)[0].strip()
                        if first_sent and len(first_sent) >= 8:
                            candidate = first_sent[:140].rstrip(". ")
                    if candidate and candidate != old:
                        await article_store.update_title(a["id"], candidate)
                        fixed += 1
                        from_summary += 1
                        continue
                    new_title = _extract_title_from_segment(
                        text=a.get("body_text") or "",
                        html=a.get("body_html") or "",
                    )
                    if (
                        new_title
                        and new_title != "(untitled)"
                        and new_title != old
                        and _looks_like_title(new_title)
                    ):
                        await article_store.update_title(a["id"], new_title)
                        fixed += 1
                        continue
                    if old and not _looks_like_title(old) and old != "(untitled)":
                        await article_store.update_title(a["id"], "(untitled)")
                        fixed += 1
                    else:
                        skipped += 1
                summary_note = f" ({from_summary} pulled from the article summary)" if from_summary else ""
                yield _reply(
                    f"\n\n✓ Refreshed **{fixed}** title(s){summary_note}; "
                    f"skipped **{skipped}** that were already clean. "
                    f"Try `show articles` to confirm."
                )
            except Exception as _rt_e:
                yield _reply(f"\n\n⚠ Refresh failed: {_rt_e}")

        elif intent == "reprocess_articles":
            # P14.F (2026-06-10) — push pre-Phase-14 articles through the
            # new pipeline. P14.H (2026-06-11) — gated by single-instance
            # lock. P14.RES (2026-06-11) — runs as a true background task
            # now; chat returns immediately instead of holding the HTTP
            # connection for 5+ minutes. User polls via `pipeline status`.
            if _ARTICLE_PIPELINE_LOCK.locked():
                st = _ARTICLE_PIPELINE_STATUS
                yield _reply(
                    f"⏳ A {st.get('operation','article')} batch is already running — "
                    f"**{st.get('sources_processed',0)}/{st.get('sources_total',0)}** sources, "
                    f"**{st.get('articles_touched',0)}** article(s) touched so far. "
                    f"Check progress with `pipeline status` any time."
                )
            else:
                from services.correspondent_processor import _summarize_articles_background

                async def _run_reprocess_bg():
                    from storage.source_store import source_store
                    from storage.article_store import article_store
                    from datetime import datetime as _dt
                    async with _ARTICLE_PIPELINE_LOCK:
                        _ARTICLE_PIPELINE_STATUS.update({
                            "running": True, "operation": "reprocess",
                            "sources_total": 0, "sources_processed": 0,
                            "articles_touched": 0,
                            "started_at": _dt.utcnow().isoformat(),
                            "finished_at": None, "last_error": None,
                        })
                        try:
                            all_by_nb = await source_store.list_all() or {}
                            source_targets = []
                            for nb_id, sources in all_by_nb.items():
                                for s in (sources or []):
                                    fmt = (s.get("format") or "").lower()
                                    if fmt not in ("email", "forward"):
                                        continue
                                    src_id = s.get("id")
                                    if not src_id:
                                        continue
                                    cnt = await article_store.count_by_source(src_id)
                                    if cnt > 0:
                                        source_targets.append((src_id, cnt))
                            _ARTICLE_PIPELINE_STATUS["sources_total"] = len(source_targets)
                            for src_id, _cnt in source_targets:
                                try:
                                    await _summarize_articles_background(src_id)
                                    _ARTICLE_PIPELINE_STATUS["sources_processed"] += 1
                                    _ARTICLE_PIPELINE_STATUS["articles_touched"] += _cnt
                                except Exception as _e:
                                    logger.debug(f"[reprocess_articles bg] source {src_id[:8]} failed: {_e}")
                                await asyncio.sleep(0.5)
                            _ARTICLE_PIPELINE_STATUS["finished_at"] = _dt.utcnow().isoformat()
                        except Exception as _e:
                            _ARTICLE_PIPELINE_STATUS["last_error"] = str(_e)[:300]
                        finally:
                            _ARTICLE_PIPELINE_STATUS["running"] = False

                asyncio.create_task(_run_reprocess_bg())
                yield _reply(
                    "🔁 **Reprocess started in the background.** Walking every newsletter source through "
                    "the Phase 14 pipeline (classifier + summary + entity + section + brain event). "
                    "Expect ~3-5s per article; with the Ollama concurrency caps in place, this stays "
                    "stable even if IMAP pulls new mail simultaneously.\n\n"
                    "Check progress at any time with `pipeline status`."
                )

        elif intent == "reextract_articles":
            # P14.G (2026-06-11) — one-time backfill: re-run extraction
            # on sources currently at 1 article. P14.RES (2026-06-11) —
            # runs as a true background task. Chat returns immediately.
            if _ARTICLE_PIPELINE_LOCK.locked():
                st = _ARTICLE_PIPELINE_STATUS
                yield _reply(
                    f"⏳ A {st.get('operation','article')} batch is already running — "
                    f"**{st.get('sources_processed',0)}/{st.get('sources_total',0)}** sources, "
                    f"**{st.get('articles_touched',0)}** article(s) touched so far. "
                    f"Check progress with `pipeline status` any time."
                )
            else:
                async def _run_reextract_bg():
                    from storage.source_store import source_store
                    from storage.article_store import article_store
                    from services.article_extractor import extract_articles
                    from services.correspondent_processor import _summarize_articles_background
                    from services.article_rag import synthetic_id_for_article
                    from services.entity_extractor import entity_extractor
                    from services.rag_engine import rag_engine
                    from datetime import datetime as _dt
                    async with _ARTICLE_PIPELINE_LOCK:
                        _ARTICLE_PIPELINE_STATUS.update({
                            "running": True, "operation": "reextract",
                            "sources_total": 0, "sources_processed": 0,
                            "articles_touched": 0,
                            "started_at": _dt.utcnow().isoformat(),
                            "finished_at": None, "last_error": None,
                        })
                        try:
                            all_by_nb = await source_store.list_all() or {}
                            targets = []
                            for nb_id, sources in all_by_nb.items():
                                for s in (sources or []):
                                    fmt = (s.get("format") or "").lower()
                                    if fmt not in ("email", "forward"):
                                        continue
                                    src_id = s.get("id")
                                    if not src_id:
                                        continue
                                    cnt = await article_store.count_by_source(src_id)
                                    # P14.EXT (2026-06-11) — include sources
                                    # with any current count. We still only
                                    # replace when new extraction yields MORE
                                    # articles (handled below per-source), so
                                    # already-correctly-split sources stay put.
                                    # cnt=0 sources also picked up (matches
                                    # backfill behavior).
                                    targets.append((nb_id, src_id, s, cnt))
                            _ARTICLE_PIPELINE_STATUS["sources_total"] = len(targets)
                            for nb_id, src_id, source, current_cnt in targets:
                                text_body = source.get("content") or ""
                                meta = source.get("metadata") or {}
                                html_body = meta.get("content_html") if isinstance(meta, dict) else source.get("content_html")
                                if not (text_body or html_body):
                                    _ARTICLE_PIPELINE_STATUS["sources_processed"] += 1
                                    continue
                                try:
                                    new_articles = extract_articles(
                                        html_body=html_body or "",
                                        text_body=text_body,
                                        fallback_title=source.get("filename") or "(untitled)",
                                    )
                                except Exception:
                                    _ARTICLE_PIPELINE_STATUS["sources_processed"] += 1
                                    continue
                                # P14.EXT (2026-06-11) — replace when new
                                # extraction yields MORE articles than the
                                # existing count, OR when current count is 0
                                # (backfill case). Leave alone if extraction
                                # found ≤ current — no improvement.
                                if len(new_articles) <= max(1, current_cnt):
                                    _ARTICLE_PIPELINE_STATUS["sources_processed"] += 1
                                    continue
                                try:
                                    old_rows = await article_store.list_by_source(src_id)
                                    for old in old_rows:
                                        ar_id = old.get("id")
                                        if not ar_id:
                                            continue
                                        synth = synthetic_id_for_article(ar_id)
                                        try:
                                            await rag_engine.delete_source(nb_id, synth)
                                        except Exception:
                                            pass
                                        try:
                                            entity_extractor.delete_source_entities(nb_id, synth)
                                        except Exception:
                                            pass
                                    await article_store.delete_by_source(src_id)
                                except Exception as _cl:
                                    logger.debug(f"[reextract bg] cleanup failed on {src_id[:8]}: {_cl}")
                                try:
                                    count = await article_store.create_batch(
                                        source_id=src_id,
                                        notebook_id=nb_id,
                                        sender=source.get("sender") or source.get("original_sender"),
                                        articles=[
                                            {"position": a.position, "title": a.title,
                                             "body_text": a.body_text, "body_html": a.body_html,
                                             "body_text_offset": a.body_text_offset}
                                            for a in new_articles
                                        ],
                                    )
                                    await source_store.update(nb_id, src_id, {"article_count": count})
                                    _ARTICLE_PIPELINE_STATUS["articles_touched"] += count
                                except Exception as _ce:
                                    logger.warning(f"[reextract bg] persist failed on {src_id[:8]}: {_ce}")
                                    _ARTICLE_PIPELINE_STATUS["sources_processed"] += 1
                                    continue
                                try:
                                    await _summarize_articles_background(src_id)
                                except Exception as _bge:
                                    logger.debug(f"[reextract bg] post-extract failed on {src_id[:8]}: {_bge}")
                                _ARTICLE_PIPELINE_STATUS["sources_processed"] += 1
                                await asyncio.sleep(0.5)
                            _ARTICLE_PIPELINE_STATUS["finished_at"] = _dt.utcnow().isoformat()
                        except Exception as _e:
                            _ARTICLE_PIPELINE_STATUS["last_error"] = str(_e)[:300]
                        finally:
                            _ARTICLE_PIPELINE_STATUS["running"] = False

                asyncio.create_task(_run_reextract_bg())
                yield _reply(
                    "🔪 **Re-extract started in the background.** Walking single-article sources and "
                    "re-attempting the split. Multi-section newsletters will split into sub-articles; "
                    "single-article-by-structure ones (personal blogs, hustle) stay as one.\n\n"
                    "Check progress at any time with `pipeline status`."
                )

        elif intent == "article_pipeline_status":
            st = _ARTICLE_PIPELINE_STATUS
            if st.get("running"):
                yield _reply(
                    f"🔁 **{st.get('operation','article').title()} batch running** — "
                    f"**{st.get('sources_processed',0)}/{st.get('sources_total',0)}** sources, "
                    f"**{st.get('articles_touched',0)}** article(s) touched. "
                    f"Started {st.get('started_at','?')}."
                )
            elif st.get("started_at"):
                err = st.get("last_error")
                if err:
                    yield _reply(
                        f"⚠ Last {st.get('operation','article')} batch aborted: {err}\n"
                        f"Touched {st.get('articles_touched',0)} article(s) before failing."
                    )
                else:
                    yield _reply(
                        f"✓ Last {st.get('operation','article')} batch finished "
                        f"{st.get('finished_at','?')}.\n"
                        f"Walked **{st.get('sources_processed',0)}/{st.get('sources_total',0)}** sources; "
                        f"**{st.get('articles_touched',0)}** article(s) touched."
                    )
            else:
                yield _reply(
                    "No article-pipeline batch has run yet. "
                    "Try `@correspondent re-extract all` then `@correspondent reprocess articles`."
                )

        elif intent == "diagnose_extraction":
            # P14.DX (2026-06-11) — read-only diagnostic. Walks every
            # email/forward source, runs each heuristic independently,
            # writes a JSON report. Chat reply summarizes.
            yield _reply(
                "🔬 Running article-extraction diagnostic across all email/forward sources. "
                "Read-only — no articles will be re-extracted or changed. Building report…"
            )
            try:
                from services.article_extraction_diagnostic import run_diagnostic
                report = await run_diagnostic()
                summary = report.get("summary") or {}
                lines = [
                    "\n\n📊 **Article extraction diagnostic complete**\n",
                    f"- **Total sources analyzed:** {summary.get('total_sources', 0)}",
                    f"- **Split correctly (≥2 articles):** {summary.get('split_correctly_ge2', 0)}",
                    f"- **Resolved to 1 article:** {summary.get('single_article_total', 0)}",
                    f"  - **Probable misfires** (structural markers suggest more articles): **{summary.get('single_article_probable_misfire', 0)}**",
                    f"  - **Probable genuine single-article** (no structural signals): **{summary.get('single_article_probable_genuine', 0)}**",
                ]
                if summary.get("extraction_failed"):
                    lines.append(f"- **Extraction raised an exception:** {summary['extraction_failed']}")
                hbu = summary.get("heuristics_used_for_correct_splits") or {}
                if hbu:
                    lines.append("")
                    lines.append("**Heuristics that split correctly:**")
                    for h, n in sorted(hbu.items(), key=lambda x: x[1], reverse=True):
                        lines.append(f"- `{h}`: {n} source(s)")
                top = summary.get("top_misfire_senders") or []
                if top:
                    lines.append("")
                    lines.append("**Top senders with probable misfires:**")
                    for s in top[:10]:
                        lines.append(
                            f"- **{s['source_count']}** source(s) from `{s['sender']}` — "
                            f"avg body {s['avg_body_size']:,} chars · {s['sample_diagnosis']}"
                        )
                written_to = report.get("_written_to")
                if written_to:
                    lines.append("")
                    lines.append(f"**Full per-source detail written to:**")
                    lines.append(f"`{written_to}`")
                    lines.append("")
                    lines.append("Paste the contents of that file (or share it) so we can build targeted heuristics for the top misfire senders.")
                yield _reply("\n".join(lines))
            except Exception as _dx_e:
                yield _reply(f"\n\n⚠ Diagnostic failed: {_dx_e}")

        elif intent == "backfill_status":
            try:
                from api.articles import _BACKFILL_STATUS
                st = dict(_BACKFILL_STATUS)
                if st.get("running"):
                    yield _reply(
                        f"🔄 Backfill running: **{st.get('processed', 0)}**/"
                        f"**{st.get('queued', 0)}** sources, "
                        f"**{st.get('articles_created', 0)}** article(s) extracted so far."
                    )
                elif st.get("started_at"):
                    err = st.get("last_error")
                    if err:
                        yield _reply(f"⚠ Last backfill aborted: {err}")
                    else:
                        yield _reply(
                            f"✓ Last backfill finished. Processed **{st.get('processed', 0)}** "
                            f"source(s), extracted **{st.get('articles_created', 0)}** article(s). "
                            f"Try `show articles` to see them."
                        )
                else:
                    yield _reply("No backfill has run yet. Say `backfill articles` to start one.")
            except Exception as _bs_e:
                yield _reply(f"⚠ Couldn't read backfill status: {_bs_e}")

        elif intent == "cluster_deep_read":
            from services.cluster_deep_read import run as run_deep_read
            label = (params.get("label") or "").strip()
            if not label:
                yield _reply("Tell me which topic — e.g. `deep read AI agents`.")
            else:
                # Q4 (2026-06-10) — trailing \n\n so the next reply with
                # a code fence is treated as a separate block.
                yield _reply(f"🔭 Combining your newsletter coverage of **{label}** with a fresh web sweep…\n\n")
                try:
                    result = await run_deep_read(label=label, notebook_id=chat_query.notebook_id)
                except Exception as _de:
                    yield _reply(f"⚠ Deep-read failed: {_de}")
                else:
                    if not result.get("ok"):
                        yield _reply(
                            f"No cluster matching `{label}`. Say `whats hot deep` to see active clusters."
                        )
                    else:
                        articles = result.get("articles") or []
                        web = result.get("web_results") or []
                        briefing = result.get("briefing") or "_(no briefing produced)_"
                        skipped = result.get("skipped_domains") or []
                        # Compose the multi-section reply
                        lines: List[str] = []
                        lines.append(briefing)
                        lines.append("")
                        # Embed the article cards inline so the user can
                        # jump straight to the parent newsletter sources.
                        if articles:
                            articles_payload = {
                                "items": [
                                    {
                                        "id": a.get("id"),
                                        "title": a.get("title") or "(untitled)",
                                        "sender": a.get("sender"),
                                        "summary": a.get("summary") or (a.get("body_text") or "")[:200],
                                        "position": a.get("position"),
                                        "source_id": a.get("source_id"),
                                        "notebook_id": a.get("notebook_id"),
                                        "created_at": a.get("created_at"),
                                        "topic_tags": a.get("topic_tags") or [],
                                    }
                                    for a in articles[:8]
                                ],
                                "empty_message": "No articles in this cluster.",
                            }
                            lines.append("---\n**Your existing coverage** (tap to open):")
                            lines.append("```json-correspondent-articles\n" + json.dumps(articles_payload) + "\n```")
                        if skipped:
                            lines.append(
                                f"_Filtered out web results from domains you already follow: "
                                f"{', '.join(skipped[:5])}._"
                            )
                        yield _reply("\n".join(lines))

        elif intent == "show_cluster_articles":
            from storage.article_store import article_store
            from storage.database import get_db
            label = (params.get("label") or "").strip()
            if not label:
                yield _reply("Tell me which cluster — e.g. `show cluster AI agents`.")
            else:
                # Look up cluster by case-insensitive label match
                row = get_db().get_connection().execute(
                    "SELECT * FROM topic_clusters WHERE LOWER(label) LIKE ? LIMIT 1",
                    (f"%{label.lower()}%",),
                ).fetchone()
                if not row:
                    yield _reply(f"No cluster matching `{label}`. Say `whats hot deep` to see active clusters.")
                else:
                    try:
                        article_ids = json.loads(row["article_ids"]) if row["article_ids"] else []
                    except Exception:
                        article_ids = []
                    article_rows = []
                    for aid in article_ids[:25]:
                        a = await article_store.get(aid)
                        if a:
                            article_rows.append(a)
                    payload_obj = {
                        "items": [
                            {
                                "id": a.get("id"),
                                "title": a.get("title") or "(untitled)",
                                "sender": a.get("sender"),
                                "summary": a.get("summary") or (a.get("body_text") or "")[:200],
                                "position": a.get("position"),
                                "source_id": a.get("source_id"),
                                "notebook_id": a.get("notebook_id"),
                                "created_at": a.get("created_at"),
                                "topic_tags": a.get("topic_tags") or [],
                            }
                            for a in article_rows
                        ],
                        "empty_message": f"No articles in cluster `{label}` (it may have been re-clustered).",
                    }
                    intro = (
                        f"**Cluster:** {row['label']} · "
                        f"{len(article_ids)} article(s) across {len(json.loads(row['sender_counts']) or {})} sender(s) "
                        f"and {len(json.loads(row['notebook_counts']) or {})} notebook(s).\n"
                    )
                    yield _reply(intro + "\n```json-correspondent-articles\n" + json.dumps(payload_obj) + "\n```")

        elif intent in ("show_articles", "show_articles_from_sender"):
            from storage.article_store import article_store
            try:
                limit = int(params.get("limit") or 12)
            except (TypeError, ValueError):
                limit = 12
            if intent == "show_articles_from_sender":
                query = (params.get("email_or_name") or "").strip()
                if not query:
                    yield _reply("Tell me which sender — e.g. `articles from Stratechery`.")
                else:
                    articles = await article_store.list_by_sender(query, limit=limit)
            else:
                articles = await article_store.list_recent(limit=limit)
            if not articles:
                yield _reply(
                    "📭 No extracted articles yet. Articles are pulled from new newsletters as they arrive — "
                    "if you just added an inbox, wait for the next sync."
                )
            else:
                payload_obj = {
                    "items": [
                        {
                            "id": a.get("id"),
                            "title": a.get("title") or "(untitled)",
                            "sender": a.get("sender"),
                            "summary": a.get("summary") or (a.get("body_text") or "")[:200],
                            "position": a.get("position"),
                            "source_id": a.get("source_id"),
                            "notebook_id": a.get("notebook_id"),
                            "created_at": a.get("created_at"),
                            "topic_tags": a.get("topic_tags") or [],
                        }
                        for a in articles
                    ],
                    "empty_message": "No articles match that filter.",
                }
                yield _reply("```json-correspondent-articles\n" + json.dumps(payload_obj) + "\n```")

        elif intent in ("show_entities", "show_entities_for_sender"):
            try:
                from services.entity_extractor import entity_extractor
                try:
                    limit = int(params.get("limit") or 20)
                except (TypeError, ValueError):
                    limit = 20

                if intent == "show_entities_for_sender":
                    sender_query = (params.get("email_or_name") or "").strip().lower()
                    if not sender_query:
                        yield _reply("Tell me which sender — e.g. `show entities for Stratechery`.")
                        yield _done()
                        return
                    # Collect notebooks containing articles from this sender,
                    # then aggregate their entities.
                    from storage.article_store import article_store
                    matched_articles = await article_store.list_by_sender(sender_query, limit=200)
                    notebooks_seen = {a.get("notebook_id") for a in matched_articles if a.get("notebook_id")}
                else:
                    sender_query = None
                    from storage.notebook_store import notebook_store
                    nbs = await notebook_store.list() or []
                    notebooks_seen = {nb["id"] for nb in nbs}

                if not notebooks_seen:
                    yield _reply("No entity data yet — new newsletter ingests will start populating this.")
                else:
                    counts: Dict[str, Dict[str, Any]] = {}
                    for nb_id in notebooks_seen:
                        try:
                            entries = entity_extractor.get_entities(nb_id)
                        except Exception:
                            entries = []
                        for e in (entries or [])[:200]:
                            name = (e.name if hasattr(e, "name") else e.get("name", "")).strip()
                            etype = e.type if hasattr(e, "type") else e.get("type")
                            mentions = e.mentions if hasattr(e, "mentions") else e.get("mentions", 1)
                            if not name:
                                continue
                            key = (etype, name.lower())
                            if key in counts:
                                counts[key]["mentions"] += int(mentions or 1)
                            else:
                                counts[key] = {
                                    "name": name,
                                    "type": etype,
                                    "mentions": int(mentions or 1),
                                }
                    top = sorted(counts.values(), key=lambda c: c["mentions"], reverse=True)[:limit]
                    if not top:
                        yield _reply(
                            f"No entities yet{' for that sender' if sender_query else ''}. "
                            "New newsletter ingests will populate this."
                        )
                    else:
                        scope_label = f"sender `{sender_query}`" if sender_query else "all notebooks"
                        lines = [f"**📚 Top {len(top)} entities — {scope_label}:**\n"]
                        # Bucket by type for readability
                        by_type: Dict[str, List[str]] = {}
                        for c in top:
                            label = f"`{c['name']}` ({c['mentions']})"
                            by_type.setdefault(c["type"], []).append(label)
                        for etype in ("person", "company", "product"):
                            if etype in by_type:
                                lines.append(f"\n**{etype.capitalize()}s:** " + ", ".join(by_type[etype]))
                        yield _reply("\n".join(lines))
            except Exception as _ent_e:
                logger.warning(f"[correspondent.show_entities] failed: {_ent_e}")
                yield _reply(f"⚠ Couldn't load entities: {_ent_e}")

        # ─────────────────────────────────────────────────────────────
        # LIST-UNSUBSCRIBE ACTION (Phase 5 Tier 2 / F follow-up — 2026-06-10)
        # ─────────────────────────────────────────────────────────────
        elif intent == "try_unsubscribe":
            target = (params.get("email_or_name") or "").strip()
            if not target:
                yield _reply("Tell me which sender — e.g. `try unsubscribe Stratechery`.")
            else:
                from services.list_unsubscribe import find_unsub_target, create_pending
                info = await find_unsub_target(target)
                if not info:
                    yield _reply(
                        f"⚠ No valid List-Unsubscribe target found for `{target}`. Either they don't "
                        f"include the RFC 2369 header, or the header URL fails our domain-suffix check "
                        f"(no cross-domain unsubs allowed). Use `unsubscribe {target}` to block locally instead."
                    )
                else:
                    token = create_pending(info)
                    if not token:
                        yield _reply("⚠ Couldn't stash the pending request. Try again in a moment.")
                    else:
                        kind = info["kind"]
                        target_url = info["target"]
                        action_label = (
                            "HTTPS POST (one-click)" if info.get("one_click") and kind == "https_post"
                            else "HTTPS POST" if kind == "https_post"
                            else "send a mailto with 'unsubscribe' as the subject"
                        )
                        yield _reply(
                            f"⚠️ **Confirm List-Unsubscribe for `{info['sender_email']}`**\n\n"
                            f"- **Action:** {action_label}\n"
                            f"- **Target:** `{target_url}`\n"
                            f"- **Domain check:** `{info['sender_domain']}` (suffix match required ✓)\n\n"
                            f"This will execute the action on the newsletter operator's side and add a permanent "
                            f"entry to the audit log. Even if it succeeds, the sender will also be added to your "
                            f"local blocklist as a safety net.\n\n"
                            f"To execute, send: `confirm unsubscribe {token}` within the next 5 minutes. "
                            f"Or do nothing — the token expires and no action is taken."
                        )

        elif intent == "confirm_unsubscribe":
            from services.list_unsubscribe import execute
            token = (params.get("token") or "").strip()
            if not token:
                yield _reply("I need the confirmation token — e.g. `confirm unsubscribe abc123def456`.")
            else:
                yield _reply(f"🔒 Executing unsubscribe request `{token}`…")
                try:
                    result = await execute(token)
                except Exception as _ee:
                    yield _reply(f"⚠ Execute failed: {_ee}")
                else:
                    if result.get("result") == "expired":
                        yield _reply(f"⚠ Token expired or unknown. Run `try unsubscribe <sender>` again to get a fresh token.")
                    elif result.get("ok"):
                        yield _reply(
                            f"✅ Unsubscribe sent for `{result['sender_email']}` "
                            f"({result['target_type']}). {result.get('detail', '')}. "
                            f"Local blocklist entry also added as a safety net."
                        )
                    else:
                        yield _reply(
                            f"⚠ Unsubscribe failed for `{result['sender_email']}`: "
                            f"{result.get('detail', 'unknown error')}. "
                            f"Local blocklist entry was added anyway so we stop ingesting."
                        )

        elif intent == "show_unsubscribe_log":
            from services.list_unsubscribe import get_recent_log
            log_rows = get_recent_log(limit=20)
            if not log_rows:
                yield _reply("📭 No List-Unsubscribe attempts logged yet.")
            else:
                lines = [f"**📋 List-Unsubscribe audit log ({len(log_rows)} entries):**\n"]
                for r in log_rows:
                    emoji = "✅" if r.get("result") == "sent" else "⚠"
                    ts = (r.get("ts") or "")[:19].replace("T", " ")
                    detail = r.get("result_detail") or ""
                    lines.append(
                        f"- {emoji} {ts} · `{r.get('sender_email', '?')}` "
                        f"→ {r.get('target_type')} → **{r.get('result')}** ({detail[:80]})"
                    )
                yield _reply("\n".join(lines))

        # ─────────────────────────────────────────────────────────────
        # EFFECTIVENESS DASHBOARD (Phase 4 Tier 2 / I — 2026-06-10)
        # ─────────────────────────────────────────────────────────────
        elif intent == "show_score":
            try:
                from services.correspondent_dashboard import compute_dashboard, compose_dashboard_html
                metrics_data = await compute_dashboard()
                dashboard_html = compose_dashboard_html(metrics_data)
                yield _reply("```html\n" + dashboard_html + "\n```")
            except Exception as _de:
                logger.warning(f"[correspondent.show_score] failed: {_de}")
                yield _reply(f"⚠ Couldn't compute the dashboard: {_de}")

        # ─────────────────────────────────────────────────────────────
        # FREQUENCY TUNER (Phase 4 Tier 2 / G — 2026-06-10)
        # ─────────────────────────────────────────────────────────────
        elif intent == "digest_mode":
            target = (params.get("email_or_name") or "").strip()
            if not target:
                yield _reply("Tell me which sender — e.g. `digest mode Stratechery`.")
            else:
                from services.sender_frequency import set_mode, detect_cadence_async
                # Auto-align digest day with sender cadence (G.2 locked)
                cadence = await detect_cadence_async(target)
                day = cadence.get("suggested_digest_day", 1)
                ok = set_mode(target, "weekly_digest", digest_day=day)
                if not ok:
                    yield _reply(f"⚠ Couldn't switch `{target}` to digest mode.")
                else:
                    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                    day_label = day_names[day - 1] if 1 <= day <= 7 else "Mon"
                    rate = cadence.get("weekly_rate", 0.0)
                    rate_str = f" (sends ~{rate:.1f}/week)" if rate else ""
                    yield _reply(
                        f"✅ Switched `{target}` to **weekly digest mode**{rate_str}. "
                        f"New emails will be buffered; you'll get one summary on **{day_label}** mornings. "
                        f"Reverse with `live mode {target}`."
                    )

        elif intent == "live_mode":
            target = (params.get("email_or_name") or "").strip()
            if not target:
                yield _reply("Tell me which sender — e.g. `live mode Stratechery`.")
            else:
                from services.sender_frequency import set_mode, list_pending
                # Switching back to live — if pending items exist, surface
                # that we still have them (won't ingest until next digest tick).
                pending = list_pending(target)
                ok = set_mode(target, "live")
                if not ok:
                    yield _reply(f"⚠ Couldn't switch `{target}` to live mode.")
                else:
                    pending_msg = ""
                    if pending:
                        pending_msg = (
                            f" There were **{len(pending)}** pending email(s) buffered — they'll wait until "
                            f"the next digest cycle for that sender, or you can force-ship with "
                            f"`@correspondent force digest {target}` (TODO)."
                        )
                    yield _reply(
                        f"✅ Switched `{target}` back to **live ingest mode**.{pending_msg}"
                    )

        elif intent == "show_digest_mode":
            from services.sender_frequency import list_settings, list_pending
            settings_rows = list_settings()
            digest_rows = [s for s in settings_rows if s.get("bundle_mode") == "weekly_digest"]
            if not digest_rows:
                yield _reply(
                    "📭 No senders in digest mode. "
                    "Switch one with `digest mode <sender>` to bundle their emails into a weekly summary."
                )
            else:
                day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                lines = [f"**📨 {len(digest_rows)} sender(s) in weekly digest mode:**\n"]
                for s in digest_rows:
                    sender = s["sender_email"]
                    day = int(s.get("digest_day") or 1)
                    day_label = day_names[day - 1] if 1 <= day <= 7 else "Mon"
                    pending_count = len(list_pending(sender))
                    pending_label = f" · **{pending_count}** pending" if pending_count else ""
                    lines.append(f"- `{sender}` → ships **{day_label}** mornings{pending_label}")
                lines.append("\n_Switch back with `live mode <sender>`._")
                yield _reply("\n".join(lines))

        # ─────────────────────────────────────────────────────────────
        # ROUTING HISTOGRAM (Phase 4 Tier 2 / J — 2026-06-10)
        # ─────────────────────────────────────────────────────────────
        elif intent == "show_routing":
            from services.routing_telemetry import get_distribution
            dist = get_distribution(days=14)
            if dist["total"] == 0:
                # Q7 (2026-06-10) — more useful empty state: tell the
                # user exactly what would populate this.
                yield _reply(
                    "📊 **No routing decisions logged yet over the last 14 days.**\n\n"
                    "This populates when:\n"
                    "- The IMAP poller routes a new newsletter (auto or queue)\n"
                    "- You manually approve a queued item from chat or Settings\n\n"
                    "Trigger a manual sync with `@correspondent sync now`, or wait for the next 8-hour poll cycle."
                )
            else:
                threshold = dist["threshold"]
                auto_rate = dist["auto_rate"]
                buckets = dist["buckets"]
                # Q6 (2026-06-10) — three series now: auto, manual, queued.
                non_empty = [b for b in buckets if (b["auto"] + b.get("manual", 0) + b["queued"]) > 0]
                labels = [f"{b['lo']:.2f}" for b in non_empty]
                series = [
                    {"label": "auto-routed", "data": [b["auto"] for b in non_empty]},
                ]
                if dist.get("manual", 0) > 0:
                    series.append({"label": "manual approve", "data": [b.get("manual", 0) for b in non_empty]})
                series.append({"label": "queued", "data": [b["queued"] for b in non_empty]})
                chart = {
                    "kind": "bar",
                    "title": f"Routing decisions — last {dist['window_days']}d",
                    "labels": labels,
                    "series": series,
                }
                # Threshold-tuning advice
                above_thr = sum(b["auto"] + b.get("manual", 0) + b["queued"] for b in non_empty if b["lo"] >= threshold)
                advice = ""
                if dist["total"] >= 20:
                    pct_at_thr = above_thr / dist["total"]
                    if pct_at_thr > 0.9:
                        advice = (
                            f"\n_Most routes ({pct_at_thr:.0%}) clear the {threshold:.2f} threshold easily — "
                            f"you could try raising it to 0.80 for tighter quality._"
                        )
                    elif pct_at_thr < 0.5:
                        advice = (
                            f"\n_Only {pct_at_thr:.0%} clear {threshold:.2f}. Most stuff goes to queue. "
                            f"Lowering to 0.70 would auto-route more (risk: more mis-routes)._"
                        )
                lines = [
                    f"**📊 Routing confidence — last {dist['window_days']} days**\n",
                    f"- **Total decisions:** {dist['total']}",
                    f"- **Auto-routed:** {dist['auto']} ({dist['auto_rate']:.0%})",
                ]
                if dist.get("manual", 0) > 0:
                    lines.append(f"- **Manual approves:** {dist['manual']} ({dist['manual'] / dist['total']:.0%})")
                lines.append(f"- **Queued (no approval yet):** {dist['queued']}")
                lines.append(f"- **Threshold:** {threshold:.2f}")
                lines.append(advice)
                lines.append("")
                lines.append("```json-chart")
                lines.append(json.dumps(chart))
                lines.append("```")
                yield _reply("\n".join(lines))

        # ─────────────────────────────────────────────────────────────
        # UNSUBSCRIBE SURFACE (Phase 3 Tier 2 / F — 2026-06-10)
        # ─────────────────────────────────────────────────────────────
        elif intent == "show_unsubscribe_candidates":
            from services.unsubscribe_suggestions import list_candidates, DEFAULT_GRADE_THRESHOLD
            try:
                cands = await list_candidates()
            except Exception as _ue:
                cands = []
                logger.warning(f"[correspondent.unsub] candidate fetch failed: {_ue}")
            if not cands:
                yield _reply(
                    f"✓ No senders are scoring at or below {DEFAULT_GRADE_THRESHOLD} with enough volume to drop. "
                    f"Either every subscription is earning its keep or there's not enough history yet."
                )
            else:
                lines = [
                    f"**🌙 {len(cands)} unsubscribe candidate(s)** — scored {DEFAULT_GRADE_THRESHOLD} or worse for several weeks:\n"
                ]
                for c in cands[:8]:
                    lines.append(
                        f"- `{c['sender_email']}` — grade **{c.get('grade') or '—'}**, "
                        f"composite **{c.get('composite_score', 0):.2f}**, "
                        f"{c.get('lifetime_emails', 0)} email(s) ingested. "
                        f"Drop with `unsubscribe {c['sender_email']}` (or `unsubscribe {c['sender_email']} snooze 30`)."
                    )
                lines.append(
                    "\n_Reminder: this adds the sender to a local blocklist so we stop ingesting. "
                    "It does NOT email-unsubscribe (use the link in your inbox for that). "
                    "Reverse with `unblock <sender>`._"
                )
                yield _reply("\n".join(lines))

        elif intent == "unsubscribe_sender":
            from services.unsubscribe_suggestions import add_to_blocklist
            target = (params.get("email_or_name") or "").strip()
            snooze_days_param = params.get("snooze_days")
            if not target:
                yield _reply("Tell me which sender — e.g. `unsubscribe alice@news.io` or `unsubscribe Stratechery snooze 30`.")
            else:
                # Allow inline "snooze N" suffix in the natural-language input
                snooze_days = None
                if snooze_days_param:
                    try:
                        snooze_days = int(snooze_days_param)
                    except (TypeError, ValueError):
                        snooze_days = None
                if snooze_days is None:
                    import re as _re_sn
                    m = _re_sn.search(r"(.+?)\s+snooze\s+(\d+)", target)
                    if m:
                        target = m.group(1).strip()
                        snooze_days = int(m.group(2))
                ok = add_to_blocklist(
                    sender_email=target,
                    reason="user requested via chat",
                    snooze_days=snooze_days,
                )
                if not ok:
                    yield _reply(f"⚠ Couldn't block `{target}`.")
                elif snooze_days:
                    yield _reply(
                        f"😴 Snoozed `{target}` for **{snooze_days} day(s)**. Will resume ingestion after that. "
                        f"Reverse anytime with `unblock {target}`."
                    )
                else:
                    yield _reply(
                        f"🛑 Stopped ingesting from `{target}`. New emails will be silently dropped. "
                        f"To actually email-unsubscribe, use the link in your inbox. "
                        f"Reverse with `unblock {target}`."
                    )

        elif intent == "show_blocklist":
            from services.unsubscribe_suggestions import list_blocked
            blocked = list_blocked()
            if not blocked:
                yield _reply("📭 Blocklist is empty.")
            else:
                now_iso = datetime.utcnow().isoformat()
                active = []
                snoozed = []
                for b in blocked:
                    if b.get("snooze_until") and b["snooze_until"] > now_iso:
                        snoozed.append(b)
                    else:
                        active.append(b)
                lines = []
                if active:
                    lines.append(f"**🛑 Blocked ({len(active)}):**")
                    for b in active:
                        lines.append(f"- `{b['sender_email']}` — blocked {b.get('blocked_at', '?')[:10]}")
                if snoozed:
                    lines.append(f"\n**😴 Snoozed ({len(snoozed)}):**")
                    for b in snoozed:
                        lines.append(f"- `{b['sender_email']}` — resumes {b.get('snooze_until', '?')[:10]}")
                lines.append("\n_Reverse with `unblock <sender>`._")
                yield _reply("\n".join(lines))

        elif intent == "unblock_sender":
            from services.unsubscribe_suggestions import remove_from_blocklist, list_blocked
            target = (params.get("email_or_name") or "").strip()
            if not target:
                yield _reply("Tell me which sender — e.g. `unblock alice@news.io`.")
            else:
                # Allow LIKE match
                all_blocked = list_blocked()
                match = next(
                    (b for b in all_blocked if target.lower() in b["sender_email"].lower()),
                    None,
                )
                if not match:
                    yield _reply(f"Couldn't find `{target}` on the blocklist.")
                else:
                    ok = remove_from_blocklist(match["sender_email"])
                    if ok:
                        yield _reply(f"✓ Resumed ingesting from `{match['sender_email']}`.")
                    else:
                        yield _reply(f"⚠ Couldn't remove `{match['sender_email']}`.")

        # ─────────────────────────────────────────────────────────────
        # SCORECARDS (Phase 2.5 Tier 2 — 2026-06-09)
        # ─────────────────────────────────────────────────────────────
        elif intent == "score_sender":
            sender_query = (params.get("email_or_name") or "").strip()
            if not sender_query:
                yield _reply("Tell me which sender — e.g. `score Stratechery`.")
            else:
                from services.newsletter_scorecard import (
                    get_scorecard, recompute_all, load_weights, grade_color,
                )
                card = await get_scorecard(sender_query)
                if not card:
                    # Try one recompute in case data is new
                    yield _reply("🔄 Computing scorecards for the first time…")
                    await recompute_all()
                    card = await get_scorecard(sender_query)
                if not card:
                    yield _reply(f"Couldn't find a scorecard for `{sender_query}`. Have they sent any newsletters yet?")
                else:
                    raw_weights = load_weights()
                    color = grade_color(card.get("grade") or "—")
                    lines = [
                        f"**{color} `{card['sender_email']}` — Grade: {card.get('grade') or '—'}**\n",
                        f"- **Composite score:** {card.get('composite_score', 0):.2f}",
                        f"- **Volume:** {card.get('volume_per_week', 0):.1f} emails/week",
                        f"- **Highlight rate:** {card.get('highlight_rate', 0):.2f}",
                        f"- **Read-through:** {card.get('read_through', 0):.2f} _(coming soon)_",
                        f"- **Citation rate:** {card.get('citation_rate', 0):.2f} _(coming soon)_",
                        f"- **Action conversion:** {card.get('action_conversion', 0):.2f} _(coming soon)_",
                        "",
                        "<details><summary><strong>How this is calculated</strong></summary>",
                        "",
                        f"Composite score = "
                        f"{raw_weights['highlight_rate']:.0%} highlight_rate + "
                        f"{raw_weights['citation_rate']:.0%} citation_rate + "
                        f"{raw_weights['read_through']:.0%} read_through + "
                        f"{raw_weights['action_conversion']:.0%} action_conversion.",
                        "",
                        "Until citation_rate, read_through, and action_conversion data pipelines are wired up, ",
                        "their weights are redistributed to the available metric (highlight_rate). Once they come ",
                        "online the formula reverts to the designed weights automatically.",
                        "",
                        "Grade thresholds: A ≥ 0.80 · B ≥ 0.60 · C ≥ 0.40 · D ≥ 0.20 · F < 0.20. ",
                        f"Insufficient data when fewer than 5 newsletters in the last 30 days.",
                        "",
                        "</details>",
                    ]
                    yield _reply("\n".join(lines))

        elif intent == "show_scorecards":
            from services.newsletter_scorecard import (
                list_scorecards, recompute_all, grade_color,
            )
            scards = await list_scorecards(limit=30)
            if not scards:
                yield _reply("🔄 Computing scorecards for the first time…")
                await recompute_all()
                scards = await list_scorecards(limit=30)
            if not scards:
                yield _reply("No scorecards yet. Add some inboxes and let some newsletters ingest first.")
            else:
                lines = [f"**📊 Newsletter scorecards ({len(scards)} sender(s)):**\n"]
                lines.append("| Grade | Sender | Volume/wk | Highlights | Score |")
                lines.append("|:--|:--|--:|--:|--:|")
                for c in scards:
                    color = grade_color(c.get("grade") or "—")
                    lines.append(
                        f"| {color} {c.get('grade') or '—'} "
                        f"| `{(c.get('sender_email') or '?')[:40]}` "
                        f"| {c.get('volume_per_week', 0):.1f} "
                        f"| {c.get('highlight_rate', 0):.2f} "
                        f"| {c.get('composite_score', 0):.2f} |"
                    )
                lines.append("\n_Drop into one with `@correspondent score <sender>` for the full card._")
                yield _reply("\n".join(lines))

        # ─────────────────────────────────────────────────────────────
        # HOT / COLD + SUMMARIES (I6)
        # ─────────────────────────────────────────────────────────────
        elif intent in ("whats_hot", "whats_cold", "summarize_recent"):
            try:
                days = int(params.get("days") or 7)
            except (TypeError, ValueError):
                days = 7
            from services.correspondent_trends import compute_topic_trends, summarize_recent_intake

            # P2.3 — when user asks for "deep" / "cluster" mode (or
            # passes deep=true), serve article-level clusters instead of
            # the tag-based trend bars.
            deep_param = params.get("deep")
            deep_mode = False
            if isinstance(deep_param, bool):
                deep_mode = deep_param
            elif isinstance(deep_param, str):
                deep_mode = deep_param.lower() in ("true", "1", "yes", "deep", "cluster")
            else:
                # fallback heuristic — user query contains the magic word
                deep_mode = any(w in q.lower() for w in ("deep", "cluster", "theme"))

            if intent == "summarize_recent":
                lines = await summarize_recent_intake(days=days)
                yield _reply(lines)
            elif deep_mode:
                from services.article_clusterer import get_recent_clusters, recluster_all
                clusters = await get_recent_clusters(limit=8)
                # If no clusters exist yet (or are stale), kick off a recluster
                clustering_msg = ""
                if not clusters:
                    yield _reply("🔄 Clustering articles… this is the first time so it may take ~30 seconds.\n\n")
                    await recluster_all()
                    clusters = await get_recent_clusters(limit=8)
                if not clusters:
                    yield _reply(
                        "Not enough embedded articles to cluster yet. "
                        "New newsletters are being embedded in the background — try again after a few more ingests."
                    )
                else:
                    polarity = "hot" if intent == "whats_hot" else "cold"
                    items = [c for c in clusters if (c["delta"] > 0 if polarity == "hot" else c["delta"] < 0)]
                    if not items:
                        yield _reply(f"No clusters {polarity} right now. Try `@correspondent whats_{polarity}` for the tag-based view instead.")
                    else:
                        items.sort(key=lambda c: abs(c["delta"]), reverse=True)
                        items = items[:6]
                        # Card payload (per C.1 locked decision)
                        payload_obj = {
                            "polarity": polarity,
                            "items": [
                                {
                                    "label": c.get("label") or "(unlabeled)",
                                    "size": int(c.get("size", 0)),
                                    "recent_size": int(c.get("recent_size", 0)),
                                    "baseline_size": int(c.get("baseline_size", 0)),
                                    "delta": int(c.get("delta", 0)),
                                    "sender_count": len(c.get("sender_counts") or {}),
                                    "notebook_count": len(c.get("notebook_counts") or {}),
                                    "sample_senders": list((c.get("sender_counts") or {}).keys())[:3],
                                }
                                for c in items
                            ],
                        }
                        # Q4 (2026-06-10) — leading \n\n so the fence is
                        # always on its own line even when concatenated
                        # after a prior status message.
                        yield _reply("\n\n```json-correspondent-hot-clusters\n" + json.dumps(payload_obj) + "\n```")
            else:
                trends = await compute_topic_trends(days=days)
                if not trends:
                    yield _reply(f"Not enough newsletter data in the last {days * 2} days to compute trends. Add more sources and try again.")
                else:
                    polarity = "hot" if intent == "whats_hot" else "cold"
                    items = [t for t in trends if (t["delta"] > 0 if polarity == "hot" else t["delta"] < 0)]
                    items.sort(key=lambda t: abs(t["delta"]), reverse=True)
                    items = items[:8]
                    if not items:
                        yield _reply(f"Nothing notably {polarity} right now over the last {days} days. Try `whats_hot deep=true` for article-cluster view.")
                    else:
                        title = "Topics gaining momentum" if polarity == "hot" else "Topics cooling off"
                        lines = [f"**🌡 {title} (last {days}d vs prior {days}d):**\n"]
                        for t in items:
                            arrow = "↑" if t["delta"] > 0 else "↓"
                            lines.append(f"- `{t['topic']}` — {arrow} {t['recent']} (was {t['baseline']})")
                        chart = {
                            "kind": "bar",
                            "title": title,
                            "labels": [t["topic"][:30] for t in items],
                            "series": [
                                {"label": f"last {days}d", "data": [t["recent"] for t in items]},
                                {"label": f"prior {days}d", "data": [t["baseline"] for t in items]},
                            ],
                        }
                        lines.append("\n```json-chart\n" + json.dumps(chart) + "\n```")
                        lines.append("\n_Tip: try `whats_hot deep=true` for article-level clusters (richer signal)._")
                        yield _reply("\n".join(lines))

        # ─────────────────────────────────────────────────────────────
        # SHOW_STATUS (default)
        # ─────────────────────────────────────────────────────────────
        else:
            status = correspondent_agent.status() or {}
            accounts = status.get("accounts") or {}
            queue = correspondent_agent.list_queue()
            subs = correspondent_agent.list_subscription_queue()
            if not accounts:
                yield _reply("No polling activity yet. Add an inbox in Settings → Correspondent.")
            else:
                lines = ["**📬 Correspondent status**\n"]
                lines.append(f"- **Pending approvals:** {len(queue)}")
                lines.append(f"- **Subscription proposals:** {len(subs)}")
                lines.append("\n**Inboxes:**")
                for email, info in accounts.items():
                    res = info.get("last_result") or {}
                    err = info.get("last_error")
                    line = (
                        f"- `{email}` — last sync {info.get('last_polled_at', '?')[:19].replace('T', ' ')}: "
                        f"{res.get('ingested', 0)} auto-routed, {res.get('queued', 0)} queued"
                    )
                    if err:
                        line += f" · ⚠ {err[:80]}"
                    lines.append(line)
                if queue:
                    lines.append("\n_Say `show queue` to triage pending items, or `what's hot` for trends._")
                yield _reply("\n".join(lines))

        yield _done()
    except Exception as e:
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'error': f'Correspondent error: {e}'})}\n\n"


async def _stream_research(chat_query: ChatQuery, injected_action: Optional[Dict[str, Any]] = None):
    """Stream a Research agent response in SSE format.

    LLM-based NLP intent router with three modes:
      - web_search:  broad web search
      - site_search: domain-scoped search
      - deep_dive:   multi-hop search → scrape → quality-score → synthesise
    Results are streamed as a narrative summary followed by a structured
    'research_results' event so the frontend can render approval cards.
    """
    from services.ollama_service import ollama_service
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
        # ── Intent classification (bypassed if injected by dispatcher) ──
        if injected_action:
            classified = injected_action
        else:
            classified = await classify_intent(q, "research")
        intent = classified["intent"]
        params = classified.get("params", {})

        # Curator Phase 2a: emit research intent dispatch event.
        try:
            from services.curator_event_bus import event_bus
            event_bus.emit_now(
                actor="@research",
                action="research_intent_dispatched",
                notebook_id=notebook_id,
                intent=intent,
                payload={
                    "message_chars": len(q),
                    "confidence": classified.get("confidence", 0.5),
                    "injected": bool(injected_action),
                },
            )
        except Exception as _e:
            logger.debug(f"[chat] research intent emit failed: {_e}")

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
            follow_ups = ['Deep dive into the top result', 'Narrow the search', 'Search a specific site']
        else:
            follow_ups = ['Try a broader search', 'Search a specific site', 'Deep dive with filters']

        yield f"data: {json.dumps({'type': 'done', 'follow_up_questions': follow_ups, 'agent_name': 'Research', 'agent_type': 'research'})}\n\n"

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'error': f'Research error: {e}'})}\n\n"


async def _stream_studio(chat_query: ChatQuery, injected_action: Optional[Dict[str, Any]] = None):
    """Stream a Studio agent response in SSE format.

    LLM-based intent router — lets the user create Studio content (audio,
    documents, quizzes, visuals, videos) directly from the chat, using the
    current conversation as context.
    """
    from services.ollama_service import ollama_service
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
        # ── Intent classification (bypassed if injected by dispatcher) ──
        if injected_action:
            classified = injected_action
        else:
            classified = await classify_intent(q, "studio")
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
        # GENERATE FLASH CARDS (reuses quiz generator, directed to Cards tab)
        # -----------------------------------------------------------------
        elif intent == "generate_flashcards":
            yield f"data: {json.dumps({'type': 'status', 'message': 'Studio generating flash cards...', 'query_type': 'studio'})}\n\n"
            try:
                from api.quiz import generate_quiz as _gen_quiz, GenerateQuizRequest

                # Flash Cards accept 3..50 (backend model is 1..50, we tighten here)
                num_cards = int(params.get("num_cards", params.get("num_questions", 10)))
                if num_cards < 3: num_cards = 3
                if num_cards > 50: num_cards = 50
                difficulty = (params.get("difficulty") or "medium").strip().lower()
                if difficulty not in ("easy", "medium", "hard"):
                    difficulty = "medium"

                # Flash-card-friendly mix: mostly short_answer (free recall) plus a
                # few multiple_choice for quick wins. No T/F (too easy to guess on
                # flashcards) and no spot_the_error (needs highlighted context).
                result = await _gen_quiz(GenerateQuizRequest(
                    notebook_id=notebook_id,
                    num_questions=num_cards,
                    difficulty=difficulty,
                    topic=topic,
                    chat_context=chat_context,
                    question_types=["short_answer", "multiple_choice", "fill_in_the_blank"],
                ))
                questions = result.questions
                log_content_generated(notebook_id, "flashcards", "flashcards", topic or "chat-context")

                lines = [
                    f"**Flash Cards ready!** 🧠  ({len(questions)} cards, {difficulty})",
                    f"",
                    f"An interactive deck has been dropped onto your canvas — answer by click, type, or voice. "
                    f"Your tutor will read feedback aloud when you miss one.",
                    f"",
                ]
                # Preview the first few card fronts
                for i, q_item in enumerate(questions[:3]):
                    lines.append(f"**Card {i+1}.** {q_item.question}")
                if len(questions) > 3:
                    lines.append(f"")
                    lines.append(f"*...plus {len(questions) - 3} more cards waiting.*")
                reply = "\n".join(lines)
                follow_ups = ['Make it harder', 'Give me fewer cards', 'Switch to a full quiz']

            except Exception as fe:
                reply = f"Flash card generation failed: {fe}"

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
        except Exception as _e:
            logger.debug(f"[chat] log_chat_qa failed (non-fatal): {_e}")

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
