"""
LLM-based Intent Classifier for @curator, @collector, and @research agents.

Replaces fragile regex pattern matching with natural language understanding.
Uses the local LLM for fast, accurate intent classification with parameter extraction.
"""
import json
import logging
from typing import Dict, Any, Optional, List

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
    {"id": "generate_quiz", "desc": "User wants to create a quiz, test, practice questions, or knowledge check", "params": "topic: the topic or focus, num_questions: number of questions if specified, difficulty: easy/medium/hard if specified"},
    {"id": "generate_visual", "desc": "User wants to create a visual, diagram, chart, infographic, mind map, flowchart, or any visual representation", "params": "topic: the topic or focus, visual_type: type of visual if specified"},
    {"id": "generate_video", "desc": "User wants to create a video, explainer video, or visual presentation with narration", "params": "topic: the topic or focus, duration_minutes: length if specified, visual_style: style if specified, voice: voice preference if specified"},
]

COLLECTOR_INTENTS: List[Dict[str, str]] = [
    {"id": "add_url", "desc": "User wants to add a URL (web page or RSS feed) as a source", "params": "url: the URL, is_rss: true if RSS/feed, schedule: frequency if mentioned (hourly/daily/weekly)"},
    {"id": "remove_source", "desc": "User wants to remove, stop, disable, or unsubscribe from a source", "params": "url: the URL to remove"},
    {"id": "add_keyword", "desc": "User wants to track a news keyword or topic for alerts", "params": "keyword: the keyword or topic"},
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

_SYSTEM_PROMPT = """You are an intent classifier for a research notebook app. Given a user message directed at an AI agent, classify the intent and extract parameters.

Available intents:
{intent_list}

Respond with ONLY valid JSON (no markdown, no explanation):
{{"intent": "<intent_id>", "params": {{...extracted parameters...}}, "confidence": <0.0-1.0>}}

Rules:
- Pick the single best matching intent
- Extract relevant parameters from the message
- If the message is a general question or doesn't match a specific command, use the fallback intent
- confidence should reflect how well the message matches the intent
- URLs should be extracted exactly as they appear"""


async def classify_intent(
    message: str,
    agent_type: str,
    ollama_client=None,
) -> Dict[str, Any]:
    """
    Classify user intent using the local LLM.

    Args:
        message: The user's message
        agent_type: 'curator' or 'collector'
        ollama_client: OllamaClient instance

    Returns:
        Dict with 'intent', 'params', and 'confidence' keys
    """
    if ollama_client is None:
        from services.ollama_client import ollama_client as _client
        ollama_client = _client

    if agent_type == "curator":
        intents = CURATOR_INTENTS
        fallback = "cross_notebook_search"
    elif agent_type == "research":
        intents = RESEARCH_INTENTS
        fallback = "web_search"
    elif agent_type == "studio":
        intents = STUDIO_INTENTS
        fallback = "generate_document"
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
        result = await ollama_client.generate(
            prompt=prompt,
            system=system,
            temperature=0.0,
            timeout=15.0,
        )
        raw = result.get("response", "").strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        parsed = json.loads(raw)

        # Validate intent ID
        valid_ids = {i["id"] for i in intents}
        if parsed.get("intent") not in valid_ids:
            logger.warning(f"LLM returned unknown intent '{parsed.get('intent')}', falling back to {fallback}")
            parsed["intent"] = fallback
            parsed["confidence"] = 0.3

        return {
            "intent": parsed.get("intent", fallback),
            "params": parsed.get("params", {}),
            "confidence": parsed.get("confidence", 0.5),
        }

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Intent classification parse error: {e}, raw='{raw[:200] if 'raw' in dir() else 'N/A'}'")
        return {"intent": fallback, "params": {}, "confidence": 0.1}
    except Exception as e:
        logger.warning(f"Intent classification failed: {e}")
        return {"intent": fallback, "params": {}, "confidence": 0.1}
