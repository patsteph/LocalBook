# _common.py — shared internals extracted from the former api/chat.py (Wave 5 split).
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
