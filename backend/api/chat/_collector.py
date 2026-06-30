"""_stream_collector handler + ingest helpers — extracted from api/chat.py (Wave 5 split)."""
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
