"""Core chat endpoints (/query, /query/stream) — Wave 5 split."""
from ._router import router  # noqa: F401
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
from ._curator import _stream_curator
from ._collector import _stream_collector
from ._correspondent import _stream_correspondent
from ._research import _stream_research
from ._studio import _stream_studio

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
