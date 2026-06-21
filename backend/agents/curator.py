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


class CuratorAgent:
    """
    The overseer of all Collectors. Acts as judge/parent/teacher/cop.
    Has cross-notebook access and editorial judgment.
    """
    
    DEFAULT_CONFIG = {
        "name": "Curator",
        "personality": "thorough, slightly formal, always cites sources",
        "oversight": {
            "auto_approve_threshold": 0.85,
            "require_approval_for": ["new_source_types", "large_batches", "low_confidence"]
        },
        "synthesis": {
            "proactive_insights": True,
            "insight_frequency": "daily"
        },
        # Curator Phase 6a (2026-05-13): narrative voice for written
        # output (morning brief, weekly wrap, drafts). One of the
        # named voices in VOICE_PROMPTS below. Separate from
        # `personality` which is the free-text persona descriptor.
        "narrative_voice": "conversational_analyst",
        "voice": {
            "style": "professional",
            "verbosity": "concise"
        }
    }
    
    def __init__(self):
        self.config = self._load_config()
        self.name = self.config.get("name", "Curator")
        self.personality = self.config.get("personality", "helpful and thorough")
        # Curator Phase 6a: narrative voice for written output. Falls back
        # to default when missing or invalid.
        _vc = self.config.get("narrative_voice")
        self.narrative_voice = _vc if _vc in VALID_VOICES else DEFAULT_VOICE
        # Insights moved to curator_brain.db as of 2026-05-12 (Phase 1).
        # Brain handles legacy curator_insights.json migration on first init.
        # Master scheduler lock — ensures only ONE notebook collection runs at a time.
        # Prevents Ollama contention when multiple notebooks are due simultaneously.
        self._collection_lock = asyncio.Lock()
        self._active_collection: Optional[str] = None  # notebook_id currently collecting

        # Weekly-wrap single-flight guards. Prevents two concurrent generations
        # (e.g. chat-triggered + scheduler-triggered) from racing and the second
        # empty/shorter wrap overwriting the first one the user was reading.
        self._weekly_wrap_lock = asyncio.Lock()
        self._weekly_wrap_cache: Optional[WeeklyWrapUp] = None
        self._weekly_wrap_cached_at: Optional[datetime] = None
        # Same treatment for morning briefs — same failure mode exists there.
        self._morning_brief_lock = asyncio.Lock()
        self._morning_brief_cache: Optional[MorningBrief] = None
        self._morning_brief_cached_at: Optional[datetime] = None
    
    def _get_config_path(self) -> Path:
        """Get path to curator config file"""
        return Path(settings.data_dir) / "curator_config.yaml"
    
    def _load_config(self) -> Dict[str, Any]:
        """Load curator configuration from YAML file"""
        config_path = self._get_config_path()
        
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = yaml.safe_load(f)
                    if config:
                        return {**self.DEFAULT_CONFIG, **config}
            except Exception as e:
                logger.error(f"Error loading curator config: {e}")
        
        # Save default config if none exists
        self._save_config(self.DEFAULT_CONFIG)
        return self.DEFAULT_CONFIG
    
    def _save_config(self, config: Dict[str, Any]) -> None:
        """Save curator configuration to YAML file"""
        config_path = self._get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)
    
    # Insight persistence moved to services/curator_brain.py — see
    # add_insights / get_active_insights / find_insights_by_entity. The
    # legacy _get_insights_path / _load_insights / _save_insights helpers
    # were removed 2026-05-12 (Curator Phase 1). One-shot migration of
    # curator_insights.json → brain.db happens inside CuratorBrain.__init__.

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update curator configuration"""
        # Curator Phase 6a: validate narrative_voice before persisting.
        # Silently coerce invalid values to default so a bad set_voice
        # call doesn't break the curator.
        if "narrative_voice" in updates:
            requested = updates["narrative_voice"]
            if requested not in VALID_VOICES:
                logger.warning(
                    f"[curator] update_config: invalid narrative_voice "
                    f"{requested!r}; coercing to default ({DEFAULT_VOICE})"
                )
                updates["narrative_voice"] = DEFAULT_VOICE

        self.config.update(updates)
        self._save_config(self.config)

        # Update instance attributes
        if "name" in updates:
            self.name = updates["name"]
        if "personality" in updates:
            self.personality = updates["personality"]
        if "narrative_voice" in updates:
            self.narrative_voice = updates["narrative_voice"]

        return self.config
    
    def get_config(self) -> Dict[str, Any]:
        """Get current curator configuration"""
        return self.config

    def _get_user_timezone(self) -> str:
        """Return the user's configured timezone, defaulting to America/Chicago.

        Set via @curator set timezone <tz> or directly in curator_config.yaml.
        Stored as config key 'timezone'.
        """
        return self.config.get("timezone", "America/Chicago")
    
    # =========================================================================
    # Judgment System
    # =========================================================================
    
    async def judge_collection(
        self, 
        collector_id: str,
        proposed_items: List[CollectedItem],
        notebook_intent: str,
        deadline: float = 0
    ) -> List[JudgmentResult]:
        """
        Review items a Collector wants to add.
        Returns judgment for each item (parallel with bounded concurrency).
        If deadline is set and approaching, auto-defers remaining items to user review.
        """
        import asyncio
        import time as _time
        sem = asyncio.Semaphore(4)
        
        async def _judge_bounded(item):
            # Mid-flight yield: pause scheduled-collection judging if a
            # foreground op is active (no-op for "Collect Now").
            from services.memory_steward import yield_if_background
            await yield_if_background()
            # If less than 10s left, skip LLM and auto-defer
            if deadline and _time.time() > deadline - 10:
                return JudgmentResult(
                    decision=JudgmentDecision.DEFER_TO_USER,
                    reason="Deferred to keep collection fast. Will review in background.",
                    confidence=item.overall_confidence
                )
            async with sem:
                return await self._judge_single_item(item, notebook_intent, collector_id)

        results = await asyncio.gather(*[_judge_bounded(item) for item in proposed_items])
        return list(results)
    
    async def judge_collected_item(
        self,
        item: CollectedItem,
        intent: str,
        collector_id: str,
    ) -> JudgmentResult:
        """Public contract for collector pre-triage (Phase C.1).

        Synchronous quality-gate decision the Collector calls on each
        proposed item before queueing. The Collector treats Curator as a
        verdict source via this method; that's the only entry point.

        Internally delegates to `_judge_single_item` (the implementation
        also used by the batched `judge_proposed_items` path).
        """
        result = await self._judge_single_item(item, intent, collector_id)
        # Phase C.1 (2026-05-22): emit an observability event so the brain's
        # event log captures every pre-triage decision. The collector still
        # gets the verdict synchronously above; this is purely additive.
        try:
            from services.curator_event_bus import event_bus
            event_bus.emit_now(
                actor="@curator",
                action="collector_item_pre_triaged",
                notebook_id=getattr(item, "notebook_id", None),
                payload={
                    "decision": result.decision.value,
                    "confidence": float(result.confidence or 0.0),
                    "item_title": (item.title or "")[:120],
                    "url": (item.url or "")[:240],
                    "collector_id": collector_id,
                },
                outcome="success",
            )
        except Exception as _bus_err:
            logger.debug(f"[curator] pre-triage event emit failed (non-fatal): {_bus_err}")
        return result

    async def _judge_single_item(
        self,
        item: CollectedItem,
        intent: str,
        collector_id: str
    ) -> JudgmentResult:
        """Judge a single collected item.

        Implementation detail of `judge_collected_item`. External callers
        should use the public method — this is kept as an internal name so
        the batched `_judge_bounded` helper and any back-compat tooling
        don't break.
        """
        auto_threshold = self.config.get("oversight", {}).get("auto_approve_threshold", 0.85)
        
        # High confidence items get auto-approved
        if item.overall_confidence >= auto_threshold:
            return JudgmentResult(
                decision=JudgmentDecision.APPROVE,
                reason=f"High confidence match ({item.overall_confidence:.0%})",
                confidence=item.overall_confidence
            )
        
        # Low confidence items get deferred to user
        if item.overall_confidence < 0.5:
            return JudgmentResult(
                decision=JudgmentDecision.DEFER_TO_USER,
                reason=f"Low confidence ({item.overall_confidence:.0%}). Needs human review.",
                confidence=item.overall_confidence
            )
        
        # Temporal Intelligence: reject high-overlap items with no new information
        if hasattr(item, 'knowledge_overlap') and item.knowledge_overlap > 0.8:
            delta = getattr(item, 'delta_summary', None) or ""
            no_new_info = not delta or "no new" in delta.lower() or "no significant" in delta.lower() or "already" in delta.lower()
            if no_new_info:
                return JudgmentResult(
                    decision=JudgmentDecision.REJECT,
                    reason=f"High overlap ({item.knowledge_overlap:.0%}) with existing knowledge. No significant new information.",
                    confidence=item.knowledge_overlap
                )
        
        # Medium confidence - use LLM to evaluate
        try:
            prompt = f"""You are {self.name}, the Curator of a research system. Your personality: {self.personality}

A Collector wants to add this item to a notebook with intent: "{intent}"

Item to evaluate:
- Title: {item.title}
- Source: {item.source_name}
- Preview: {item.preview[:500]}
- Relevance Score: {item.relevance_score:.0%}

Evaluate if this item matches the notebook's intent. Consider:
1. Does it directly relate to the stated intent?
2. Is it from a trustworthy source?
3. Is this information fresh/relevant?

Respond with JSON only:
{{
    "decision": "approve" | "reject" | "modify" | "defer_to_user",
    "reason": "brief explanation",
    "confidence": 0.0-1.0,
    "modifications": null or ["suggestion1", "suggestion2"]
}}"""

            response = await ollama_service.generate(
                prompt=prompt,
                system="You are an editorial judgment system. Respond only with valid JSON.",
                model=settings.ollama_fast_model,  # Fast model — sufficient for approve/reject JSON
                temperature=0.3
            )
            
            # Parse JSON response
            result_text = response.get("response", "")
            # Extract JSON from response
            json_start = result_text.find("{")
            json_end = result_text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                result_json = json.loads(result_text[json_start:json_end])
                return JudgmentResult(
                    decision=JudgmentDecision(result_json.get("decision", "defer_to_user")),
                    reason=result_json.get("reason", "LLM evaluation"),
                    confidence=float(result_json.get("confidence", 0.5)),
                    modifications=result_json.get("modifications")
                )
        except Exception as e:
            logger.error(f"LLM judgment failed: {e}")
        
        # Fallback: defer to user
        return JudgmentResult(
            decision=JudgmentDecision.DEFER_TO_USER,
            reason="Unable to automatically evaluate. Human review recommended.",
            confidence=item.overall_confidence
        )
    
    # =========================================================================
    # User-Added Content Scoring (Learning from User Behavior)
    # =========================================================================
    
    async def score_user_item(
        self,
        notebook_id: str,
        title: str,
        content: str,
        url: Optional[str] = None,
        source_type: str = "web",
        user_weight_bonus: float = 1.5
    ) -> Dict[str, Any]:
        """
        Score and learn from user-provided content.
        
        When a user manually adds/captures content, this is a STRONG signal
        of what they find important. We:
        1. Score the content for relevance to notebook intent
        2. Extract topics and entities
        3. Record as a positive learning signal with bonus weight
        4. Return scoring info for storage
        
        Args:
            notebook_id: Which notebook the content is being added to
            title: Content title
            content: Content text
            url: Optional source URL
            source_type: Type of source (web, pdf, manual, etc.)
            user_weight_bonus: Multiplier for user-provided content (default 1.5x)
            
        Returns:
            Dict with scoring results and extracted metadata
        """
        from agents.collector import get_collector
        
        result = {
            "relevance_score": 0.5,
            "topics": [],
            "entities": [],
            "importance": "medium",
            "user_provided": True,
            "user_weight": user_weight_bonus,
            "effective_score": 0.5
        }
        
        try:
            # Get notebook intent from Collector config
            collector = get_collector(notebook_id)
            config = collector.get_config()
            intent = config.intent or ""
            focus_areas = config.focus_areas or []
            
            # Score relevance using LLM
            if intent or focus_areas:
                prompt = f"""Analyze this user-provided content for a research notebook.

Notebook intent: {intent}
Focus areas: {', '.join(focus_areas) if focus_areas else 'Not specified'}

Content title: {title}
Content preview: {content[:1000]}

Respond with JSON only:
{{
    "relevance_score": 0.0-1.0,
    "topics": ["topic1", "topic2"],
    "entities": ["entity1", "entity2"],
    "importance": "low" | "medium" | "high" | "critical",
    "reasoning": "brief explanation"
}}"""

                response = await ollama_service.generate(
                    prompt=prompt,
                    model=settings.ollama_fast_model,
                    temperature=0.2
                )
                
                text = response.get("response", "")
                json_start = text.find("{")
                json_end = text.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    parsed = json.loads(text[json_start:json_end])
                    result["relevance_score"] = float(parsed.get("relevance_score", 0.5))
                    result["topics"] = parsed.get("topics", [])
                    result["entities"] = parsed.get("entities", [])
                    result["importance"] = parsed.get("importance", "medium")
            
            # Apply user weight bonus - user explicitly added this, so it matters
            result["effective_score"] = min(1.0, result["relevance_score"] * user_weight_bonus)
            
            # Record as strong positive signal for learning
            memory_store.record_user_signal(
                notebook_id=notebook_id,
                signal_type="user_capture",
                signal_value=1.0,  # Strong positive
                metadata={
                    "title": title[:200],
                    "url": url,
                    "source_type": source_type,
                    "topics": result["topics"],
                    "entities": result["entities"],
                    "relevance_score": result["relevance_score"],
                    "importance": result["importance"]
                }
            )
            
            # Also record topic preferences for pattern learning
            for topic in result["topics"][:5]:
                memory_store.record_user_signal(
                    notebook_id=notebook_id,
                    signal_type="topic_interest",
                    signal_value=1.0,
                    metadata={"topic": topic, "source": "user_capture"}
                )
            
            logger.info(f"Scored user item for {notebook_id}: {result['relevance_score']:.2f} -> {result['effective_score']:.2f}")
            
        except Exception as e:
            logger.error(f"Error scoring user item: {e}")
        
        return result
    
    async def get_learned_preferences(self, notebook_id: str) -> Dict[str, Any]:
        """
        Retrieve learned preferences from user signals for a notebook.
        
        Returns aggregated patterns from:
        - User captures (what they manually add)
        - Approvals/rejections
        - Topic interests
        """
        preferences = {
            "preferred_topics": [],
            "preferred_sources": [],
            "rejected_patterns": [],
            "capture_count": 0,
            "approval_rate": 0.0
        }
        
        try:
            # Get all signals for this notebook (signal_type=None gets all types)
            signals = memory_store.get_user_signals(
                notebook_id=notebook_id,
                signal_type=None,  # Get all signal types
                since_days=90,  # Look back 90 days
                limit=200
            )
            
            # Filter to relevant signal types (includes highlights as strongest signal)
            relevant_types = {"user_capture", "topic_interest", "item_approved", "item_rejected", "source_approved", "source_rejected", "content_highlighted"}
            signals = [s for s in signals if s.get("signal_type") in relevant_types]
            
            topic_counts: Dict[str, int] = {}
            source_counts: Dict[str, int] = {}
            rejected_sources: set = set()
            approvals = 0
            rejections = 0
            highlight_count = 0
            
            for signal in signals:
                meta = signal.get("metadata", {})
                
                if signal["signal_type"] == "content_highlighted":
                    # HIGHEST weight - user explicitly marked this as important
                    highlight_count += 1
                    for topic in meta.get("topics", []):
                        topic_counts[topic] = topic_counts.get(topic, 0) + 3  # Triple weight for highlights
                    for entity in meta.get("entities", []):
                        topic_counts[entity] = topic_counts.get(entity, 0) + 2
                
                elif signal["signal_type"] == "user_capture":
                    preferences["capture_count"] += 1
                    for topic in meta.get("topics", []):
                        topic_counts[topic] = topic_counts.get(topic, 0) + 2  # Double weight for captures
                
                elif signal["signal_type"] == "topic_interest":
                    topic = meta.get("topic")
                    if topic:
                        topic_counts[topic] = topic_counts.get(topic, 0) + 1
                
                elif signal["signal_type"] == "item_approved":
                    approvals += 1
                    source = meta.get("source_name")
                    if source:
                        source_counts[source] = source_counts.get(source, 0) + 1
                
                elif signal["signal_type"] == "item_rejected":
                    rejections += 1
                
                elif signal["signal_type"] == "source_rejected":
                    rejected_sources.add(meta.get("source_url", ""))
            
            preferences["highlight_count"] = highlight_count
            
            # Sort topics by frequency
            sorted_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)
            preferences["preferred_topics"] = [t[0] for t in sorted_topics[:10]]
            
            # Sort sources by approval count
            sorted_sources = sorted(source_counts.items(), key=lambda x: x[1], reverse=True)
            preferences["preferred_sources"] = [s[0] for s in sorted_sources[:10]]
            
            preferences["rejected_patterns"] = list(rejected_sources)[:10]
            
            if approvals + rejections > 0:
                preferences["approval_rate"] = approvals / (approvals + rejections)
            
        except Exception as e:
            logger.error(f"Error getting learned preferences: {e}")
        
        return preferences
    
    # =========================================================================
    # Cross-Notebook Synthesis
    # =========================================================================
    
    async def score_text_against_notebooks(
        self,
        text: str,
        exclude_notebook_id: Optional[str] = None,
        notebook_ids: Optional[List[str]] = None,
        per_notebook_limit: int = 5,
        max_results: int = 3,
    ) -> List[Dict[str, Any]]:
        """Cross-notebook similarity for a candidate text blob (depth+1 expansion).

        Adapter on top of the same memory_store search the existing
        synthesize_across_notebooks() helper uses. Skips the LLM synthesis
        step — the link expander only needs the relevance signal per
        notebook (so it can render a "📌 Also relevant: NotebookX" hint
        in the approval queue), not a paragraph of synthesized prose.

        Args:
            text: Candidate article text (or summary). The first ~1500 chars
                  are used as the search query — long enough to surface
                  thematic overlap, short enough to keep search cheap.
            exclude_notebook_id: Notebook the article is being added to.
                  Excluded from the cross-notebook hint because "this is
                  also relevant to its own notebook" is noise.
            notebook_ids: Restrict search to these notebooks. None = all.
            per_notebook_limit: Memory matches fetched per notebook. The
                  highest-scoring match wins per notebook.
            max_results: Cap the returned list. Default 3 — UI shows at
                  most 3 chips per queue item to stay readable.

        Returns:
            [{notebook_id, notebook_name, score, snippet}] sorted by score
            descending, capped at max_results. Empty list if nothing
            crosses the relevance threshold.
        """
        if not text or not text.strip():
            return []

        # Resolve notebook IDs + names once so we can stamp human-readable
        # labels on the results (the queue UI shows the name, not the ID).
        all_notebooks = await notebook_store.list()
        nb_name_by_id = {n["id"]: n.get("name", "") or n.get("title", "") or n["id"][:8] for n in all_notebooks}
        if not notebook_ids:
            notebook_ids = [n["id"] for n in all_notebooks]
        notebook_ids = [nid for nid in notebook_ids if nid != exclude_notebook_id]
        if not notebook_ids:
            return []

        # Use a leading slice as the search query — full text would slow
        # the embedding step without adding signal beyond the first
        # paragraph or two.
        query = text[:1500]

        # Aggregate the BEST score per notebook. A notebook with one strong
        # hit beats a notebook with many weak hits — that's what the user
        # wants when seeing "📌 Also relevant: …".
        best_by_notebook: Dict[str, Dict[str, Any]] = {}
        for nb_id in notebook_ids:
            try:
                results = memory_store.search_archival_memory(
                    query=query,
                    namespace=AgentNamespace.CURATOR,
                    notebook_id=nb_id,
                    cross_notebook=True,
                    limit=per_notebook_limit,
                )
            except Exception as e:
                logger.debug(f"[curator] cross-notebook search failed for {nb_id}: {e}")
                continue
            for r in results:
                score = float(getattr(r, "combined_score", 0.0) or 0.0)
                if score <= 0:
                    continue
                cur = best_by_notebook.get(nb_id)
                if cur is None or score > cur["score"]:
                    best_by_notebook[nb_id] = {
                        "notebook_id": nb_id,
                        "notebook_name": nb_name_by_id.get(nb_id, nb_id[:8]),
                        "score": round(score, 3),
                        "snippet": (r.entry.content or "")[:240],
                    }

        # Threshold: only surface meaningful matches. The exact value is
        # tuned to memory_store's combined_score scale (0-1 with semantic
        # similarity). 0.45 is "noticeable thematic overlap" — below that,
        # showing a chip is just noise.
        ranked = sorted(
            (m for m in best_by_notebook.values() if m["score"] >= 0.45),
            key=lambda m: m["score"],
            reverse=True,
        )
        return ranked[:max_results]

    async def synthesize_across_notebooks(
        self,
        query: str,
        notebook_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Answer questions that span multiple notebooks.
        E.g., "What themes appear in both my Pepsi and Coca-Cola research?"
        """
        # Get all notebooks if not specified
        if not notebook_ids:
            notebooks = await notebook_store.list()
            notebook_ids = [n["id"] for n in notebooks]
        
        # Search across all specified notebooks using Curator's cross-notebook access
        all_results = []
        for nb_id in notebook_ids:
            results = memory_store.search_archival_memory(
                query=query,
                namespace=AgentNamespace.CURATOR,
                notebook_id=nb_id,
                cross_notebook=True,
                limit=10
            )
            for r in results:
                all_results.append({
                    "notebook_id": nb_id,
                    "content": r.entry.content,
                    "score": r.combined_score
                })
        
        # Sort by score and take top results
        all_results.sort(key=lambda x: x["score"], reverse=True)
        top_results = all_results[:20]
        
        if not top_results:
            return {
                "synthesis": "No relevant content found across the specified notebooks.",
                "sources": [],
                "notebooks_searched": notebook_ids
            }
        
        # Use LLM to synthesize
        context = "\n".join([
            f"[Notebook {r['notebook_id'][:8]}]: {r['content'][:500]}"
            for r in top_results
        ])
        
        try:
            prompt = f"""You are {self.name}, synthesizing information across multiple research notebooks.

Query: {query}

Content from multiple notebooks:
{context}

Provide a synthesis that:
1. Identifies common themes across notebooks
2. Notes any contradictions or differences
3. Highlights connections the user might not have noticed

Be concise and cite which notebook each insight comes from."""

            response = await ollama_service.generate(
                prompt=prompt,
                system=f"You are {self.name}, a research curator. Personality: {self.personality}",
                model=settings.ollama_model,
                temperature=0.5
            )
            
            synthesis = response.get("response", "Unable to synthesize.")
            
            # Store synthesis in Curator namespace
            entry = ArchivalMemoryEntry(
                content=f"Cross-notebook synthesis for: {query}\n\n{synthesis}",
                content_type="cross_notebook_synthesis",
                source_type=MemorySourceType.SYSTEM,
                topics=["synthesis", "cross_notebook"],
                importance=MemoryImportance.MEDIUM,
            )
            memory_store.add_archival_memory(entry, namespace=AgentNamespace.CURATOR)
            
            return {
                "synthesis": synthesis,
                "sources": [{"notebook_id": r["notebook_id"], "score": r["score"]} for r in top_results[:5]],
                "notebooks_searched": notebook_ids
            }
        except Exception as e:
            logger.error(f"Cross-notebook synthesis failed: {e}")
            return {
                "synthesis": f"Error during synthesis: {str(e)}",
                "sources": [],
                "notebooks_searched": notebook_ids
            }
    
    # =========================================================================
    # Morning Brief (Enhancement #2)
    # =========================================================================
    
    async def generate_morning_brief(self, last_seen: datetime) -> MorningBrief:
        """Single-flight wrapper around the morning-brief generator.

        Two concurrent callers (scheduler + user-triggered chat, or UI polling
        + background refresh) would otherwise both spend ~30–90 s building
        narratives and the second completion silently overwrite the first.
        We serialize on a lock and, inside the lock, short-circuit if a
        fresh result is already in memory (<90 s old) so concurrent callers
        all get the same object.
        """
        # Fast path: return cached result without taking the lock at all.
        now_ts = datetime.utcnow()
        if (
            self._morning_brief_cache is not None
            and self._morning_brief_cached_at is not None
            and (now_ts - self._morning_brief_cached_at).total_seconds() < 90
        ):
            return self._morning_brief_cache

        async with self._morning_brief_lock:
            # Re-check under the lock — another coroutine may have finished
            # generating while we were waiting.
            now_ts = datetime.utcnow()
            if (
                self._morning_brief_cache is not None
                and self._morning_brief_cached_at is not None
                and (now_ts - self._morning_brief_cached_at).total_seconds() < 90
            ):
                logger.info(
                    "[curator] Reusing morning brief generated %.1fs ago (single-flight)",
                    (now_ts - self._morning_brief_cached_at).total_seconds(),
                )
                return self._morning_brief_cache

            result = await self._generate_morning_brief_impl(last_seen)
            self._morning_brief_cache = result
            self._morning_brief_cached_at = datetime.utcnow()
            return result

    async def _generate_morning_brief_impl(self, last_seen: datetime) -> MorningBrief:
        """
        Generate a newsletter-quality morning brief summarizing activity since
        the user last opened the app.
        
        Gathers rich data per notebook, then uses LLM to synthesize into a
        narrative the user actually wants to read.
        """
        # Phase 1: Use deterministic temporal context instead of utcnow() guesswork
        from services.temporal import TemporalContext
        temporal = TemporalContext(user_tz=self._get_user_timezone())
        now = temporal.now  # timezone-aware local time
        duration_str = temporal.duration_from(last_seen)
        temporal_block = temporal.for_prompt(last_seen)
        
        notebooks = await notebook_store.list()
        summaries = []
        
        # Pre-load all sources once (not per-notebook) for scalability
        from storage.source_store import source_store
        all_sources_by_nb = await source_store.list_all()
        
        # Gather activity stats for ALL notebooks in PARALLEL (not serial)
        import asyncio
        activity_tasks = [
            self._get_activity_since(nb["id"], last_seen, all_sources_by_nb.get(nb["id"], []))
            for nb in notebooks
        ]
        all_stats = await asyncio.gather(*activity_tasks, return_exceptions=True)
        
        for notebook, stats in zip(notebooks, all_stats):
            if isinstance(stats, Exception):
                logger.error(f"Activity stats failed for {notebook['id']}: {stats}")
                continue
            
            # Include notebook if it has ANY recent activity signal
            studio_created = (
                stats.get("docs_generated", 0) + stats.get("audio_generated", 0) +
                stats.get("visuals_generated", 0) + stats.get("quizzes_generated", 0) +
                stats.get("videos_generated", 0)
            )
            has_activity = (
                stats["items_added"] > 0 or stats["pending_approval"] > 0 or
                stats.get("person_changes") or stats.get("upcoming_key_dates") or
                stats.get("recent_stories") or
                stats.get("collection_runs", 0) > 0 or
                stats.get("sources_this_week", 0) > 0 or
                stats.get("highlights_since", 0) > 0 or
                stats.get("interactions_since", 0) > 0 or
                stats.get("unfinished_threads") or
                studio_created > 0
            )
            if has_activity:
                # Get the collector subject for this notebook
                subject = ""
                try:
                    from agents.collector import get_collector
                    collector = get_collector(notebook["id"])
                    cfg = collector.get_config()
                    subject = cfg.subject if hasattr(cfg, "subject") and cfg.subject else ""
                except Exception as _e:
                    logger.debug(f"[curator] {type(_e).__name__}: {_e}")
                
                # Curator Phase 5: filter + boost stories using
                # suppressions and engagement signal.
                stories_raw = stats.get("recent_stories", [])
                stories_raw = self._filter_and_rank_stories(
                    notebook["id"], stories_raw,
                )
                # Strip 'origin' from story dicts before passing to RecentStory model
                stories = []
                for sr in stories_raw:
                    sr_copy = {k: v for k, v in sr.items() if k != "origin"}
                    stories.append(RecentStory(**sr_copy))

                # Phase 5: record story_offered engagement for each story
                # that survived filtering — the brain uses these to track
                # which topics are repeatedly shown but not clicked.
                self._record_stories_offered(notebook["id"], stories_raw)
                
                summaries.append(NotebookSummary(
                    notebook_id=notebook["id"],
                    name=notebook.get("title", notebook.get("name", "Untitled")),
                    subject=subject,
                    items_added=stats["items_added"],
                    flagged_important=stats.get("flagged", 0),
                    pending_approval=stats.get("pending_approval", 0),
                    top_finding=stats.get("top_item"),
                    recent_stories=stories,
                    person_changes=stats.get("person_changes", []),
                    upcoming_key_dates=stats.get("upcoming_key_dates", []),
                    collection_runs=stats.get("collection_runs", 0),
                    collection_items_found=stats.get("collection_items_found", 0),
                    collection_items_approved=stats.get("collection_items_approved", 0),
                    collection_items_rejected=stats.get("collection_items_rejected", 0),
                    collection_items_pending=stats.get("collection_items_pending", 0),
                    collector_added=stats.get("collector_added", 0),
                    user_added=stats.get("user_added", 0),
                    total_sources=stats.get("total_sources", 0),
                    sources_this_week=stats.get("sources_this_week", 0),
                    sources_last_week=stats.get("sources_last_week", 0),
                    sources_summarized=stats.get("sources_summarized", 0),
                    sources_unread=stats.get("sources_unread", 0),
                    highlights_since=stats.get("highlights_since", 0),
                    recent_highlight_texts=stats.get("recent_highlight_texts", []),
                    interactions_since=stats.get("interactions_since", 0),
                    chat_queries=stats.get("chat_queries", 0),
                    searches=stats.get("searches", 0),
                    docs_read=stats.get("docs_read", 0),
                    docs_generated=stats.get("docs_generated", 0),
                    audio_generated=stats.get("audio_generated", 0),
                    visuals_generated=stats.get("visuals_generated", 0),
                    quizzes_generated=stats.get("quizzes_generated", 0),
                    videos_generated=stats.get("videos_generated", 0),
                    studio_topics=stats.get("studio_topics", []),
                    unfinished_threads=stats.get("unfinished_threads", []),
                    emerging_topics=stats.get("emerging_topics", []),
                    one_week_ago_items=stats.get("one_week_ago_items", []),
                    notes_created=stats.get("notes_created", 0),
                    note_titles=stats.get("note_titles", []),
                    total_notes=stats.get("total_notes", 0),
                ))
        
        # Get any active cross-notebook insight from the brain
        cross_insight = None
        try:
            from services.curator_brain import curator_brain
            _active = curator_brain.get_active_insights(limit=1)
            if _active:
                cross_insight = _active[0]["summary"]
        except Exception as _e:
            logger.debug(f"[curator] get_active_insights (morning brief): {_e}")
        
        # Curator Phase 5 (2026-05-13): build a per-notebook "What's
        # changed in your understanding" diff block. Aggregates across
        # all notebooks. Passed to the synthesizer as a separate input
        # so the LLM can naturally include a "What's new" section.
        understanding_diff_block = ""
        try:
            from services.curator_brain import curator_brain as _cb
            since_iso = last_seen.isoformat() if last_seen else None
            if since_iso:
                diff_lines: List[str] = []
                for nb in notebooks[:10]:
                    diff = _cb.compute_understanding_diff(nb["id"], since_iso)
                    has_signal = (
                        diff.get("new_connections")
                        or diff.get("mental_model_changes")
                        or diff.get("new_dissent_sources")
                    )
                    if not has_signal:
                        continue
                    name = nb.get("title", nb.get("name", "Untitled"))
                    nb_lines = [f"  {name}:"]
                    if diff.get("mental_model_changes"):
                        mm = diff["mental_model_changes"][0]
                        if mm.get("thesis"):
                            nb_lines.append(f"    - thesis refined: {mm['thesis'][:120]}")
                        if mm.get("stage"):
                            nb_lines.append(f"    - stage: {mm['stage']}")
                    if diff.get("new_dissent_sources"):
                        for d in diff["new_dissent_sources"][:2]:
                            nb_lines.append(
                                f"    - new contradicting source: "
                                f"{(d.get('rationale') or '')[:120]}"
                            )
                    if diff.get("new_connections"):
                        for c in diff["new_connections"][:2]:
                            nb_lines.append(
                                f"    - new cross-notebook link: "
                                f"{(c.get('description') or '')[:120]}"
                            )
                    if len(nb_lines) > 1:
                        diff_lines.extend(nb_lines)
                if diff_lines:
                    understanding_diff_block = (
                        "What changed in the curator's understanding since last brief:\n"
                        + "\n".join(diff_lines)
                    )
        except Exception as _e:
            logger.debug(f"[curator] understanding diff fetch failed (non-fatal): {_e}")

        # Generate LLM narrative — turn raw data into a newsletter people look forward to
        narrative = await self._synthesize_brief_narrative(
            summaries, duration_str, cross_insight, temporal_block,
            understanding_diff=understanding_diff_block,
        )

        # Phase 10 — consensus detection + deep-read trigger + HTML dashboard.
        # Always runs after the markdown narrative so the existing path is
        # unaffected if any new piece fails.
        consensus_clusters: List[Dict[str, Any]] = []
        deep_reads_triggered: List[Dict[str, Any]] = []
        narrative_html: Optional[str] = None
        try:
            from services.consensus_detector import detect_consensus
            clusters = await detect_consensus(since_days=3, min_cluster_size=3)
            consensus_clusters = [c.model_dump() for c in clusters]
            deep_reads_triggered = self._fire_deep_reads_for_clusters(clusters)
            total_recent_ingests = sum(c.size for c in clusters) if clusters else 0
            narrative_html = self._compose_brief_html(
                duration_str=duration_str,
                summaries=summaries,
                narrative=narrative,
                cross_insight=cross_insight,
                clusters=clusters,
                deep_reads=deep_reads_triggered,
                total_recent_ingests=total_recent_ingests,
            )
        except Exception as _e:
            logger.debug(f"[curator] Phase 10 dashboard skipped (non-fatal): {_e}")

        return MorningBrief(
            away_duration=duration_str,
            notebook_summaries=summaries,
            cross_notebook_insight=cross_insight,
            narrative=narrative,
            generated_at=now,
            narrative_html=narrative_html,
            consensus_clusters=consensus_clusters,
            deep_reads_triggered=deep_reads_triggered,
        )
    
    async def generate_weekly_wrap_up(self) -> WeeklyWrapUp:
        """Single-flight wrapper around the weekly-wrap generator.

        The user-reported failure mode: they were reading a wrap with a long
        narrative, then a second (shorter / nearly-empty) wrap appeared and
        replaced it. Root cause: two callers (chat "weekly wrap" intent +
        UI refresh, or scheduler + manual) both invoked this method, each
        wrote its result to `memory/weekly_wrap_YYYY-MM-DD.json`, and the
        second write clobbered the first.

        Fix: serialize concurrent callers on `_weekly_wrap_lock` and reuse
        a cached result for 5 minutes. Narrative generation takes 30–90 s;
        5 minutes comfortably covers any double-click / polling / scheduler
        overlap without ever going stale enough to mislead the user.
        """
        now_ts = datetime.utcnow()
        if (
            self._weekly_wrap_cache is not None
            and self._weekly_wrap_cached_at is not None
            and (now_ts - self._weekly_wrap_cached_at).total_seconds() < 300
        ):
            return self._weekly_wrap_cache

        async with self._weekly_wrap_lock:
            now_ts = datetime.utcnow()
            if (
                self._weekly_wrap_cache is not None
                and self._weekly_wrap_cached_at is not None
                and (now_ts - self._weekly_wrap_cached_at).total_seconds() < 300
            ):
                logger.info(
                    "[curator] Reusing weekly wrap generated %.1fs ago (single-flight)",
                    (now_ts - self._weekly_wrap_cached_at).total_seconds(),
                )
                return self._weekly_wrap_cache

            result = await self._generate_weekly_wrap_up_impl()
            self._weekly_wrap_cache = result
            self._weekly_wrap_cached_at = datetime.utcnow()
            return result

    async def _generate_weekly_wrap_up_impl(self) -> WeeklyWrapUp:
        """Generate a Weekly Wrap Up covering the past 7 days of research activity.
        
        Designed to replace the Monday Morning Brief — gives a broader view of
        what was discovered, debated, and created over the entire week.
        Generated lazily on Monday morning (or on demand).
        """
        from datetime import timedelta
        import asyncio
        
        now = datetime.utcnow()
        # Cover the past 7 days (previous Mon through Sun)
        week_end = now
        week_start = now - timedelta(days=7)
        
        notebooks = await notebook_store.list()
        
        # Pre-load all sources once
        from storage.source_store import source_store
        all_sources_by_nb = await source_store.list_all()
        
        # Gather activity for the full week in parallel
        activity_tasks = [
            self._get_activity_since(nb["id"], week_start, all_sources_by_nb.get(nb["id"], []))
            for nb in notebooks
        ]
        all_stats = await asyncio.gather(*activity_tasks, return_exceptions=True)
        
        summaries = []
        total_sources = 0
        total_collector = 0
        total_user = 0
        total_convos = 0
        
        for notebook, stats in zip(notebooks, all_stats):
            if isinstance(stats, Exception):
                logger.error(f"Weekly stats failed for {notebook['id']}: {stats}")
                continue
            
            has_activity = (
                stats["items_added"] > 0 or stats.get("collection_runs", 0) > 0 or
                stats.get("interactions_since", 0) > 0 or stats.get("highlights_since", 0) > 0
            )
            if not has_activity:
                continue
            
            subject = ""
            try:
                from agents.collector import get_collector
                collector = get_collector(notebook["id"])
                cfg = collector.get_config()
                subject = cfg.subject if hasattr(cfg, "subject") and cfg.subject else ""
            except Exception as _e:
                logger.debug(f"[curator] {type(_e).__name__}: {_e}")
            
            # Curator Phase 5: same filter+boost as the primary path
            stories_raw = stats.get("recent_stories", [])
            stories_raw = self._filter_and_rank_stories(notebook["id"], stories_raw)
            stories = []
            for sr in stories_raw:
                sr_copy = {k: v for k, v in sr.items() if k != "origin"}
                stories.append(RecentStory(**sr_copy))
            self._record_stories_offered(notebook["id"], stories_raw)

            summaries.append(NotebookSummary(
                notebook_id=notebook["id"],
                name=notebook.get("title", notebook.get("name", "Untitled")),
                subject=subject,
                items_added=stats["items_added"],
                flagged_important=stats.get("flagged", 0),
                pending_approval=stats.get("pending_approval", 0),
                top_finding=stats.get("top_item"),
                recent_stories=stories,
                person_changes=stats.get("person_changes", []),
                upcoming_key_dates=stats.get("upcoming_key_dates", []),
                collection_runs=stats.get("collection_runs", 0),
                collection_items_found=stats.get("collection_items_found", 0),
                collection_items_approved=stats.get("collection_items_approved", 0),
                collection_items_rejected=stats.get("collection_items_rejected", 0),
                collection_items_pending=stats.get("collection_items_pending", 0),
                collector_added=stats.get("collector_added", 0),
                user_added=stats.get("user_added", 0),
                total_sources=stats.get("total_sources", 0),
                sources_this_week=stats.get("sources_this_week", 0),
                sources_last_week=stats.get("sources_last_week", 0),
                sources_summarized=stats.get("sources_summarized", 0),
                sources_unread=stats.get("sources_unread", 0),
                highlights_since=stats.get("highlights_since", 0),
                recent_highlight_texts=stats.get("recent_highlight_texts", []),
                interactions_since=stats.get("interactions_since", 0),
                chat_queries=stats.get("chat_queries", 0),
                searches=stats.get("searches", 0),
                docs_read=stats.get("docs_read", 0),
                docs_generated=stats.get("docs_generated", 0),
                audio_generated=stats.get("audio_generated", 0),
                visuals_generated=stats.get("visuals_generated", 0),
                quizzes_generated=stats.get("quizzes_generated", 0),
                videos_generated=stats.get("videos_generated", 0),
                studio_topics=stats.get("studio_topics", []),
                unfinished_threads=stats.get("unfinished_threads", []),
                emerging_topics=stats.get("emerging_topics", []),
                one_week_ago_items=stats.get("one_week_ago_items", []),
            ))
            
            total_sources += stats["items_added"]
            total_collector += stats.get("collector_added", 0)
            total_user += stats.get("user_added", 0)
            total_convos += stats.get("chat_queries", 0)
        
        # Count audio and document generations this week (from event logger, more accurate)
        total_audio = sum(s.audio_generated for s in summaries)
        total_docs = sum(s.docs_generated for s in summaries)
        
        # Cross-notebook insight (from the brain — used to be self._pending_insights)
        cross_insight = None
        try:
            from services.curator_brain import curator_brain
            _active = curator_brain.get_active_insights(limit=1)
            if _active:
                cross_insight = _active[0]["summary"]
        except Exception as _e:
            logger.debug(f"[curator] get_active_insights (weekly): {_e}")
        
        narrative = await self._synthesize_weekly_narrative(
            summaries, cross_insight, total_sources, total_collector,
            total_user, total_convos, total_audio, total_docs
        )
        
        # Phase 14 — compose HTML variant so the wrap can render as a
        # dashboard card via the ```html fence handler (parallels Phase 10
        # morning brief). Non-blocking; falls back to narrative-only on
        # any failure.
        narrative_html: Optional[str] = None
        try:
            narrative_html = self._compose_weekly_wrap_html(
                week_start=week_start.strftime("%Y-%m-%d"),
                week_end=week_end.strftime("%Y-%m-%d"),
                summaries=summaries,
                narrative=narrative,
                cross_insight=cross_insight,
                total_sources=total_sources,
                total_collector=total_collector,
                total_user=total_user,
                total_convos=total_convos,
                total_audio=total_audio,
                total_docs=total_docs,
            )
        except Exception as e:
            logger.debug(f"[curator] weekly wrap HTML composition skipped: {e}")

        return WeeklyWrapUp(
            week_start=week_start.strftime("%Y-%m-%d"),
            week_end=week_end.strftime("%Y-%m-%d"),
            notebook_summaries=summaries,
            cross_notebook_insight=cross_insight,
            narrative=narrative,
            narrative_html=narrative_html,
            generated_at=now,
            total_sources_added=total_sources,
            total_collector_added=total_collector,
            total_user_added=total_user,
            total_conversations=total_convos,
            total_audio_generated=total_audio,
            total_documents_generated=total_docs,
        )
    
    async def _synthesize_weekly_narrative(
        self,
        summaries: List['NotebookSummary'],
        cross_insight: Optional[str],
        total_sources: int,
        total_collector: int,
        total_user: int,
        total_convos: int,
        total_audio: int,
        total_docs: int,
    ) -> str:
        """Use LLM to generate a Weekly Wrap Up narrative."""
        if not summaries:
            return ""
        
        # Pull memory context for weekly narrative (same pattern as morning brief)
        import asyncio
        memory_context_by_nb = {}
        try:
            from storage.memory_store import memory_store
            from models.memory import AgentNamespace
            
            async def _fetch_memory(nb_id, nb_name):
                results = await asyncio.to_thread(
                    memory_store.search_archival_memory,
                    query=f"weekly progress decisions key findings {nb_name}",
                    namespace=AgentNamespace.CURATOR,
                    notebook_id=nb_id,
                    cross_notebook=True,
                    limit=3
                )
                return nb_id, results
            
            mem_tasks = [_fetch_memory(nb.notebook_id, nb.name) for nb in summaries]
            mem_results = await asyncio.gather(*mem_tasks, return_exceptions=True)
            for item in mem_results:
                if isinstance(item, Exception):
                    continue
                nb_id, results = item
                if results:
                    snippets = [r.entry.content[:250] for r in results if r.combined_score > 0.2]
                    if snippets:
                        memory_context_by_nb[nb_id] = snippets
        except Exception as e:
            logger.debug(f"Memory context for weekly wrap failed (non-fatal): {e}")
        
        # Build structured data (reuse the same format as morning brief)
        notebook_sections = []
        for nb in summaries:
            section = f"Notebook: {nb.name}"
            if nb.subject:
                section += f" (tracking: {nb.subject})"
            details = []
            
            if nb.recent_stories:
                for story in nb.recent_stories[:5]:
                    detail = f"  - \"{story.title}\""
                    if story.source_name:
                        detail += f" ({story.source_name})"
                    if story.summary:
                        detail += f" — {story.summary[:150]}"
                    details.append(detail)
            
            if nb.collector_added > 0 or nb.user_added > 0:
                origin_parts = []
                if nb.collector_added > 0:
                    origin_parts.append(f"{nb.collector_added} auto-collected")
                if nb.user_added > 0:
                    origin_parts.append(f"{nb.user_added} you added")
                details.append(f"  - Sources this week: {'; '.join(origin_parts)}")
            
            if nb.collection_runs > 0:
                details.append(f"  - Collector ran {nb.collection_runs}x: examined {nb.collection_items_found} items, stored {nb.collection_items_approved}, rejected {nb.collection_items_rejected}")
                if nb.collection_items_found > 0 and nb.collection_items_approved == 0:
                    details.append(f"    NOTE: collector found items but NONE passed quality filters — zero new sources added by collector")
            
            if nb.total_sources > 0:
                details.append(f"  - Library: {nb.total_sources} total sources")
            
            if nb.interactions_since > 0:
                activity_parts = []
                if nb.chat_queries > 0:
                    activity_parts.append(f"{nb.chat_queries} conversations")
                if nb.searches > 0:
                    activity_parts.append(f"{nb.searches} searches")
                if activity_parts:
                    details.append(f"  - Your activity: {', '.join(activity_parts)}")
            
            # Studio content creation
            studio_total = nb.docs_generated + nb.audio_generated + nb.visuals_generated + nb.quizzes_generated + nb.videos_generated
            if studio_total > 0:
                studio_parts = []
                if nb.docs_generated > 0:
                    studio_parts.append(f"{nb.docs_generated} document{'s' if nb.docs_generated != 1 else ''}")
                if nb.audio_generated > 0:
                    studio_parts.append(f"{nb.audio_generated} podcast{'s' if nb.audio_generated != 1 else ''}")
                if nb.visuals_generated > 0:
                    studio_parts.append(f"{nb.visuals_generated} visual{'s' if nb.visuals_generated != 1 else ''}")
                if nb.quizzes_generated > 0:
                    studio_parts.append(f"{nb.quizzes_generated} quiz{'zes' if nb.quizzes_generated != 1 else ''}")
                if nb.videos_generated > 0:
                    studio_parts.append(f"{nb.videos_generated} video{'s' if nb.videos_generated != 1 else ''}")
                details.append(f"  - Studio output: created {', '.join(studio_parts)}")
                if nb.studio_topics:
                    details.append(f"    Topics: {', '.join(nb.studio_topics)}")
            
            if nb.highlights_since > 0:
                details.append(f"  - Highlighted {nb.highlights_since} passages")
            
            if nb.unfinished_threads:
                details.append(f"  - Open threads: {'; '.join(nb.unfinished_threads[:2])}")
            
            if nb.emerging_topics:
                details.append(f"  - Emerging topics: {', '.join(nb.emerging_topics)}")
            
            # Memory context — what the user was discussing/deciding this week
            nb_memories = memory_context_by_nb.get(nb.notebook_id, [])
            if nb_memories:
                details.append(f"  - Research context from memory:")
                for mem in nb_memories[:2]:
                    details.append(f"    📝 {mem}")
            
            if details:
                section += "\n" + "\n".join(details)
            notebook_sections.append(section)
        
        raw_data = "\n\n".join(notebook_sections)
        if cross_insight:
            raw_data += f"\n\nCross-notebook insight: {cross_insight}"
        
        # Aggregate stats block
        total_visuals = sum(s.visuals_generated for s in summaries)
        total_quizzes = sum(s.quizzes_generated for s in summaries)
        total_videos = sum(s.videos_generated for s in summaries)
        
        raw_data += f"\n\nWEEKLY TOTALS:"
        raw_data += f"\n  - Total sources added: {total_sources} ({total_collector} by collector, {total_user} by you)"
        raw_data += f"\n  - Conversations: {total_convos}"
        studio_total_week = total_audio + total_docs + total_visuals + total_quizzes + total_videos
        if studio_total_week > 0:
            studio_week_parts = []
            if total_docs > 0:
                studio_week_parts.append(f"{total_docs} document{'s' if total_docs != 1 else ''}")
            if total_audio > 0:
                studio_week_parts.append(f"{total_audio} podcast{'s' if total_audio != 1 else ''}")
            if total_visuals > 0:
                studio_week_parts.append(f"{total_visuals} visual{'s' if total_visuals != 1 else ''}")
            if total_quizzes > 0:
                studio_week_parts.append(f"{total_quizzes} quiz{'zes' if total_quizzes != 1 else ''}")
            if total_videos > 0:
                studio_week_parts.append(f"{total_videos} video{'s' if total_videos != 1 else ''}")
            raw_data += f"\n  - Studio output: {', '.join(studio_week_parts)}"
        
        today_str = datetime.utcnow().strftime("%B %d, %Y")
        prompt = f"""You are a personal research assistant writing a WEEKLY WRAP UP for {today_str}. This covers the ENTIRE past week of the user's research activity — a broader, more reflective view than the daily morning brief.

RAW DATA:
{raw_data}

WEEKLY WRAP UP STRUCTURE:
1. **Opening** — A warm "Here's your week in review" opening. Set a reflective tone.
2. **The Big Picture** — What were the major themes across all notebooks this week? Any patterns emerging?
3. **Per-notebook highlights** — For each active notebook, summarize the week's key additions and discoveries. Use actual titles.
4. **Collector Report** — If the background collector gathered sources, summarize what it found. Distinguish clearly from what the user added themselves.
5. **Your Activity** — How actively did the user engage? Conversations, searches, highlights. Frame it positively.
6. **Threads to Pick Up** — Open questions and unfinished conversations worth revisiting this week.
7. **Looking Ahead** — Based on this week's momentum, what should the user focus on next week? Any upcoming dates?
8. **Weekly Stat Line** — End with a clean summary: "This week: X sources added, Y conversations, Z audio pieces generated."

RULES:
- Use exact numbers from the data. Never invent or round.
- This is a WEEKLY summary — use "this week", "over the past week", "this week's research" framing.
- Distinguish collector-gathered sources from user-added ones.
- Length: 300-500 words. More substantial than the daily brief.

NEWSLETTER FORMATTING (CRITICAL):
- Use markdown extensively for a modern newsletter layout.
- Use `###` headers for each notebook or major section to break up text visually.
- Use **bold** liberally for source titles, key metrics, and important entities.
- Use bullet points (`-`) for lists of items (like newly discovered sources or threads).
- Keep paragraphs very short (1-2 sentences). Absolutely NO dense walls of text. Be highly scannable.
- Insert blank lines between sections to give the text room to breathe.
- Tone: warm, reflective, slightly celebratory of progress. Like a trusted advisor reviewing the week together.

Write the weekly wrap up now:"""
        
        try:
            from services.rag_engine import rag_engine
            from config import settings

            # Routed through rag_engine._call_ollama for two reasons:
            #   1. num_predict=2000 — the original call left this unset, so
            #      Ollama defaulted to ~128 tokens and clipped a 300-500
            #      word newsletter mid-sentence. The clipped tail broke
            #      markdown pairs (**bold**, [link]()) and rendered as
            #      raw chars in the UI — the "markdown leakage" symptom.
            #   2. rag_engine respects the active model's rag_profile,
            #      including use_chat_endpoint=true for Gemma4. Calling
            #      ollama_client.generate directly always hits /api/generate
            #      which uses the wrong template for Gemma and produces
            #      shorter, more fragmented output on memory pressure.
            # voice_modifier=False because the system prompt below already
            # carries the curator's personality and tone instructions.
            narrative = await rag_engine._call_ollama(
                system_prompt="You are a concise, insightful research assistant. Write engaging weekly summaries that help people reflect on their research progress.",
                prompt=prompt,
                model=settings.ollama_model,
                # 2026-06-08: dropped 0.7 → 0.55 for gemma4 (better
                # instruction-following than olmo; CLAUDE.md doc-gen range).
                temperature=0.55,
                num_predict=2000,
                voice_modifier=False,
            )
            narrative = (narrative or "").strip()
            if narrative and not narrative.startswith(("Request timed out", "Error:")):
                return narrative
        except Exception as e:
            logger.error(f"Weekly narrative generation failed: {e}")
        
        # Fallback
        lines = [f"# Weekly Wrap Up — {today_str}\n"]
        for nb in summaries:
            line = f"**{nb.name}**: {nb.items_added} sources added"
            if nb.collector_added > 0:
                line += f" ({nb.collector_added} by collector)"
            lines.append(line)
        lines.append(f"\n**Week totals:** {total_sources} sources, {total_convos} conversations")
        if total_audio > 0:
            lines.append(f", {total_audio} audio pieces")
        return "\n".join(lines)
    
    def _filter_and_rank_stories(
        self,
        notebook_id: str,
        stories_raw: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Apply Phase 5 brief story filter (suppressions) + boost
        (engagement-based topic click score).

        Suppression is hard — story is dropped if its title contains any
        active suppression keyword.

        Boost is soft — surviving stories get reordered with a small
        positive adjustment for topics the user has clicked recently.
        Falls back to the original order when no engagement signal exists.
        """
        if not stories_raw:
            return stories_raw
        try:
            from services.curator_brain import curator_brain as _cb
            filtered: List[Dict[str, Any]] = []
            for sr in stories_raw:
                title = sr.get("title") or ""
                if _cb.is_topic_suppressed(notebook_id, title):
                    continue
                filtered.append(sr)

            # Apply click-score boost. Topic-key extraction: first 2-3
            # significant words from title. Cheap stopword filter.
            stop = {"the", "a", "an", "of", "in", "on", "for", "to",
                    "and", "or", "is", "are", "with", "from", "by"}

            def _topic_key_for(title: str) -> str:
                words = [w.strip(".,!?:;\"'()[]").lower() for w in (title or "").split()]
                meaningful = [w for w in words if w and w not in stop and len(w) > 2]
                return " ".join(meaningful[:3])

            # Keep stable original order when scores are tied — sort with
            # a (score_desc, idx_asc) key so click-boost re-orders only
            # when there's actual engagement signal.
            # 2026-05-23 (#1 engagement-weighted brief boost): repeatedly
            # ignored topics now get a HARD negative score that pushes them
            # to the bottom. Without this, click-boost only RAISED clicked
            # topics; it didn't suppress the topics the user keeps ignoring.
            # Net effect: brief stops repeating things the user has shown
            # they don't care about.
            scored: List[tuple] = []
            for i, sr in enumerate(filtered):
                key = _topic_key_for(sr.get("title") or "")
                if not key:
                    scored.append((0.0, i, sr))
                    continue
                if _cb.is_topic_repeatedly_ignored(notebook_id, key):
                    # Push to bottom — score very negative, preserves order
                    # within the demoted bucket via the original index.
                    scored.append((1000.0, i, sr))
                    continue
                score = _cb.get_topic_click_score(notebook_id, key)
                scored.append((-score, i, sr))
            scored.sort()
            return [t[2] for t in scored]
        except Exception as e:
            logger.debug(f"[curator] _filter_and_rank_stories failed (non-fatal): {e}")
            return stories_raw

    def _record_stories_offered(
        self,
        notebook_id: str,
        stories: List[Dict[str, Any]],
    ) -> None:
        """Phase 5: record an `offered` engagement event per story that
        ends up in a brief. The brain uses these to compute future
        topic_click_score (offered + clicked → boost weight).

        Best-effort, never raises.
        """
        if not stories:
            return
        try:
            from services.curator_brain import curator_brain as _cb
            stop = {"the", "a", "an", "of", "in", "on", "for", "to",
                    "and", "or", "is", "are", "with", "from", "by"}
            for sr in stories[:20]:  # cap to avoid runaway recording
                title = sr.get("title") or ""
                words = [w.strip(".,!?:;\"'()[]").lower() for w in title.split()]
                meaningful = [w for w in words if w and w not in stop and len(w) > 2]
                topic_key = " ".join(meaningful[:3])
                if not topic_key:
                    continue
                _cb.record_engagement(
                    kind="brief",
                    signal="offered",
                    subject_type="topic",
                    subject_id=topic_key,
                    notebook_id=notebook_id,
                    payload={"title": title[:120]},
                )
        except Exception as e:
            logger.debug(f"[curator] _record_stories_offered failed (non-fatal): {e}")

    async def _get_activity_since(self, notebook_id: str, since: datetime, preloaded_sources: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Get activity stats for a notebook since a given time.

        Pulls from: collector pending queue, archival memory, collection history,
        event logger, and person change detection.
        
        Args:
            preloaded_sources: If provided, skip source_store.list() and use these.
                              Used by generate_morning_brief to avoid N file reads.
        """
        from agents.collector import get_collector
        
        stats = {
            "items_added": 0,
            "flagged": 0,
            "pending_approval": 0,
            "top_item": None,
            "collection_runs": 0,
            "collection_items_found": 0,
            "person_changes": [],
        }
        
        try:
            collector = get_collector(notebook_id)
            
            # Get pending approvals count
            pending = collector.get_pending_approvals()
            stats["pending_approval"] = len(pending)
            
            # Get top finding from pending (highest confidence)
            if pending:
                top = max(pending, key=lambda x: x.get("confidence", 0))
                stats["top_item"] = top.get("title", "")[:100]
                stats["flagged"] = len([p for p in pending if p.get("confidence", 0) >= 0.8])
            
        except Exception as e:
            logger.error(f"Error getting activity for {notebook_id}: {e}")
        
        # Collection history — how many runs happened while user was away
        try:
            from services.collection_history import get_collection_history
            history = get_collection_history(notebook_id, limit=10)
            runs_since = [h for h in history if h.get("timestamp", "") > since.isoformat()]
            stats["collection_runs"] = len(runs_since)
            stats["collection_items_found"] = sum(h.get("items_found", 0) for h in runs_since)
            stats["collection_items_approved"] = sum(h.get("items_approved", 0) for h in runs_since)
            stats["collection_items_rejected"] = sum(h.get("items_rejected", 0) for h in runs_since)
            stats["collection_items_pending"] = sum(h.get("items_pending", 0) for h in runs_since)
            if runs_since and not stats["top_item"]:
                approved = stats["collection_items_approved"]
                stats["top_item"] = f"Collector ran {len(runs_since)} time{'s' if len(runs_since) != 1 else ''}, approved {approved} of {stats['collection_items_found']} items examined"
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Phase 4: Collection quality metrics + recent syntheses for enriched brief
        try:
            from services.collection_history import get_collection_quality_metrics, get_recent_syntheses
            quality = get_collection_quality_metrics(notebook_id)
            stats["collection_health_score"] = quality.get("health_score", 0)
            stats["collection_health_status"] = quality.get("status", "no_data")
            stats["collection_approval_trend"] = quality.get("approval_trend", "stable")
            stats["collection_recommended_actions"] = quality.get("recommended_actions", [])
            
            syntheses = get_recent_syntheses(notebook_id, limit=2)
            if syntheses:
                # Extract approved titles from recent syntheses for the brief
                recent_titles = []
                for s in syntheses:
                    recent_titles.extend(s.get("approved_titles", []))
                stats["recent_approved_titles"] = recent_titles[:5]
                # Extract gap reasons if any runs had zero approvals
                gaps = [s.get("gap_reasons", {}) for s in syntheses if s.get("gap_reasons")]
                if gaps:
                    stats["collection_gap_reasons"] = gaps[0]
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # User interactions — track activity types separately (never conflate with items_added)
        try:
            from services.event_logger import event_logger, EventType
            events = event_logger.get_events_since(since, notebook_id=notebook_id)
            stats["interactions_since"] = len(events)
            stats["chat_queries"] = len([e for e in events if e.event_type == EventType.CHAT_QA.value])
            stats["searches"] = len([e for e in events if e.event_type == EventType.SEARCH_PERFORMED.value])
            stats["docs_read"] = len([e for e in events if e.event_type == EventType.DOCUMENT_READ.value])
            stats["docs_captured"] = len([e for e in events if e.event_type == EventType.DOCUMENT_CAPTURED.value])
            
            # Studio content generation — what the user actively created
            content_events = [e for e in events if e.event_type == EventType.CONTENT_GENERATED.value]
            quiz_events = [e for e in events if e.event_type == EventType.QUIZ_COMPLETED.value]
            studio_topics = set()
            for ce in content_events:
                ctype = ce.data.get("content_type", "")
                topic = ce.data.get("topic", "")
                if ctype == "audio":
                    stats["audio_generated"] = stats.get("audio_generated", 0) + 1
                elif ctype == "visual":
                    stats["visuals_generated"] = stats.get("visuals_generated", 0) + 1
                elif ctype == "video":
                    stats["videos_generated"] = stats.get("videos_generated", 0) + 1
                else:
                    stats["docs_generated"] = stats.get("docs_generated", 0) + 1
                if topic:
                    studio_topics.add(topic[:80])
            stats["quizzes_generated"] = len(quiz_events)
            for qe in quiz_events:
                topic = qe.data.get("topic", "")
                if topic:
                    studio_topics.add(topic[:80])
            stats["studio_topics"] = list(studio_topics)[:5]
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Person changes — surface profile changes for people notebooks
        try:
            from api.people import _load_config
            config = _load_config(notebook_id)
            if config.coaching_enabled and config.members:
                for member in config.members:
                    for change in getattr(member, "recent_changes", []) or []:
                        detected = change.get("detected_at", "")
                        if detected > since.isoformat():
                            stats["person_changes"].append(
                                f"{member.name}: {change.get('description', '')}"
                            )
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Key dates — surface upcoming events within 7 days
        try:
            from agents.collector import get_collector
            collector = get_collector(notebook_id)
            config = collector.get_config()
            subject = config.subject if hasattr(config, "subject") and config.subject else None
            if subject:
                from services.key_dates import get_key_dates
                from datetime import timedelta
                key_dates = await get_key_dates(company_name=subject)
                now_str = datetime.utcnow().strftime("%Y-%m-%d")
                soon_str = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d")
                upcoming = [kd for kd in key_dates if now_str <= kd.get("date", "") <= soon_str]
                if upcoming:
                    stats["upcoming_key_dates"] = [
                        f"{kd['date']}: {kd['event']}" for kd in upcoming[:3]
                    ]
                    if not stats["top_item"]:
                        stats["top_item"] = f"Upcoming: {upcoming[0]['event']} on {upcoming[0]['date']}"
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Use preloaded sources if available, otherwise load (still only once per call)
        all_sources = preloaded_sources if preloaded_sources is not None else []
        if preloaded_sources is None:
            try:
                from storage.source_store import source_store
                all_sources = await source_store.list(notebook_id)
            except Exception as _e:
                logger.warning(f"[curator] {type(_e).__name__}: {_e}")
        
        # Recent sources — pull actual titles of recently added content
        try:
            since_str = since.isoformat()
            recent = [
                s for s in all_sources
                if s.get("created_at", "") > since_str
            ]
            # Sort newest first, take top 5
            recent.sort(key=lambda s: s.get("created_at", ""), reverse=True)
            
            # Determine origin for each source (collector vs user)
            # metadata_json is merged into the source dict by source_store,
            # so collected_by is a top-level key
            def _is_collector_source(src):
                return src.get("collected_by") == "collector"
            
            stats["recent_stories"] = [
                {
                    "title": s.get("title") or s.get("filename", "Untitled"),
                    "source_name": s.get("source_type", s.get("format", "")),
                    "url": s.get("url"),
                    "summary": (s.get("summary") or s.get("description") or "")[:200],
                    "origin": "collector" if _is_collector_source(s) else "user",
                }
                for s in recent[:5]
            ]
            # items_added = actual number of sources created since user was last seen
            # This is the ONLY place items_added is set — no conflation with event logger
            stats["items_added"] = len(recent)
            
            # Split by origin
            stats["collector_added"] = len([s for s in recent if _is_collector_source(s)])
            stats["user_added"] = len(recent) - stats["collector_added"]
            
            # Notes — track separately for "what you've been thinking about"
            recent_notes = [s for s in recent if s.get("type") == "note"]
            stats["notes_created"] = len(recent_notes)
            stats["note_titles"] = [s.get("filename", "Untitled Note") for s in recent_notes[:5]]
            
            # Also count total notes in notebook for context
            all_notes = [s for s in all_sources if s.get("type") == "note"]
            stats["total_notes"] = len(all_notes)
            
            # Research velocity — compare this week vs last week (deltas, not totals)
            from datetime import timedelta
            now = datetime.utcnow()
            week_ago = (now - timedelta(days=7)).isoformat()
            two_weeks_ago = (now - timedelta(days=14)).isoformat()
            added_this_week = len([s for s in all_sources if s.get("created_at", "") > week_ago])
            added_last_week = len([s for s in all_sources if week_ago >= s.get("created_at", "") > two_weeks_ago])
            stats["total_sources"] = len(all_sources)
            stats["sources_this_week"] = added_this_week
            stats["sources_last_week"] = added_last_week
            
            # Reading progress — sources the user has actively engaged with
            # (tagged by user = reviewed; no tags = unreviewed)
            tagged = len([s for s in all_sources if s.get("tags") and len(s.get("tags", [])) > 0])
            stats["sources_summarized"] = tagged
            stats["sources_unread"] = len(all_sources) - tagged
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Recent highlights — what the user explicitly marked as important
        try:
            highlight_count = 0
            recent_highlights = []
            for src in all_sources:
                highlights = src.get("highlights", [])
                for h in highlights:
                    if h.get("created_at", "") > since.isoformat():
                        highlight_count += 1
                        if len(recent_highlights) < 3:
                            recent_highlights.append(h.get("text", "")[:100])
            if highlight_count > 0:
                stats["highlights_since"] = highlight_count
                stats["recent_highlight_texts"] = recent_highlights
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Unfinished threads — conversations where user asked a question
        # but didn't follow up (using existing recall memory SQLite)
        try:
            from storage.memory_store import memory_store
            recent_convos = memory_store.get_recent_conversations(
                limit=50, notebook_id=notebook_id, days=3
            )
            if recent_convos:
                # Group by conversation_id
                convos: Dict[str, list] = {}
                for entry in recent_convos:
                    cid = entry.conversation_id
                    if cid not in convos:
                        convos[cid] = []
                    convos[cid].append(entry)
                
                unfinished = []
                for cid, entries in convos.items():
                    # Sort by timestamp (entries come DESC, reverse for chronological)
                    entries.sort(key=lambda e: e.timestamp)
                    # Check if the user's last message was a question or conversation was short
                    user_msgs = [e for e in entries if e.role == "user"]
                    if user_msgs:
                        last_user_msg = user_msgs[-1].content.strip()
                        is_question = last_user_msg.endswith("?")
                        is_short = len(entries) <= 3  # Single exchange = likely abandoned
                        if is_question or is_short:
                            # Truncate to a readable thread hint
                            hint = last_user_msg[:120]
                            if len(last_user_msg) > 120:
                                hint += "..."
                            unfinished.append(hint)
                
                stats["unfinished_threads"] = unfinished[:3]
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Topic drift — compare recent source topics vs older ones
        try:
            from datetime import timedelta
            now = datetime.utcnow()
            week_ago = (now - timedelta(days=7)).isoformat()
            month_ago = (now - timedelta(days=30)).isoformat()
            
            recent_titles = [
                s.get("title", "").lower()
                for s in all_sources
                if s.get("created_at", "") > week_ago and s.get("title")
            ]
            older_titles = [
                s.get("title", "").lower()
                for s in all_sources
                if month_ago < s.get("created_at", "") <= week_ago and s.get("title")
            ]
            
            if recent_titles and older_titles:
                # Extract simple word-level topics (2+ word phrases would need NLP,
                # but single significant words are a good heuristic)
                import re
                stop_words = {"the","a","an","and","or","but","in","on","at","to","for",
                              "of","with","by","from","is","it","this","that","was","are",
                              "be","has","had","have","will","can","may","not","no","new",
                              "how","what","why","when","who","which","about","after","into"}
                
                def extract_words(titles):
                    words = {}
                    for title in titles:
                        for word in re.findall(r'[a-z]{3,}', title):
                            if word not in stop_words:
                                words[word] = words.get(word, 0) + 1
                    return words
                
                recent_words = extract_words(recent_titles)
                older_words = extract_words(older_titles)
                
                # Find words appearing in recent but not (or rarely) in older
                emerging = []
                for word, count in sorted(recent_words.items(), key=lambda x: -x[1]):
                    if count >= 2 and older_words.get(word, 0) == 0:
                        emerging.append(word)
                    if len(emerging) >= 3:
                        break
                
                stats["emerging_topics"] = emerging
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Temporal lookback — "this day in your research" (7 days ago)
        try:
            from datetime import timedelta
            now = datetime.utcnow()
            # Sources from exactly 6-8 days ago (window around 1 week)
            lookback_start = (now - timedelta(days=8)).isoformat()
            lookback_end = (now - timedelta(days=6)).isoformat()
            
            week_ago_sources = [
                s for s in all_sources
                if lookback_start < s.get("created_at", "") <= lookback_end and s.get("title")
            ]
            if week_ago_sources:
                stats["one_week_ago_items"] = [
                    s.get("title", "")[:100] for s in week_ago_sources[:3]
                ]
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        return stats
    
    async def _synthesize_brief_narrative(
        self,
        summaries: List['NotebookSummary'],
        duration_str: str,
        cross_insight: Optional[str],
        temporal_block: str = "",
        understanding_diff: str = "",
    ) -> str:
        """
        Use LLM to turn raw notebook activity data into a newsletter-quality
        narrative the user looks forward to reading each morning.

        understanding_diff (Curator Phase 5): optional pre-formatted block
        describing changes in the curator's understanding since the last
        brief. When non-empty, prepended to the LLM prompt so the
        synthesizer naturally weaves a "What's new in your thinking"
        section into the brief.
        """
        if not summaries:
            return ""

        # --- Phase 1C: Quiet Morning Gate ---
        # If nothing substantive happened, skip the LLM and return one sentence.
        # A notebook qualifies as "substantive" when it has new content, user
        # activity, pending items, notes, highlights, or emerging topics.
        # (Collector ran but found nothing does NOT qualify.)
        has_meaningful_activity = any(
            nb.items_added > 0
            or nb.pending_approval > 0
            or nb.collection_items_approved > 0
            or nb.highlights_since > 0
            or nb.notes_created > 0
            or nb.interactions_since > 0
            or nb.emerging_topics
            or nb.recent_stories
            for nb in summaries
        )
        if not has_meaningful_activity:
            # 2026-05-23 (Fix #1): no greeting prefix here. The CuratorPanel
            # frontend already prepends "Good {greeting}! You've been away
            # for {duration}.\n\n{narrative}" — including another "Good X."
            # produced "Good morning! You've been away... Good morning. Quiet
            # since..." (double-greeting). Narrative starts mid-thought.
            return (
                "Quiet since you were last here — nothing I'd flag as worth "
                "your time. Your notebooks are where you left them."
            )

        # --- Phase 2: Inject Curator Brain context (pre-computed understanding) ---
        # If digests exist, the LLM narrates from knowledge, not just activity stats.
        # If brain is empty (first run), brain_context is '' and we fall through to
        # today's stat-only behavior automatically.
        brain_context = ""
        try:
            from services.curator_brain import curator_brain
            brain_context = curator_brain.get_brief_context()
        except Exception as _brain_err:
            logger.debug(f"[curator] Brain context unavailable (non-fatal): {_brain_err}")

        # Pull recent memory context per notebook for richer narrative (ReMe integration)
        # Structured checkpoints from archival memory give the Curator awareness of
        # what the user has been discussing, deciding, and working on
        import asyncio
        memory_context_by_nb = {}
        try:
            from storage.memory_store import memory_store
            from models.memory import AgentNamespace
            
            async def _fetch_memory(nb_id, nb_name):
                results = await asyncio.to_thread(
                    memory_store.search_archival_memory,
                    query=f"recent work progress decisions {nb_name}",
                    namespace=AgentNamespace.CURATOR,
                    notebook_id=nb_id,
                    cross_notebook=True,
                    limit=3
                )
                return nb_id, results
            
            mem_tasks = [_fetch_memory(nb.notebook_id, nb.name) for nb in summaries]
            mem_results = await asyncio.gather(*mem_tasks, return_exceptions=True)
            for item in mem_results:
                if isinstance(item, Exception):
                    continue
                nb_id, results = item
                if results:
                    snippets = [r.entry.content[:250] for r in results if r.combined_score > 0.2]
                    if snippets:
                        memory_context_by_nb[nb_id] = snippets
        except Exception as e:
            logger.debug(f"Memory context for brief failed (non-fatal): {e}")
        
        # Build structured data for the LLM
        notebook_sections = []
        for nb in summaries:
            section = f"Notebook: {nb.name}"
            if nb.subject:
                section += f" (tracking: {nb.subject})"
            details = []
            
            # New content — specific titles
            if nb.recent_stories:
                for story in nb.recent_stories[:3]:
                    detail = f"  - New: \"{story.title}\""
                    if story.source_name:
                        detail += f" ({story.source_name})"
                    if story.summary:
                        detail += f" — {story.summary[:150]}"
                    details.append(detail)
            
            # Source origin breakdown — distinguish collector from user
            if nb.collector_added > 0 or nb.user_added > 0:
                origin_parts = []
                if nb.collector_added > 0:
                    origin_parts.append(f"{nb.collector_added} auto-gathered by your background collector (REVIEW RECOMMENDED — you didn't add these manually)")
                if nb.user_added > 0:
                    origin_parts.append(f"{nb.user_added} added by you")
                details.append(f"  - Source breakdown: {'; '.join(origin_parts)}")
            
            # Collection activity — IMPORTANT: "found" means examined, NOT stored
            if nb.collection_runs > 0:
                details.append(f"  - Background collector ran {nb.collection_runs}x overnight: examined {nb.collection_items_found} potential items, stored {nb.collection_items_approved} into notebook, rejected {nb.collection_items_rejected} (low quality/duplicate)")
                if nb.collection_items_found > 0 and nb.collection_items_approved == 0:
                    details.append(f"    NOTE: The collector found items but NONE passed quality filters — zero new sources were actually added by the collector")
            if nb.pending_approval > 0:
                details.append(f"  - {nb.pending_approval} collector items awaiting your review in the approval queue")
            
            # People updates
            if nb.person_changes:
                for pc in nb.person_changes[:3]:
                    details.append(f"  - People: {pc}")
            
            # Upcoming events
            if nb.upcoming_key_dates:
                for kd in nb.upcoming_key_dates[:2]:
                    details.append(f"  - Coming up: {kd}")
            
            # Research velocity — pre-compute ALL percentages so the LLM
            # never has to do arithmetic (LLMs are bad at math)
            if nb.total_sources > 0:
                if nb.sources_this_week > 0:
                    prior_total = nb.total_sources - nb.sources_this_week
                    lib_growth_pct = int((nb.sources_this_week / prior_total) * 100) if prior_total > 0 else 0
                    velocity_note = (f"  - Research library: {prior_total} → {nb.total_sources} sources "
                                     f"(+{nb.sources_this_week} new this week, {lib_growth_pct}% library growth)")
                    if nb.sources_last_week > 0:
                        if nb.sources_this_week > nb.sources_last_week:
                            pace_pct = int(((nb.sources_this_week - nb.sources_last_week) / nb.sources_last_week) * 100)
                            velocity_note += f". Pace: {nb.sources_this_week} added vs {nb.sources_last_week} last week (+{pace_pct}% faster)"
                        elif nb.sources_this_week < nb.sources_last_week:
                            velocity_note += f". Pace: {nb.sources_this_week} added vs {nb.sources_last_week} last week (slower)"
                        else:
                            velocity_note += f". Pace: same as last week ({nb.sources_last_week})"
                    details.append(velocity_note)
                else:
                    details.append(f"  - Research library: {nb.total_sources} sources (no new additions this week)")
            
            # Review progress — how many sources user has tagged/reviewed
            if nb.sources_unread > 0 and nb.sources_summarized > 0:
                details.append(f"  - Review progress: {nb.sources_summarized} of {nb.total_sources} sources tagged/reviewed, {nb.sources_unread} still unreviewed")
            
            # User activity — how the user has been engaging with this notebook
            if nb.interactions_since > 0:
                activity_parts = []
                if nb.chat_queries > 0:
                    activity_parts.append(f"{nb.chat_queries} chat conversation{'s' if nb.chat_queries != 1 else ''}")
                if nb.searches > 0:
                    activity_parts.append(f"{nb.searches} search{'es' if nb.searches != 1 else ''}")
                if nb.docs_read > 0:
                    activity_parts.append(f"{nb.docs_read} document{'s' if nb.docs_read != 1 else ''} read")
                if activity_parts:
                    details.append(f"  - Your activity: {', '.join(activity_parts)}")
            
            # Studio content creation — what the user actively produced
            studio_total = nb.docs_generated + nb.audio_generated + nb.visuals_generated + nb.quizzes_generated + nb.videos_generated
            if studio_total > 0:
                studio_parts = []
                if nb.docs_generated > 0:
                    studio_parts.append(f"{nb.docs_generated} document{'s' if nb.docs_generated != 1 else ''}")
                if nb.audio_generated > 0:
                    studio_parts.append(f"{nb.audio_generated} podcast{'s' if nb.audio_generated != 1 else ''}")
                if nb.visuals_generated > 0:
                    studio_parts.append(f"{nb.visuals_generated} visual{'s' if nb.visuals_generated != 1 else ''}")
                if nb.quizzes_generated > 0:
                    studio_parts.append(f"{nb.quizzes_generated} quiz{'zes' if nb.quizzes_generated != 1 else ''}")
                if nb.videos_generated > 0:
                    studio_parts.append(f"{nb.videos_generated} video{'s' if nb.videos_generated != 1 else ''}")
                details.append(f"  - Studio output: created {', '.join(studio_parts)}")
                if nb.studio_topics:
                    details.append(f"    Topics: {', '.join(nb.studio_topics)}")
            
            # Highlights — strongest signal of what the user cares about
            if nb.highlights_since > 0:
                details.append(f"  - You highlighted {nb.highlights_since} passages recently")
                for ht in nb.recent_highlight_texts[:2]:
                    details.append(f"    > \"{ht}\"")
            
            # Notes — the user's own thinking and ideas
            if nb.notes_created > 0:
                note_label = f"  - You wrote {nb.notes_created} note{'s' if nb.notes_created != 1 else ''}"
                if nb.note_titles:
                    note_label += f": {', '.join(nb.note_titles[:3])}"
                details.append(note_label)
            if nb.total_notes > 0 and nb.notes_created == 0:
                details.append(f"  - You have {nb.total_notes} note{'s' if nb.total_notes != 1 else ''} in this notebook")
            
            # Unfinished threads — conversations the user might want to continue
            if nb.unfinished_threads:
                details.append(f"  - Unfinished conversations ({len(nb.unfinished_threads)}):")
                for thread in nb.unfinished_threads[:2]:
                    details.append(f"    ? \"{thread}\"")
            
            # Emerging topics — topic drift detection
            if nb.emerging_topics:
                details.append(f"  - Emerging topics (new this week): {', '.join(nb.emerging_topics)}")
            
            # Temporal lookback — "one week ago"
            if nb.one_week_ago_items:
                details.append(f"  - One week ago you were reading:")
                for item in nb.one_week_ago_items[:2]:
                    details.append(f"    ← \"{item}\"")
            
            if nb.top_finding and not nb.recent_stories:
                details.append(f"  - Top finding: {nb.top_finding}")
            
            # Memory context — what the user was discussing/deciding in this notebook
            nb_memories = memory_context_by_nb.get(nb.notebook_id, [])
            if nb_memories:
                details.append(f"  - Recent research context from memory:")
                for mem in nb_memories[:2]:
                    details.append(f"    📝 {mem}")
            
            if details:
                section += "\n" + "\n".join(details)
            notebook_sections.append(section)
        
        raw_data = "\n\n".join(notebook_sections)
        if cross_insight:
            raw_data += f"\n\nCross-notebook insight: {cross_insight}"

        # --- Phase 1A: Temporal block prepended to prompt ---
        # If a temporal_block was provided (from generate_morning_brief), use it.
        # Fallback: build one now so this function remains independently callable.
        from zoneinfo import ZoneInfo
        if not temporal_block:
            from services.temporal import TemporalContext
            temporal_block = TemporalContext(self._get_user_timezone()).for_prompt(
                datetime.utcnow()  # best-effort fallback
            )

        today_str = datetime.now(tz=ZoneInfo(self._get_user_timezone())).strftime("%B %d, %Y")

        # Build the brain context block for the prompt
        brain_section = ""
        if brain_context:
            brain_section = (
                f"\nYOUR UNDERSTANDING OF THE USER'S RESEARCH "
                f"(from your ongoing analysis — use this to narrate from knowledge, not just stats):\n"
                f"{brain_context}\n"
            )

        # Curator Phase 5: prepend "what changed in understanding"
        # block when present. The LLM is instructed to integrate this
        # naturally — typically as a short "What's new in your thinking"
        # section near the top of the brief.
        understanding_section = ""
        if understanding_diff and understanding_diff.strip():
            understanding_section = (
                "\nUNDERSTANDING CHANGES SINCE LAST BRIEF "
                "(include as a 'What's new in your thinking' section "
                "near the top of the brief. Write a short paragraph — 2-4 "
                "sentences — that actually narrates the shifts: name the "
                "specific theses, stages, or contradicting sources that "
                "changed, not just generic phrases. Be substantive; this "
                "is the most novel signal in the brief.):\n"
                f"{understanding_diff}\n"
            )

        # Curator Phase 6a (2026-05-13): voice + observations.
        # Voice block is the FIRST thing the LLM sees so it sets the tone
        # for everything below. Observations payload follows, instructing
        # the LLM to lead the brief with what was actually noticed —
        # NOT with "Good morning, you've been away for Xh".
        voice_block = VOICE_PROMPTS.get(self.narrative_voice, VOICE_PROMPTS[DEFAULT_VOICE])

        # Build observations summary across notebooks. Includes only
        # signal-bearing fields — the rest is omitted to keep the prompt
        # tight.
        observations_section = ""
        try:
            from services.curator_brain import curator_brain as _cb
            since_iso = last_seen.isoformat() if last_seen else None
            obs_lines: List[str] = []
            if since_iso:
                for nb in notebooks[:10]:
                    obs = _cb.compute_brief_observations(nb["id"], since_iso)
                    has_signal = any([
                        obs.get("blocked_on"),
                        obs.get("recent_focus"),
                        obs.get("dissent_count", 0) > 0,
                        obs.get("is_quiet"),
                        obs.get("recent_completed_plans"),
                        obs.get("new_connections"),
                        obs.get("fresh_reclassifications", 0) > 0,
                        obs.get("has_pending_draft"),
                    ])
                    if not has_signal:
                        continue
                    name = nb.get("title", nb.get("name", "Untitled"))
                    # Curator Phase 4: tag the notebook block with mental-model
                    # confidence so the LLM's hedge-rule knows how strongly
                    # to phrase the observations.
                    mm = _cb.get_mental_model(nb["id"])
                    mm_conf = (mm.get("confidence") if mm else None) or 0
                    conf_tag = f" [confidence={mm_conf:.2f}]" if mm_conf else ""
                    nb_lines = [f"  {name}{conf_tag}:"]
                    if obs.get("blocked_on"):
                        nb_lines.append(f"    - BLOCKED ON: {obs['blocked_on'][:140]}")
                    if obs.get("recent_focus"):
                        nb_lines.append(f"    - recent focus: {obs['recent_focus'][:120]}")
                    if obs.get("stage"):
                        nb_lines.append(f"    - stage: {obs['stage']}")
                    if obs.get("dissent_count", 0) > 0:
                        line = f"    - dissent: {obs['dissent_count']} contradicting source(s)"
                        if obs.get("fresh_dissent_rationale"):
                            line += f" — \"{obs['fresh_dissent_rationale'][:120]}\""
                        nb_lines.append(line)
                    if obs.get("is_quiet"):
                        nb_lines.append("    - QUIET: no engagement here in 7+ days")
                    if obs.get("recent_completed_plans"):
                        for p in obs["recent_completed_plans"][:2]:
                            nb_lines.append(f"    - recently completed: {p[:120]}")
                    if obs.get("new_connections"):
                        for c in obs["new_connections"][:2]:
                            nb_lines.append(f"    - new cross-notebook link: {c[:120]}")
                    if obs.get("fresh_reclassifications", 0) > 0:
                        nb_lines.append(
                            f"    - {obs['fresh_reclassifications']} source(s) freshly reclassified"
                        )
                    if obs.get("has_pending_draft"):
                        kind = obs.get("pending_draft_kind") or "document"
                        nb_lines.append(
                            f"    - PENDING DRAFT: a {kind} is ready (mention it; user can run `@curator show draft`)"
                        )
                    if len(nb_lines) > 1:
                        obs_lines.extend(nb_lines)
            if obs_lines:
                observations_section = (
                    "\nOBSERVATIONS (Lead the brief with these — they are the most "
                    "interesting things noticed. Pick the strongest 1-2 to open with. "
                    "Activity stats below are supporting evidence, not the headline.):\n"
                    + "\n".join(obs_lines) + "\n"
                )
        except Exception as _e:
            logger.debug(f"[curator] observations payload failed (non-fatal): {_e}")

        # Fix #1 (2026-05-23): engagement-weighted brief boost — make the
        # LLM AWARE of click patterns so it can actively reference them in
        # the brief ("I noticed you keep coming back to X" / "I'll surface
        # less about Y for now"). The ranker already demotes ignored topics;
        # this is the prose layer the user actually reads.
        engagement_section = ""
        try:
            from services.curator_brain import curator_brain as _cb
            eng_lines: List[str] = []
            for nb in notebooks[:10]:
                summary = _cb.get_topic_engagement_summary(nb["id"])
                if not summary["liked"] and not summary["ignored"]:
                    continue
                name = nb.get("title", nb.get("name", "Untitled"))
                nb_lines = [f"  {name}:"]
                if summary["liked"]:
                    liked_str = ", ".join(
                        f"{t['topic']} ({t['clicked']}×)" for t in summary["liked"][:3]
                    )
                    nb_lines.append(f"    - user has engaged with: {liked_str}")
                if summary["ignored"]:
                    ignored_str = ", ".join(
                        f"{t['topic']} ({t['offered']}× offered)" for t in summary["ignored"][:3]
                    )
                    nb_lines.append(f"    - user has been ignoring: {ignored_str}")
                if len(nb_lines) > 1:
                    eng_lines.extend(nb_lines)
            if eng_lines:
                engagement_section = (
                    "\nUSER ENGAGEMENT PATTERNS (Phase 5 calibration — use these "
                    "to shape what you emphasize. Briefly acknowledge topics the "
                    "user keeps engaging with ('you've been digging into X') and "
                    "do NOT dwell on topics they keep ignoring. Don't list these "
                    "verbatim — internalize them and let them shape your tone "
                    "and selection.):\n"
                    + "\n".join(eng_lines) + "\n"
                )
        except Exception as _e:
            logger.debug(f"[curator] engagement payload failed (non-fatal): {_e}")

        prompt = f"""{voice_block}

{temporal_block}

You are {self.name}, a personal research assistant writing a morning brief for today, {today_str}. The user was away for {duration_str}. Turn the raw activity data below into something worth reading. IMPORTANT: Today's date is {today_str} — use this exact date, do not invent a different date.

WRITE LIKE THE VOICE BLOCK INSTRUCTS — that voice is more important than any pattern below. If the voice says "first-person", use first-person. If the voice says "minimal first-person", don't say "I". The voice block wins.
{brain_section}{observations_section}{understanding_section}{engagement_section}
ACTIVITY DATA:
{raw_data}

CONFIDENCE-AWARE LANGUAGE (Curator Phase 4 — applies to ANY claim from the observations payload that includes a confidence value):
- confidence > 0.85 → DEFINITIVE: "X is the case", "clearly", "established", "confirmed"
- confidence 0.7 – 0.85 → MODERATE: "appears to be", "seems", "looks like", "is shaping up to"
- confidence 0.5 – 0.7 → HEDGED: "I think", "this looks like", "tentatively", "I'm reading this as"
- confidence < 0.5 → SPECULATIVE: "possibly", "not sure but", "worth checking", "wouldn't bet on this yet"
- Apply this to mental-model fields, dissent rationales, insights, and any other observation that carries a confidence score. Do NOT apply to raw numerical facts (source counts, dates) — those are not hedged.

CRITICAL ACCURACY RULES — you MUST follow these:
- ALL percentages are PRE-COMPUTED in the data. Use them VERBATIM. Do NOT calculate your own percentages.
- When data says "X → Y sources (+N new this week, P% library growth)", report: "library grew from X to Y (+N new, P% growth)". The P% is already computed correctly — just copy it.
- When data says "Pace: N added vs M last week (+Q% faster)", report: "pace is up Q% (N this week vs M last)". The Q% is already computed — just copy it.
- NEVER do arithmetic yourself. NEVER compute percentages. NEVER say a number that doesn't appear in the raw data.
- "sources_this_week" is the number of NEW sources added THIS WEEK, not the total count. If data says "+11 new", say "11 new sources" not "90 sources added."
- If something says "no new additions this week," do NOT claim growth occurred.
- "tagged/reviewed" means the user has actively organized those sources with tags. "unreviewed" means no tags yet — not that the sources are unread.

TEMPORAL FRAMING — match the user's actual rhythm:
- The user was away for {duration_str}. Use that to frame your language:
  * If away < 24 hours: say "overnight", "since yesterday", "while you slept" — NOT "this week"
  * If away 1-2 days: say "over the past day" or "since you were last here"
  * If away 3+ days: then "this week" or "over the past few days" is appropriate
- NEVER default to "this week / last week" framing when the user is active daily. It feels disconnected.
- The user works in these notebooks every day. Acknowledge that continuity — "your research is building momentum" not "here's what happened this week."

COLLECTOR vs USER SOURCES — this distinction is CRITICAL:
- "examined N potential items" means the collector LOOKED AT N items from RSS feeds and web pages. This is NOT the same as adding them.
- "stored M into notebook" means M items actually passed quality filters and were ADDED as sources. Only these count as new collector sources.
- If stored = 0, the collector ran but found NOTHING worth adding. Do NOT say it "gathered" or "collected" or "found new" sources. Say it "ran but didn't find anything that passed quality filters" or simply omit the collector section.
- NEVER claim the collector added sources unless "collector_added" or "stored" is > 0 in the data.
- If the data shows "auto-gathered by background collector (REVIEW RECOMMENDED)", ONLY THEN call this out prominently
- Collector-gathered sources arrived WITHOUT the user's involvement — the user needs to know these exist and should examine them
- User-added sources need no special callout — the user already knows about those
- If there are collector sources pending review in the approval queue, make this the most actionable item in the brief
- If there are NO collector sources and NO pending approvals, do NOT write a "Collector Discoveries" section at all

STUDIO CONTENT CREATION — acknowledge what the user is BUILDING, not just reading:
- If "Studio output" data is present, the user actively generated materials (documents, podcasts, visuals, quizzes, videos)
- This is a strong engagement signal — acknowledge it warmly. Use the ACTUAL topic from the data, never write literal brackets like "[topic]" — fill them in or rephrase. Patterns to draw from:
  * "You've been actively creating — 2 podcasts and a visual show you're moving from research to synthesis" (substitute the actual subject matter when known)
  * "The quiz you generated is a great way to solidify your understanding" (mention the real subject if visible in data)
  * "You created 3 documents since your last session — your research is producing tangible output"
- If studio topics overlap with unfinished threads or emerging topics, connect the dots: "You're generating content on the same topics you were exploring in chat — your research is maturing"
- Keep it brief — 1-2 sentences integrated naturally into the per-notebook section, not a separate block
- NEVER emit a literal bracket placeholder ("[topic]", "[note titles]", "[recent topic]") — if you don't know the specific subject, omit the phrase entirely or use a generic word

USER NOTES — the user's own thinking, captured in their own words:
- Notes are first-class content — they represent what the user is ACTIVELY THINKING ABOUT
- If a user wrote notes recently, lead with that or weave it in prominently: name the actual note titles from the data — never emit literal brackets like "[note titles]"
- If a notebook has notes but none were created recently, you can reference them as context: "Your N notes in this notebook form a foundation for..." (use the actual count)
- Connect notes to other activity when possible — name the actual topics from the data, not bracketed placeholders
- Notes signal what the user cares about MORE than sources — sources are inputs, notes are the user's own synthesis

NEWSLETTER FORMATTING (CRITICAL):
- Use markdown extensively for a modern newsletter layout.
- Use `###` headers for each notebook or major section to break up text visually.
- Use **bold** liberally for source titles, key metrics, and important entities.
- Use bullet points (`-`) for lists of items (like newly discovered sources or threads).
- Keep paragraphs very short (1-2 sentences). Absolutely NO dense walls of text. Be highly scannable.
- Insert blank lines between sections to give the text room to breathe.

TONE:
- Warm, professional, like a trusted advisor who knows your research intimately
- Confident and specific — never vague or generic
- Brief — aim for 300-600 words total. ALWAYS finish your last sentence completely.
- The collector callouts, studio output, unfinished threads, emerging interests, and lookback sections are what make this feel MAGICAL — these show the user the system is paying attention. Prioritize them when present.

Write the brief now:"""

        try:
            from services.rag_engine import rag_engine
            from config import settings

            # Routed through rag_engine for the same reasons as the
            # weekly wrap above — respects rag_profile (use_chat_endpoint
            # for Gemma4, think:false to suppress channel tokens), and
            # the higher num_predict prevents truncation that manifests
            # as raw markdown chars in the UI under memory pressure.
            narrative = await rag_engine._call_ollama(
                system_prompt=(
                    f"You are {self.name}, the user's research companion. "
                    f"Personality: {self.personality}. "
                    f"You have been quietly paying attention to their research and have "
                    f"observations to share — not news to report. Use first person. "
                    f"Quote note titles when relevant. If nothing meaningful happened, "
                    f"say so briefly and stop. Never manufacture urgency."
                ),
                prompt=prompt,
                model=settings.ollama_model,
                # 2026-06-08: dropped 0.7 → 0.55 for gemma4 (better
                # instruction-following than olmo; CLAUDE.md doc-gen range).
                temperature=0.55,
                num_predict=1500,
                voice_modifier=False,
            )
            narrative = (narrative or "").strip()
            # Guard against error strings from ollama_client being treated as valid narrative
            if narrative and not narrative.startswith(("Request timed out", "Error:")):
                return narrative
            elif narrative:
                logger.warning(f"Brief LLM returned error: {narrative[:100]}")
                # Fall through to structured fallback
        except Exception as e:
            logger.error(f"Brief narrative generation failed: {e}")
        
        # Fallback: structured but not LLM-generated
        lines = []
        for nb in summaries:
            line = f"**{nb.name}**"
            if nb.subject:
                line += f" ({nb.subject})"
            parts = []
            if nb.recent_stories:
                titles = [f'"{s.title}"' for s in nb.recent_stories[:3]]
                parts.append(f"New: {', '.join(titles)}")
            elif nb.items_added > 0:
                parts.append(f"{nb.items_added} new items")
            if nb.pending_approval > 0:
                parts.append(f"{nb.pending_approval} pending review")
            if nb.person_changes:
                parts.extend(nb.person_changes[:2])
            if nb.upcoming_key_dates:
                parts.extend(nb.upcoming_key_dates[:2])
            if parts:
                line += ": " + " · ".join(parts)
            lines.append(line)
        return "\n".join(lines)
    
    # =========================================================================
    # Note → Collector Bridge — extract themes from notes, suggest keywords
    # =========================================================================

    async def suggest_collector_keywords_from_notes(self, notebook_id: str) -> Dict[str, Any]:
        """Extract themes from a notebook's notes and suggest new collector focus areas.

        Returns dict with:
          - note_themes: list of extracted themes
          - current_focus: existing collector focus_areas
          - suggestions: new keywords/focus_areas not already covered
        """
        from storage.source_store import source_store
        from agents.collector import get_collector

        all_sources = await source_store.list(notebook_id)
        notes = [s for s in all_sources if s.get("type") == "note"]
        if not notes:
            return {"note_themes": [], "current_focus": [], "suggestions": [], "message": "No notes in this notebook"}

        # Build a digest of note titles and content snippets
        note_digest_parts = []
        for n in notes[:15]:
            title = n.get("filename", "Untitled")
            content = (n.get("content") or "")[:500]
            note_digest_parts.append(f"- {title}: {content}")
        note_digest = "\n".join(note_digest_parts)

        # Get current collector config
        try:
            collector = get_collector(notebook_id)
            config = collector.get_config()
            current_focus = config.focus_areas or []
            subject = config.subject or ""
        except Exception:
            current_focus = []
            subject = ""

        # Use LLM to extract themes and suggest keywords
        prompt = f"""Analyze these user notes from a research notebook and extract the key themes and topics the user is thinking about.

NOTES:
{note_digest}

CURRENT COLLECTOR FOCUS AREAS: {', '.join(current_focus) if current_focus else 'None set'}
NOTEBOOK SUBJECT: {subject or 'Not specified'}

Return a JSON object with:
1. "note_themes" — list of 3-7 key themes/topics extracted from the notes (short phrases)
2. "suggestions" — list of 2-5 NEW search keywords or focus areas that the collector should add, based on the note themes but NOT already in the current focus areas. Each should be specific enough to yield good search results.

Return ONLY valid JSON, no explanation."""

        try:
            from services.ollama_service import ollama_service
            from config import settings
            import json

            response = await ollama_service.generate(
                prompt=prompt,
                system="You extract research themes from notes and suggest collector search keywords. Return only valid JSON.",
                model=settings.ollama_fast_model,
                temperature=0.3,
                timeout=30.0,
                num_predict=500,
                extra_options={"keep_alive": "10m"},
            )
            text = response.get("response", "").strip()
            # Parse JSON from response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                return {
                    "note_themes": data.get("note_themes", []),
                    "current_focus": current_focus,
                    "suggestions": data.get("suggestions", []),
                    "note_count": len(notes),
                    "subject": subject,
                }
        except Exception as e:
            logger.error(f"[Curator] Note theme extraction failed: {e}")

        return {"note_themes": [], "current_focus": current_focus, "suggestions": [], "note_count": len(notes)}

    async def apply_note_suggestions_to_collector(self, notebook_id: str, keywords: List[str]) -> Dict[str, Any]:
        """Apply suggested keywords from note analysis to a notebook's collector config.

        Only adds keywords that aren't already in focus_areas.
        """
        from agents.collector import get_collector

        collector = get_collector(notebook_id)
        config = collector.get_config()
        existing = set(a.lower() for a in (config.focus_areas or []))
        new_keywords = [k for k in keywords if k.lower() not in existing]

        if not new_keywords:
            return {"added": [], "message": "All suggested keywords already in focus areas"}

        updated_focus = list(config.focus_areas or []) + new_keywords
        collector.update_config({"focus_areas": updated_focus})

        return {
            "added": new_keywords,
            "total_focus_areas": len(updated_focus),
            "message": f"Added {len(new_keywords)} new focus area(s) to collector",
        }

    # =========================================================================
    # Proactive Cross-Notebook Discovery (Enhancement #7)
    # =========================================================================
    
    async def discover_cross_notebook_patterns(self) -> List[ProactiveInsight]:
        """
        Run during consolidation cycle. Finds:
        1. Overlapping entities across notebooks
        2. Contradicting information
        3. Temporal patterns (X happened after Y)
        4. Coverage gaps
        """
        insights = []
        notebooks = await notebook_store.list()
        
        if len(notebooks) < 2:
            return insights  # Need at least 2 notebooks
        
        # Find shared entities across notebooks
        shared_entities = await self._find_shared_entities(notebooks)
        
        for entity, notebook_contexts in shared_entities.items():
            if len(notebook_contexts) >= 2:
                insight = ProactiveInsight(
                    insight_type="cross_reference",
                    entity=entity,
                    notebooks=[ctx["notebook_id"] for ctx in notebook_contexts],
                    summary=f"'{entity}' appears in {len(notebook_contexts)} notebooks. Consider comparing perspectives.",
                    confidence=0.7
                )
                insights.append(insight)

        # Phase 14 (2026-06-08) — temporal_pattern producer. An entity
        # with a recent mention spike (≥3 in last 7d AND ≥2x its prior
        # 14d cadence) is worth surfacing. Cheap: scans existing event
        # payloads, no new LLM calls. Bounded to top 3 to avoid
        # flooding the insights table.
        try:
            from services.curator_brain import curator_brain as _cb
            from datetime import timedelta as _td
            from collections import Counter as _Counter

            now = datetime.utcnow()
            since_recent = (now - _td(days=7)).isoformat()
            since_baseline = (now - _td(days=21)).isoformat()

            recent_ev = _cb.recent_events(limit=2000, since_iso=since_recent)
            baseline_ev = _cb.recent_events(limit=4000, since_iso=since_baseline)

            # Mention count = case-insensitive substring hit in payload JSON
            # of an entity-string. We use the candidate-entity set we
            # already extracted above so we don't pay for a second pass.
            candidate_entities = [e for e in shared_entities.keys() if e and len(e) >= 3]
            if candidate_entities:
                recent_counts: Dict[str, int] = {}
                baseline_counts: Dict[str, int] = {}
                for ev in recent_ev:
                    blob = json.dumps(ev.get("payload") or {}).lower()
                    for ent in candidate_entities:
                        if ent.lower() in blob:
                            recent_counts[ent] = recent_counts.get(ent, 0) + 1
                for ev in baseline_ev:
                    blob = json.dumps(ev.get("payload") or {}).lower()
                    for ent in candidate_entities:
                        if ent.lower() in blob:
                            baseline_counts[ent] = baseline_counts.get(ent, 0) + 1

                spikes: List[tuple] = []
                for ent, recent_n in recent_counts.items():
                    if recent_n < 3:
                        continue
                    # Mentions in days 8-21 (baseline-window minus recent-window).
                    prior_n = max(0, baseline_counts.get(ent, 0) - recent_n)
                    # Spike: recent rate ≥ 2x prior rate (compare per-week).
                    # prior is 14 days, recent is 7 days; multiply prior by 0.5.
                    prior_per_week = prior_n * 0.5
                    if recent_n >= 2 * max(1.0, prior_per_week):
                        spikes.append((ent, recent_n, prior_per_week))

                spikes.sort(key=lambda x: x[1], reverse=True)
                for ent, recent_n, prior_per_week in spikes[:3]:
                    ctxs = shared_entities.get(ent, [])
                    nb_ids = [c["notebook_id"] for c in ctxs] if ctxs else []
                    insights.append(ProactiveInsight(
                        insight_type="temporal_pattern",
                        entity=ent,
                        notebooks=nb_ids,
                        summary=(
                            f"'{ent}' mentions spiked recently — {recent_n} in the "
                            f"last 7 days vs ~{prior_per_week:.1f}/week before. "
                            f"Worth checking what's driving the surge."
                        ),
                        confidence=0.65,
                    ))
        except Exception as _t_e:
            logger.debug(f"[curator] temporal_pattern detection skipped: {_t_e}")

        # Phase 14 (2026-06-08) — coverage_gap producer. Surfaces
        # notebooks where the user's mental model declares a `blocked_on`
        # area (curated data; the user told us what's missing) and the
        # notebook has the thesis but few sources covering that gap.
        try:
            from services.curator_brain import curator_brain as _cb2
            for nb in notebooks:
                nb_id = nb["id"]
                mm = _cb2.get_mental_model(nb_id) or {}
                thesis = (mm.get("thesis") or "").strip()
                blocked = (mm.get("blocked_on") or "").strip()
                if not thesis or not blocked or len(blocked) < 8:
                    continue
                insights.append(ProactiveInsight(
                    insight_type="coverage_gap",
                    entity=thesis[:80],
                    notebooks=[nb_id],
                    summary=(
                        f"Notebook '{nb.get('title', '(unnamed)')}' is light on "
                        f"coverage around: {blocked}. The thesis would be "
                        f"stronger with sources addressing this gap."
                    ),
                    confidence=0.7,
                ))
        except Exception as _c_e:
            logger.debug(f"[curator] coverage_gap detection skipped: {_c_e}")

        # Write the fresh batch to the brain. Brain preserves user signal
        # (thumbs_up / dismissed) when replacing the active set.
        try:
            from services.curator_brain import curator_brain
            curator_brain.add_insights([ins.model_dump() for ins in insights])
        except Exception as e:
            logger.warning(f"Could not persist insights to brain (non-fatal): {e}")
        return insights
    
    async def _find_shared_entities(self, notebooks: List[Dict]) -> Dict[str, List[Dict]]:
        """Find entities that appear in multiple notebooks"""
        entity_map = {}
        
        for notebook in notebooks:
            # Search for entities in this notebook's memories
            results = memory_store.search_archival_memory(
                query="key entities people companies topics",
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=notebook["id"],
                limit=20
            )
            
            for r in results:
                for entity in r.entry.entities:
                    if entity not in entity_map:
                        entity_map[entity] = []
                    entity_map[entity].append({
                        "notebook_id": notebook["id"],
                        "context": r.entry.content[:200]
                    })
        
        # Filter to entities in multiple notebooks
        return {k: v for k, v in entity_map.items() if len(v) >= 2}
    
    async def surface_insight_if_relevant(self, current_query: str) -> Optional[str]:
        """
        Check if any active brain insights relate to the current user query.
        If so, mention it naturally and record the surface event.

        Phase 14 (2026-06-08): the returned string is markdown — it may
        include trailing code-fences (mermaid / json-chart / klein) that
        the frontend ChatMessageBubble routes through
        MarkdownArtifactRenderer for actual visuals. Insight types map to:
          - cross_reference  → Mermaid graph LR (entity ↔ notebooks)
          - temporal_pattern → json-chart line (mentions over time)
          - coverage_gap     → Mermaid mindmap (covered + dashed missing)
        Other types (contradiction etc.) fall back to plain prose.
        """
        try:
            from services.curator_brain import curator_brain
            matches = curator_brain.find_insights_by_entity(current_query)
        except Exception as e:
            logger.warning(f"Could not query brain insights (non-fatal): {e}")
            return None

        if not matches:
            return None

        insight = matches[0]
        try:
            curator_brain.mark_insight_surfaced(insight["id"])
        except Exception as _e:
            logger.debug(f"[curator] mark_insight_surfaced: {_e}")
        # Curator Phase 4: confidence-aware hedging on the surface phrasing.
        # High-conf insights → assertive; low-conf → tentative.
        conf = insight.get("confidence") or 0
        if conf >= 0.85:
            prefix = "💡 Worth noting:"
        elif conf >= 0.5:
            prefix = "💡 By the way:"
        else:
            prefix = "💡 Possibly relevant (low-confidence):"

        base = f"{prefix} {insight['summary']}"
        visual = await self._compose_insight_visual(insight)
        return f"{base}\n\n{visual}" if visual else base

    async def _compose_insight_visual(self, insight: Dict[str, Any]) -> Optional[str]:
        """Build a code-fence visual for an insight based on its type.

        Returns the fence string (including ```), or None on failure.
        Always best-effort — visualizations are additive, never blocking.
        """
        import re as _re
        from datetime import timedelta as _td

        insight_type = insight.get("insight_type") or ""
        entity = (insight.get("entity") or "").strip()
        notebook_ids = insight.get("notebooks") or []

        def _label(s: str, n: int = 40) -> str:
            s = _re.sub(r"[\(\)\[\]\{\}\"`:,]+", " ", str(s or ""))
            s = _re.sub(r"\s+", " ", s).strip()
            return s[:n] or "—"

        async def _nb_names(ids: List[str], limit: int = 6) -> List[str]:
            names: List[str] = []
            for nb_id in (ids or [])[:limit]:
                try:
                    nb = await notebook_store.get(nb_id) or {}
                    names.append(nb.get("title") or nb.get("name") or nb_id[:8])
                except Exception:
                    names.append(str(nb_id)[:8])
            return names

        try:
            if insight_type == "cross_reference":
                if not entity or not notebook_ids:
                    return None
                names = await _nb_names(notebook_ids, limit=6)
                lines = ["graph LR"]
                root = f'root["{_label(entity, 60)}"]'
                lines.append(f"  {root}")
                for i, n in enumerate(names):
                    nid = f"nb{i}"
                    lines.append(f'  {nid}["{_label(n)}"]')
                    lines.append(f"  root --- {nid}")
                lines.append(f"  classDef hub fill:#ede9fe,stroke:#7c3aed,stroke-width:2px;")
                lines.append(f"  classDef leaf fill:#eff6ff,stroke:#3b82f6;")
                lines.append(f"  class root hub;")
                for i in range(len(names)):
                    lines.append(f"  class nb{i} leaf;")
                return "```mermaid\n" + "\n".join(lines) + "\n```"

            if insight_type == "temporal_pattern":
                # Derive a weekly mention series from recent_events. Best-
                # effort: if events are empty or entity didn't appear, skip.
                from services.curator_brain import curator_brain as _cb
                since_iso = (datetime.utcnow() - _td(days=56)).isoformat()
                try:
                    events = _cb.recent_events(limit=2000, since_iso=since_iso)
                except Exception:
                    events = []
                if not events or not entity:
                    return None
                entity_lower = entity.lower()
                # Bucket by ISO week.
                buckets: Dict[str, int] = {}
                for ev in events:
                    payload_blob = json.dumps(ev.get("payload") or {}).lower()
                    if entity_lower in payload_blob:
                        ts = ev.get("ts") or ""
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", ""))
                            iso = dt.strftime("%Y-W%V")
                            buckets[iso] = buckets.get(iso, 0) + 1
                        except Exception:
                            continue
                if len(buckets) < 2:
                    return None
                labels = sorted(buckets.keys())[-8:]
                data = [buckets[w] for w in labels]
                chart = {
                    "kind": "line",
                    "title": f"Mentions of {entity} per week",
                    "labels": labels,
                    "series": [{"label": "mentions", "data": data}],
                }
                return "```json-chart\n" + json.dumps(chart) + "\n```"

            if insight_type == "coverage_gap":
                if not entity:
                    return None
                names = await _nb_names(notebook_ids, limit=4)
                summary = insight.get("summary") or ""
                # Try to lift "missing X" / "gap on X" phrases out of the
                # summary as the dashed branch. Falls back to a generic
                # "underexplored" leaf when extraction fails.
                m = _re.search(r"(?:gap|missing|underexplor|lack(?:s|ing)?)[^.]*?(?:in|on|around)\s+([A-Za-z0-9 ,\-]{4,60})", summary, _re.I)
                missing_label = _label(m.group(1).strip(), 50) if m else "underexplored area"
                lines = ["mindmap", f"  root(({_label(entity, 60)}))"]
                if names:
                    lines.append("    Covered")
                    for n in names:
                        lines.append(f"      {_label(n)}")
                lines.append("    Missing")
                lines.append(f"      {missing_label}")
                lines.append(f"      ::icon(fa fa-question)")
                return "```mermaid\n" + "\n".join(lines) + "\n```"

        except Exception as e:
            logger.debug(f"[curator] _compose_insight_visual({insight_type}) failed: {e}")
            return None

        return None
    
    # =========================================================================
    # Devil's Advocate Mode (Enhancement #9)
    # =========================================================================
    
    async def find_counterarguments(
        self,
        notebook_id: str,
        thesis: Optional[str] = None
    ) -> CounterargumentResult:
        """
        If thesis provided, find evidence against it.
        If not, infer thesis from notebook content and find counters.

        Curator Phase 3b (2026-05-13): when source_stances has ≥3
        contradicting rows for this notebook AND no override thesis
        is supplied, prefer the cached stances over re-running an
        LLM search (faster + already curator-evaluated). Falls back
        to the original semantic-search path when stances are sparse
        or absent.
        """
        # Phase 3b: check the stance table for cached counter-evidence.
        # Only use it when the caller didn't override the thesis — if
        # they did, the cached stances may have been scored against a
        # different thesis and would be misleading.
        if thesis is None:
            try:
                from services.curator_brain import curator_brain
                mm = curator_brain.get_mental_model(notebook_id)
                if mm and mm.get("thesis"):
                    dissent_rows = curator_brain.get_dissenting_sources(notebook_id, limit=5)
                    if len(dissent_rows) >= 3:
                        # Attach source titles best-effort
                        from storage.source_store import source_store
                        counterpoints: List[Dict[str, Any]] = []
                        for d in dissent_rows:
                            title = d["source_id"]
                            try:
                                src = await source_store.get(d["source_id"])
                                if src:
                                    title = (
                                        src.get("filename")
                                        or src.get("title")
                                        or src.get("url")
                                        or d["source_id"]
                                    )
                            except Exception:
                                pass
                            counterpoints.append({
                                "query": "(cached stance)",
                                "content": f"[{title[:120]}] {d['rationale']}"[:300],
                                "score": d["confidence"],
                                "source_id": d["source_id"],
                            })
                        avg_conf = sum(d["confidence"] for d in dissent_rows) / max(1, len(dissent_rows))
                        return CounterargumentResult(
                            inferred_thesis=mm["thesis"],
                            counterpoints=counterpoints,
                            confidence=min(1.0, max(0.3, avg_conf)),
                        )
            except Exception as _e:
                logger.debug(f"[curator] find_counterarguments stance path skipped: {_e}")
                # Fall through to the legacy semantic-search path.

        # Legacy path — infer thesis if not provided, run counter-query
        # semantic search. Still used when (a) caller overrides thesis,
        # or (b) stance table is sparse for this notebook.
        if not thesis:
            thesis = await self._infer_thesis(notebook_id)

        # Generate counter-queries
        counter_queries = await self._generate_counter_queries(thesis)

        counterpoints = []

        for query in counter_queries:
            # Search notebook for contradicting evidence
            results = memory_store.search_archival_memory(
                query=query,
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=notebook_id,
                limit=5
            )

            for r in results:
                counterpoints.append({
                    "query": query,
                    "content": r.entry.content[:300],
                    "score": r.combined_score
                })

        # Rank and dedupe
        counterpoints.sort(key=lambda x: x["score"], reverse=True)

        return CounterargumentResult(
            inferred_thesis=thesis,
            counterpoints=counterpoints[:5],
            confidence=0.6 if counterpoints else 0.3
        )
    
    async def _infer_thesis(self, notebook_id: str) -> str:
        """Infer the main thesis/hypothesis from notebook content"""
        results = memory_store.search_archival_memory(
            query="main thesis hypothesis conclusion argument",
            namespace=AgentNamespace.COLLECTOR,
            notebook_id=notebook_id,
            limit=10
        )
        
        if not results:
            return "Unable to infer thesis from notebook content."
        
        context = "\n".join([r.entry.content[:300] for r in results])
        
        try:
            prompt = f"""Based on this research content, what is the main thesis or hypothesis being explored?

Content:
{context}

State the thesis in one clear sentence."""

            response = await ollama_service.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.3
            )
            return response.get("response", "Unable to infer thesis.")
        except Exception as e:
            logger.error(f"Thesis inference failed: {e}")
            return "Unable to infer thesis."
    
    async def _generate_counter_queries(self, thesis: str) -> List[str]:
        """Generate search queries to find contradicting evidence"""
        try:
            prompt = f"""Given this thesis: "{thesis}"

Generate 3 search queries that would find contradicting evidence or alternative perspectives.
Return only the queries, one per line."""

            response = await ollama_service.generate(
                prompt=prompt,
                model=settings.ollama_fast_model,
                temperature=0.5
            )
            
            queries = response.get("response", "").strip().split("\n")
            return [q.strip() for q in queries if q.strip()][:3]
        except Exception as e:
            logger.error(f"Counter-query generation failed: {e}")
            return [f"evidence against {thesis}", f"criticism of {thesis}"]
    
    # =========================================================================
    # Conversational Onboarding (Enhancement #11)
    # =========================================================================
    
    async def generate_setup_followup(self, notebook_id: str) -> Optional[str]:
        """
        After initial template setup, generate a contextual follow-up suggestion.
        Called once after config is saved. Returns a short message or None.
        """
        from agents.collector import get_collector
        
        try:
            collector = get_collector(notebook_id)
            config = collector.get_config()
            
            if not config.intent:
                return None
            
            prompt = f"""You are {self.name}, helping set up a research notebook.
The user just configured:
- Subject: {config.subject}
- Intent: {config.intent}
- Focus areas: {', '.join(config.focus_areas or [])}

Generate ONE helpful follow-up question or suggestion. Examples:
- "Any specific competitors to watch alongside [subject]?"
- "Want me to focus on recent news or go back further?"
- "I noticed you're tracking financials — should I watch SEC filings too?"

Keep it conversational and short (1-2 sentences). Return just the message, no JSON."""

            response = await ollama_service.generate(
                prompt=prompt,
                system=f"You are {self.name}. Personality: {self.personality}",
                model=settings.ollama_fast_model,
                temperature=0.5
            )
            
            text = response.get("response", "").strip()
            if text and len(text) < 300:
                return text
        except Exception as e:
            logger.debug(f"Setup follow-up generation failed (non-fatal): {e}")
        
        return None
    
    async def infer_config_from_content(
        self,
        notebook_id: str,
        filenames: List[str],
        sample_content: str
    ) -> Dict[str, Any]:
        """
        Analyze dropped files and suggest Collector configuration.
        Called after files are ingested into a new notebook.
        
        Returns:
            Dict with suggested_subject, suggested_intent, suggested_focus_areas,
            suggested_template, and a curator_message for the user.
        """
        filenames_str = ", ".join(filenames[:10])
        prompt = f"""Analyze these research files and suggest how to configure a research assistant.

Filenames: {filenames_str}
Sample content (first 2000 chars): {sample_content[:2000]}

Respond with JSON only:
{{
    "suggested_subject": "main subject or entity these files are about",
    "suggested_intent": "one paragraph describing what this research covers",
    "suggested_focus_areas": ["area1", "area2", "area3"],
    "suggested_template": "company_intel or industry_watch or topic_research or project_archive",
    "curator_message": "Friendly 2-3 sentence message to the user summarizing what you found and suggesting next steps"
}}"""

        try:
            response = await ollama_service.generate(
                prompt=prompt,
                system=f"You are {self.name}. Analyze research content and suggest configuration. Respond with valid JSON only.",
                model=settings.ollama_fast_model,
                temperature=0.3
            )
            
            text = response.get("response", "")
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                result = json.loads(text[json_start:json_end])
                return {
                    "suggested_subject": result.get("suggested_subject", ""),
                    "suggested_intent": result.get("suggested_intent", ""),
                    "suggested_focus_areas": result.get("suggested_focus_areas", []),
                    "suggested_template": result.get("suggested_template", "custom"),
                    "curator_message": result.get("curator_message", ""),
                    "notebook_id": notebook_id
                }
        except Exception as e:
            logger.error(f"Config inference from content failed: {e}")
        
        # Fallback — use filenames to guess
        subject_guess = filenames[0].rsplit(".", 1)[0] if filenames else "Research"
        return {
            "suggested_subject": subject_guess,
            "suggested_intent": f"Research related to {subject_guess}",
            "suggested_focus_areas": [],
            "suggested_template": "custom",
            "curator_message": f"I see you've added some files. Want me to set up a Collector to find related content?",
            "notebook_id": notebook_id
        }
    
    # =========================================================================
    # Collection Task Building (called from agents/collection_graph.py)
    # =========================================================================
    # Note: orchestrate_collection + _orchestrate_notebook_collection chain was
    # removed 2026-05-12 (Curator Phase 1) as dead code. The live orchestration
    # path is assign_immediate_collection (below). _create_collection_task and
    # its exploration helpers are reached via collection_graph.run_collection.
    
    async def _build_exploration_context(
        self,
        notebook_id: str,
    ) -> Dict[str, Any]:
        """
        Build a rich context of the user's recent activity for exploration.
        
        Pulls signals from multiple sources to understand what the user
        is currently thinking about, curious about, and engaged with.
        This feeds into adjacent/tangential query generation so the
        collector explores non-linearly — like a research librarian
        who reads adjacent shelves.
        
        Returns dict with:
            recent_questions: Questions the user asked in chat
            recent_highlights: Passages the user highlighted
            recent_searches: Searches the user performed
            recent_additions: Titles of sources the user recently added
            recent_topics: Topics from archival memory
        """
        from datetime import timedelta
        context = {
            "recent_questions": [],
            "recent_highlights": [],
            "recent_searches": [],
            "recent_additions": [],
            "recent_topics": [],
        }
        
        lookback = datetime.utcnow() - timedelta(days=7)
        
        # Pull recent events from the event logger
        try:
            from services.event_logger import event_logger, EventType
            events = event_logger.get_events_since(lookback, notebook_id=notebook_id)
            
            for evt in events:
                if evt.event_type == EventType.CHAT_QA.value:
                    question = evt.data.get("question", "")
                    if question and len(question) > 10:
                        context["recent_questions"].append(question[:200])
                
                elif evt.event_type == EventType.HIGHLIGHT_CREATED.value:
                    text = evt.data.get("text", "")
                    if text and len(text) > 15:
                        context["recent_highlights"].append(text[:300])
                
                elif evt.event_type == EventType.SEARCH_PERFORMED.value:
                    query = evt.data.get("query", "")
                    if query and len(query) > 3:
                        context["recent_searches"].append(query[:150])
                
                elif evt.event_type == EventType.DOCUMENT_CAPTURED.value:
                    title = evt.data.get("title", "")
                    if title:
                        context["recent_additions"].append(title[:150])
                
                elif evt.event_type == EventType.SOURCE_APPROVED.value:
                    src = evt.data.get("source", {})
                    title = src.get("title", src.get("filename", ""))
                    if title:
                        context["recent_additions"].append(title[:150])
        except Exception as e:
            logger.debug(f"Exploration context: event fetch failed (non-fatal): {e}")
        
        # Pull recent source titles (last 7 days by created_at)
        try:
            from storage.source_store import source_store
            all_sources = await source_store.list(notebook_id)
            for s in all_sources:
                created = s.get("created_at", "")
                if created and created > lookback.isoformat():
                    title = s.get("filename", s.get("title", ""))
                    if title and title not in context["recent_additions"]:
                        context["recent_additions"].append(title[:150])
        except Exception as e:
            logger.debug(f"Exploration context: source fetch failed (non-fatal): {e}")
        
        # Pull topic threads from archival memory (cross-notebook for richer signal)
        try:
            from storage.memory_store import memory_store
            from models.memory import AgentNamespace
            results = memory_store.search_archival_memory(
                query="recent research interests topics discussions",
                namespace=AgentNamespace.CURATOR,
                notebook_id=notebook_id,
                cross_notebook=True,
                limit=5
            )
            if results:
                for r in results:
                    if r.combined_score > 0.2:
                        context["recent_topics"].append(r.entry.content[:200])
        except Exception as e:
            logger.debug(f"Exploration context: memory fetch failed (non-fatal): {e}")
        
        # Cap everything to avoid prompt bloat
        context["recent_questions"] = context["recent_questions"][-8:]
        context["recent_highlights"] = context["recent_highlights"][-5:]
        context["recent_searches"] = context["recent_searches"][-6:]
        context["recent_additions"] = context["recent_additions"][-10:]
        context["recent_topics"] = context["recent_topics"][-5:]
        
        return context
    
    async def _generate_exploration_queries(
        self,
        notebook_id: str,
        config,
        exploration_context: Dict[str, Any],
        recently_used_queries: List[str],
    ) -> List[str]:
        """
        Generate ADJACENT/TANGENTIAL search queries for non-linear discovery.
        
        Unlike smart queries (which target the notebook's direct focus areas),
        exploration queries deliberately push into related but unexplored territory.
        Think: a research librarian who says "based on what you've been reading,
        you might also find this interesting..."
        
        The key insight: the user's research path is linear and intentional.
        The collector's discovery should be non-linear and serendipitous.
        This opens up possibilities the user wouldn't find on their own.
        
        Args:
            config: Notebook collector config (intent, focus_areas, subject)
            exploration_context: Recent user activity from _build_exploration_context
            recently_used_queries: Queries from recent runs to avoid repeating
        
        Returns:
            List of 3-5 adjacent/tangential search queries
        """
        from services.ollama_service import ollama_service
        from config import settings
        
        subject = config.subject.strip() if hasattr(config, 'subject') else ""
        focus_areas_str = ", ".join(config.focus_areas[:8]) if config.focus_areas else "general"
        
        # Build activity signal for the LLM
        activity_lines = []
        
        if exploration_context.get("recent_questions"):
            activity_lines.append("QUESTIONS THE USER ASKED IN CHAT RECENTLY:")
            for q in exploration_context["recent_questions"][-5:]:
                activity_lines.append(f"  ? {q}")
        
        if exploration_context.get("recent_highlights"):
            activity_lines.append("PASSAGES THE USER HIGHLIGHTED (they found these important):")
            for h in exploration_context["recent_highlights"][-4:]:
                activity_lines.append(f"  > {h}")
        
        if exploration_context.get("recent_searches"):
            activity_lines.append("SEARCHES THE USER PERFORMED:")
            for s in exploration_context["recent_searches"][-4:]:
                activity_lines.append(f"  🔍 {s}")
        
        if exploration_context.get("recent_additions"):
            activity_lines.append("SOURCES THE USER RECENTLY ADDED:")
            for a in exploration_context["recent_additions"][-6:]:
                activity_lines.append(f"  + {a}")
        
        if exploration_context.get("recent_topics"):
            activity_lines.append("TOPICS FROM RECENT RESEARCH MEMORY:")
            for t in exploration_context["recent_topics"][-3:]:
                activity_lines.append(f"  📝 {t}")
        
        activity_text = "\n".join(activity_lines) if activity_lines else "(No recent activity signals available)"
        
        # Build recently-used queries block
        avoid_text = ""
        if recently_used_queries:
            avoid_text = f"""
QUERIES ALREADY USED IN RECENT COLLECTION RUNS (do NOT repeat these or close variants):
{chr(10).join(f'  ✗ {q}' for q in recently_used_queries[-15:])}"""
        
        prompt = f"""You are a creative research librarian. Your job is to suggest ADJACENT, TANGENTIAL research directions that the user hasn't thought of yet — based on what they've been reading, asking about, and exploring.

NOTEBOOK SUBJECT: {subject or '(general)'}
FOCUS AREAS: {focus_areas_str}
NOTEBOOK PURPOSE: {config.intent}

{activity_text}
{avoid_text}

Generate 3-5 EXPLORATION queries that are ADJACENT to the user's interests — not the same topics, but related concepts, counterarguments, historical parallels, cross-disciplinary connections, or emerging intersections.

EXPLORATION PRINCIPLES:
- If they're researching "leadership styles" → explore "organizational psychology", "decision fatigue in executives", "military leadership lessons for business"
- If they're studying "machine learning" → explore "cognitive science of pattern recognition", "statistical mechanics and neural networks", "ethics of automated decision making"
- If they highlighted passages about X → find the intellectual NEIGHBORS of X — what scholars in adjacent fields would say about it
- Connect dots across their different interests — if they read about A and asked about B, find where A and B intersect
- Include at least 1 query that a smart colleague would suggest: "have you considered looking at it from THIS angle?"
- Include at least 1 contrarian or counterpoint query: find content that challenges what the user has been reading
- Each query should be 3-8 words, suitable for Google News or web search
- DO NOT repeat recent queries or generate close variants of them

Respond with ONLY a JSON array of strings, no other text:
["query 1", "query 2", ...]"""

        try:
            import asyncio as _asyncio
            response = await _asyncio.wait_for(
                ollama_service.generate(
                    prompt=prompt,
                    system="You are a creative research librarian specializing in cross-disciplinary discovery. Respond only with a valid JSON array of search query strings.",
                    model=settings.ollama_model,
                    temperature=0.9  # Higher creativity for exploration
                ),
                timeout=45
            )
            
            text = response.get("response", "")
            bracket_start = text.find("[")
            bracket_end = text.rfind("]") + 1
            if bracket_start >= 0 and bracket_end > bracket_start:
                parsed = json.loads(text[bracket_start:bracket_end])
                if isinstance(parsed, list):
                    queries = [q.strip() for q in parsed if isinstance(q, str) and len(q.strip()) > 3][:5]
                    if queries:
                        print(f"[CURATOR] 🔭 Generated {len(queries)} exploration queries: {queries}")
                        logger.info(f"Exploration queries for {notebook_id}: {queries}")
                        return queries
        except Exception as e:
            logger.warning(f"Exploration query generation failed (non-fatal): {e}")
            print(f"[CURATOR] Exploration query generation failed: {e}")
        
        return []
    
    async def _create_collection_task(
        self,
        notebook_id: str,
        config
    ) -> Dict[str, Any]:
        """
        Curator creates a specific collection task for a Collector.
        This is where Curator's intelligence directs what to look for.
        
        Instead of just passing raw config through, the Curator:
        1. Analyzes existing sources to understand what's already covered
        2. Uses LLM to generate specific, targeted search queries
        3. Auto-populates news_keywords so Google News gets searched
        4. Identifies knowledge gaps and emerging subtopics to pursue
        5. Generates EXPLORATION queries for adjacent/tangential discovery
        6. Rotates queries to avoid searching the same things every run
        """
        from storage.source_store import source_store
        
        task = {
            "notebook_id": notebook_id,
            "intent": config.intent,
            "focus_areas": config.focus_areas,
            "sources": config.sources,
            "mode": config.collection_mode.value if hasattr(config.collection_mode, 'value') else str(config.collection_mode),
            "created_by": "curator",
            "created_at": datetime.utcnow().isoformat()
        }
        
        # ── Get recently used queries for rotation/dedup ──
        recently_used_queries = []
        try:
            from services.collection_history import get_recent_queries
            recently_used_queries = get_recent_queries(notebook_id, lookback_runs=5)
            if recently_used_queries:
                print(f"[CURATOR] 🔄 Loaded {len(recently_used_queries)} recently used queries for rotation")
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        cross_notebook_seeds = []

        # ── Build a knowledge snapshot of what we already have ──
        source_titles = []
        source_domains = set()
        try:
            sources = await source_store.list(notebook_id)
            for s in sources[:80]:  # Cap to avoid huge prompts
                title = s.get("filename", s.get("title", ""))
                if title:
                    source_titles.append(title)
                url = s.get("url", "")
                if url:
                    try:
                        from urllib.parse import urlparse
                        domain = urlparse(url).netloc.lower().replace("www.", "")
                        if domain:
                            source_domains.add(domain)
                    except Exception as _e:
                        logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        except Exception as e:
            logger.debug(f"Could not load sources for smart directives: {e}")
        
        # ── Check archival memory for recent coverage ──
        recent_topics_text = ""
        try:
            existing_memories = memory_store.search_archival_memory(
                query=config.intent,
                limit=10,
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=notebook_id
            )
            if existing_memories:
                recent_topics = [m.entry.content[:120] for m in existing_memories[:5]]
                task["avoid_similar_to"] = recent_topics
                recent_topics_text = "\n".join(f"- {t}" for t in recent_topics)
        except Exception as e:
            logger.debug(f"Could not check existing content: {e}")
        
        # ── Use LLM to generate smart, specific search queries ──
        smart_queries = []
        try:
            subject = config.subject.strip() if hasattr(config, 'subject') else ""
            focus_areas_str = ", ".join(config.focus_areas[:10]) if config.focus_areas else "general"
            
            # Build the prompt with existing knowledge context
            existing_context = ""
            if source_titles:
                sample_titles = source_titles[-20:]  # Most recent 20
                existing_context = f"""
The notebook already has {len(source_titles)} sources. Here are the most recent titles:
{chr(10).join(f'- {t}' for t in sample_titles)}

Known domains already collected from: {', '.join(list(source_domains)[:15])}"""
            
            if recent_topics_text:
                existing_context += f"""

Recent content summaries already in the notebook:
{recent_topics_text}"""
            
            # Build recently-used queries block for rotation
            avoid_queries_text = ""
            if recently_used_queries:
                avoid_queries_text = f"""
QUERIES USED IN RECENT RUNS (do NOT repeat these or close variants — generate FRESH queries):
{chr(10).join(f'  ✗ {q}' for q in recently_used_queries[-12:])}"""

            # ── Adaptive query learning: inject successful/failed patterns ──
            adaptive_block = ""
            try:
                from services.collection_history import get_successful_query_patterns, get_failed_query_patterns
                successful = get_successful_query_patterns(notebook_id, min_approval_rate=0.3, limit=5)
                failed = get_failed_query_patterns(notebook_id, limit=5)
                
                if successful:
                    good_examples = [f'  ✓ "{p["query"]}" ({p["approval_rate"]*100:.0f}% approved)' for p in successful]
                    adaptive_block += f"""
QUERY PATTERNS THAT WORKED WELL (generate similar styles):
{chr(10).join(good_examples)}"""
                
                if failed:
                    bad_examples = [f'  ✗ "{q}"' for q in failed]
                    adaptive_block += f"""
QUERY PATTERNS THAT ALWAYS FAILED (avoid these styles):
{chr(10).join(bad_examples)}"""
                
                if adaptive_block:
                    print(f"[CURATOR] 📈 Adaptive learning: {len(successful)} good patterns, {len(failed)} bad patterns")
            except Exception as _e:
                logger.debug(f"[curator] {type(_e).__name__}: {_e}")
            
            prompt = f"""You are a research librarian planning the next collection run for a research notebook.

NOTEBOOK PURPOSE: {config.intent}
SUBJECT: {subject or '(general)'}
FOCUS AREAS: {focus_areas_str}
{existing_context}
{avoid_queries_text}
{adaptive_block}

Generate 6-8 SPECIFIC search queries that would find NEW, valuable content not already covered.

Rules:
- Be SPECIFIC, not generic. "transformer architecture scaling laws 2026" is good. "AI research papers" is bad.
- Target specific researchers, labs, conferences, techniques, or recent developments
- Include at least 1 query targeting a specific research venue (arXiv, conference, journal)
- Include at least 1 query targeting a specific person/lab in this field
- Include at least 1 query about a recent development or trend
- Avoid queries that would return content already in the notebook
- DO NOT repeat or closely paraphrase any recently used queries listed above
- Each query should be 3-8 words, suitable for Google News or web search

Respond with ONLY a JSON array of strings, no other text:
["query 1", "query 2", ...]"""

            import asyncio as _asyncio
            response = await _asyncio.wait_for(
                ollama_service.generate(
                    prompt=prompt,
                    system="You are a research librarian. Respond only with a valid JSON array of search query strings.",
                    model=settings.ollama_model,  # Main model — this is the strategic brain
                    temperature=0.7  # Some creativity in query generation
                ),
                timeout=45  # 45s max for main model query generation — fall back to defaults if slow
            )
            
            text = response.get("response", "")
            # Extract JSON array
            bracket_start = text.find("[")
            bracket_end = text.rfind("]") + 1
            if bracket_start >= 0 and bracket_end > bracket_start:
                parsed = json.loads(text[bracket_start:bracket_end])
                if isinstance(parsed, list):
                    smart_queries = [q.strip() for q in parsed if isinstance(q, str) and len(q.strip()) > 3][:8]
            
            if smart_queries:
                print(f"[CURATOR] 🧠 Generated {len(smart_queries)} smart queries: {smart_queries}")
                logger.info(f"Smart collection queries for {notebook_id}: {smart_queries}")
                
        except Exception as e:
            logger.warning(f"Smart query generation failed (will use defaults): {e}")
            print(f"[CURATOR] Smart query generation failed: {e}")
        
        # ── Generate EXPLORATION queries for adjacent/tangential discovery ──
        exploration_queries = []
        try:
            exploration_context = await self._build_exploration_context(notebook_id)
            has_activity = any(
                exploration_context.get(k)
                for k in ["recent_questions", "recent_highlights", "recent_searches", "recent_additions", "recent_topics"]
            )
            if has_activity:
                exploration_queries = await self._generate_exploration_queries(
                    notebook_id, config, exploration_context,
                    recently_used_queries + smart_queries  # Avoid overlap with smart queries too
                )
            else:
                print(f"[CURATOR] No recent user activity for {notebook_id} — skipping exploration queries")
        except Exception as e:
            logger.warning(f"Exploration query generation failed (non-fatal): {e}")
            print(f"[CURATOR] Exploration queries failed: {e}")
        
        # ── Enrich the task with smart directives + exploration queries ──
        if smart_queries:
            task["smart_queries"] = smart_queries
            task["curator_directive"] = (
                f"Use these targeted queries to find specific, high-quality content: "
                f"{', '.join(smart_queries[:4])}..."
            )
        else:
            task["curator_directive"] = "Find NEW information not covered by existing content"
        
        if exploration_queries:
            task["exploration_queries"] = exploration_queries
            # Blend exploration queries into smart_queries so they get used by the collector
            all_queries = list(smart_queries) + list(exploration_queries)
            task["smart_queries"] = all_queries
            print(f"[CURATOR] 🧭 Task has {len(smart_queries)} targeted + {len(exploration_queries)} exploration queries")
        
        # ── Auto-populate news_keywords if empty ──
        # This ensures Google News actually gets searched
        sources = task.get("sources", {})
        existing_news_kw = sources.get("news_keywords", [])
        
        if not existing_news_kw:
            auto_news_keywords = []
            subject = config.subject.strip() if hasattr(config, 'subject') else ""
            
            # Use smart queries as news keywords (they're already specific)
            if smart_queries:
                auto_news_keywords.extend(smart_queries[:4])
            
            # Include exploration queries in news search for adjacent discovery
            if exploration_queries:
                auto_news_keywords.extend(exploration_queries[:3])
            
            # Also add subject + top focus areas as fallback
            if subject:
                for area in config.focus_areas[:3]:
                    kw = f"{subject} {area}" if subject.lower() not in area.lower() else area
                    if kw not in auto_news_keywords:
                        auto_news_keywords.append(kw)
                if subject not in auto_news_keywords:
                    auto_news_keywords.append(subject)
            
            if auto_news_keywords:
                # Deep copy sources to avoid mutating config
                task["sources"] = {k: list(v) if isinstance(v, list) else v for k, v in sources.items()}
                task["sources"]["news_keywords"] = auto_news_keywords
                print(f"[CURATOR] 📰 Auto-populated {len(auto_news_keywords)} news keywords: {auto_news_keywords}")
        
        # ── Auto-populate arxiv_categories for research-oriented notebooks ──
        arxiv_categories = sources.get("arxiv_categories", [])
        if not arxiv_categories:
            intent_lower = (config.intent or "").lower()
            subject_lower = (config.subject if hasattr(config, 'subject') else "").lower()
            combined = f"{intent_lower} {subject_lower}"
            
            auto_arxiv = []
            # Map common research topics to arXiv categories
            arxiv_hints = {
                "cs.AI": ["artificial intelligence", "ai research", "ai "],
                "cs.LG": ["machine learning", "deep learning", "neural network"],
                "cs.CL": ["natural language", "nlp", "language model", "llm", "gpt", "transformer"],
                "cs.CV": ["computer vision", "image recognition", "object detection"],
                "cs.RO": ["robotics", "robot"],
                "cs.CR": ["cybersecurity", "security", "cryptography"],
                "stat.ML": ["statistical learning", "bayesian"],
                "cs.SE": ["software engineering"],
                "q-fin": ["quantitative finance", "algorithmic trading"],
                "econ": ["economics research"],
            }
            for category, triggers in arxiv_hints.items():
                if any(t in combined for t in triggers):
                    auto_arxiv.append(category)
            
            if auto_arxiv:
                if "sources" not in task or task["sources"] is sources:
                    task["sources"] = {k: list(v) if isinstance(v, list) else v for k, v in sources.items()}
                task["sources"]["arxiv_categories"] = auto_arxiv[:3]
                print(f"[CURATOR] 📚 Auto-added arXiv categories: {auto_arxiv[:3]}")
                
                # Also use smart queries for direct arXiv search (not just browsing categories)
                if smart_queries:
                    task["sources"]["arxiv_queries"] = smart_queries[:4]
                    print(f"[CURATOR] 🔬 Auto-added {len(smart_queries[:4])} arXiv search queries")
        
        return task
    
    async def assign_immediate_collection(
        self,
        notebook_id: str,
        specific_query: Optional[str] = None,
        deadline_seconds: Optional[int] = 120,
        trigger: str = "manual",
    ) -> Dict[str, Any]:
        """
        Curator assigns an immediate collection task for a specific notebook.
        Called when user clicks "Collect Now" - but Curator still orchestrates.
        
        Master scheduler lock ensures only one notebook collects at a time,
        preventing Ollama contention from parallel collection runs.
        
        Args:
            deadline_seconds: Max seconds for the pipeline. None = no deadline
                              (used by background scheduler for thorough runs).
            trigger: 'manual', 'scheduled', or 'specific' — recorded in history.
        """
        # Acquire collection lock — only one notebook collects at a time
        if self._collection_lock.locked():
            active = self._active_collection or "unknown"
            logger.info(f"[CURATOR] Collection queued for {notebook_id[:8]} — waiting on {active[:8]}")
            print(f"[CURATOR] ⏳ Waiting for {active[:8]}... to finish before collecting {notebook_id[:8]}")
        
        async with self._collection_lock:
            self._active_collection = notebook_id
            try:
                return await self._execute_collection(
                    notebook_id, specific_query, deadline_seconds, trigger
                )
            finally:
                self._active_collection = None
    
    async def _execute_collection(
        self,
        notebook_id: str,
        specific_query: Optional[str] = None,
        deadline_seconds: Optional[int] = 120,
        trigger: str = "manual",
    ) -> Dict[str, Any]:
        """Inner collection logic — always called under _collection_lock."""
        import time as _time
        deadline = (_time.time() + deadline_seconds) if deadline_seconds else None

        from agents.collector import get_collector

        print(f"[CURATOR] assign_immediate_collection: getting collector for {notebook_id}")
        collector = get_collector(notebook_id)
        config = collector.get_config()

        if not config.intent:
            return {"error": "Collector not configured", "items_collected": 0}

        print(f"[CURATOR] Config loaded. Sources: {list(config.sources.keys()) if config.sources else 'none'}")

        # Curator Phase 2a: register a 3-step plan in the brain so the
        # UI plan card (Phase 2b) and the audit log have visibility into
        # this multi-step action. plan_id is None if brain is offline —
        # everything below tolerates that gracefully.
        # Curator Phase 2b: also register the plan as cancellable so the
        # UI Stop button can signal it via POST /curator/plans/{id}/cancel.
        plan_id: Optional[str] = None
        try:
            from services.curator_brain import curator_brain
            plan_summary = (
                f"Collect for notebook: {config.name or notebook_id[:8]}"
                + (f" — focus: {specific_query}" if specific_query else "")
            )
            plan_id = curator_brain.create_plan(
                intent="assign_immediate_collection",
                summary=plan_summary,
                steps=[
                    {"name": "search", "description": "Gather candidate items from configured sources"},
                    {"name": "judge", "description": "Curator judges each candidate for relevance"},
                    {"name": "store", "description": "Persist approved items + queue ambiguous ones for review"},
                ],
                notebook_id=notebook_id,
                user_visible=True,
            )
            if plan_id:
                curator_brain.register_cancellable(plan_id)
                curator_brain.start_plan(plan_id)
                curator_brain.start_step(plan_id, 1)
        except Exception as _e:
            logger.debug(f"[curator] plan setup failed (non-fatal): {_e}")

        # Create task with optional specific query
        task = await self._create_collection_task(notebook_id, config)
        if specific_query:
            task["specific_query"] = specific_query
            task["curator_directive"] = f"Focus on: {specific_query}"
        
        # Pass deadline to collector so it can manage its time
        task["_deadline"] = deadline
        
        if deadline:
            print(f"[CURATOR] Task created. Executing collection... (budget: {deadline - _time.time():.0f}s remaining)")
        else:
            print(f"[CURATOR] Task created. Executing collection... (no deadline)")
        
        # Execute collection
        collected_items = await collector.execute_collection_task(task)

        print(f"[CURATOR] Collection returned {len(collected_items) if collected_items else 0} items")

        # Plan step 1 (search) — complete with count summary
        if plan_id:
            try:
                from services.curator_brain import curator_brain
                curator_brain.complete_step(
                    plan_id, 1,
                    output_summary=f"{len(collected_items) if collected_items else 0} candidate items"
                )
            except Exception as _e:
                logger.debug(f"[curator] plan step1 complete: {_e}")

        # ── Cancel breakpoint 1: after search, before judge ──────────
        # If the user clicked Stop while step 1 was running, exit before
        # spending compute on step 2. No data loss possible — nothing
        # is persisted yet.
        if plan_id:
            try:
                from services.curator_brain import curator_brain
                if curator_brain.is_cancelled(plan_id):
                    curator_brain.cancel_plan(plan_id, reason="user_requested")
                    curator_brain.unregister_cancellable(plan_id)
                    return {
                        "items_collected": 0,
                        "cancelled": True,
                        "message": "Cancelled by user after search",
                    }
            except Exception as _e:
                logger.debug(f"[curator] cancel check 1: {_e}")

        if not collected_items:
            # Still record history so query rotation works (avoids repeating same queries next run)
            try:
                from services.collection_history import record_collection_run
                record_collection_run(
                    notebook_id=notebook_id,
                    items_found=0, items_approved=0, items_pending=0, items_rejected=0,
                    sources_checked=len(config.sources.get("rss_feeds", [])) + len(config.sources.get("web_pages", [])),
                    trigger=trigger,
                    keywords_used=task.get("focus_areas", [])[:5],
                    queries_used=task.get("smart_queries", []),
                    exploration_queries=task.get("exploration_queries", []),
                )
            except Exception as _e:
                logger.debug(f"[curator] {type(_e).__name__}: {_e}")
            # Plan: no items found — cancel remaining steps (judge + store
            # have nothing to do). Plan ends in cancelled state with reason.
            if plan_id:
                try:
                    from services.curator_brain import curator_brain
                    curator_brain.cancel_plan(plan_id, reason="no_items_found")
                    curator_brain.unregister_cancellable(plan_id)
                except Exception as _e:
                    logger.debug(f"[curator] plan cancel: {_e}")
            return {"items_collected": 0, "message": "No new items found"}

        # Plan step 2 (judge) — start
        if plan_id:
            try:
                from services.curator_brain import curator_brain
                curator_brain.start_step(plan_id, 2)
            except Exception as _e:
                logger.debug(f"[curator] plan step2 start: {_e}")

        # Judge results (pass deadline so judgment can auto-defer if time is tight)
        if deadline:
            remaining = deadline - _time.time()
            print(f"[CURATOR] Judging {len(collected_items)} items... ({remaining:.0f}s remaining)")
        else:
            print(f"[CURATOR] Judging {len(collected_items)} items... (no deadline)")
        judgments = await self.judge_collection(
            collector_id=notebook_id,
            proposed_items=collected_items,
            notebook_intent=config.intent,
            deadline=deadline
        )

        # Plan step 2 (judge) — complete
        if plan_id:
            try:
                from services.curator_brain import curator_brain
                curator_brain.complete_step(
                    plan_id, 2,
                    output_summary=f"{len(judgments)} judgments returned"
                )
            except Exception as _e:
                logger.debug(f"[curator] plan step2 complete: {_e}")

        # ── Cancel breakpoint 2: after judge, before store ───────────
        # The user can still stop here. Judging work is sunk cost, but
        # no items have been persisted to the notebook yet.
        if plan_id:
            try:
                from services.curator_brain import curator_brain
                if curator_brain.is_cancelled(plan_id):
                    curator_brain.cancel_plan(plan_id, reason="user_requested")
                    curator_brain.unregister_cancellable(plan_id)
                    return {
                        "items_collected": 0,
                        "cancelled": True,
                        "message": "Cancelled by user after judging",
                    }
                curator_brain.start_step(plan_id, 3)
            except Exception as _e:
                logger.debug(f"[curator] cancel check 2 / step3 start: {_e}")
        
        approved = 0
        pending = 0
        rejected = 0
        filtered = 0
        approved_titles = []
        filtered_titles = []
        rejection_reasons: Dict[str, int] = {}  # Track why items fail
        CONFIDENCE_FLOOR = 0.50  # Nothing below 50% is ever added
        
        # Pre-fetch existing URLs once for dedup (avoids N × source_store.list() calls)
        from storage.source_store import source_store
        existing_sources = await source_store.list(notebook_id)
        existing_urls = {s.get("url") for s in existing_sources if s.get("url")}

        # Curator Phase 2b: track whether the user cancelled mid-store.
        # If they do, we break out of the loop and report partial counts.
        cancelled_mid_store = False

        for item, judgment in zip(collected_items, judgments):
            # ── Cancel breakpoint 3: per-iteration ──────────────────────
            # User can stop mid-store. Items already processed in earlier
            # iterations stay (they're committed). Future iterations skip.
            if plan_id:
                try:
                    from services.curator_brain import curator_brain
                    if curator_brain.is_cancelled(plan_id):
                        cancelled_mid_store = True
                        break
                except Exception as _e:
                    logger.debug(f"[curator] cancel check 3: {_e}")

            # Hard confidence floor: items below threshold are always filtered
            if item.overall_confidence < CONFIDENCE_FLOOR:
                filtered += 1
                reason = f"below_{int(CONFIDENCE_FLOOR*100)}pct_threshold"
                filtered_titles.append({
                    "title": item.title, "source": item.source_name, 
                    "confidence": item.overall_confidence, 
                    "reason": reason
                })
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                continue
            
            if judgment.decision == JudgmentDecision.APPROVE:
                # Directly store approved items (they aren't in the approval queue)
                try:
                    was_stored = await collector._store_approved_item(item, _existing_urls=existing_urls)
                    if was_stored:
                        approved += 1
                        approved_titles.append({"id": item.id, "title": item.title, "source": item.source_name, "confidence": item.overall_confidence})
                    else:
                        # Item was approved but couldn't be stored (duplicate URL or shallow).
                        # Route to approval queue so the user can still see it, rather
                        # than silently dropping potentially relevant content.
                        queue_result = await collector._add_to_approval_queue(item)
                        if queue_result == 'queued':
                            pending += 1
                        else:
                            filtered += 1
                            filtered_titles.append({"title": item.title, "source": item.source_name, "confidence": item.overall_confidence, "reason": "shallow_or_duplicate"})
                except Exception as e:
                    logger.error(f"Failed to store approved item '{item.title}': {e}")
                    filtered += 1
            elif judgment.decision == JudgmentDecision.REJECT:
                rejected += 1
                reason = getattr(judgment, 'reason', 'curator_rejected') or 'curator_rejected'
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            else:
                # Queue for user review (may auto-approve if high confidence in mixed mode)
                queue_result = await collector._add_to_approval_queue(item)
                if queue_result == 'queued':
                    pending += 1
                elif queue_result == 'stored':
                    approved += 1
                    approved_titles.append({"id": item.id, "title": item.title, "source": item.source_name, "confidence": item.overall_confidence})
                else:
                    filtered += 1
                    filtered_titles.append({"title": item.title, "source": item.source_name, "confidence": item.overall_confidence, "reason": "shallow_or_duplicate"})
        
        print(f"[CURATOR] Done: {approved} approved, {pending} pending, {rejected} rejected, {filtered} filtered (shallow/dup)")

        # Plan step 3 (store) — complete (or mark cancelled if the user
        # stopped mid-iteration). Plan auto-completes from complete_step
        # when this is the final step; cancel_plan overrides to cancelled.
        if plan_id:
            try:
                from services.curator_brain import curator_brain
                if cancelled_mid_store:
                    curator_brain.complete_step(
                        plan_id, 3,
                        output_summary=(
                            f"{approved} approved, {pending} pending, "
                            f"{rejected} rejected before cancel"
                        ),
                    )
                    curator_brain.cancel_plan(plan_id, reason="user_requested")
                else:
                    curator_brain.complete_step(
                        plan_id, 3,
                        output_summary=f"{approved} approved, {pending} pending, {rejected} rejected, {filtered} filtered"
                    )
            except Exception as _e:
                logger.debug(f"[curator] plan step3 complete: {_e}")
            finally:
                # Always unregister so the cancellation registry doesn't
                # accumulate dead entries.
                try:
                    from services.curator_brain import curator_brain as _cb
                    _cb.unregister_cancellable(plan_id)
                except Exception:
                    pass
        
        # Record in collection history
        try:
            from services.collection_history import record_collection_run
            record_collection_run(
                notebook_id=notebook_id,
                items_found=len(collected_items),
                items_approved=approved,
                items_pending=pending,
                items_rejected=rejected,
                sources_checked=len(config.sources.get("rss_feeds", [])) + len(config.sources.get("web_pages", [])) + len(config.sources.get("news_keywords", [])),
                trigger="specific" if specific_query else trigger,
                keywords_used=task.get("focus_areas", [])[:5],
                queries_used=task.get("smart_queries", []),
                exploration_queries=task.get("exploration_queries", []),
                rejection_reasons=rejection_reasons if rejection_reasons else None,
            )
        except Exception as hist_err:
            logger.warning(f"Failed to record collection history (non-fatal): {hist_err}")
        
        # ── Adaptive query learning: record per-query outcomes ──
        try:
            from services.collection_history import record_query_outcomes
            # Build query→outcome map: attribute each item's result to its likely source query
            # Simple heuristic: match item title words to query words
            all_queries = list(task.get("smart_queries", [])) + list(task.get("exploration_queries", []))
            if all_queries:
                query_outcomes: Dict[str, Dict[str, int]] = {}
                for q in all_queries:
                    query_outcomes[q] = {"approved": 0, "rejected": 0, "total": 0}
                
                # Attribute each item to the best-matching query
                for item, judgment in zip(collected_items, judgments):
                    best_query = None
                    best_overlap = 0
                    title_words = set(item.title.lower().split())
                    for q in all_queries:
                        q_words = set(q.lower().split())
                        overlap = len(title_words & q_words)
                        if overlap > best_overlap:
                            best_overlap = overlap
                            best_query = q
                    if not best_query:
                        best_query = all_queries[0]  # Default to first query
                    
                    query_outcomes[best_query]["total"] += 1
                    if judgment.decision == JudgmentDecision.APPROVE:
                        query_outcomes[best_query]["approved"] += 1
                    elif judgment.decision == JudgmentDecision.REJECT:
                        query_outcomes[best_query]["rejected"] += 1
                
                record_query_outcomes(notebook_id, query_outcomes)
                logger.info(f"[Adaptive] Recorded outcomes for {len(query_outcomes)} queries")
        except Exception as aq_err:
            logger.debug(f"Adaptive query recording failed (non-fatal): {aq_err}")
        
        # ── Phase 4: Record collection pattern (CBR) + post-run synthesis ──
        try:
            from services.collection_history import record_collection_pattern, record_run_synthesis
            
            total_judged = approved + pending + rejected + filtered
            approval_rate = approved / max(total_judged, 1)
            strategy_used = task.get("strategy", "auto")
            if strategy_used == "auto":
                strategy_used = "iterative" if not deadline else "standard"
            
            # Record pattern for CBR
            record_collection_pattern(notebook_id, {
                "strategy": strategy_used,
                "queries": task.get("smart_queries", [])[:6],
                "items_found": len(collected_items),
                "items_approved": approved,
                "approval_rate": round(approval_rate, 2),
                "trigger": "specific" if specific_query else trigger,
                "iteration_count": task.get("_iteration_count"),
                "total_queries_used": task.get("_total_queries_used"),
            })
            
            # Record post-run synthesis
            synthesis = {
                "approved_titles": [t["title"] for t in approved_titles[:5]],
                "items_found": len(collected_items),
                "items_approved": approved,
                "items_pending": pending,
                "strategy": strategy_used,
                "trigger": "specific" if specific_query else trigger,
                "top_sources": list(set(t.get("source", "") for t in approved_titles))[:4],
            }
            # Add gap info if nothing was approved
            if approved == 0 and rejection_reasons:
                synthesis["gap_reasons"] = dict(list(rejection_reasons.items())[:3])
            record_run_synthesis(notebook_id, synthesis)
            
        except Exception as p4_err:
            logger.debug(f"Phase 4 recording failed (non-fatal): {p4_err}")

        # ── Auto-expand source discovery (the collector's wander reflex) ──
        # After every sweep, look at recently approved items for patterns —
        # new domains that keep showing up, RSS feeds hiding in article
        # content — and add a capped handful to the config. Always-on so
        # the collector gradually widens its net without nagging the user
        # to manually add sources. Non-fatal: any failure is logged and
        # the sweep result is still returned.
        try:
            discovery = await collector.auto_discover_sources()
            if discovery.get("auto_expanded"):
                logger.info(
                    f"[curator] auto-expand applied to {notebook_id[:8]}: "
                    f"+{len(discovery.get('added_domains', []))} domains, "
                    f"+{len(discovery.get('added_feeds', []))} feeds"
                )
        except Exception as exp_err:
            logger.debug(f"[curator] auto-expand discovery failed (non-fatal): {exp_err}")

        return {
            "items_collected": len(collected_items),
            "items_approved": approved,
            "items_pending": pending,
            "items_rejected": rejected,
            "items_filtered": filtered,
            "auto_approved": approved_titles,
            "filtered": filtered_titles
        }
    
    # =========================================================================
    # Conversational Chat (Curator Tab)
    # =========================================================================
    
    async def conversational_reply(
        self,
        message: str,
        notebook_id: Optional[str] = None,
        history: List[Dict[str, str]] = None
    ) -> str:
        """
        Handle a conversational message from the user in the Curator tab.
        The Curator has cross-notebook awareness and can synthesize, advise,
        play devil's advocate, and discuss research strategy.
        """
        history = history or []

        # Intent detection: morning brief recall
        msg_lower = message.lower().strip()

        # 2026-06-07 — direct shortcut for the anticipatory-draft pill.
        # The CuratorPanel sends `'show draft'` / `'discard draft'` straight
        # through this conversational endpoint, which previously fell
        # through to the generic LLM clarifier. Match the keyword and route
        # to the brain directly, mirroring the `_stream_curator` intent
        # handlers in chat.py.
        draft_show_triggers = (
            "show draft", "show me the draft", "open the draft",
            "what did you draft", "show the draft", "view draft",
        )
        draft_discard_triggers = (
            "discard draft", "discard the draft", "trash that draft",
            "don't want that draft", "no thanks on the draft", "reject draft",
        )
        if any(trigger in msg_lower for trigger in draft_show_triggers) and notebook_id:
            try:
                from services.curator_brain import curator_brain
                draft = curator_brain.get_latest_unconsumed_draft(notebook_id)
                if not draft:
                    return (
                        "No pending draft for this notebook. Curator pre-drafts "
                        "Studio content for notebooks with ≥15 sources, a stable "
                        "thesis, and no recent Studio output — yours might not "
                        "qualify yet."
                    )
                curator_brain.mark_draft_consumed(draft["id"])
                return (
                    f"Here's the draft I prepared (**{draft['kind']}**):\n\n"
                    f"---\n\n{draft['content_markdown']}\n\n---\n\n"
                    f"Say *@curator discard draft* if it's not useful — "
                    f"I'll back off on this notebook for a couple weeks."
                )
            except Exception as _e:
                logger.debug(f"[curator.conversational_reply] show_draft shortcut failed: {_e}")
                return f"Couldn't fetch the draft: {_e}"
        if any(trigger in msg_lower for trigger in draft_discard_triggers) and notebook_id:
            try:
                from services.curator_brain import curator_brain
                draft = curator_brain.get_latest_unconsumed_draft(notebook_id) or curator_brain.get_latest_draft(notebook_id)
                if not draft:
                    return "No recent draft for this notebook."
                curator_brain.mark_draft_discarded(draft["id"])
                return (
                    "Discarded. I won't draft for this notebook for the next "
                    "14 days — say *@curator show draft* again after that."
                )
            except Exception as _e:
                logger.debug(f"[curator.conversational_reply] discard_draft shortcut failed: {_e}")
                return f"Couldn't discard the draft: {_e}"

        brief_triggers = [
            "morning brief", "show brief", "show me the brief",
            "today's brief", "todays brief", "daily brief",
            "what did i miss", "what happened", "catch me up",
            "recap", "show the morning brief", "display the morning brief",
            "display morning brief", "recall brief",
        ]
        if any(trigger in msg_lower for trigger in brief_triggers):
            try:
                import json
                from pathlib import Path
                from services.event_logger import event_logger
                
                brief_dir = Path(event_logger.data_dir) / "memory"
                today_str = datetime.utcnow().strftime("%Y-%m-%d")
                brief_file = brief_dir / f"morning_brief_{today_str}.json"
                
                if not brief_file.exists():
                    brief_files = sorted(brief_dir.glob("morning_brief_*.json"), reverse=True)
                    if brief_files:
                        brief_file = brief_files[0]
                
                if brief_file.exists():
                    brief = json.loads(brief_file.read_text())
                    narrative = brief.get("narrative", "")
                    if narrative:
                        brief_date_raw = brief_file.stem.replace("morning_brief_", "")
                        try:
                            from datetime import datetime as _dt
                            brief_date = _dt.strptime(brief_date_raw, "%Y-%m-%d").strftime("%B %d, %Y")
                        except Exception:
                            brief_date = brief_date_raw
                        return f"Here's your brief from **{brief_date}**:\n\n---\n\n{narrative}\n\n---\n*Want me to dig deeper into any of these topics?*"
                    else:
                        # Fallback: reconstruct from notebook data
                        notebooks = brief.get("notebooks", [])
                        if notebooks:
                            try:
                                from datetime import datetime as _dt
                                _fd = _dt.strptime(brief_file.stem.replace('morning_brief_', ''), "%Y-%m-%d").strftime("%B %d, %Y")
                            except Exception:
                                _fd = brief_file.stem.replace('morning_brief_', '')
                            lines = [f"Here's your brief from **{_fd}**:\n"]
                            for nb in notebooks:
                                parts = []
                                added = nb.get('items_added', 0)
                                if added > 0:
                                    parts.append(f"{added} new source{'s' if added != 1 else ''}")
                                interactions = nb.get('interactions_since', 0)
                                if interactions > 0:
                                    parts.append(f"{interactions} interaction{'s' if interactions != 1 else ''}")
                                pending = nb.get('pending_approval', 0)
                                if pending > 0:
                                    parts.append(f"{pending} pending review")
                                summary = ", ".join(parts) if parts else "no recent activity"
                                lines.append(f"**{nb.get('name', 'Notebook')}**: {summary}")
                                for story in (nb.get('recent_stories') or [])[:3]:
                                    lines.append(f"  - \"{story.get('title', '')}\"")
                            return "\n".join(lines) + "\n\n---\n*Want me to dig deeper into any of these topics?*"
                
                return "I don't have a saved morning brief yet. I'll generate one next time you open LocalBook after being away!"
            except Exception as e:
                logger.error(f"Morning brief recall failed: {e}")
        
        # Build context from all notebooks
        notebooks = await notebook_store.list()
        notebook_context = ""
        if notebooks:
            nb_lines = []
            for nb in notebooks[:10]:
                nb_lines.append(f"- {nb.get('name', nb.get('title', 'Untitled'))} (id: {nb['id'][:8]}...)")
            notebook_context = f"Available notebooks:\n" + "\n".join(nb_lines)
        
        # If a specific notebook is referenced, search it for context
        search_context = ""
        if notebook_id:
            try:
                results = memory_store.search_archival_memory(
                    query=message,
                    namespace=AgentNamespace.COLLECTOR,
                    notebook_id=notebook_id,
                    limit=5
                )
                if results:
                    search_context = "\nRelevant content from current notebook:\n" + "\n".join(
                        f"- {r.entry.content[:200]}" for r in results
                    )
            except Exception as _e:
                logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        # Curator Phase 3a: mental-model context injection. When the
        # current notebook has an inferred mental model with reasonable
        # confidence, surface it so the curator's reply can lean on
        # what we already understand about the user's project. Empty /
        # low-confidence / model-missing cases fall through silently.
        mental_model_context = ""
        if notebook_id:
            try:
                from services.curator_brain import curator_brain as _cb
                _mm = _cb.get_mental_model(notebook_id)
                # Phase 3b hotfix: dropped the >0.3 confidence floor to
                # match the stance scorer. A low-confidence inferred
                # mental model is still useful context for chat replies.
                if (
                    _mm
                    and (_mm.get("thesis") or _mm.get("stage"))
                ):
                    _lines = []
                    if _mm.get("thesis"):
                        _lines.append(f"  - thesis: {_mm['thesis']}")
                    if _mm.get("stage"):
                        _lines.append(f"  - stage: {_mm['stage']}")
                    if _mm.get("blocked_on"):
                        _lines.append(f"  - blocked_on: {_mm['blocked_on']}")
                    if _mm.get("recent_focus"):
                        _lines.append(f"  - recent_focus: {_mm['recent_focus']}")
                    if _mm.get("goals"):
                        _goals = ", ".join(_mm["goals"][:3])
                        _lines.append(f"  - goals: {_goals}")
                    if _lines:
                        mental_model_context = (
                            "\nMental model for this notebook (use as context, do not repeat verbatim):\n"
                            + "\n".join(_lines)
                        )
            except Exception as _e:
                logger.debug(f"[curator] mental_model context fetch: {_e}")

        # Curator Phase 3c: ambient dissent context. Injects top
        # contradicting sources into the system prompt; LLM is
        # instructed to mention them ONLY if relevant to the user's
        # question. Gated by nag budget. The pending overwatch aside
        # (event-bus triggered) is surfaced separately by _stream_curator
        # via curator_aside, not here.
        dissent_context = ""
        if notebook_id:
            try:
                from services.curator_brain import curator_brain as _cb
                # Dissent in chat is medium priority — relevant to user query
                # but not urgent enough to bypass the daily cap.
                if _cb.can_fire_nag("dissent_ambient_in_chat", notebook_id, priority="medium"):
                    dissenters = _cb.get_dissenting_sources(notebook_id, limit=2)
                    if dissenters:
                        # Best-effort: attach source titles for readability.
                        from storage.source_store import source_store as _ss
                        _dlines = []
                        for d in dissenters:
                            title = d.get("source_id")
                            try:
                                src = await _ss.get(d["source_id"])
                                if src:
                                    title = (
                                        src.get("filename") or src.get("title")
                                        or src.get("url") or title
                                    )
                            except Exception:
                                pass
                            _dlines.append(
                                f"  - \"{str(title)[:100]}\": {d.get('rationale', '')[:200]}"
                            )
                        if _dlines:
                            dissent_context = (
                                "\nDissenting evidence in this notebook "
                                "(mention ONLY if the user's question touches the thesis; "
                                "stay silent otherwise — do not force-surface this):\n"
                                + "\n".join(_dlines)
                            )
                            _cb.record_nag("dissent_ambient_in_chat", notebook_id=notebook_id)
            except Exception as _e:
                logger.debug(f"[curator] dissent context fetch: {_e}")

        # Search across ALL notebooks for cross-references (PARALLEL)
        cross_context = ""
        if notebooks and len(notebooks) > 1:
            try:
                import asyncio
                other_nbs = [nb for nb in notebooks[:5] if nb["id"] != notebook_id]
                
                async def _search_nb(nb):
                    return nb, await asyncio.to_thread(
                        memory_store.search_archival_memory,
                        query=message,
                        namespace=AgentNamespace.COLLECTOR,
                        notebook_id=nb["id"],
                        cross_notebook=True,
                        limit=3
                    )
                
                nb_results = await asyncio.gather(
                    *[_search_nb(nb) for nb in other_nbs],
                    return_exceptions=True
                )
                for item in nb_results:
                    if isinstance(item, Exception):
                        continue
                    nb, results = item
                    for r in results:
                        if r.combined_score > 0.3:
                            nb_name = nb.get("name", nb.get("title", "Untitled"))
                            cross_context += f"\n- [{nb_name}]: {r.entry.content[:200]}"
            except Exception as _e:
                logger.debug(f"[curator] {type(_e).__name__}: {_e}")
        
        if cross_context:
            cross_context = f"\nCross-notebook connections:\n{cross_context}"
        
        # Build conversation history for LLM
        history_text = ""
        if history:
            for msg in history[-6:]:  # Last 6 messages
                role = msg.get("role", "user")
                content = msg.get("content", "")[:500]
                history_text += f"\n{role.upper()}: {content}"
        
        # Get user profile for personalization
        try:
            from api.settings import get_user_profile_sync, build_user_context
            user_profile = get_user_profile_sync()
            user_context = build_user_context(user_profile)
        except Exception:
            user_context = ""
        
        # Pull core memory for deeper user awareness (ReMe integration)
        core_memory_block = ""
        try:
            core_memory = memory_store.load_core_memory()
            core_memory_block = core_memory.to_prompt_block()
        except Exception as _e:
            logger.warning(f"[curator] {type(_e).__name__}: {_e}")
        
        system_prompt = f"""You are {self.name}, the Curator of a research system called LocalBook.
Your personality: {self.personality}

Your role:
- You oversee ALL notebooks and have cross-notebook awareness
- You can synthesize information across research areas
- You can play devil's advocate and find counterarguments
- You advise on research strategy and identify gaps
- You are a guide and advisor, not a search engine

{user_context}

{core_memory_block}

{notebook_context}
{search_context}
{cross_context}
{mental_model_context}
{dissent_context}

Rules:
- Be conversational and concise (2-4 sentences typical)
- Proactively mention cross-notebook connections when relevant
- If asked about something specific, search your knowledge
- If you don't have the information, say so honestly
- Sign off naturally, no forced personality"""

        prompt = message
        if history_text:
            prompt = f"Conversation so far:{history_text}\n\nUSER: {message}"
        
        try:
            response = await ollama_service.generate(
                prompt=prompt,
                system=system_prompt,
                model=settings.ollama_model,
                temperature=0.5
            )
            reply_text = response.get("response", "I'm having trouble processing that right now.")
            # Curator Phase 1: emit observability event so the brain's
            # consumer loop knows the user just talked to the curator.
            try:
                from services.curator_event_bus import event_bus
                event_bus.emit_now(
                    actor="@curator",
                    action="conversational_reply",
                    notebook_id=notebook_id,
                    payload={
                        "message_chars": len(message),
                        "reply_chars": len(reply_text),
                        "had_cross_context": bool(cross_context),
                    },
                    outcome="success",
                )
            except Exception as _e:
                pass
            return reply_text
        except Exception as e:
            logger.error(f"Curator chat failed: {e}")
            try:
                from services.curator_event_bus import event_bus
                event_bus.emit_now(
                    actor="@curator",
                    action="conversational_reply",
                    notebook_id=notebook_id,
                    payload={"error": str(e)[:200]},
                    outcome="failed",
                )
            except Exception:
                pass
            return "I'm experiencing a technical issue. Please try again."
    
    # =========================================================================
    # Overwatch — Ambient cross-notebook awareness in regular chat
    # =========================================================================

    async def maybe_fire_anticipatory_draft(
        self,
        notebook_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Pre-draft a Studio output for a mature notebook (Curator Phase 6a).

        Gating (all must be true):
          - Notebook has ≥15 sources (mature enough to draft from)
          - Mental model exists with a thesis (something to write toward)
          - Mental model is stable (last_inferred_at OR last_user_edit_at
            ≥ 3 days ago — thesis isn't churning)
          - No anticipatory draft active for this notebook (existing
            unconsumed draft would just get clobbered)
          - Not in discard cool-off (user hasn't recently rejected one)
          - `can_fire_nag('anticipatory_draft', nb, priority='low')` allows

        Returns the new draft dict on success, None on skip/failure.
        """
        try:
            from services.curator_brain import curator_brain as _cb
            from storage.source_store import source_store
            from datetime import timedelta

            # Gate 1: nag budget (low priority — chatty surface)
            if not _cb.can_fire_nag("anticipatory_draft", notebook_id, priority="low"):
                logger.debug(
                    f"[curator] anticipatory_draft({notebook_id[:8]}): "
                    f"nag budget blocked"
                )
                return None

            # Gate 2: not in discard cool-off
            if _cb.is_drafting_suppressed(notebook_id):
                logger.debug(
                    f"[curator] anticipatory_draft({notebook_id[:8]}): "
                    f"in 14-day discard cool-off"
                )
                return None

            # Gate 3: no existing unconsumed draft
            existing = _cb.get_latest_unconsumed_draft(notebook_id)
            if existing:
                logger.debug(
                    f"[curator] anticipatory_draft({notebook_id[:8]}): "
                    f"existing unconsumed draft #{existing['id']}"
                )
                return None

            # Gate 4: source count ≥ 15
            sources = await source_store.list(notebook_id)
            if len(sources) < 15:
                return None

            # Gate 5: mental model exists with a thesis
            mm = _cb.get_mental_model(notebook_id)
            if not mm:
                return None
            thesis = (mm.get("thesis") or "").strip()
            if not thesis:
                return None

            # Gate 6: mental model is STABLE (≥3 days since last change)
            now = datetime.utcnow()
            three_days_ago = now - timedelta(days=3)
            stable = True
            for ts_field in ("last_user_edit_at", "last_inferred_at"):
                ts_str = mm.get(ts_field)
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if ts > three_days_ago:
                            stable = False
                            break
                    except (ValueError, TypeError):
                        pass
            if not stable:
                logger.debug(
                    f"[curator] anticipatory_draft({notebook_id[:8]}): "
                    f"mental model still settling (changed in last 3d)"
                )
                return None

            # Pick kind based on stage. Default = executive_brief.
            stage = (mm.get("stage") or "").strip().lower()
            if stage in ("exploration", "gathering"):
                kind = "mind_map"
            else:
                kind = "executive_brief"

            # Generate the draft content via the same fast model path
            # the brief synthesizer uses — keeps cost low and matches
            # the curator's voice.
            recent_sources = sources[:15]
            source_titles = "\n".join(
                f"  - {(s.get('filename') or s.get('title') or s.get('url') or 'Untitled')[:120]}"
                for s in recent_sources
            )
            voice_block = VOICE_PROMPTS.get(self.narrative_voice, "")
            prompt = (
                f"{voice_block}\n\n"
                f"You are pre-drafting a {kind} for a researcher who has been working on:\n"
                f"Thesis: {thesis}\n"
                f"Stage: {stage or 'unspecified'}\n"
                f"Recent focus: {mm.get('recent_focus') or 'unspecified'}\n"
                f"Blocked on: {mm.get('blocked_on') or 'nothing in particular'}\n"
                f"\n"
                f"They have {len(sources)} sources collected. Recent ones:\n"
                f"{source_titles}\n"
                f"\n"
                f"Draft a substantive markdown document. For an executive_brief: "
                f"executive summary, key findings, open questions, suggested next step. "
                f"For a mind_map: a structured concept map in markdown with the thesis "
                f"at the root and 5-8 child branches reflecting the sub-themes. "
                f"Keep it tight (under 800 words). Use H2/H3 headings.\n"
                f"\n"
                f"Output ONLY the markdown — no preamble, no commentary."
            )

            try:
                result = await ollama_service.generate(
                    prompt=prompt,
                    system=f"You are {self.name}, drafting pre-emptive Studio content.",
                    model=settings.ollama_model,  # main model — quality matters here
                    temperature=0.5,
                    timeout=120.0,
                )
                content = (result.get("response") or "").strip()
            except Exception as e:
                logger.warning(f"[curator] anticipatory_draft LLM call failed: {e}")
                return None

            if not content or len(content) < 200:
                logger.debug(
                    f"[curator] anticipatory_draft({notebook_id[:8]}): "
                    f"content too short ({len(content)} chars), skipping"
                )
                return None

            # For mind_map kind, prepend an actual Mermaid mindmap visual
            # built from digest data (themes + entities). The frontend
            # MarkdownArtifactRenderer dispatches mermaid code-fences to
            # MermaidRenderer, so the user sees the diagram alongside the
            # prose instead of just prose describing one.
            if kind == "mind_map":
                try:
                    import re
                    digest = _cb.get_digest(notebook_id) or {}
                    themes_raw = digest.get("key_themes") or "[]"
                    entities_raw = digest.get("key_entities") or "[]"
                    themes = json.loads(themes_raw) if isinstance(themes_raw, str) else (themes_raw or [])
                    entities = json.loads(entities_raw) if isinstance(entities_raw, str) else (entities_raw or [])

                    def _mm_label(s: str, n: int = 60) -> str:
                        # Mermaid mindmap node text is line-sensitive and
                        # chokes on parens/brackets. Keep it boring.
                        s = str(s or "").strip()
                        s = re.sub(r"[\(\)\[\]\{\}\"`]+", "", s)
                        s = re.sub(r"\s+", " ", s)
                        return s[:n].strip() or "—"

                    root_label = _mm_label(thesis, 80)
                    lines = ["mindmap", f"  root(({root_label}))"]
                    theme_list = [t for t in (themes or []) if t][:6]
                    entity_list = [e for e in (entities or []) if e][:6]

                    if theme_list:
                        lines.append("    Key themes")
                        for t in theme_list:
                            lines.append(f"      {_mm_label(t)}")
                    if entity_list:
                        lines.append("    Key entities")
                        for e in entity_list:
                            lines.append(f"      {_mm_label(e)}")
                    if not theme_list and not entity_list:
                        # Fall back to recent source titles so the mindmap
                        # is never empty when digest hasn't been built yet.
                        lines.append("    Recent sources")
                        for s in recent_sources[:5]:
                            title = (s.get("filename") or s.get("title") or s.get("url") or "Untitled")
                            lines.append(f"      {_mm_label(title)}")

                    mermaid_block = "```mermaid\n" + "\n".join(lines) + "\n```"
                    content = f"{mermaid_block}\n\n{content}"
                except Exception as e:
                    # Never block the draft on a visualization failure —
                    # the prose is still useful on its own.
                    logger.debug(
                        f"[curator] mind_map mermaid composition failed (non-fatal): {e}"
                    )

            # For executive_brief kind, prepend the Phase 13 notebook
            # dashboard HTML so the user lands on a structured overview
            # (cornerstone summary + themes/entities chips + activity grid
            # + consensus cards) rather than walls of prose. The ```html
            # fence (Phase 14, 2026-06-08) routes through
            # HtmlArtifactRenderer's Shadow DOM + DOMPurify strict.
            if kind == "executive_brief":
                try:
                    dashboard_html = await self.compose_notebook_dashboard_html(notebook_id)
                    if dashboard_html and len(dashboard_html) > 100:
                        content = f"```html\n{dashboard_html}\n```\n\n{content}"
                except Exception as e:
                    logger.debug(
                        f"[curator] executive_brief dashboard composition failed (non-fatal): {e}"
                    )

            # Persist + record nag fire so daily cap counts it
            draft_id = _cb.queue_draft(
                notebook_id=notebook_id,
                kind=kind,
                content_markdown=content,
                source_signal=f"stage={stage}; sources={len(sources)}",
            )
            _cb.record_nag(
                "anticipatory_draft",
                notebook_id=notebook_id,
                subject_id=str(draft_id) if draft_id else None,
            )
            logger.info(
                f"[curator] anticipatory_draft queued for notebook {notebook_id[:8]}: "
                f"kind={kind} chars={len(content)} id={draft_id}"
            )
            return _cb.get_latest_unconsumed_draft(notebook_id)
        except Exception as e:
            logger.warning(f"[curator] maybe_fire_anticipatory_draft failed: {e}")
            return None

    async def maybe_fire_dissent_overwatch(
        self,
        notebook_id: str,
        new_source_id: Optional[str] = None,
    ) -> Optional[str]:
        """Generate a dissent overwatch aside text if conditions are right.

        Curator Phase 3c (2026-05-13). Called by the event-bus consumer
        when a new stance scores as high-confidence contradicts. Returns
        a one-sentence aside text OR None.

        Fires when ALL of:
          - can_fire_nag('dissent_overwatch_aside', notebook_id) is True
          - notebook supporting count ≥ 5 (real consensus to dissent against)
          - at least 1 contradicting source with confidence > 0.6 exists

        On fire: records the nag, queues a pending aside on the brain so
        the next @curator chat reply surfaces it via curator_aside.
        """
        try:
            from services.curator_brain import curator_brain as _cb
            # Nag budget gate first — cheapest check.
            # Contradiction surfacing is HIGH priority — bypasses daily cap.
            # If the curator detects a contradiction in user-supported thesis,
            # that's worth surfacing even on a chatty day. Cool-off still applies.
            if not _cb.can_fire_nag("dissent_overwatch_aside", notebook_id, priority="high"):
                logger.debug(
                    f"[curator] maybe_fire_dissent_overwatch({notebook_id[:8]}): nag budget blocked"
                )
                return None

            counts = _cb.get_notebook_stance_counts(notebook_id)
            if counts.get("supports", 0) < 5:
                return None

            dissenters = _cb.get_dissenting_sources(notebook_id, limit=5)
            top = next((d for d in dissenters if (d.get("confidence") or 0) > 0.6), None)
            if not top:
                return None

            # Best-effort: get the source title for a friendlier aside.
            title = top.get("source_id") or "a source"
            try:
                from storage.source_store import source_store
                src = await source_store.get(top["source_id"])
                if src:
                    title = (
                        src.get("filename") or src.get("title") or src.get("url") or title
                    )
            except Exception:
                pass

            # If the trigger came from a specific newly-scored source, prefer that one.
            if new_source_id:
                new_match = next(
                    (d for d in dissenters if d.get("source_id") == new_source_id),
                    None,
                )
                if new_match and (new_match.get("confidence") or 0) > 0.6:
                    top = new_match
                    try:
                        from storage.source_store import source_store
                        src = await source_store.get(new_source_id)
                        if src:
                            title = (
                                src.get("filename") or src.get("title") or src.get("url") or title
                            )
                    except Exception:
                        pass

            aside = (
                f"Heads up — \"{str(title)[:120]}\" actually contradicts the notebook's thesis: "
                f"{top.get('rationale', '')[:200]}"
            )

            # Phase 14 (2026-06-08) — append a Mermaid quadrantChart so the
            # user sees this dissenter plotted against the notebook's
            # existing stance distribution. Renders via MarkdownArtifact-
            # Renderer's mermaid fence handler. Skipped silently on any
            # failure — prose aside still surfaces.
            try:
                import re as _re

                def _clean(s: str, n: int = 40) -> str:
                    s = _re.sub(r"[\[\]\"`:,]+", " ", str(s or ""))
                    s = _re.sub(r"\s+", " ", s).strip()
                    return s[:n] or "source"

                top_conf = float(top.get("confidence") or 0)
                # Stance is contradicting here; lower-right quadrant.
                # x-axis = confidence (0-1), y-axis = supports vs contradicts.
                new_point = (_clean(title), round(min(0.95, max(0.05, top_conf)), 2), 0.15)
                support_dots: List[tuple] = []
                # Plot up to 3 supporting sources as upper-area dots.
                try:
                    supports = _cb.get_supporting_sources(notebook_id, limit=3)
                except Exception:
                    supports = []
                for i, s in enumerate(supports or []):
                    sc = float(s.get("confidence") or 0.7)
                    sx = round(min(0.95, max(0.05, sc)), 2)
                    sy = round(0.75 + (i * 0.05), 2)
                    label = _clean(s.get("source_id") or f"src{i}", 28)
                    support_dots.append((label, sx, sy))

                lines = [
                    "quadrantChart",
                    "  title Stance vs confidence",
                    "  x-axis Low conf --> High conf",
                    "  y-axis Contradicts --> Supports",
                    "  quadrant-1 Strong support",
                    "  quadrant-2 Weak support",
                    "  quadrant-3 Weak contradiction",
                    "  quadrant-4 Strong contradiction",
                    f"  {new_point[0]}: [{new_point[1]}, {new_point[2]}]",
                ]
                for label, x, y in support_dots:
                    lines.append(f"  {label}: [{x}, {y}]")
                aside = aside + "\n\n```mermaid\n" + "\n".join(lines) + "\n```"
            except Exception as _e:
                logger.debug(f"[curator] dissent quadrant composition failed (non-fatal): {_e}")

            # Record + queue
            nag_id = _cb.record_nag(
                "dissent_overwatch_aside",
                notebook_id=notebook_id,
                subject_id=top.get("source_id"),
            )
            _cb.queue_pending_aside(
                notebook_id=notebook_id,
                kind="dissent",
                aside_text=aside,
                nag_id=nag_id,
            )
            logger.info(
                f"[curator] dissent overwatch queued for notebook {notebook_id[:8]}: {aside[:100]}"
            )
            return aside
        except Exception as e:
            logger.warning(f"[curator] maybe_fire_dissent_overwatch failed: {e}")
            return None

    async def generate_overwatch_aside(
        self,
        query: str,
        answer: str,
        notebook_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Phase D.1 (2026-05-22): trigger-driven only. No probabilistic fallback.

        Returns a dict {aside_text, nag_id, kind} ONLY when an explicit
        upstream trigger has queued one. Otherwise returns None — the user
        typed a regular question; we stay quiet.

        Fix #5 (2026-05-23): now returns the full payload (including nag_id)
        instead of just the aside text, so the UI can wire thumbs feedback
        to POST /curator/asides/{nag_id}/thumbs.

        Surfacing channels (all upstream signals that emit pending_asides
        via the event bus):
          - contradiction (Phase 3b/3c: stance_scored 'contradicts' + high conf)
          - connection (Phase 5: connection_discovered with strength > 0.7)
          - plan_completed (Phase 5: plan_completed with user_visible plans)
          - mental_model_shift (Phase 3a: pending — added when emitted)

        NOT surfaced here (intentionally moved away from chat asides):
          - stagnation: now lives in the Collector panel only (Phase A.1)
          - generic cross-notebook search: required the user to suspect a
            connection exists; if it's genuinely useful, surface it via
            @curator instead

        Pre-D this method ran a brain digest LLM call + a parallel vector
        search across every other notebook + another LLM to decide if any
        of it was useful, on EVERY chat reply. Two LLM hops + N-notebook
        archival scans for a sidebar note the user usually didn't need.
        """
        try:
            from services.curator_brain import curator_brain as _cb
            pending = _cb.consume_pending_aside(notebook_id)
            if pending and pending.get("aside_text"):
                logger.debug(
                    f"[curator] generate_overwatch_aside({notebook_id[:8]}): "
                    f"surfacing pending aside (kind={pending.get('kind')})"
                )
                return {
                    "aside_text": pending["aside_text"],
                    "nag_id": pending.get("nag_id"),
                    "kind": pending.get("kind"),
                }
        except Exception as _e:
            logger.debug(f"[curator] pending aside consume failed: {_e}")

        # No pending aside = nothing event-driven to say. Stay quiet.
        return None
    
    # =========================================================================
    # Source Discovery Validation - Curator validates discovered sources
    # =========================================================================
    
    async def validate_discovered_sources(
        self,
        notebook_id: str,
        intent: str,
        discovered_sources: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Curator reviews discovered sources and provides recommendations.
        
        For each source:
        - Validates relevance to intent
        - Assigns recommendation (auto_approve, suggest, skip)
        - Provides reasoning for user review
        
        Args:
            notebook_id: The notebook these sources are for
            intent: The user's stated intent
            discovered_sources: List of sources from SourceDiscoveryService
            
        Returns:
            List of sources with curator recommendations added
        """
        logger.info(f"Curator validating {len(discovered_sources)} discovered sources for notebook {notebook_id}")
        
        validated_sources = []
        
        for source in discovered_sources:
            validated = await self._validate_single_source(source, intent)
            validated_sources.append(validated)
        
        # Sort by recommendation priority: auto_approve first, then suggest, then skip
        priority_order = {"auto_approve": 0, "suggest": 1, "skip": 2}
        validated_sources.sort(
            key=lambda s: priority_order.get(s.get("curator_recommendation", "skip"), 3)
        )
        
        # Store validation in memory for learning (non-fatal if it fails)
        try:
            entry = ArchivalMemoryEntry(
                content=f"Source discovery for: {intent}\nValidated {len(validated_sources)} sources",
                content_type="source_discovery_validation",
                source_type=MemorySourceType.SYSTEM,
                source_notebook_id=notebook_id,
                topics=["source_discovery", "validation"],
                importance=MemoryImportance.LOW,
            )
            memory_store.add_archival_memory(entry, namespace=AgentNamespace.CURATOR)
        except Exception as mem_err:
            logger.warning(f"Failed to store discovery validation in memory (non-fatal): {mem_err}")
        
        return validated_sources
    
    async def _validate_single_source(
        self,
        source: Dict[str, Any],
        intent: str
    ) -> Dict[str, Any]:
        """Validate a single discovered source against intent"""
        source_name = source.get("name", "Unknown")
        source_type = source.get("source_type", "unknown")
        source_confidence = source.get("confidence", 0.5)
        source_desc = source.get("description", "")
        
        # High confidence sources from discovery engine get auto-approved
        if source_confidence >= 0.85 and source.get("auto_approve", False):
            source["curator_recommendation"] = "auto_approve"
            source["curator_reason"] = "High relevance source for your research"
            return source
        
        # Use LLM for medium confidence sources
        if source_confidence >= 0.5:
            try:
                prompt = f"""You are {self.name}, validating a source for research.

Research Intent: {intent}

Source to evaluate:
- Name: {source_name}
- Type: {source_type}
- Description: {source_desc}

Should this source be included? Consider:
1. Is it directly relevant to the research intent?
2. Is it a reputable/useful source type?

Respond with JSON only:
{{
    "recommendation": "suggest" or "skip",
    "reason": "one sentence explanation"
}}"""

                response = await ollama_service.generate(
                    prompt=prompt,
                    model=settings.ollama_fast_model,
                    temperature=0.3
                )
                
                text = response.get("response", "")
                json_start = text.find("{")
                json_end = text.rfind("}") + 1
                
                if json_start >= 0 and json_end > json_start:
                    result = json.loads(text[json_start:json_end])
                    source["curator_recommendation"] = result.get("recommendation", "suggest")
                    source["curator_reason"] = result.get("reason", "Potentially relevant source")
                    return source
            except Exception as e:
                logger.error(f"Source validation LLM failed: {e}")
        
        # Low confidence or validation failed - suggest with caveat
        source["curator_recommendation"] = "suggest" if source_confidence >= 0.4 else "skip"
        source["curator_reason"] = "Lower confidence - review before including"
        return source
    
    async def learn_from_source_decisions(
        self,
        notebook_id: str,
        approved_sources: List[Dict[str, Any]],
        rejected_sources: List[Dict[str, Any]]
    ) -> None:
        """
        Learn from user's source approval decisions.
        Improves future discovery recommendations.
        """
        # Store approval patterns in memory
        if approved_sources:
            approved_types = [s.get("source_type") for s in approved_sources]
            memory_store.record_user_signal(
                notebook_id=notebook_id,
                signal_type="source_approval",
                metadata={
                    "approved_count": len(approved_sources),
                    "source_types": approved_types
                }
            )
        
        if rejected_sources:
            rejected_types = [s.get("source_type") for s in rejected_sources]
            memory_store.record_user_signal(
                notebook_id=notebook_id,
                signal_type="source_rejection",
                metadata={
                    "rejected_count": len(rejected_sources),
                    "source_types": rejected_types
                }
            )

    # ==================================================================
    # Phase 10 — HTML brief dashboard + deep-read trigger + skip-digest.
    # ==================================================================

    # Hardcoded defaults — promote to CuratorConfig when the user-tuning
    # surface lands.
    _AUTO_DEEP_READ = True
    _MAX_DEEP_READS_PER_BRIEF = 2

    def _fire_deep_reads_for_clusters(self, clusters: List[Any]) -> List[Dict[str, Any]]:
        """For the top consensus clusters, fire research_engine.deep_dive
        as fire-and-forget. Returns a list of {topic_label, notebook_id,
        query, cluster_id} dicts describing what was triggered.

        Honors `_AUTO_DEEP_READ` switch + max-per-brief cap. Never blocks
        the brief assembly path; any per-cluster failure is debug-logged.
        """
        triggered: List[Dict[str, Any]] = []
        if not self._AUTO_DEEP_READ or not clusters:
            return triggered
        try:
            from services.research_engine import research_engine
            from services.curator_event_bus import event_bus
        except Exception as e:
            logger.debug(f"[curator.fire_deep_reads] import failed: {e}")
            return triggered

        for cl in clusters[: self._MAX_DEEP_READS_PER_BRIEF]:
            try:
                if not cl.primary_notebook_id or not cl.topic_label:
                    continue
                query = cl.topic_label
                safe_create_task(
                    research_engine.deep_dive(
                        query=query,
                        notebook_id=cl.primary_notebook_id,
                    )
                )
                event_bus.emit_now(
                    actor="@curator",
                    action="deep_read_triggered",
                    notebook_id=cl.primary_notebook_id,
                    payload={
                        "cluster_id": cl.cluster_id,
                        "query": query,
                        "cluster_size": cl.size,
                    },
                    outcome="success",
                )
                triggered.append({
                    "cluster_id": cl.cluster_id,
                    "topic_label": cl.topic_label,
                    "notebook_id": cl.primary_notebook_id,
                    "query": query,
                })
            except Exception as e:
                logger.debug(f"[curator.fire_deep_reads] cluster fire failed: {e}")
        return triggered

    def _compose_brief_html(
        self,
        *,
        duration_str: str,
        summaries: List["NotebookSummary"],
        narrative: str,
        cross_insight: Optional[str],
        clusters: List[Any],
        deep_reads: List[Dict[str, Any]],
        total_recent_ingests: int,
    ) -> Optional[str]:
        """Compose the HTML dashboard variant of the morning brief.

        Server-side composition (not LLM-generated HTML) — for a layout
        this structured, deterministic assembly is more reliable than
        prompting gemma4 to produce dashboard HTML reliably. The LLM-
        authored prose (`narrative`) sits inside the dashboard wrapper.

        Strict mode: output uses the Tailwind subset that
        HtmlArtifactRenderer's Shadow DOM injects. No <script>, no inline
        styles requiring url(), no <img>.
        """
        import html as _html

        # Skip-digest path: quiet morning (P10.D). The check happens here
        # so the HTML output stays in lockstep with the skip-digest
        # decision in the markdown narrative.
        meaningful = any(
            nb.items_added > 0 or nb.pending_approval > 0
            or nb.collection_items_approved > 0 or nb.highlights_since > 0
            or nb.notes_created > 0 or nb.interactions_since > 0
            or nb.emerging_topics or nb.recent_stories
            for nb in summaries
        )
        if not clusters and total_recent_ingests < 5 and not meaningful:
            return (
                '<div class="lb-html-artifact p-6 max-w-2xl mx-auto">'
                '<h3 class="text-lg font-semibold text-gray-800 mb-2">Quiet morning</h3>'
                f'<p class="text-sm text-gray-600">'
                f'{total_recent_ingests} items came in across your notebooks. '
                'Nothing converging yet — your notebooks are where you left them.'
                '</p>'
                '</div>'
            )

        parts: List[str] = []
        parts.append('<div class="lb-html-artifact p-4 max-w-3xl mx-auto">')

        # Cornerstone / header
        parts.append('<div class="mb-6">')
        parts.append(
            f'<p class="text-xs uppercase tracking-wide text-gray-500 mb-1">'
            f'You\'ve been away for {_html.escape(duration_str)}'
            '</p>'
        )
        if cross_insight:
            parts.append(
                '<p class="text-sm text-gray-700 italic">'
                + _html.escape(cross_insight)
                + '</p>'
            )
        parts.append('</div>')

        # Narrative prose intentionally omitted from the HTML dashboard
        # (K3, 2026-06-09). The LLM-generated narrative often contains
        # markdown (### headings, **bold**) which renders as raw text
        # when shoved through html.escape + <p> wrapping. CuratorPanel
        # now renders the narrative as a separate Markdown artifact
        # below the dashboard so heading styles + emphasis work properly.

        # Consensus clusters
        if clusters:
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">What\'s converging</h3>'
            )
            parts.append('<div class="grid grid-cols-2 gap-3 mb-6">')
            for cl in clusters[:6]:
                top_senders = sorted(
                    (cl.sender_counts or {}).items(), key=lambda x: x[1], reverse=True
                )[:4]
                notebooks_count = len(cl.notebook_counts or {})

                # Phase 14 (2026-06-08) — per-cluster sender share bars.
                # CSS-only (no scripts) so it renders inside the strict
                # HtmlArtifactRenderer Shadow DOM. Bars are normalized to
                # the strongest sender in the cluster so visual weight
                # tracks agenda concentration. Empty senders → fall back
                # to "various sources" prose.
                # Bucket continuous pct → twelfths because the strict
                # HtmlArtifactRenderer Shadow DOM strips `style` attributes
                # via DOMPurify. Tailwind subset (export_assets +
                # htmlArtifactTailwindSubset) defines w-1/12 .. w-11/12 so
                # bars render proportionally with class names only. The CSS
                # selectors escape the slash; the class attribute emits a
                # literal `/`.
                def _bucket_class(p: int) -> str:
                    twelfths = max(1, min(12, round(p / 8.333)))
                    return "w-full" if twelfths >= 12 else f"w-{twelfths}/12"
                bars_html = ""
                max_n = max((n for _, n in top_senders if n), default=0)
                if max_n > 0:
                    rows = []
                    for s, n in top_senders:
                        if not s:
                            continue
                        pct = max(8, int((n / max_n) * 100))
                        width_cls = _bucket_class(pct)
                        rows.append(
                            '<div class="flex items-center gap-2 mb-1">'
                            f'<div class="text-xs text-gray-700 w-24 truncate" title="{_html.escape(s)}">{_html.escape(s)}</div>'
                            '<div class="flex-1 bg-blue-100 rounded h-2 overflow-hidden">'
                            f'<div class="bg-blue-500 h-2 rounded {width_cls}"></div>'
                            '</div>'
                            f'<div class="text-xs text-gray-600 w-6 text-right">{n}</div>'
                            '</div>'
                        )
                    bars_html = "".join(rows)
                else:
                    bars_html = '<p class="text-xs text-gray-500 italic">various sources</p>'

                parts.append(
                    '<div class="rounded-lg border border-blue-200 bg-blue-50 p-3">'
                    f'<p class="text-xs uppercase tracking-wide text-blue-700 mb-1">'
                    f'{cl.size} sources across {notebooks_count} notebook'
                    f'{"s" if notebooks_count != 1 else ""}</p>'
                    f'<p class="text-sm font-medium text-gray-800 mb-2">'
                    f'{_html.escape(cl.topic_label or "(unlabeled)")}</p>'
                    f'<div class="mt-2">{bars_html}</div>'
                    '</div>'
                )
            parts.append('</div>')

        # Deep reads triggered
        if deep_reads:
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">Deep reads triggered</h3>'
            )
            parts.append('<ul class="mb-6">')
            for dr in deep_reads:
                parts.append(
                    f'<li class="text-sm text-gray-700">'
                    f'Researching <strong>{_html.escape(dr.get("topic_label", "topic"))}</strong> '
                    '— will surface results in the notebook shortly.'
                    '</li>'
                )
            parts.append('</ul>')

        # Per-notebook activity
        active = [nb for nb in summaries if (
            nb.items_added or nb.pending_approval or nb.notes_created
            or nb.highlights_since or nb.interactions_since
            or nb.collection_items_approved or nb.emerging_topics or nb.recent_stories
        )]
        if active:
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">Today across your notebooks</h3>'
            )
            parts.append('<div class="flex flex-col gap-2">')
            for nb in active[:8]:
                bits: List[str] = []
                if nb.items_added:
                    bits.append(f"{nb.items_added} new")
                if nb.pending_approval:
                    bits.append(f"{nb.pending_approval} pending")
                if nb.notes_created:
                    bits.append(f"{nb.notes_created} note{'s' if nb.notes_created != 1 else ''}")
                if nb.highlights_since:
                    bits.append(f"{nb.highlights_since} highlight{'s' if nb.highlights_since != 1 else ''}")
                summary_bits = " · ".join(bits) if bits else "activity"
                parts.append(
                    '<div class="rounded-md border border-gray-200 bg-white p-3">'
                    f'<p class="text-sm font-medium text-gray-800">{_html.escape(nb.name)}</p>'
                    f'<p class="text-xs text-gray-500">{summary_bits}</p>'
                    '</div>'
                )
            parts.append('</div>')

        parts.append('</div>')
        return "".join(parts)


    def _compose_weekly_wrap_html(
        self,
        *,
        week_start: str,
        week_end: str,
        summaries: List["NotebookSummary"],
        narrative: str,
        cross_insight: Optional[str],
        total_sources: int,
        total_collector: int,
        total_user: int,
        total_convos: int,
        total_audio: int,
        total_docs: int,
    ) -> Optional[str]:
        """Server-composed HTML dashboard for the weekly wrap-up.

        Same Tailwind subset / strict-HTML constraints as Phase 10's
        morning brief composer — no scripts, no inline styles, no <img>.
        Renders via the Phase 14 ```html fence handler in chat replies.
        """
        import html as _html

        parts: List[str] = []
        parts.append('<div class="lb-html-artifact p-4 max-w-3xl mx-auto">')

        # Header
        parts.append(
            '<div class="mb-6">'
            '<p class="text-xs uppercase tracking-wide text-gray-500 mb-1">Weekly wrap-up</p>'
            f'<p class="text-lg font-semibold text-gray-900 mb-1">{_html.escape(week_start)} → {_html.escape(week_end)}</p>'
            '</div>'
        )

        if cross_insight:
            parts.append(
                '<p class="text-sm text-gray-700 italic mb-4">'
                + _html.escape(cross_insight)
                + '</p>'
            )

        # Aggregate stats grid — 6 tiles, 3 columns
        stats = [
            ("Sources added", total_sources),
            ("Collected", total_collector),
            ("Added by you", total_user),
            ("Conversations", total_convos),
            ("Audio created", total_audio),
            ("Docs created", total_docs),
        ]
        parts.append(
            '<h3 class="text-base font-semibold text-gray-800 mb-2">This week at a glance</h3>'
            '<div class="grid grid-cols-3 gap-2 mb-6">'
        )
        for label, value in stats:
            parts.append(
                '<div class="rounded-lg border border-gray-200 bg-gray-50 p-3 text-center">'
                f'<p class="text-xl font-semibold text-gray-900">{value}</p>'
                f'<p class="text-xs text-gray-500 mt-1">{_html.escape(label)}</p>'
                '</div>'
            )
        parts.append('</div>')

        # Narrative (sanitized prose paragraphs)
        if narrative:
            paras = [p.strip() for p in narrative.split("\n\n") if p.strip()]
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">The story</h3>'
                '<div class="mb-6">'
            )
            for p in paras[:10]:
                parts.append(
                    '<p class="text-sm text-gray-800 mb-3">' + _html.escape(p) + '</p>'
                )
            parts.append('</div>')

        # Per-notebook activity
        active = [nb for nb in summaries if (
            nb.items_added or nb.notes_created or nb.highlights_since
            or nb.interactions_since or nb.collection_items_approved
            or nb.emerging_topics or nb.recent_stories
        )]
        if active:
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">Across your notebooks</h3>'
                '<div class="flex flex-col gap-2">'
            )
            for nb in active[:8]:
                bits: List[str] = []
                if nb.items_added:
                    bits.append(f"{nb.items_added} sources")
                if nb.notes_created:
                    bits.append(f"{nb.notes_created} note{'s' if nb.notes_created != 1 else ''}")
                if nb.highlights_since:
                    bits.append(f"{nb.highlights_since} highlight{'s' if nb.highlights_since != 1 else ''}")
                if nb.interactions_since:
                    bits.append(f"{nb.interactions_since} chat{'s' if nb.interactions_since != 1 else ''}")
                summary_bits = " · ".join(bits) if bits else "activity"
                parts.append(
                    '<div class="rounded-md border border-gray-200 bg-white p-3">'
                    f'<p class="text-sm font-medium text-gray-800">{_html.escape(nb.name)}</p>'
                    f'<p class="text-xs text-gray-500">{summary_bits}</p>'
                    '</div>'
                )
            parts.append('</div>')

        parts.append('</div>')
        return "".join(parts)

    # ==================================================================
    # Phase 13 — per-notebook dashboard (A1).
    # ==================================================================

    async def compose_notebook_dashboard_html(self, notebook_id: str) -> str:
        """Server-composed HTML overview for a single notebook.

        Reuses the same digest + activity pipeline that powers Phase 10's
        morning brief but scoped to one notebook. Includes consensus
        clusters from the last 7 days filtered to this notebook.
        """
        import html as _html
        from datetime import timedelta as _td

        def esc(s: Any) -> str:
            return _html.escape(str(s or ""), quote=True)

        # 1. Digest
        try:
            from services.curator_brain import curator_brain
            digest = curator_brain.get_digest(notebook_id) or {}
        except Exception:
            digest = {}

        # 2. Notebook record (for name)
        notebook = await notebook_store.get(notebook_id) or {}
        nb_name = notebook.get("title", "Notebook")

        # 3. Recent activity (last 7 days)
        try:
            from storage.source_store import source_store as _src_store
            all_sources_by_nb = await _src_store.list_all()
            sources_for_nb = all_sources_by_nb.get(notebook_id, [])
        except Exception:
            sources_for_nb = []
        since = datetime.utcnow() - _td(days=7)
        try:
            activity = await self._get_activity_since(notebook_id, since, sources_for_nb)
        except Exception as e:
            logger.debug(f"[curator.dashboard] activity fetch failed: {e}")
            activity = {}

        # 4. Filtered consensus
        consensus: List[Any] = []
        try:
            from services.consensus_detector import detect_consensus
            all_clusters = await detect_consensus(since_days=7, min_cluster_size=2)
            consensus = [c for c in all_clusters if c.primary_notebook_id == notebook_id]
        except Exception as e:
            logger.debug(f"[curator.dashboard] consensus fetch failed: {e}")

        # Decode key_themes / key_entities (stored as JSON strings)
        def _decode_json_list(s: Any) -> List[str]:
            if not s:
                return []
            if isinstance(s, list):
                return [str(x) for x in s]
            try:
                v = json.loads(s) if isinstance(s, str) else []
                return [str(x) for x in v] if isinstance(v, list) else []
            except Exception:
                return []

        themes = _decode_json_list(digest.get("key_themes"))[:8]
        entities = _decode_json_list(digest.get("key_entities"))[:10]
        summary = digest.get("current_summary") or "Notebook still warming up — generate some content to see synthesis."

        parts: List[str] = []
        parts.append('<div class="lb-html-artifact p-4 max-w-3xl mx-auto">')
        # Header
        parts.append(
            '<div class="mb-6">'
            '<p class="text-xs uppercase tracking-wide text-gray-500 mb-1">Notebook dashboard</p>'
            f'<p class="text-lg font-semibold text-gray-900 mb-1">{esc(nb_name)}</p>'
            f'<p class="text-sm text-gray-700">{esc(summary)}</p>'
            '</div>'
        )

        # Themes + entities chips
        if themes or entities:
            parts.append('<div class="mb-6 grid grid-cols-2 gap-3">')
            if themes:
                chips = "".join(
                    f'<span class="text-xs rounded-full px-2 py-0.5 bg-blue-50 text-blue-700 mr-1 mb-1 inline-block">{esc(t)}</span>'
                    for t in themes
                )
                parts.append(
                    '<div class="rounded-lg border border-gray-200 bg-white p-3">'
                    '<p class="text-xs uppercase tracking-wide text-gray-500 mb-2">Key themes</p>'
                    f'<div>{chips}</div></div>'
                )
            if entities:
                chips = "".join(
                    f'<span class="text-xs rounded-full px-2 py-0.5 bg-purple-50 text-purple-700 mr-1 mb-1 inline-block">{esc(e)}</span>'
                    for e in entities
                )
                parts.append(
                    '<div class="rounded-lg border border-gray-200 bg-white p-3">'
                    '<p class="text-xs uppercase tracking-wide text-gray-500 mb-2">Key entities</p>'
                    f'<div>{chips}</div></div>'
                )
            parts.append('</div>')

        # Activity grid (last 7 days)
        items_added = activity.get("items_added", 0)
        pending = activity.get("pending_approval", 0)
        notes_created = activity.get("notes_created", 0)
        highlights = activity.get("highlights_since", 0)
        if any([items_added, pending, notes_created, highlights]):
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">This week\'s activity</h3>'
                '<div class="grid grid-cols-4 gap-2 mb-6">'
            )
            for label, value in (
                ("New sources", items_added),
                ("Pending", pending),
                ("Notes", notes_created),
                ("Highlights", highlights),
            ):
                parts.append(
                    '<div class="rounded-lg border border-gray-200 bg-gray-50 p-3 text-center">'
                    f'<p class="text-xl font-semibold text-gray-900">{value}</p>'
                    f'<p class="text-xs text-gray-500 mt-1">{esc(label)}</p>'
                    '</div>'
                )
            parts.append('</div>')

        # Consensus
        if consensus:
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">What\'s converging in this notebook</h3>'
                '<div class="grid grid-cols-2 gap-3 mb-6">'
            )
            for cl in consensus[:6]:
                parts.append(
                    '<div class="rounded-lg border border-blue-200 bg-blue-50 p-3">'
                    f'<p class="text-xs uppercase tracking-wide text-blue-700 mb-1">{cl.size} sources</p>'
                    f'<p class="text-sm font-medium text-gray-800">{esc(cl.topic_label or "(unlabeled)")}</p>'
                    '</div>'
                )
            parts.append('</div>')

        parts.append('</div>')
        return "".join(parts)


# Singleton instance
curator = CuratorAgent()
