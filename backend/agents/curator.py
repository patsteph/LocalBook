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
import json
import logging
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from enum import Enum
from pydantic import BaseModel

from storage.memory_store import memory_store, AgentNamespace
from storage.notebook_store import notebook_store
from models.memory import ArchivalMemoryEntry, MemorySourceType, MemoryImportance
from services.ollama_client import ollama_client
from config import settings

logger = logging.getLogger(__name__)


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
    collection_items_found: int = 0
    # Wow features
    total_sources: int = 0
    sources_this_week: int = 0
    sources_last_week: int = 0
    sources_summarized: int = 0
    sources_unread: int = 0
    highlights_since: int = 0
    recent_highlight_texts: List[str] = []
    # Unfinished threads from recall memory
    unfinished_threads: List[str] = []
    # Topic drift — new topics emerging vs established ones
    emerging_topics: List[str] = []
    # Temporal lookback — "this day in your research"
    one_week_ago_items: List[str] = []


class MorningBrief(BaseModel):
    away_duration: str
    notebook_summaries: List[NotebookSummary]
    cross_notebook_insight: Optional[str] = None
    narrative: str = ""  # LLM-generated newsletter-quality summary
    generated_at: datetime


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
        "voice": {
            "style": "professional",
            "verbosity": "concise"
        }
    }
    
    def __init__(self):
        self.config = self._load_config()
        self.name = self.config.get("name", "Curator")
        self.personality = self.config.get("personality", "helpful and thorough")
        self._pending_insights: List[ProactiveInsight] = []
    
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
    
    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update curator configuration"""
        self.config.update(updates)
        self._save_config(self.config)
        
        # Update instance attributes
        if "name" in updates:
            self.name = updates["name"]
        if "personality" in updates:
            self.personality = updates["personality"]
        
        return self.config
    
    def get_config(self) -> Dict[str, Any]:
        """Get current curator configuration"""
        return self.config
    
    # =========================================================================
    # Judgment System
    # =========================================================================
    
    async def judge_collection(
        self, 
        collector_id: str,
        proposed_items: List[CollectedItem],
        notebook_intent: str
    ) -> List[JudgmentResult]:
        """
        Review items a Collector wants to add.
        Returns judgment for each item.
        """
        results = []
        
        for item in proposed_items:
            result = await self._judge_single_item(item, notebook_intent, collector_id)
            results.append(result)
        
        return results
    
    async def _judge_single_item(
        self,
        item: CollectedItem,
        intent: str,
        collector_id: str
    ) -> JudgmentResult:
        """Judge a single collected item"""
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

            response = await ollama_client.generate(
                prompt=prompt,
                system="You are an editorial judgment system. Respond only with valid JSON.",
                model=settings.ollama_fast_model,
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

                response = await ollama_client.generate(
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

            response = await ollama_client.generate(
                prompt=prompt,
                system=f"You are {self.name}, a research curator. Personality: {self.personality}",
                model=settings.default_model,
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
        """
        Generate a newsletter-quality morning brief summarizing activity since
        the user last opened the app.
        
        Gathers rich data per notebook, then uses LLM to synthesize into a
        narrative the user actually wants to read.
        """
        now = datetime.utcnow()
        away_duration = now - last_seen
        
        # Format duration
        if away_duration.days > 0:
            duration_str = f"{away_duration.days} day{'s' if away_duration.days != 1 else ''}"
        else:
            hours = away_duration.seconds // 3600
            duration_str = f"{hours} hour{'s' if hours != 1 else ''}"
        
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
            has_activity = (
                stats["items_added"] > 0 or stats["pending_approval"] > 0 or
                stats.get("person_changes") or stats.get("upcoming_key_dates") or
                stats.get("recent_stories") or
                stats.get("collection_runs", 0) > 0 or
                stats.get("sources_this_week", 0) > 0 or
                stats.get("highlights_since", 0) > 0 or
                stats.get("unfinished_threads")
            )
            if has_activity:
                # Get the collector subject for this notebook
                subject = ""
                try:
                    from agents.collector import get_collector
                    collector = get_collector(notebook["id"])
                    cfg = collector.get_config()
                    subject = cfg.subject if hasattr(cfg, "subject") and cfg.subject else ""
                except Exception:
                    pass
                
                summaries.append(NotebookSummary(
                    notebook_id=notebook["id"],
                    name=notebook.get("title", notebook.get("name", "Untitled")),
                    subject=subject,
                    items_added=stats["items_added"],
                    flagged_important=stats.get("flagged", 0),
                    pending_approval=stats.get("pending_approval", 0),
                    top_finding=stats.get("top_item"),
                    recent_stories=[
                        RecentStory(**s) for s in stats.get("recent_stories", [])
                    ],
                    person_changes=stats.get("person_changes", []),
                    upcoming_key_dates=stats.get("upcoming_key_dates", []),
                    collection_runs=stats.get("collection_runs", 0),
                    collection_items_found=stats.get("collection_items_found", 0),
                    total_sources=stats.get("total_sources", 0),
                    sources_this_week=stats.get("sources_this_week", 0),
                    sources_last_week=stats.get("sources_last_week", 0),
                    sources_summarized=stats.get("sources_summarized", 0),
                    sources_unread=stats.get("sources_unread", 0),
                    highlights_since=stats.get("highlights_since", 0),
                    recent_highlight_texts=stats.get("recent_highlight_texts", []),
                    unfinished_threads=stats.get("unfinished_threads", []),
                    emerging_topics=stats.get("emerging_topics", []),
                    one_week_ago_items=stats.get("one_week_ago_items", []),
                ))
        
        # Get any pending cross-notebook insight
        cross_insight = None
        if self._pending_insights:
            insight = self._pending_insights[0]
            cross_insight = insight.summary
        
        # Generate LLM narrative — turn raw data into a newsletter people look forward to
        narrative = await self._synthesize_brief_narrative(
            summaries, duration_str, cross_insight
        )
        
        return MorningBrief(
            away_duration=duration_str,
            notebook_summaries=summaries,
            cross_notebook_insight=cross_insight,
            narrative=narrative,
            generated_at=now
        )
    
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
            
            # Count items added to memory since last_seen
            results = memory_store.search_archival_memory(
                query="*",
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=notebook_id,
                limit=50
            )
            
            items_since = [
                r for r in results 
                if r.entry.created_at > since
            ]
            stats["items_added"] = len(items_since)
            
        except Exception as e:
            logger.error(f"Error getting activity for {notebook_id}: {e}")
        
        # Collection history — how many runs happened while user was away
        try:
            from services.collection_history import get_collection_history
            history = get_collection_history(notebook_id, limit=10)
            runs_since = [h for h in history if h.get("timestamp", "") > since.isoformat()]
            stats["collection_runs"] = len(runs_since)
            stats["collection_items_found"] = sum(h.get("items_found", 0) for h in runs_since)
            if runs_since and not stats["top_item"]:
                stats["top_item"] = f"Collector ran {len(runs_since)} time{'s' if len(runs_since) != 1 else ''}, found {stats['collection_items_found']} items"
        except Exception:
            pass
        
        # Event logger — count user actions since last_seen
        try:
            from services.event_logger import event_logger
            events = event_logger.get_events_since(since, notebook_id=notebook_id)
            stats["items_added"] = max(stats["items_added"], len(events))
        except Exception:
            pass
        
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
        except Exception:
            pass
        
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
        except Exception:
            pass
        
        # Use preloaded sources if available, otherwise load (still only once per call)
        all_sources = preloaded_sources if preloaded_sources is not None else []
        if preloaded_sources is None:
            try:
                from storage.source_store import source_store
                all_sources = await source_store.list(notebook_id)
            except Exception:
                pass
        
        # Recent sources — pull actual titles of recently added content
        try:
            since_str = since.isoformat()
            recent = [
                s for s in all_sources
                if s.get("created_at", "") > since_str
            ]
            # Sort newest first, take top 5
            recent.sort(key=lambda s: s.get("created_at", ""), reverse=True)
            stats["recent_stories"] = [
                {
                    "title": s.get("title") or s.get("filename", "Untitled"),
                    "source_name": s.get("source_type", s.get("format", "")),
                    "url": s.get("url"),
                    "summary": (s.get("summary") or s.get("description") or "")[:200],
                }
                for s in recent[:5]
            ]
            # Update items_added to reflect actual source count if higher
            if len(recent) > stats["items_added"]:
                stats["items_added"] = len(recent)
            
            # Research velocity — compare this week vs last week
            from datetime import timedelta
            now = datetime.utcnow()
            week_ago = (now - timedelta(days=7)).isoformat()
            two_weeks_ago = (now - timedelta(days=14)).isoformat()
            this_week = len([s for s in all_sources if s.get("created_at", "") > week_ago])
            last_week = len([s for s in all_sources if week_ago >= s.get("created_at", "") > two_weeks_ago])
            stats["total_sources"] = len(all_sources)
            stats["sources_this_week"] = this_week
            stats["sources_last_week"] = last_week
            
            # Reading progress — how many sources have been summarized
            summarized = len([s for s in all_sources if s.get("summary") or s.get("status") == "completed"])
            stats["sources_summarized"] = summarized
            stats["sources_unread"] = len(all_sources) - summarized
        except Exception:
            pass
        
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
        except Exception:
            pass
        
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
        except Exception:
            pass
        
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
        except Exception:
            pass
        
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
        except Exception:
            pass
        
        return stats
    
    async def _synthesize_brief_narrative(
        self,
        summaries: List['NotebookSummary'],
        duration_str: str,
        cross_insight: Optional[str]
    ) -> str:
        """
        Use LLM to turn raw notebook activity data into a newsletter-quality
        narrative the user looks forward to reading each morning.
        """
        if not summaries:
            return ""
        
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
            
            # Collection activity
            if nb.collection_runs > 0:
                details.append(f"  - Collector ran {nb.collection_runs}x, found {nb.collection_items_found} items")
            if nb.pending_approval > 0:
                details.append(f"  - {nb.pending_approval} items awaiting your review")
            
            # People updates
            if nb.person_changes:
                for pc in nb.person_changes[:3]:
                    details.append(f"  - People: {pc}")
            
            # Upcoming events
            if nb.upcoming_key_dates:
                for kd in nb.upcoming_key_dates[:2]:
                    details.append(f"  - Coming up: {kd}")
            
            # Research velocity
            if nb.total_sources > 0:
                velocity_note = f"  - Research library: {nb.total_sources} total sources"
                if nb.sources_this_week > 0 and nb.sources_last_week > 0:
                    if nb.sources_this_week > nb.sources_last_week:
                        pct = int(((nb.sources_this_week - nb.sources_last_week) / nb.sources_last_week) * 100)
                        velocity_note += f" (up {pct}% vs last week)"
                    elif nb.sources_this_week < nb.sources_last_week:
                        velocity_note += f" (slowed down vs last week)"
                elif nb.sources_this_week > 0:
                    velocity_note += f" ({nb.sources_this_week} added this week)"
                details.append(velocity_note)
            
            # Reading progress
            if nb.sources_unread > 0:
                details.append(f"  - Reading progress: {nb.sources_summarized} summarized, {nb.sources_unread} still unread")
            
            # Highlights — strongest signal of what the user cares about
            if nb.highlights_since > 0:
                details.append(f"  - You highlighted {nb.highlights_since} passages recently")
                for ht in nb.recent_highlight_texts[:2]:
                    details.append(f"    > \"{ht}\"")
            
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
            
            if details:
                section += "\n" + "\n".join(details)
            notebook_sections.append(section)
        
        raw_data = "\n\n".join(notebook_sections)
        if cross_insight:
            raw_data += f"\n\nCross-notebook insight: {cross_insight}"
        
        today_str = datetime.utcnow().strftime("%B %d, %Y")
        prompt = f"""You are a personal research assistant writing a morning brief for today, {today_str}. The user was away for {duration_str}. Turn the following raw activity data into a short, engaging newsletter they'll look forward to reading. IMPORTANT: Today's date is {today_str} — use this exact date, do not invent a different date.

RAW DATA:
{raw_data}

NEWSLETTER STRUCTURE (use the sections that have data, skip empty ones):
1. **Lead** — Start with the single most interesting or actionable finding. If there's a specific article title, use it.
2. **Per-notebook updates** — For each notebook with activity, write 1-3 sentences highlighting specifics. Use actual titles, names, and details. Never say "1 new items" — say what the item IS.
3. **Research momentum** — If there's velocity data (sources growing, highlights being made), weave in an encouraging note about their research momentum. Make it feel like progress, not a chore.
4. **Coming up** — If there are upcoming events or key dates, make them feel urgent.
5. **Unfinished threads** — If there are unfinished conversations, gently remind the user: "You were exploring [topic] a few days ago — want to pick that back up?" Frame it as helpful, not nagging.
6. **Emerging interests** — If topic drift is detected (emerging topics that are new this week), mention it: "I'm noticing a growing interest in [topic] — this is new territory for your research." Make the user feel seen.
7. **One week ago** — If there are temporal lookback items, create a "This time last week" moment: "A week ago you were reading about [X] — since then, [Y] has happened." Connect past to present.
8. **Did you know?** — If the data is thin (few new items), generate one thought-provoking question or insight based on the notebook subjects. Something that makes the reader think "huh, I should look into that."
9. **Suggested action** — End with ONE specific, actionable next step (e.g., "Review the 3 pending items in your AI Research notebook" or "Continue your conversation about [unfinished thread topic]").

TONE:
- Warm, professional, like a trusted advisor who knows your research intimately
- Confident and specific — never vague or generic
- Brief — aim for 200-400 words total
- Use markdown: **bold** for emphasis, bullet points for lists, but keep it readable
- The unfinished threads, emerging interests, and lookback sections are what make this feel MAGICAL — these show the user the system is paying attention. Prioritize them when present.

Write the brief now:"""

        try:
            from services.ollama_client import ollama_client
            from config import settings
            
            response = await ollama_client.generate(
                prompt=prompt,
                system="You are a concise, insightful research assistant. Write engaging morning briefs that make people smarter about their research topics.",
                model=settings.ollama_model,
                temperature=0.7,
                timeout=90.0
            )
            narrative = response.get("response", "").strip()
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
        
        self._pending_insights = insights
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
        Check if any pending insights relate to current user query.
        If so, mention it naturally in response.
        """
        if not self._pending_insights:
            return None
        
        query_lower = current_query.lower()
        
        for insight in self._pending_insights:
            if insight.entity and insight.entity.lower() in query_lower:
                return f"💡 By the way: {insight.summary}"
        
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
        """
        # Infer thesis if not provided
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

            response = await ollama_client.generate(
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

            response = await ollama_client.generate(
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

            response = await ollama_client.generate(
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
            response = await ollama_client.generate(
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
    # Collector Orchestration - Curator as Central Brain
    # =========================================================================
    
    async def orchestrate_collection(
        self,
        notebook_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Central orchestration method. Curator decides what to collect across notebooks.
        Collectors are workers that execute Curator's decisions.
        
        Flow:
        1. Curator analyzes each notebook's needs
        2. Curator assigns collection tasks to Collectors
        3. Collectors execute and return results
        4. Curator judges all results before they enter notebooks
        """
        # Get all notebooks if none specified
        if notebook_ids is None:
            notebooks = await notebook_store.list()
            notebook_ids = [nb.get("id") for nb in notebooks if nb.get("id")]
        
        results = {
            "notebooks_processed": 0,
            "items_collected": 0,
            "items_approved": 0,
            "items_pending": 0,
            "items_rejected": 0,
            "notebook_results": []
        }
        
        for notebook_id in notebook_ids:
            try:
                nb_result = await self._orchestrate_notebook_collection(notebook_id)
                results["notebook_results"].append(nb_result)
                results["notebooks_processed"] += 1
                results["items_collected"] += nb_result.get("items_collected", 0)
                results["items_approved"] += nb_result.get("items_approved", 0)
                results["items_pending"] += nb_result.get("items_pending", 0)
                results["items_rejected"] += nb_result.get("items_rejected", 0)
            except Exception as e:
                logger.error(f"Orchestration failed for notebook {notebook_id}: {e}")
                results["notebook_results"].append({
                    "notebook_id": notebook_id,
                    "error": str(e)
                })
        
        return results
    
    async def _orchestrate_notebook_collection(
        self,
        notebook_id: str
    ) -> Dict[str, Any]:
        """Orchestrate collection for a single notebook"""
        from agents.collector import get_collector
        
        collector = get_collector(notebook_id)
        config = collector.get_config()
        
        # Skip if collector not configured
        if not config.intent or config.intent.strip() == "":
            return {
                "notebook_id": notebook_id,
                "skipped": True,
                "reason": "Collector not configured"
            }
        
        result = {
            "notebook_id": notebook_id,
            "items_collected": 0,
            "items_approved": 0,
            "items_pending": 0,
            "items_rejected": 0
        }
        
        # Step 1: Curator assigns collection task
        collection_task = await self._create_collection_task(notebook_id, config)
        
        # Step 2: Collector executes the task (worker mode)
        collected_items = await collector.execute_collection_task(collection_task)
        result["items_collected"] = len(collected_items)
        
        if not collected_items:
            return result
        
        # Step 3: Curator judges ALL collected items
        judgments = await self.judge_collection(
            collector_id=notebook_id,
            proposed_items=collected_items,
            notebook_intent=config.intent
        )
        
        # Step 4: Apply judgments
        for item, judgment in zip(collected_items, judgments):
            if judgment.decision == JudgmentDecision.APPROVE:
                await collector.approve_item(item.id, curator_approved=True)
                result["items_approved"] += 1
            elif judgment.decision == JudgmentDecision.REJECT:
                await collector.reject_item(item.id, judgment.reason, "curator_rejected")
                result["items_rejected"] += 1
            else:  # DEFER_TO_USER or MODIFY
                # Leave in pending queue for user review
                result["items_pending"] += 1
        
        return result
    
    async def _create_collection_task(
        self,
        notebook_id: str,
        config
    ) -> Dict[str, Any]:
        """
        Curator creates a specific collection task for a Collector.
        This is where Curator's intelligence directs what to look for.
        """
        # Analyze what the notebook needs
        task = {
            "notebook_id": notebook_id,
            "intent": config.intent,
            "focus_areas": config.focus_areas,
            "sources": config.sources,
            "mode": config.collection_mode.value if hasattr(config.collection_mode, 'value') else str(config.collection_mode),
            "created_by": "curator",
            "created_at": datetime.utcnow().isoformat()
        }
        
        # Curator can add specific directives based on notebook state
        # e.g., "focus on recent developments" or "find contradicting evidence"
        try:
            # Check what's already in the notebook to avoid duplicates
            existing_memories = memory_store.search_archival_memory(
                query=config.intent,
                limit=5,
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=notebook_id
            )
            
            if existing_memories:
                recent_topics = [m.entry.content[:100] for m in existing_memories[:3]]
                task["avoid_similar_to"] = recent_topics
                task["curator_directive"] = "Find NEW information not covered by existing content"
        except Exception as e:
            logger.debug(f"Could not check existing content: {e}")
        
        return task
    
    async def assign_immediate_collection(
        self,
        notebook_id: str,
        specific_query: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Curator assigns an immediate collection task for a specific notebook.
        Called when user clicks "Collect Now" - but Curator still orchestrates.
        """
        from agents.collector import get_collector
        
        print(f"[CURATOR] assign_immediate_collection: getting collector for {notebook_id}")
        collector = get_collector(notebook_id)
        config = collector.get_config()
        
        if not config.intent:
            return {"error": "Collector not configured", "items_collected": 0}
        
        print(f"[CURATOR] Config loaded. Sources: {list(config.sources.keys()) if config.sources else 'none'}")
        
        # Create task with optional specific query
        task = await self._create_collection_task(notebook_id, config)
        if specific_query:
            task["specific_query"] = specific_query
            task["curator_directive"] = f"Focus on: {specific_query}"
        
        print("[CURATOR] Task created. Executing collection...")
        
        # Execute collection
        collected_items = await collector.execute_collection_task(task)
        
        print(f"[CURATOR] Collection returned {len(collected_items) if collected_items else 0} items")
        
        if not collected_items:
            return {"items_collected": 0, "message": "No new items found"}
        
        # Judge results
        print(f"[CURATOR] Judging {len(collected_items)} items...")
        judgments = await self.judge_collection(
            collector_id=notebook_id,
            proposed_items=collected_items,
            notebook_intent=config.intent
        )
        
        approved = 0
        pending = 0
        rejected = 0
        filtered = 0
        approved_titles = []
        filtered_titles = []
        
        CONFIDENCE_FLOOR = 0.50  # Hard minimum — nothing below 50% is ever added
        
        for item, judgment in zip(collected_items, judgments):
            # Hard confidence floor: items below threshold are always filtered
            if item.overall_confidence < CONFIDENCE_FLOOR:
                filtered += 1
                filtered_titles.append({
                    "title": item.title, "source": item.source_name, 
                    "confidence": item.overall_confidence, 
                    "reason": f"below_{int(CONFIDENCE_FLOOR*100)}%_threshold"
                })
                continue
            
            if judgment.decision == JudgmentDecision.APPROVE:
                # Directly store approved items (they aren't in the approval queue)
                try:
                    was_stored = await collector._store_approved_item(item)
                    if was_stored:
                        approved += 1
                        approved_titles.append({"id": item.id, "title": item.title, "source": item.source_name, "confidence": item.overall_confidence})
                    else:
                        # Item was approved but filtered (shallow content, duplicate, etc.)
                        filtered += 1
                        filtered_titles.append({"title": item.title, "source": item.source_name, "confidence": item.overall_confidence, "reason": "shallow_or_duplicate"})
                except Exception as e:
                    logger.error(f"Failed to store approved item '{item.title}': {e}")
                    filtered += 1
            elif judgment.decision == JudgmentDecision.REJECT:
                rejected += 1
            else:
                # Queue for user review (may auto-approve if high confidence in mixed mode)
                was_queued = await collector._add_to_approval_queue(item)
                if was_queued:
                    pending += 1
                else:
                    # Was auto-approved by queue logic — check if actually stored
                    was_stored = True  # _add_to_approval_queue already called _store_approved_item
                    if was_stored:
                        approved += 1
                        approved_titles.append({"id": item.id, "title": item.title, "source": item.source_name, "confidence": item.overall_confidence})
                    else:
                        filtered += 1
                        filtered_titles.append({"title": item.title, "source": item.source_name, "confidence": item.overall_confidence, "reason": "shallow_or_duplicate"})
        
        print(f"[CURATOR] Done: {approved} approved, {pending} pending, {rejected} rejected, {filtered} filtered (shallow/dup)")
        
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
                trigger="specific" if specific_query else "manual",
                keywords_used=task.get("focus_areas", [])[:5],
            )
        except Exception as hist_err:
            logger.warning(f"Failed to record collection history (non-fatal): {hist_err}")
        
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
                                lines.append(f"**{nb.get('name', 'Notebook')}**: {nb.get('items_added', 0)} new items")
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
            except Exception:
                pass
        
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
            except Exception:
                pass
        
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
        
        system_prompt = f"""You are {self.name}, the Curator of a research system called LocalBook.
Your personality: {self.personality}

Your role:
- You oversee ALL notebooks and have cross-notebook awareness
- You can synthesize information across research areas
- You can play devil's advocate and find counterarguments
- You advise on research strategy and identify gaps
- You are a guide and advisor, not a search engine

{user_context}

{notebook_context}
{search_context}
{cross_context}

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
            response = await ollama_client.generate(
                prompt=prompt,
                system=system_prompt,
                model=settings.ollama_model,
                temperature=0.5
            )
            return response.get("response", "I'm having trouble processing that right now.")
        except Exception as e:
            logger.error(f"Curator chat failed: {e}")
            return "I'm experiencing a technical issue. Please try again."
    
    # =========================================================================
    # Overwatch — Ambient cross-notebook awareness in regular chat
    # =========================================================================
    
    async def generate_overwatch_aside(
        self,
        query: str,
        answer: str,
        notebook_id: str
    ) -> Optional[str]:
        """
        After a regular chat answer, check if the Curator should chime in
        with cross-notebook context. Only returns something when genuinely useful.
        
        Returns a short aside string or None.
        """
        notebooks = await notebook_store.list()
        
        # Need at least 2 notebooks for cross-notebook insight
        if len(notebooks) < 2:
            return None
        
        # Search other notebooks for related content (PARALLEL)
        import asyncio
        cross_hits = []
        other_nbs = [nb for nb in notebooks if nb["id"] != notebook_id]
        
        async def _search_overwatch(nb):
            return nb, await asyncio.to_thread(
                memory_store.search_archival_memory,
                query=query,
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=nb["id"],
                cross_notebook=True,
                limit=3
            )
        
        try:
            nb_results = await asyncio.gather(
                *[_search_overwatch(nb) for nb in other_nbs],
                return_exceptions=True
            )
            for item in nb_results:
                if isinstance(item, Exception):
                    continue
                nb, results = item
                for r in results:
                    if r.combined_score > 0.5:  # Only high-relevance hits
                        cross_hits.append({
                            "notebook": nb.get("name", nb.get("title", "Untitled")),
                            "content": r.entry.content[:200],
                            "score": r.combined_score
                        })
        except Exception:
            pass
        
        if not cross_hits:
            # Also check pending insights
            insight = await self.surface_insight_if_relevant(query)
            return insight
        
        # Use LLM to decide if cross-notebook context is actually useful
        cross_summary = "\n".join(
            f"- [{h['notebook']}] {h['content']}" for h in cross_hits[:5]
        )
        
        try:
            prompt = f"""The user asked: "{query[:200]}"
The answer discussed: {answer[:300]}

Related content found in OTHER notebooks:
{cross_summary}

Is there a genuinely useful cross-notebook connection here? If YES, write a brief 1-2 sentence aside that adds value. If the connection is weak or obvious, respond with exactly "SKIP".

Rules:
- Only surface connections that the user likely hasn't noticed
- Be specific about which notebook the connection comes from
- Be concise — this is a sidebar note, not a full response"""

            response = await ollama_client.generate(
                prompt=prompt,
                system=f"You are {self.name}, providing brief cross-notebook insights. Only speak up when you have something genuinely useful to add.",
                model=settings.ollama_fast_model,
                temperature=0.3,
                timeout=15.0
            )
            
            text = response.get("response", "").strip()
            if text and "SKIP" not in text.upper() and len(text) > 10 and len(text) < 500:
                return text
            return None
        except Exception as e:
            logger.warning(f"Overwatch aside generation failed: {e}")
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

                response = await ollama_client.generate(
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


# Singleton instance
curator = CuratorAgent()
