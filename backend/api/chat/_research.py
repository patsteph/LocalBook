"""_stream_research handler — extracted from api/chat.py (Wave 5 split)."""
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
