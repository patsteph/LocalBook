"""
Curator Agent - The overseer of all Collectors

The Curator acts as judge/parent/teacher/cop for the multi-agent system:
- Cross-notebook synthesis and insights
- Collector oversight (approve/reject/modify collected items)
- User-nameable personality
- Morning Brief generation
- Proactive cross-notebook discovery
- Devil's Advocate mode
"""
import asyncio
import json
import logging
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from enum import Enum
from pydantic import BaseModel, Field

from storage.memory_store import memory_store, AgentNamespace
from storage.notebook_store import notebook_store
from models.memory import ArchivalMemoryEntry, MemorySourceType, MemoryImportance
from services.ollama_service import ollama_service
from config import settings
from utils.tasks import safe_create_task

logger = logging.getLogger(__name__)


# Cross-notebook keyword router — used by api/chat.py to auto-route
# queries that obviously span notebooks to the Curator. Previously lived
# in agents/supervisor.py (archived 2026-05-12 Phase 1); moved here
# because the routing is curator-specific.
CROSS_NOTEBOOK_KEYWORDS = [
    # Plural / explicit cross-notebook phrasings
    "across notebooks", "all notebooks", "compare notebooks", "both notebooks",
    "multiple notebooks", "between notebooks", "between my notebooks",
    # Natural-language variants users actually type (2026-05-22)
    # NOTE: do NOT add "my notebooks" or "other notebooks" — those false-fire
    # on innocent queries like "show my notebooks" / "list other notebooks".
    "cross notebook", "cross-notebook", "cross notebooks",
    "look across", "look cross", "search across", "search cross",
    # Aggregations across the user's whole library
    "all my research", "everything i have", "cross-reference",
    # Pattern / theme phrasings
    "patterns across", "themes across", "what do i know about", "synthesis",
    # Devil's-advocate / challenge phrasings (these specifically invoke curator)
    "devil's advocate", "challenge my", "counterarguments", "prove me wrong",
]


def is_cross_notebook_query(query: str) -> bool:
    """Cheap keyword check to detect cross-notebook queries.

    Used by api/chat.py to bypass the LLM intent classifier for obvious cases.
    """
    query_lower = query.lower()
    return any(kw in query_lower for kw in CROSS_NOTEBOOK_KEYWORDS)


# ── Curator Voice (Phase 6a — 2026-05-13) ────────────────────────────────
#
# User-configurable narrative style for written curator output. Distinct
# from `personality` (free-text persona description). Each voice is a
# prompt block injected at the top of the brief synthesizer. The user
# can switch via `@curator set voice [name]`.

VOICE_PROMPTS: Dict[str, str] = {
    "smart_colleague": (
        "VOICE: Write like a smart colleague who has been paying attention to "
        "this person's research for a while. Observational, opinion-bearing, "
        "gently candid. Use phrases like 'I noticed', 'you've been wrestling "
        "with', 'this looks like'. Don't pad with statistics — name the "
        "actual thing. First-person curator voice. Comfortable being a "
        "little blunt when something matters."
    ),
    "executive_brief": (
        "VOICE: Write a crisp executive brief. Status. Open questions. "
        "Suggested action. Lead with what changed. Use short paragraphs and "
        "bulleted observations. Tone: deliberate, professional, useful. "
        "Minimal first-person — focus on the situation. Skip pleasantries; "
        "the reader is busy."
    ),
    "conversational_analyst": (
        "VOICE: Write like a curious analyst chatting through what they're "
        "seeing. Use phrases like 'curious what you'll make of', 'this "
        "actually undercuts', 'I keep coming back to'. First-person curator "
        "voice, conversational but substantive. Signpost your reasoning so "
        "the user can follow your thinking. Mix observations with light "
        "questions — invite engagement."
    ),
}

VOICE_DESCRIPTIONS: Dict[str, str] = {
    "smart_colleague": "Observational, opinion-bearing, gently candid",
    "executive_brief": "Crisp, status-focused, action-oriented",
    "conversational_analyst": "Chatty, curious, signposts thinking, invites engagement",
}

VALID_VOICES = frozenset(VOICE_PROMPTS.keys())
DEFAULT_VOICE = "conversational_analyst"


class JudgmentDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"
    DEFER_TO_USER = "defer_to_user"


class JudgmentResult(BaseModel):
    decision: JudgmentDecision
    reason: str
    confidence: float
    modifications: Optional[List[str]] = None


class CollectedItem(BaseModel):
    id: str
    title: str
    url: Optional[str] = None
    preview: str
    source_name: str
    collected_at: datetime
    relevance_score: float = 0.5
    source_trust: float = 0.5
    freshness_score: float = 0.5
    overall_confidence: float = 0.5
    confidence_reasons: List[str] = []


class RecentStory(BaseModel):
    """A specific piece of content discovered while user was away"""
    title: str
    source_name: str = ""
    url: Optional[str] = None
    summary: str = ""


class NotebookSummary(BaseModel):
    notebook_id: str
    name: str
    subject: str = ""
    items_added: int = 0
    flagged_important: int = 0
    pending_approval: int = 0
    top_finding: Optional[str] = None
    recent_stories: List[RecentStory] = []
    person_changes: List[str] = []
    upcoming_key_dates: List[str] = []
    collection_runs: int = 0
    collection_items_found: int = 0  # items EXAMINED by collector (not stored)
    collection_items_approved: int = 0  # items actually STORED by collector
    collection_items_rejected: int = 0
    collection_items_pending: int = 0
    # Source origin breakdown — collector vs user
    collector_added: int = 0  # sources auto-gathered by background collector
    user_added: int = 0       # sources manually added by the user
    # Wow features
    total_sources: int = 0
    sources_this_week: int = 0
    sources_last_week: int = 0
    sources_summarized: int = 0
    sources_unread: int = 0
    highlights_since: int = 0
    recent_highlight_texts: List[str] = []
    # User interaction tracking — separate from items_added (source additions)
    interactions_since: int = 0
    chat_queries: int = 0
    searches: int = 0
    docs_read: int = 0
    # Studio content generation — what the user created (not just consumed)
    docs_generated: int = 0
    audio_generated: int = 0
    visuals_generated: int = 0
    quizzes_generated: int = 0
    videos_generated: int = 0
    studio_topics: List[str] = []  # topics the user generated content about
    # Unfinished threads from recall memory
    unfinished_threads: List[str] = []
    # Topic drift — new topics emerging vs established ones
    emerging_topics: List[str] = []
    # Temporal lookback — "this day in your research"
    one_week_ago_items: List[str] = []
    # Notes — user's own thinking and ideas
    notes_created: int = 0  # notes created since last seen
    note_titles: List[str] = []  # titles of recent notes
    total_notes: int = 0  # total notes in notebook


class MorningBrief(BaseModel):
    away_duration: str
    notebook_summaries: List[NotebookSummary]
    cross_notebook_insight: Optional[str] = None
    narrative: str = ""  # LLM-generated newsletter-quality summary
    generated_at: datetime
    # Phase 10 — HTML dashboard variant. None when generation fails or the
    # CuratorConfig has html_dashboard disabled; frontend falls back to the
    # markdown narrative in that case.
    narrative_html: Optional[str] = None
    consensus_clusters: List[Dict[str, Any]] = Field(default_factory=list)
    deep_reads_triggered: List[Dict[str, Any]] = Field(default_factory=list)


class WeeklyWrapUp(BaseModel):
    """Broader weekly summary covering the full week's research activity.
    Generated lazily on Monday (covers previous Mon-Sun), replaces that day's Morning Brief."""
    week_start: str  # ISO date of the Monday that starts the covered week
    week_end: str    # ISO date of the Sunday that ends the covered week
    notebook_summaries: List[NotebookSummary]
    cross_notebook_insight: Optional[str] = None
    narrative: str = ""
    # Phase 14 — server-composed HTML variant (matches MorningBrief shape).
    # None when generation fails; chat fallback uses plain narrative.
    narrative_html: Optional[str] = None
    generated_at: datetime
    # Weekly aggregate stats
    total_sources_added: int = 0
    total_collector_added: int = 0
    total_user_added: int = 0
    total_conversations: int = 0
    total_audio_generated: int = 0
    total_documents_generated: int = 0


class ProactiveInsight(BaseModel):
    insight_type: str  # cross_reference, contradiction, temporal_pattern, coverage_gap
    entity: Optional[str] = None
    notebooks: List[str]
    summary: str
    confidence: float


class CounterargumentResult(BaseModel):
    inferred_thesis: str
    counterpoints: List[Dict[str, Any]]
    confidence: float
