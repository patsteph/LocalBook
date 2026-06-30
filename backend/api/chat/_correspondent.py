"""_stream_correspondent handler — extracted from api/chat.py (Wave 5 split)."""
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
