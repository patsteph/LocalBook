"""
LLM-based Intent Classifier for @curator, @collector, and @research agents.

Replaces fragile regex pattern matching with natural language understanding.
Uses the local LLM for fast, accurate intent classification with parameter extraction.
"""
import json
import logging
from typing import Dict, Any, Optional, List

from config import settings
from utils.json_repair import robust_json_parse

logger = logging.getLogger(__name__)


# ─── Intent Definitions ─────────────────────────────────────────────────────

CURATOR_INTENTS: List[Dict[str, str]] = [
    {"id": "set_name", "desc": "User wants to rename the curator", "params": "name: the new name"},
    {"id": "set_personality", "desc": "User wants to change the curator's personality, tone, or style", "params": "personality: description of the personality"},
    {"id": "toggle_overwatch", "desc": "User wants to enable or disable overwatch/interjections", "params": "enabled: true or false"},
    {"id": "exclude_notebook", "desc": "User wants to exclude a notebook from cross-notebook operations", "params": "notebook_name: name of the notebook"},
    {"id": "include_notebook", "desc": "User wants to include a notebook back into cross-notebook operations", "params": "notebook_name: name of the notebook"},
    {"id": "morning_brief", "desc": "User wants their morning brief, catch-up, or summary of what they missed", "params": "none"},
    {"id": "weekly_wrap_up", "desc": "User wants a weekly wrap up, week in review, or weekly summary of research activity", "params": "none"},
    {"id": "discover_patterns", "desc": "User wants to find patterns, connections, or common themes across notebooks", "params": "none"},
    {"id": "devils_advocate", "desc": "User wants counterarguments, challenges, or a devil's advocate perspective", "params": "thesis: the claim or topic to challenge (if specified)"},
    {"id": "show_profile", "desc": "User wants to see the curator's current configuration or settings", "params": "none"},
    {"id": "note_themes", "desc": "User wants to extract themes from their notes, suggest keywords for the collector based on notes, or connect their notes to collector searches", "params": "none"},
    {"id": "collection_schedule", "desc": "User wants to see the collection schedule, when collectors last ran, what they found, or overall collection status across all notebooks", "params": "none"},
    {"id": "brain_status", "desc": "User wants to see the brain status, notebook digests, cross-notebook connections, or what the Curator currently understands about their research", "params": "none"},
    {"id": "dismiss_connection", "desc": "User wants to dismiss, ignore, or remove a cross-notebook connection — says it is wrong, not useful, or they don't want to see it again", "params": "connection_id: integer ID of the connection if the user specifies it"},
    {"id": "approve_connection", "desc": "User wants to confirm, approve, or thumbs-up a cross-notebook connection — says it is useful, accurate, or interesting", "params": "connection_id: integer ID of the connection if the user specifies it"},
    {"id": "show_weakest_hypothesis", "desc": "User wants the curator to surface the claim it is LEAST confident about — phrases like 'what are you least sure about', 'show me your weakest hypothesis', 'what should I correct', 'what's your shakiest claim', 'where might you be wrong'.", "params": "none"},
    {"id": "set_voice", "desc": "User wants to change the curator's narrative voice/writing style — phrases like 'set my voice to X', 'make the curator more conversational', 'switch to executive brief mode', 'change writing style to smart colleague'. The voice option must be one of: smart_colleague, executive_brief, conversational_analyst.", "params": "voice: one of smart_colleague | executive_brief | conversational_analyst (normalize the user's phrasing — e.g. 'executive' → 'executive_brief', 'colleague' → 'smart_colleague', 'analyst' or 'conversational' → 'conversational_analyst')"},
    {"id": "show_voice", "desc": "User wants to see the curator's current narrative voice and the available options — phrases like 'what voice are you using', 'show me your writing style', 'what voices can I pick', 'list voices'.", "params": "none"},
    {"id": "show_draft", "desc": "User wants to view an anticipatory draft the curator pre-generated — phrases like 'show me the draft', 'show draft', 'what did you draft', 'open the draft for me'.", "params": "none"},
    {"id": "discard_draft", "desc": "User wants to discard / reject / delete an anticipatory draft the curator pre-generated — phrases like 'discard the draft', 'trash that draft', 'I don't want that draft', 'no thanks on the draft'.", "params": "none"},
    {"id": "suppress_brief_topic", "desc": "User EXPLICITLY wants to mute, suppress, hide, or block a specific topic keyword from appearing in future content. REQUIRES an action verb like 'mute', 'suppress', 'stop showing', 'hide', 'block', 'don't show me' AND a topic keyword. Examples: 'stop showing me crypto stories', 'mute AI safety', 'hide leadership content'. Do NOT match for general requests about briefs or summaries — only when the user is actively asking to BLOCK something.", "params": "topic: the keyword/phrase to suppress (extract just the topic, not filler words)"},
    {"id": "unsuppress_brief_topic", "desc": "User EXPLICITLY wants to unmute, unblock, or reinstate a previously-muted topic. REQUIRES verbs like 'unmute', 'unblock', 'show me X again', 'undo the mute on X'. Do NOT match for general requests.", "params": "topic: the keyword/phrase to unmute"},
    {"id": "list_suppressed_topics", "desc": "User wants to LIST what they have already muted — phrases like 'what topics am I muting', 'show me my mutes', 'what have I blocked'. Do NOT match for general 'show me' or 'what's in my' requests.", "params": "none"},
    {"id": "cross_notebook_search", "desc": "User is asking a question that requires searching across notebooks (default for general questions)", "params": "query: the search question"},
]

RESEARCH_INTENTS: List[Dict[str, str]] = [
    {"id": "web_search", "desc": "User wants to search the web broadly for information on a topic (default)", "params": "query: the search query, max_results: number of results if specified"},
    {"id": "site_search", "desc": "User wants to search a specific website or domain (mentions site:, domain, or a specific website name)", "params": "query: the search query, site: the domain to search (e.g. arxiv.org, reddit.com)"},
    {"id": "deep_dive", "desc": "User wants an in-depth, thorough, multi-source research with quality filters. Triggered by words like 'deep dive', 'thorough research', 'comprehensive', 'in-depth', or when user specifies quality criteria like citation count, recency, sentiment, peer review, etc.", "params": "query: the search query, recency_days: max age in days, min_word_count: minimum article length, topic_qualifiers: list of quality criteria the user specified"},
]

STUDIO_INTENTS: List[Dict[str, str]] = [
    {"id": "generate_audio", "desc": "User wants to create a podcast, audio, interview, discussion, or listen to content. Keywords: podcast, audio, listen, interview, discussion, conversation, debate, hosts", "params": "topic: the topic or focus, skill_id: podcast style if specified (podcast/interview/feynman_curriculum/deep_analysis), host1_gender: male or female, host2_gender: male or female, duration_minutes: length if specified"},
    {"id": "generate_document", "desc": "User wants to create a written document, summary, report, brief, study guide, cheat sheet, or any text-based content", "params": "topic: the topic or focus, skill_id: document type if clear (executive_brief/study_guide/cheat_sheet/research_summary/white_paper/lesson_plan), style: writing style if mentioned"},
    {"id": "generate_quiz", "desc": "User wants to create a quiz, test, practice questions, or knowledge check — multi-question test with options/grading. Keywords: quiz, test, assessment, questions.", "params": "topic: the topic or focus, num_questions: number of questions if specified, difficulty: easy/medium/hard if specified"},
    {"id": "generate_flashcards", "desc": "User wants to create flash cards to study interactively — a deck of Q/A cards for self-review, one card at a time, with click/type/voice answering. Keywords: flash cards, flashcards, study cards, memorize, drill, study deck, learning cards. Prefer this over generate_quiz when the user mentions 'cards', 'study', 'memorize', or 'drill'.", "params": "topic: the topic or focus, num_cards: number of cards (3-50) if specified, difficulty: easy/medium/hard if specified"},
    {"id": "generate_visual", "desc": "User wants to create a visual, diagram, chart, infographic, mind map, flowchart, or any visual representation", "params": "topic: the topic or focus, visual_type: type of visual if specified"},
    {"id": "generate_video", "desc": "User wants to create a video, explainer video, or visual presentation with narration", "params": "topic: the topic or focus, duration_minutes: length if specified, visual_style: style if specified, voice: voice preference if specified"},
]

COLLECTOR_INTENTS: List[Dict[str, str]] = [
    {"id": "subscribe", "desc": "User wants to subscribe to, follow, or monitor a source for recurring new content (YouTube channel, blog, podcast, newsletter). Includes phrases like 'subscribe', 'follow', 'monitor', 'watch for new', 'keep checking', or any URL with a schedule request", "params": "url: the URL, schedule: frequency if mentioned (hourly/daily/weekly)"},
    {"id": "add_url", "desc": "User wants to add a single specific URL (web page or RSS feed) as a one-time source without subscribing for updates", "params": "url: the URL, is_rss: true if RSS/feed, schedule: frequency if mentioned (hourly/daily/weekly)"},
    {"id": "remove_source", "desc": "User wants to remove, stop, disable, or unsubscribe from a source", "params": "url: the URL to remove"},
    {"id": "add_keyword", "desc": "User wants to track a news keyword or topic for alerts", "params": "keyword: the keyword or topic"},
    {"id": "add_note", "desc": "User wants to save a personal note/thought/observation as a source in this notebook. Triggered by phrases like 'add a note', 'note that', 'save this note', 'jot down', 'remember this', 'capture my thoughts on', 'add a note about what we discussed', 'save the research above'. Notes become searchable sources — do NOT confuse with add_url (which needs a URL) or add_keyword (news monitoring term).", "params": "title: short note title if the user suggested one, content: the literal note body the user dictated (leave empty if they're asking to summarize recent chat), from_chat: true if the user is asking to capture the preceding conversation (e.g. 'note what we just discussed', 'save the chat above', 'add a note about the research we are discussing')"},
    {"id": "set_intent", "desc": "User is describing the intent, purpose, or goal of this notebook/collector", "params": "intent: the described intent"},
    {"id": "set_subject", "desc": "User is setting the research subject or topic area", "params": "subject: the subject"},
    {"id": "set_focus", "desc": "User wants to set or add focus areas", "params": "areas: list of focus areas, add_to_existing: true if adding rather than replacing"},
    {"id": "set_excluded", "desc": "User wants to exclude, ignore, or block certain topics", "params": "topics: list of topics to exclude, add_to_existing: true if adding rather than replacing"},
    {"id": "set_name", "desc": "User wants to rename the collector", "params": "name: the new name"},
    {"id": "set_mode", "desc": "User wants to change collection mode to manual, automatic, or hybrid", "params": "mode: manual/automatic/hybrid"},
    {"id": "set_approval", "desc": "User wants to change approval mode (auto-approve, require review, mixed)", "params": "mode: trust_me/show_me/mixed"},
    {"id": "set_schedule", "desc": "User wants to set collection schedule frequency", "params": "frequency: hourly/daily/weekly"},
    {"id": "set_filters", "desc": "User wants to set max age or min relevance filters", "params": "max_age_days: number, min_relevance: number"},
    {"id": "collect_now", "desc": "User wants to trigger an immediate collection run", "params": "none"},
    {"id": "show_pending", "desc": "User wants to see pending approval items", "params": "none"},
    {"id": "approve_all", "desc": "User wants to approve all pending items", "params": "none"},
    {"id": "source_health", "desc": "User wants to check source health or find broken/failing sources", "params": "none"},
    {"id": "show_profile", "desc": "User wants to see the collector's profile, configuration, or setup", "params": "none"},
    {"id": "show_status", "desc": "User wants a general status overview (default for vague messages)", "params": "none"},
    {"id": "show_history", "desc": "User wants to see recent collection run history or when the last run happened", "params": "none"},
]

CORRESPONDENT_INTENTS: List[Dict[str, str]] = [
    # Status + sync
    {"id": "show_status", "desc": "User wants a status overview — last poll time, message counts, account health, recent activity (default for vague messages)", "params": "none"},
    {"id": "sync_now", "desc": "User wants to trigger an immediate IMAP poll across all enabled accounts", "params": "none"},
    {"id": "show_accounts", "desc": "User wants to list connected inboxes and their state", "params": "none"},
    {"id": "pause", "desc": "User wants to pause polling without removing the account", "params": "email: account email if specified"},
    {"id": "resume", "desc": "User wants to resume a paused account", "params": "email: account email if specified"},
    # Approval queue
    {"id": "show_queue", "desc": "User wants to SEE the list of pending newsletter routing approvals — synonyms include 'show queue', 'show approval queue', 'show pending', 'show approvals', 'what's in the queue', 'list queued items'. Pick this for any READ/DISPLAY query about queued items. Do NOT pick approve_queued unless the user explicitly names a specific item by number.", "params": "none"},
    {"id": "approve_queued", "desc": "User wants to APPROVE a SPECIFIC queued item by its numeric position (e.g. 'approve 3', 'approve item 2', 'accept the first one'). REQUIRES an explicit number. Do NOT pick this for phrases like 'show approval queue' or 'show pending' — those are show_queue.", "params": "index: 1-based numeric position of the item to approve (REQUIRED — do not pick this intent without an explicit number)"},
    {"id": "reroute_queued", "desc": "User wants to approve a queued item but into a different notebook than the suggested one (e.g. 'send item 2 to AI Research', 'reroute 3 to my Cisco notebook'). REQUIRES both an explicit index AND a notebook name.", "params": "index: 1-based position; notebook: notebook name or fragment"},
    {"id": "dismiss_queued", "desc": "User wants to DROP/skip a SPECIFIC queued item by its numeric position (e.g. 'dismiss 3', 'skip item 2', 'delete the first one'). REQUIRES an explicit number.", "params": "index: 1-based numeric position"},
    # Subscription + entity proposals
    {"id": "show_subscriptions", "desc": "User wants to see sister-newsletter or entity-watch proposals waiting for approval", "params": "none"},
    {"id": "approve_subscription", "desc": "User wants to accept a subscription proposal by its position (e.g. 'subscribe to 2', 'accept proposal 1')", "params": "index: 1-based position"},
    {"id": "dismiss_subscription", "desc": "User wants to reject a subscription proposal by position", "params": "index: 1-based position"},
    # Trend + intelligence
    {"id": "whats_hot", "desc": "User wants to see which topics, senders, or themes are trending up across recently-ingested newsletters. If user says 'deep' or 'cluster' or 'theme', they want article-level clusters instead of topic tags.", "params": "days: lookback window in days, default 7; deep: true if user wants article-cluster mode"},
    {"id": "whats_cold", "desc": "User wants to see which topics or senders have gone quiet (declining mentions)", "params": "days: lookback window in days, default 7; deep: true if user wants article-cluster mode"},
    {"id": "summarize_recent", "desc": "User wants a summary of newsletters ingested over a period", "params": "days: lookback window in days, default 7"},
    # Sender routing learning
    {"id": "show_senders", "desc": "User wants to see which senders have learned-routing preferences (sender → notebook mappings the system has built up)", "params": "none"},
    {"id": "forget_sender", "desc": "User wants to reset the learned routing for a specific sender so future emails route by similarity again (e.g. 'forget what you learned about alice@news.io')", "params": "email: sender email address"},
    # Discovery + audit (added 2026-06-09)
    {"id": "show_recent", "desc": "User wants to see the most recent newsletters ingested chronologically (e.g. 'show recent', 'what came in today', 'list the latest')", "params": "limit: how many items, default 10"},
    {"id": "show_sender", "desc": "User wants a deep dive on one sender: how much they send, which notebook(s) they route to, recent topics (e.g. 'show me alice@news.io', 'tell me about Stratechery')", "params": "email_or_name: sender email or display-name fragment"},
    {"id": "quiet_senders", "desc": "User wants to see which senders have gone silent — haven't sent anything in 21+ days (helps decide who to unsubscribe from)", "params": "days: silence threshold in days, default 21"},
    {"id": "move_source", "desc": "User wants to re-route a source that was already ingested to a different notebook (e.g. 'move that source to AI Research', 'this should be in Cisco notebook')", "params": "source_query: title/subject fragment to find the source; notebook: target notebook name"},
    # Phase 1C Tier 2 (added 2026-06-10) — backfill
    {"id": "backfill_articles", "desc": "User wants to extract articles for existing newsletters that were ingested before per-article extraction was added (e.g. 'backfill articles', 'extract articles for old newsletters', 'rebuild article index')", "params": "none"},
    {"id": "backfill_status", "desc": "User wants to check progress of a running or recent backfill job (e.g. 'backfill status', 'how is the backfill going', 'backfill progress')", "params": "none"},
    {"id": "refresh_titles", "desc": "User wants to fix article titles in bulk — many existing articles were saved with URL or template-string titles ('View Online', 'Sign Up'). Re-runs title extraction on stored body text. (e.g. 'refresh titles', 'fix article titles', 'rebuild titles')", "params": "none"},
    {"id": "reprocess_articles", "desc": "User wants to push pre-Phase-14 articles through the new pipeline (skip classifier, per-article entity extraction, notebook section assignment, article_ingested brain events). Idempotent — already-processed articles skip via the intelligence_processed flag. (e.g. 'reprocess articles', 'reclassify articles', 'rerun phase 14')", "params": "none"},
    {"id": "reextract_articles", "desc": "User wants to re-run article extraction (split into sub-articles) on every existing newsletter source that currently has only 1 article — one-time backfill. Sources that already split correctly are left alone. When new extraction produces >=2 sub-articles, old article rows are replaced (with their RAG chunks + entity associations cleaned up) and the Phase 14 pipeline runs on the new rows. (e.g. 're-extract all', 're-split newsletters', 'reextract articles')", "params": "none"},
    {"id": "article_pipeline_status", "desc": "User wants to peek at the article pipeline batch (reprocess or re-extract) — is it running, how far has it gotten, did it finish or fail. Safe to call any time; never re-fires the batch. (e.g. 'pipeline status', 'reprocess status', 're-extract status')", "params": "none"},
    {"id": "diagnose_extraction", "desc": "User wants a read-only diagnostic of WHY most newsletters resolve to 1 article instead of splitting into sub-articles. Walks every email/forward source, runs all extraction heuristics independently per source, captures structural markers (HR / headers / tables / class repeats), and classifies each source as 'split correctly' / 'probable misfire' / 'probable genuine single-article'. Writes a JSON report. No behavior change. (e.g. 'diagnose extraction', 'extraction diagnostic', 'article extraction diagnostic')", "params": "none"},
    {"id": "show_cluster_articles", "desc": "User wants to see the articles that make up a specific hot/cold cluster — used by the 'Articles' CTA on cluster cards (e.g. 'show cluster AI agents', 'articles in cluster X')", "params": "label: cluster label/theme name"},
    # Phase 3 Tier 2 (added 2026-06-10) — unsubscribe surface
    {"id": "show_unsubscribe_candidates", "desc": "User wants to see senders whose newsletters are scoring poorly and might be worth unsubscribing from (e.g. 'show unsubscribe candidates', 'suggest unsubscribes', 'which newsletters should I drop')", "params": "none"},
    {"id": "unsubscribe_sender", "desc": "User wants to stop ingesting from a specific sender (e.g. 'unsubscribe Stratechery', 'stop ingesting alice@news.io', 'drop The Diff'). Adds to local blocklist — doesn't actually email-unsubscribe.", "params": "email_or_name: sender email or fragment; snooze_days: optional snooze duration"},
    {"id": "show_blocklist", "desc": "User wants to see the list of senders currently blocked or snoozed (e.g. 'show blocklist', 'list blocked senders')", "params": "none"},
    {"id": "unblock_sender", "desc": "User wants to remove a sender from the blocklist so ingestion resumes (e.g. 'unblock Stratechery', 'resume ingesting alice@news.io')", "params": "email_or_name: sender email or fragment"},
    # Phase 4 Tier 2 (added 2026-06-10) — J/G/I
    {"id": "show_routing", "desc": "User wants to see the routing-confidence histogram — cosine score distribution for recent routing decisions, so they can tell if the auto-route threshold is tuned right (e.g. 'show routing', 'routing histogram', 'show confidence distribution')", "params": "none"},
    {"id": "digest_mode", "desc": "User wants to switch a sender from live ingest to weekly-digest mode — held in a buffer, one summary email per week (e.g. 'digest mode Stratechery', 'weekly digest for alice@news.io', 'bundle X into weekly')", "params": "email_or_name: sender; digest_day: optional 1=Mon..7=Sun"},
    {"id": "live_mode", "desc": "User wants to switch a sender back to live ingest from weekly-digest mode (e.g. 'live mode Stratechery', 'live ingest for alice@news.io')", "params": "email_or_name: sender email or fragment"},
    {"id": "show_digest_mode", "desc": "User wants to see which senders are in live vs weekly-digest mode (e.g. 'show digest mode', 'list senders in digest mode')", "params": "none"},
    {"id": "show_score", "desc": "User wants the overall Correspondent effectiveness dashboard — sync uptime, auto-route rate, approval throughput, dedup hit rate, sender learning impact, etc. (distinct from per-sender 'score X'). Triggered by 'score' / 'effectiveness' / 'show score' / 'how effective' WITHOUT a sender name.", "params": "none"},
    {"id": "cluster_deep_read", "desc": "User wants a combined briefing on a hot-cluster topic: what their existing newsletters say + what the web adds today + what to read next. Triggered by 'deep read <topic>' or by the Deep-read CTA on cluster cards.", "params": "label: topic / cluster label"},
    {"id": "try_unsubscribe", "desc": "User wants to actually email-unsubscribe from a sender using the newsletter's List-Unsubscribe header (RFC 2369). Riskier than 'unsubscribe' which only blocks locally — this sends an HTTPS POST or mailto to the newsletter operator. Requires explicit user confirmation. (e.g. 'try unsubscribe Stratechery', 'really unsubscribe alice@news.io', 'force unsubscribe X')", "params": "email_or_name: sender"},
    {"id": "confirm_unsubscribe", "desc": "User wants to confirm a pending List-Unsubscribe action. The previous try_unsubscribe reply included a confirmation token; this intent picks that token (e.g. 'confirm unsubscribe abc123', 'yes execute abc123', 'confirm abc123')", "params": "token: confirmation token"},
    {"id": "show_unsubscribe_log", "desc": "User wants to see the audit history of all List-Unsubscribe attempts (e.g. 'show unsubscribe log', 'show unsub history', 'list unsubscribe attempts')", "params": "none"},
    # Phase 2 Tier 2 (added 2026-06-09) — scorecards
    {"id": "score_sender", "desc": "User wants the 'earns its keep' scorecard for ONE specific newsletter sender (e.g. 'score Stratechery', 'what's the grade for alice@news.io', 'is The Diff worth keeping'). REQUIRES a sender name.", "params": "email_or_name: sender email or fragment"},
    {"id": "show_scorecards", "desc": "User wants to see ALL newsletter sender scorecards ranked best to worst (e.g. 'show scorecards', 'show grades', 'rank my newsletters', 'which subscriptions earn their keep')", "params": "none"},
    # Phase 1 Tier 2 (added 2026-06-09)
    {"id": "show_articles", "desc": "User wants to see individual articles extracted from recent newsletters (per-article, not per-newsletter; e.g. 'show articles', 'list articles', 'what articles came in', 'show me what was in the newsletters')", "params": "limit: how many items, default 10"},
    {"id": "show_articles_from_sender", "desc": "User wants the article list filtered to one sender (e.g. 'articles from Stratechery', 'show articles from alice@news.io')", "params": "email_or_name: sender email or fragment"},
    {"id": "show_entities", "desc": "User wants to see the top entities (people, companies, products) extracted from recent newsletters across all notebooks", "params": "limit: how many entities, default 20"},
    {"id": "show_entities_for_sender", "desc": "User wants the top entities from one sender's newsletters (helps understand what a sender focuses on)", "params": "email_or_name: sender email or fragment"},
]


_SYSTEM_PROMPT = """You are an intent classifier for a research notebook app. Given a user message directed at an AI agent, classify the intent(s) and extract parameters.

Available intents:
{intent_list}

Respond with ONLY valid JSON (no markdown, no explanation).

For a SINGLE-intent message, use this format:
{{"intent": "<intent_id>", "params": {{...extracted parameters...}}, "confidence": <0.0-1.0>}}

For a COMPOUND message that asks the agent to perform MULTIPLE distinct actions (e.g., "add this URL AND set my focus to X"), use this format:
{{"actions": [
  {{"intent": "<intent_id_1>", "params": {{...}}, "confidence": <0.0-1.0>}},
  {{"intent": "<intent_id_2>", "params": {{...}}, "confidence": <0.0-1.0>}}
], "confidence": <0.0-1.0>}}

Rules:
- Prefer the single-intent format unless the message clearly requests two or more DISTINCT operations
- Do NOT split a single operation into multiple intents (e.g., "subscribe to this channel daily" is ONE subscribe intent with schedule=daily, not two actions)
- Extract relevant parameters from the message
- If the message is a general question or doesn't match a specific command, use the fallback intent
- confidence should reflect how well the message matches each intent
- URLs should be extracted exactly as they appear"""


async def classify_intent(
    message: str,
    agent_type: str,
    ollama_service=None,
) -> Dict[str, Any]:
    """
    Classify user intent using the local LLM.

    Args:
        message: The user's message
        agent_type: 'curator' or 'collector'
        ollama_service: LLM service instance (defaults to the canonical ollama_service)

    Returns:
        Dict with 'intent', 'params', and 'confidence' keys
    """
    if ollama_service is None:
        from services.ollama_service import ollama_service as _default
        ollama_service = _default

    if agent_type == "curator":
        intents = CURATOR_INTENTS
        fallback = "cross_notebook_search"
    elif agent_type == "research":
        intents = RESEARCH_INTENTS
        fallback = "web_search"
    elif agent_type == "studio":
        intents = STUDIO_INTENTS
        fallback = "generate_document"
    elif agent_type == "correspondent":
        intents = CORRESPONDENT_INTENTS
        fallback = "show_status"
    else:
        intents = COLLECTOR_INTENTS
        fallback = "show_status"

    # Build intent list for the prompt
    intent_lines = []
    for i in intents:
        intent_lines.append(f'- {i["id"]}: {i["desc"]} (params: {i["params"]})')
    intent_list = "\n".join(intent_lines)

    system = _SYSTEM_PROMPT.format(intent_list=intent_list)
    prompt = f'User message: "{message}"'

    try:
        # 2026-06-29: classify on the FAST model + JSON mode. It was defaulting to
        # the heavy main model (gemma4), which timed out at 15s under memory pressure
        # → empty response → fallback intent → misrouted @curator/@collector commands
        # (e.g. "Morning brief" → cross_notebook_search). phi4-mini is fast, always
        # warm, and is the model prescribed for intent classification.
        result = await ollama_service.generate(
            prompt=prompt,
            system=system,
            model=settings.ollama_fast_model,
            temperature=0.0,
            format="json",
            timeout=15.0,
        )
        raw = result.get("response", "").strip()

        # The response may carry a valid JSON object followed by trailing markdown fences +
        # prose — phi4 on the MLX path does this because format="json" isn't grammar-clamped
        # the way Ollama's is (user report 2026-07-23: intent classify "got worse" on MLX,
        # every case falling to the fallback intent). robust_json_parse extracts the first
        # balanced JSON object and is the mandated shared parser (CLAUDE.md centralization
        # rule) — replaces the old leading-fence-only strip + raw json.loads.
        parsed = robust_json_parse(raw, expect="object", fallback=None, label="IntentClassifier")
        if not isinstance(parsed, dict):
            raise json.JSONDecodeError("no JSON object found in LLM output", raw or "", 0)

        valid_ids = {i["id"] for i in intents}

        # Normalize into a list of actions. Supports both:
        #   {"intent": "...", "params": {...}, "confidence": 0.x}                (single)
        #   {"actions": [{intent, params, confidence}, ...], "confidence": 0.x}  (compound)
        actions: List[Dict[str, Any]] = []
        if isinstance(parsed.get("actions"), list) and parsed["actions"]:
            for act in parsed["actions"]:
                if not isinstance(act, dict):
                    continue
                intent_id = act.get("intent")
                if intent_id not in valid_ids:
                    logger.warning(f"LLM returned unknown intent '{intent_id}' in compound action; skipping")
                    continue
                actions.append({
                    "intent": intent_id,
                    "params": act.get("params", {}) or {},
                    "confidence": float(act.get("confidence", 0.5)),
                })
        else:
            intent_id = parsed.get("intent")
            if intent_id not in valid_ids:
                logger.warning(f"LLM returned unknown intent '{intent_id}', falling back to {fallback}")
                intent_id = fallback
                parsed["confidence"] = 0.3
            actions.append({
                "intent": intent_id,
                "params": parsed.get("params", {}) or {},
                "confidence": float(parsed.get("confidence", 0.5)),
            })

        # Fallback if all compound actions were invalid
        if not actions:
            actions.append({"intent": fallback, "params": {}, "confidence": 0.1})

        # Return legacy top-level fields (intent/params) for backward compat plus
        # the new 'actions' list that multi-intent dispatchers can iterate over.
        return {
            "intent": actions[0]["intent"],
            "params": actions[0]["params"],
            "confidence": actions[0]["confidence"],
            "actions": actions,
        }

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Intent classification parse error: {e}, raw='{raw[:200] if 'raw' in dir() else 'N/A'}'")
        return {
            "intent": fallback,
            "params": {},
            "confidence": 0.1,
            "actions": [{"intent": fallback, "params": {}, "confidence": 0.1}],
        }
    except Exception as e:
        logger.warning(f"Intent classification failed: {e}")
        return {
            "intent": fallback,
            "params": {},
            "confidence": 0.1,
            "actions": [{"intent": fallback, "params": {}, "confidence": 0.1}],
        }
