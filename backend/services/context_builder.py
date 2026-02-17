"""Adaptive Context Builder — Centralized source selection for all content generation.

Replaces the naive 'sources[:10], content[:4000], context[:12000]' pattern with
intelligent, output-type-aware source selection that leverages the full RAG stack:

1. Source summaries (computed at ingestion) for topic-relevant ranking
2. RAG engine embeddings for semantic similarity scoring
3. Adaptive budgets per output type (summary vs deep dive vs Feynman)
4. Chunk-level retrieval via LanceDB for precision context
5. Map-reduce for large notebooks (summarize → synthesize → retrieve)

Every content generation pipeline (content.py, audio_generator.py, writing.py,
agents/tools.py, visual.py) should call build_context() instead of rolling its own
source selection logic.

Design Principles:
- REUSE existing infrastructure (rag_engine, source_store, knowledge_graph)
- NEVER duplicate what the chat pipeline already does well
- ADAPTIVE budgets — different outputs need different amounts of context
- TOPIC-AWARE — when a topic is given, rank sources by relevance
- GRACEFUL DEGRADATION — if embeddings fail, fall back to recency + size
"""

import asyncio
import logging
import time
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# =============================================================================
# CONTEXT PROFILES — Per-output-type budgets
# =============================================================================

@dataclass
class ContextProfile:
    """Defines how much and what kind of context an output type needs."""
    max_sources: int          # Max number of sources to include
    chars_per_source: int     # Max chars per source (before adaptive adjustment)
    total_context_chars: int  # Total context budget in characters
    strategy: str             # "breadth" (many sources, less each) | "depth" (fewer, more each) | "exhaustive" (everything)
    use_chunks: bool          # If True, use RAG chunk retrieval instead of raw source content
    chunk_top_k: int          # How many chunks to retrieve when use_chunks=True
    use_map_reduce: bool      # If True, use map-reduce for large source sets


# Output-type profiles — tuned to each skill's needs
CONTEXT_PROFILES: Dict[str, ContextProfile] = {
    # Short, breadth-first outputs
    "summary": ContextProfile(
        max_sources=10, chars_per_source=3000, total_context_chars=16000,
        strategy="breadth", use_chunks=False, chunk_top_k=0, use_map_reduce=False
    ),
    "explain": ContextProfile(
        max_sources=8, chars_per_source=4000, total_context_chars=16000,
        strategy="depth", use_chunks=False, chunk_top_k=0, use_map_reduce=False
    ),
    
    # Medium outputs — need good coverage
    "briefing": ContextProfile(
        max_sources=15, chars_per_source=4000, total_context_chars=24000,
        strategy="breadth", use_chunks=True, chunk_top_k=20, use_map_reduce=False
    ),
    "faq": ContextProfile(
        max_sources=15, chars_per_source=4000, total_context_chars=24000,
        strategy="breadth", use_chunks=True, chunk_top_k=20, use_map_reduce=False
    ),
    "debate": ContextProfile(
        max_sources=15, chars_per_source=5000, total_context_chars=28000,
        strategy="breadth", use_chunks=True, chunk_top_k=25, use_map_reduce=False
    ),
    
    # Large outputs — need comprehensive coverage
    "study_guide": ContextProfile(
        max_sources=20, chars_per_source=5000, total_context_chars=32000,
        strategy="depth", use_chunks=True, chunk_top_k=30, use_map_reduce=True
    ),
    "deep_dive": ContextProfile(
        max_sources=50, chars_per_source=6000, total_context_chars=40000,
        strategy="exhaustive", use_chunks=True, chunk_top_k=40, use_map_reduce=True
    ),
    "feynman_curriculum": ContextProfile(
        max_sources=50, chars_per_source=6000, total_context_chars=40000,
        strategy="exhaustive", use_chunks=True, chunk_top_k=40, use_map_reduce=True
    ),
    
    # Audio — scales with duration (overridden dynamically)
    "podcast_script": ContextProfile(
        max_sources=15, chars_per_source=4000, total_context_chars=24000,
        strategy="breadth", use_chunks=False, chunk_top_k=0, use_map_reduce=False
    ),
    
    # Writing assistant — focused
    "writing": ContextProfile(
        max_sources=8, chars_per_source=3000, total_context_chars=12000,
        strategy="depth", use_chunks=False, chunk_top_k=0, use_map_reduce=False
    ),
    
    # Visual — needs less context but relevant
    "visual": ContextProfile(
        max_sources=5, chars_per_source=2000, total_context_chars=8000,
        strategy="depth", use_chunks=True, chunk_top_k=10, use_map_reduce=False
    ),
    
    # Default fallback
    "default": ContextProfile(
        max_sources=10, chars_per_source=4000, total_context_chars=20000,
        strategy="breadth", use_chunks=False, chunk_top_k=0, use_map_reduce=False
    ),
}


@dataclass
class BuiltContext:
    """Result of context building — everything needed for content generation."""
    context: str                        # The assembled context string
    sources_used: int                   # Number of sources included
    source_names: List[str]             # Filenames of sources used
    total_chars: int                    # Total characters in context
    strategy_used: str                  # Which strategy was applied
    profile_used: str                   # Which profile was applied
    topic_relevance_scores: Dict[str, float] = field(default_factory=dict)  # source_id → relevance
    build_time_ms: int = 0             # How long context building took


# =============================================================================
# CONTEXT BUILDER — The main service
# =============================================================================

class ContextBuilder:
    """Builds optimized context for content generation using the full RAG stack."""
    
    async def build_context(
        self,
        notebook_id: str,
        skill_id: str = "default",
        topic: Optional[str] = None,
        source_ids: Optional[List[str]] = None,
        duration_minutes: Optional[int] = None,
    ) -> BuiltContext:
        """Build optimized context for content generation.
        
        Args:
            notebook_id: The notebook to pull sources from
            skill_id: The output type (e.g., "briefing", "feynman_curriculum")
            topic: Optional topic focus for relevance ranking
            source_ids: Optional specific source IDs to use (overrides ranking)
            duration_minutes: For audio, scales the context budget
            
        Returns:
            BuiltContext with assembled context and metadata
        """
        start_time = time.time()
        
        # Get profile for this output type
        profile = self._get_profile(skill_id, duration_minutes)
        
        logger.info(f"[ContextBuilder] Building context for skill={skill_id}, "
                    f"topic={topic or 'none'}, profile={profile.strategy}, "
                    f"budget={profile.total_context_chars} chars")
        
        # Import here to avoid circular imports
        from storage.source_store import source_store
        
        # Step 1: Get all sources for notebook
        all_sources = await source_store.list(notebook_id)
        if not all_sources:
            return BuiltContext(
                context="", sources_used=0, source_names=[],
                total_chars=0, strategy_used="none", profile_used=skill_id
            )
        
        # Step 2: Filter to specific sources if requested
        if source_ids:
            all_sources = [s for s in all_sources if s.get("id") in source_ids]
        
        # Step 3: Rank sources by relevance to topic
        ranked_sources = await self._rank_sources(all_sources, topic, notebook_id)
        
        # Step 4: Select top sources within budget
        selected_sources = ranked_sources[:profile.max_sources]
        
        # Step 5: Build context using the appropriate strategy
        if profile.use_chunks and topic:
            # Use RAG engine's vector search for chunk-level precision
            context_parts, source_names = await self._build_chunk_context(
                notebook_id, topic, selected_sources, profile
            )
        elif profile.use_map_reduce and len(all_sources) > profile.max_sources:
            # Map-reduce: summarize all sources, then use full content of top sources
            context_parts, source_names = await self._build_map_reduce_context(
                notebook_id, topic, all_sources, selected_sources, profile
            )
        else:
            # Direct source content with adaptive per-source budgets
            context_parts, source_names = await self._build_direct_context(
                notebook_id, selected_sources, profile
            )
        
        # Step 6: Assemble final context within budget
        context = "\n\n---\n\n".join(context_parts)
        if len(context) > profile.total_context_chars:
            context = context[:profile.total_context_chars]
        
        build_time = int((time.time() - start_time) * 1000)
        
        # Build relevance scores dict
        relevance_scores = {}
        for s in ranked_sources:
            if s.get("id") and s.get("_relevance_score") is not None:
                relevance_scores[s["id"]] = s["_relevance_score"]
        
        result = BuiltContext(
            context=context,
            sources_used=len(source_names),
            source_names=source_names,
            total_chars=len(context),
            strategy_used=profile.strategy,
            profile_used=skill_id,
            topic_relevance_scores=relevance_scores,
            build_time_ms=build_time
        )
        
        logger.info(f"[ContextBuilder] Built {result.total_chars} chars from "
                    f"{result.sources_used} sources in {build_time}ms "
                    f"(strategy={profile.strategy})")
        
        return result
    
    def _get_profile(self, skill_id: str, duration_minutes: Optional[int] = None) -> ContextProfile:
        """Get the context profile for a skill, with dynamic adjustments."""
        profile = CONTEXT_PROFILES.get(skill_id, CONTEXT_PROFILES["default"])
        
        # For audio, scale budget with duration
        if duration_minutes and skill_id in ("podcast_script", "audio"):
            if duration_minutes <= 5:
                return ContextProfile(
                    max_sources=3, chars_per_source=2000, total_context_chars=6000,
                    strategy="depth", use_chunks=False, chunk_top_k=0, use_map_reduce=False
                )
            elif duration_minutes <= 15:
                return ContextProfile(
                    max_sources=8, chars_per_source=4000, total_context_chars=16000,
                    strategy="breadth", use_chunks=False, chunk_top_k=0, use_map_reduce=False
                )
            else:
                return ContextProfile(
                    max_sources=15, chars_per_source=6000, total_context_chars=32000,
                    strategy="exhaustive", use_chunks=False, chunk_top_k=0, use_map_reduce=False
                )
        
        return profile
    
    async def _rank_sources(
        self,
        sources: List[Dict],
        topic: Optional[str],
        notebook_id: str
    ) -> List[Dict]:
        """Rank sources by relevance to the topic using embeddings + summaries.
        
        Uses the existing source summaries (computed at ingestion) and the RAG
        engine's embedding model to find the most relevant sources.
        Falls back to recency + size if embeddings fail.
        """
        if not topic or len(sources) <= 1:
            # No topic — sort by recency (newest first), assign neutral scores
            sorted_sources = sorted(
                sources,
                key=lambda s: s.get("created_at", ""),
                reverse=True
            )
            for s in sorted_sources:
                s["_relevance_score"] = 1.0
            return sorted_sources
        
        try:
            from services.rag_engine import rag_engine
            
            # Encode the topic
            topic_embedding = rag_engine.encode([topic])[0]
            
            # Build texts to embed — use summary if available, else filename + first 200 chars
            source_texts = []
            for s in sources:
                summary = s.get("summary", "")
                if summary:
                    source_texts.append(summary)
                else:
                    # Fallback: filename + tags
                    text = s.get("filename", "")
                    tags = s.get("tags", [])
                    if tags:
                        text += " " + " ".join(tags)
                    source_texts.append(text or "untitled")
            
            # Encode all source texts in one batch (fast)
            source_embeddings = rag_engine.encode(source_texts)
            
            # Compute cosine similarity
            for i, s in enumerate(sources):
                similarity = float(np.dot(topic_embedding, source_embeddings[i]) / (
                    np.linalg.norm(topic_embedding) * np.linalg.norm(source_embeddings[i]) + 1e-8
                ))
                s["_relevance_score"] = max(0.0, similarity)
            
            # Sort by relevance (highest first)
            ranked = sorted(sources, key=lambda s: s.get("_relevance_score", 0), reverse=True)
            
            top_scores = [(s.get("filename", "?"), f"{s.get('_relevance_score', 0):.3f}") 
                         for s in ranked[:5]]
            logger.info(f"[ContextBuilder] Topic ranking top 5: {top_scores}")
            
            return ranked
            
        except Exception as e:
            logger.warning(f"[ContextBuilder] Embedding ranking failed, falling back to recency: {e}")
            # Fallback: sort by recency
            sorted_sources = sorted(
                sources,
                key=lambda s: s.get("created_at", ""),
                reverse=True
            )
            for s in sorted_sources:
                s["_relevance_score"] = 1.0
            return sorted_sources
    
    async def _build_direct_context(
        self,
        notebook_id: str,
        sources: List[Dict],
        profile: ContextProfile
    ) -> Tuple[List[str], List[str]]:
        """Build context by reading source content directly with adaptive per-source budgets.
        
        More relevant sources get more characters. Less relevant sources get less.
        """
        from storage.source_store import source_store
        
        content_parts = []
        source_names = []
        total_chars = 0
        
        for i, source in enumerate(sources):
            if total_chars >= profile.total_context_chars:
                break
            
            source_content = await source_store.get_content(notebook_id, source["id"])
            if not source_content or not source_content.get("content"):
                continue
            
            # Adaptive budget: top sources get full budget, lower ranked get less
            relevance = source.get("_relevance_score", 1.0)
            # Top 3 sources get full budget, rest scale down
            if i < 3:
                budget = profile.chars_per_source
            else:
                budget = int(profile.chars_per_source * max(0.4, relevance))
            
            # Don't exceed remaining total budget
            remaining = profile.total_context_chars - total_chars
            budget = min(budget, remaining)
            
            content = source_content["content"][:budget]
            filename = source.get("filename", "Unknown")
            
            content_parts.append(f"## Source: {filename}\n{content}")
            source_names.append(filename)
            total_chars += len(content_parts[-1])
        
        return content_parts, source_names
    
    async def _build_chunk_context(
        self,
        notebook_id: str,
        topic: str,
        sources: List[Dict],
        profile: ContextProfile
    ) -> Tuple[List[str], List[str]]:
        """Build context using RAG engine's vector search for chunk-level precision.
        
        Instead of blindly truncating sources, we find the most relevant CHUNKS
        for the topic across all selected sources. This is the same approach
        the chat pipeline uses.
        """
        try:
            from services.rag_engine import rag_engine
            
            # Get the LanceDB table for this notebook
            table = rag_engine._get_table(notebook_id)
            
            # Encode the topic for vector search
            topic_embedding = rag_engine.encode([topic])[0].tolist()
            
            # Build source ID filter
            source_id_set = {s["id"] for s in sources}
            
            # Vector search across all selected sources
            results = (
                table.search(topic_embedding)
                .limit(profile.chunk_top_k * 2)  # Overcollect for filtering
                .to_list()
            )
            
            # Filter to selected sources
            results = [r for r in results if r.get("source_id") in source_id_set]
            
            # Rerank if available
            if hasattr(rag_engine, '_use_reranker') and rag_engine._use_reranker and len(results) > profile.chunk_top_k:
                results = rag_engine.rerank(topic, results, top_k=profile.chunk_top_k)
            else:
                results = results[:profile.chunk_top_k]
            
            # Assemble chunks into context, grouped by source
            source_chunks: Dict[str, List[str]] = {}
            source_name_map: Dict[str, str] = {}
            
            for r in results:
                sid = r.get("source_id", "unknown")
                text = r.get("text", "")
                fname = r.get("filename", "Unknown")
                if text:
                    source_chunks.setdefault(sid, []).append(text)
                    source_name_map[sid] = fname
            
            content_parts = []
            source_names = []
            total_chars = 0
            
            for sid, chunks in source_chunks.items():
                if total_chars >= profile.total_context_chars:
                    break
                
                fname = source_name_map.get(sid, "Unknown")
                combined = "\n\n".join(chunks)
                
                # Respect per-source budget
                remaining = profile.total_context_chars - total_chars
                budget = min(profile.chars_per_source, remaining)
                combined = combined[:budget]
                
                content_parts.append(f"## Source: {fname}\n{combined}")
                source_names.append(fname)
                total_chars += len(content_parts[-1])
            
            if content_parts:
                logger.info(f"[ContextBuilder] Chunk retrieval: {len(results)} chunks "
                           f"from {len(source_chunks)} sources")
                return content_parts, source_names
            
            # Fallback to direct if chunk retrieval returned nothing
            logger.warning("[ContextBuilder] Chunk retrieval empty, falling back to direct")
            return await self._build_direct_context(notebook_id, sources, profile)
            
        except Exception as e:
            logger.warning(f"[ContextBuilder] Chunk retrieval failed, falling back to direct: {e}")
            return await self._build_direct_context(notebook_id, sources, profile)
    
    async def _build_map_reduce_context(
        self,
        notebook_id: str,
        topic: Optional[str],
        all_sources: List[Dict],
        top_sources: List[Dict],
        profile: ContextProfile
    ) -> Tuple[List[str], List[str]]:
        """Map-reduce for large notebooks: use summaries for breadth, full content for depth.
        
        Phase 1 (Map): Include pre-computed summaries from ALL sources
        Phase 2 (Reduce): Include full content from the top-ranked sources
        
        This ensures the LLM knows about everything in the notebook (via summaries)
        while having deep access to the most relevant sources (via full content).
        """
        from storage.source_store import source_store
        
        content_parts = []
        source_names = []
        total_chars = 0
        
        # --- Phase 1: Summary overview of ALL sources ---
        summary_parts = []
        for s in all_sources:
            summary = s.get("summary", "")
            fname = s.get("filename", "Unknown")
            if summary:
                summary_parts.append(f"- **{fname}**: {summary}")
        
        if summary_parts:
            # Budget: allocate ~25% of total to the overview
            overview_budget = profile.total_context_chars // 4
            overview = "## Source Overview (all notebook sources)\n" + "\n".join(summary_parts)
            overview = overview[:overview_budget]
            content_parts.append(overview)
            total_chars += len(overview)
            logger.info(f"[ContextBuilder] Map phase: {len(summary_parts)} source summaries "
                       f"({len(overview)} chars)")
        
        # --- Phase 2: Full content from top-ranked sources ---
        # Use remaining budget for detailed content
        remaining_budget = profile.total_context_chars - total_chars
        
        # If we have a topic and chunks available, use chunk retrieval for the detail phase
        if topic and profile.use_chunks:
            detail_profile = ContextProfile(
                max_sources=profile.max_sources,
                chars_per_source=profile.chars_per_source,
                total_context_chars=remaining_budget,
                strategy="depth",
                use_chunks=True,
                chunk_top_k=profile.chunk_top_k,
                use_map_reduce=False
            )
            detail_parts, detail_names = await self._build_chunk_context(
                notebook_id, topic, top_sources, detail_profile
            )
        else:
            detail_profile = ContextProfile(
                max_sources=profile.max_sources,
                chars_per_source=profile.chars_per_source,
                total_context_chars=remaining_budget,
                strategy="depth",
                use_chunks=False,
                chunk_top_k=0,
                use_map_reduce=False
            )
            detail_parts, detail_names = await self._build_direct_context(
                notebook_id, top_sources, detail_profile
            )
        
        content_parts.extend(detail_parts)
        source_names.extend(detail_names)
        
        logger.info(f"[ContextBuilder] Reduce phase: {len(detail_parts)} detailed sources")
        
        return content_parts, source_names


# Singleton
context_builder = ContextBuilder()
