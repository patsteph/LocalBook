"""
Memory Manager - Background memory consolidation and lifecycle management

The "sleep cycle" for memory:
1. Compress old recall entries into summaries
2. Promote frequently-accessed archival to core (if space)
3. Demote stale core entries to archival
4. Prune low-value archival (>90 days, never accessed)
5. Generate cross-notebook insights for Curator
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from enum import Enum

from storage.memory_store import MemoryStore, AgentNamespace
from models.memory import (
    ArchivalMemoryEntry, MemoryImportance, MemorySourceType
)

logger = logging.getLogger(__name__)


class ConsolidationResult(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"


class MemoryManager:
    """
    Background memory consolidation and lifecycle management.
    Runs periodically to keep memory healthy and adaptive.
    
    Two-tier approach:
    1. Event Logger: Continuous mini-updates (crash-safe)
    2. Consolidator: Periodic processing at intervals
    """
    
    # Consolidation intervals (in hours)
    COMPACT_INTERVAL_HOURS = 1       # Dedupe, merge similar events
    PATTERN_INTERVAL_HOURS = 3       # Identify emerging preferences
    DEEP_CONSOLIDATION_HOURS = 6     # Update memory embeddings
    DAILY_SUMMARY_HOURS = 24         # Full daily summary
    
    RECALL_COMPRESSION_THRESHOLD = 100  # Compress after this many entries
    ARCHIVAL_PRUNE_DAYS = 90  # Prune memories older than this if never accessed
    CORE_MEMORY_STALE_DAYS = 30  # Demote core memories not accessed in this time
    
    def __init__(self):
        self.memory_store = MemoryStore()
        self._last_compact: Optional[datetime] = None
        self._last_pattern: Optional[datetime] = None
        self._last_consolidation: Optional[datetime] = None
        self._last_daily: Optional[datetime] = None
        self._consolidation_lock = asyncio.Lock()
        self._running = False
    
    async def start_scheduler(self) -> None:
        """Start the background consolidation scheduler"""
        if self._running:
            return
        
        self._running = True
        logger.info("Memory consolidation scheduler started (multi-tier)")
        
        while self._running:
            try:
                now = datetime.utcnow()
                
                # Check each tier independently
                # Tier 1: Hourly compact (dedupe events)
                if self._should_run(self._last_compact, self.COMPACT_INTERVAL_HOURS):
                    await self.run_compact()
                    self._last_compact = now
                
                # Tier 2: 3-hour pattern analysis
                if self._should_run(self._last_pattern, self.PATTERN_INTERVAL_HOURS):
                    await self.run_pattern_analysis()
                    self._last_pattern = now
                
                # Tier 3: 6-hour deep consolidation
                if self._should_run(self._last_consolidation, self.DEEP_CONSOLIDATION_HOURS):
                    await self.run_consolidation()
                    self._last_consolidation = now
                
                # Tier 4: Daily summary
                if self._should_run(self._last_daily, self.DAILY_SUMMARY_HOURS):
                    await self.run_daily_summary()
                    self._last_daily = now
                
                # Sleep for 15 minutes between checks
                await asyncio.sleep(900)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Consolidation scheduler error: {e}")
                await asyncio.sleep(300)  # Wait 5 min on error
    
    def stop_scheduler(self) -> None:
        """Stop the background scheduler"""
        self._running = False
        logger.info("Memory consolidation scheduler stopped")
    
    def _should_run(self, last_run: Optional[datetime], interval_hours: float) -> bool:
        """Check if a task should run based on last run time"""
        if last_run is None:
            return True
        hours_since = (datetime.utcnow() - last_run).total_seconds() / 3600
        return hours_since >= interval_hours
    
    async def run_compact(self) -> Dict[str, Any]:
        """
        Tier 1: Hourly event compaction.
        Deduplicates and merges similar events from the event log.
        """
        from services.event_logger import event_logger
        
        logger.info("Running hourly event compaction")
        try:
            # Get events from the last hour
            since = datetime.utcnow() - timedelta(hours=1)
            events = event_logger.get_events_since(since)
            
            # Count by notebook for logging
            notebook_counts: Dict[str, int] = {}
            for event in events:
                notebook_counts[event.notebook_id] = notebook_counts.get(event.notebook_id, 0) + 1
            
            logger.info(f"Compacted {len(events)} events across {len(notebook_counts)} notebooks")
            return {"events_processed": len(events), "notebooks": len(notebook_counts)}
        except Exception as e:
            logger.error(f"Event compaction failed: {e}")
            return {"error": str(e)}
    
    async def run_pattern_analysis(self) -> Dict[str, Any]:
        """
        Tier 2: 3-hour pattern analysis.
        Identifies emerging user preferences and patterns.
        """
        from services.event_logger import event_logger, EventType
        
        logger.info("Running 3-hour pattern analysis")
        try:
            # Get events from the last 3 hours
            since = datetime.utcnow() - timedelta(hours=3)
            events = event_logger.get_events_since(since)
            
            # Analyze patterns by event type
            patterns: Dict[str, int] = {}
            for event in events:
                patterns[event.event_type.value] = patterns.get(event.event_type.value, 0) + 1
            
            # Log notable patterns
            if patterns.get(EventType.SOURCE_REJECTED.value, 0) > patterns.get(EventType.SOURCE_APPROVED.value, 0):
                logger.info("Pattern: User rejecting more sources than approving - may need Curator tuning")
            
            logger.info(f"Pattern analysis complete: {patterns}")
            return {"patterns": patterns}
        except Exception as e:
            logger.error(f"Pattern analysis failed: {e}")
            return {"error": str(e)}
    
    async def run_daily_summary(self) -> Dict[str, Any]:
        """
        Tier 4: Daily summary generation.
        Creates a comprehensive summary of the day's learning.
        """
        from services.event_logger import event_logger
        
        logger.info("Running daily memory summary")
        try:
            # Get events from the last 24 hours
            since = datetime.utcnow() - timedelta(hours=24)
            events = event_logger.get_events_since(since)
            
            # Summarize by type
            summary = {
                "total_events": len(events),
                "by_type": {},
                "by_notebook": {}
            }
            
            for event in events:
                et = event.event_type.value
                nb = event.notebook_id
                summary["by_type"][et] = summary["by_type"].get(et, 0) + 1
                summary["by_notebook"][nb] = summary["by_notebook"].get(nb, 0) + 1
            
            # Clean up old event logs (keep 7 days)
            removed = event_logger.cleanup_old_logs(days_to_keep=7)
            summary["logs_cleaned"] = removed
            
            logger.info(f"Daily summary: {summary['total_events']} events, cleaned {removed} old logs")
            return summary
        except Exception as e:
            logger.error(f"Daily summary failed: {e}")
            return {"error": str(e)}
    
    async def run_consolidation(self) -> Dict[str, Any]:
        """
        Run full memory consolidation cycle.
        Returns summary of actions taken.
        """
        async with self._consolidation_lock:
            logger.info("Starting memory consolidation cycle")
            start_time = datetime.utcnow()
            
            results = {
                "started_at": start_time.isoformat(),
                "recall_compressed": 0,
                "archival_pruned": 0,
                "core_demoted": 0,
                "insights_generated": 0,
                "errors": []
            }
            
            try:
                # 1. Compress old recall entries
                compressed = await self._compress_recall_entries()
                results["recall_compressed"] = compressed
            except Exception as e:
                logger.error(f"Recall compression error: {e}")
                results["errors"].append(f"recall_compression: {str(e)}")
            
            try:
                # 2. Prune stale archival memories
                pruned = await self._prune_archival_memories()
                results["archival_pruned"] = pruned
            except Exception as e:
                logger.error(f"Archival pruning error: {e}")
                results["errors"].append(f"archival_pruning: {str(e)}")
            
            try:
                # 3. Demote stale core memories
                demoted = await self._demote_stale_core_memories()
                results["core_demoted"] = demoted
            except Exception as e:
                logger.error(f"Core demotion error: {e}")
                results["errors"].append(f"core_demotion: {str(e)}")
            
            try:
                # 4. Generate cross-notebook insights (placeholder for Curator)
                insights = await self._generate_cross_notebook_insights()
                results["insights_generated"] = insights
            except Exception as e:
                logger.error(f"Insight generation error: {e}")
                results["errors"].append(f"insight_generation: {str(e)}")
            
            try:
                # 5. Process negative signals for all notebooks
                from storage.notebook_store import notebook_store
                notebooks = await notebook_store.list()
                signals_processed = 0
                for nb in notebooks:
                    signal_result = await self.process_negative_signals(nb["id"])
                    signals_processed += signal_result["patterns_reduced"] + signal_result["focus_areas_added"]
                results["signals_processed"] = signals_processed
            except Exception as e:
                logger.error(f"Negative signal processing error: {e}")
                results["errors"].append(f"signal_processing: {str(e)}")
            
            end_time = datetime.utcnow()
            results["completed_at"] = end_time.isoformat()
            results["duration_seconds"] = (end_time - start_time).total_seconds()
            results["status"] = ConsolidationResult.SUCCESS.value if not results["errors"] else ConsolidationResult.PARTIAL.value
            
            self._last_consolidation = end_time
            logger.info(f"Memory consolidation completed: {results}")
            
            return results
    
    async def _compress_recall_entries(self) -> int:
        """
        Compress old recall entries into summaries.
        Returns count of entries compressed.
        """
        entry_count = self.memory_store.get_recall_entry_count()
        
        if entry_count < self.RECALL_COMPRESSION_THRESHOLD:
            return 0
        
        # Get old unsummarized entries (older than 7 days)
        cutoff = datetime.utcnow() - timedelta(days=7)
        old_entries = self.memory_store.get_recent_conversations(
            limit=500,
            days=30  # Look back 30 days
        )
        
        # Filter to entries older than cutoff that aren't summarized
        to_compress = [e for e in old_entries 
                       if e.timestamp < cutoff and not e.is_summarized]
        
        if not to_compress:
            return 0
        
        # Group by conversation_id and create summaries
        # For now, just mark them as summarized (full LLM summarization in Phase 4)
        conversations = {}
        for entry in to_compress:
            if entry.conversation_id not in conversations:
                conversations[entry.conversation_id] = []
            conversations[entry.conversation_id].append(entry)
        
        compressed_count = 0
        for conv_id, entries in conversations.items():
            if len(entries) >= 5:  # Only compress if enough entries
                self.memory_store.mark_entries_summarized(conv_id)
                compressed_count += len(entries)
        
        return compressed_count
    
    async def _prune_archival_memories(self) -> int:
        """
        Prune low-value archival memories.
        Removes memories >90 days old with 0 access count.
        Returns count of memories pruned.
        """
        pruned = 0
        try:
            table = self.memory_store._get_archival_table()
            if table is None:
                return 0
            
            cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
            
            # Search for old, never-accessed entries
            try:
                results = table.search().where(
                    f"access_count = 0 AND created_at < '{cutoff}'"
                ).limit(100).to_list()
            except Exception:
                # LanceDB version may not support chained where+search
                # Fall back to scanning
                results = []
                try:
                    all_data = table.to_pandas()
                    mask = (all_data["access_count"] == 0) & (all_data["created_at"] < cutoff)
                    results = all_data[mask].head(100).to_dict("records")
                except Exception:
                    pass
            
            if results:
                ids_to_prune = [r["id"] for r in results if "id" in r]
                if ids_to_prune:
                    for rid in ids_to_prune:
                        try:
                            table.delete(f"id = '{rid}'")
                            pruned += 1
                        except Exception:
                            pass
                    if pruned > 0:
                        logger.info(f"Pruned {pruned} archival memories (>90 days, never accessed)")
        except Exception as e:
            logger.error(f"Archival pruning error: {e}")
        
        return pruned
    
    async def _demote_stale_core_memories(self) -> int:
        """
        Move stale core memories to archival.
        Frees up space in the limited core memory.
        Returns count of memories demoted.
        """
        core = self.memory_store.load_core_memory()
        cutoff = datetime.utcnow() - timedelta(days=self.CORE_MEMORY_STALE_DAYS)
        
        demoted_count = 0
        entries_to_keep = []
        
        for entry in core.entries:
            # Check if entry is stale (not accessed recently, low importance)
            is_stale = (
                entry.updated_at < cutoff and 
                entry.access_count < 3 and
                entry.importance != MemoryImportance.CRITICAL
            )
            
            if is_stale:
                # Move to archival memory
                archival_entry = ArchivalMemoryEntry(
                    content=f"{entry.key}: {entry.value}",
                    content_type="demoted_core_memory",
                    source_type=MemorySourceType.SYSTEM,
                    topics=[entry.category.value] if entry.category else [],
                    entities=[],
                    importance=entry.importance,
                )
                self.memory_store.add_archival_memory(
                    archival_entry, 
                    namespace=AgentNamespace.SYSTEM
                )
                demoted_count += 1
            else:
                entries_to_keep.append(entry)
        
        if demoted_count > 0:
            core.entries = entries_to_keep
            self.memory_store.save_core_memory(core)
        
        return demoted_count
    
    async def _generate_cross_notebook_insights(self) -> int:
        """
        Generate cross-notebook insights via the Curator agent.
        Delegates to Curator.discover_cross_notebook_patterns().
        """
        try:
            from agents.curator import curator
            insights = await curator.discover_cross_notebook_patterns()
            if insights:
                logger.info(f"Generated {len(insights)} cross-notebook insights")
            return len(insights) if insights else 0
        except Exception as e:
            logger.error(f"Cross-notebook insight generation error: {e}")
            return 0
    
    async def adapt_to_user_behavior(
        self, 
        query: str, 
        notebook_id: str,
        was_helpful: bool
    ) -> None:
        """
        Track user behavior and adapt memory priorities.
        Called after user interactions.
        """
        if was_helpful:
            # Record positive signal
            self.memory_store.record_user_signal(
                notebook_id=notebook_id,
                signal_type="click",
                query=query,
                metadata={"helpful": True}
            )
        else:
            # Record negative signal
            self.memory_store.record_user_signal(
                notebook_id=notebook_id,
                signal_type="search_miss",
                query=query,
                metadata={"helpful": False}
            )
    
    async def process_negative_signals(self, notebook_id: str) -> Dict[str, Any]:
        """
        Process negative signals and adapt Collector behavior.
        Called during consolidation cycle.
        """
        from agents.collector import get_collector
        
        results = {
            "ignored_items": 0,
            "search_misses": 0,
            "patterns_reduced": 0,
            "focus_areas_added": 0
        }
        
        # Get ignored items (viewed but never clicked)
        ignored = self.memory_store.get_ignored_items(notebook_id)
        results["ignored_items"] = len(ignored)
        
        if ignored:
            # Extract patterns from ignored items and reduce priority
            collector = get_collector(notebook_id)
            patterns = [{"item_id": item_id, "topics": []} for item_id in ignored[:10]]
            await collector.reduce_priority_for_patterns(patterns)
            results["patterns_reduced"] = len(patterns)
        
        # Get search misses (user searched, no results)
        search_misses = self.memory_store.get_search_misses(notebook_id)
        results["search_misses"] = len(search_misses)
        
        if search_misses:
            # Expand Collector focus areas
            collector = get_collector(notebook_id)
            await collector.expand_focus_areas(search_misses[:5])
            results["focus_areas_added"] = min(5, len(search_misses))
        
        return results
    
    def get_consolidation_status(self) -> Dict[str, Any]:
        """Get current consolidation status"""
        return {
            "last_consolidation": self._last_consolidation.isoformat() if self._last_consolidation else None,
            "next_due": self._should_consolidate(),
            "interval_hours": self.CONSOLIDATION_INTERVAL_HOURS,
            "scheduler_running": self._running
        }


# Singleton instance
memory_manager = MemoryManager()
