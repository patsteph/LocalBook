"""RAG Metrics and Monitoring Service

Tracks performance, quality, and errors across the RAG pipeline.
Provides insights into what's working and what's failing.
"""
import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from enum import Enum

from config import settings


class RAGStage(Enum):
    """Stages in the RAG pipeline for timing/tracking."""
    QUERY_ANALYSIS = "query_analysis"
    EMBEDDING = "embedding"
    VECTOR_SEARCH = "vector_search"
    BM25_SEARCH = "bm25_search"
    HYBRID_FUSION = "hybrid_fusion"
    RERANKING = "reranking"
    CONTEXT_BUILD = "context_build"
    LLM_GENERATION = "llm_generation"
    QUALITY_CHECK = "quality_check"
    CORRECTIVE_RETRIEVAL = "corrective_retrieval"
    FOLLOWUP_GENERATION = "followup_generation"
    TOTAL = "total"


class SearchStrategy(Enum):
    """Search strategies used in adaptive search."""
    HYBRID = "hybrid"
    ENTITY_FOCUSED = "entity_focused"
    TIME_FOCUSED = "time_focused"
    KEYWORD_SCAN = "keyword_scan"


@dataclass
class QueryMetrics:
    """Metrics for a single query."""
    query_id: str
    timestamp: str
    notebook_id: str
    question_preview: str  # First 100 chars
    query_type: str  # factual, synthesis, complex
    
    # Timing (ms)
    timings: Dict[str, float] = field(default_factory=dict)
    total_time_ms: float = 0
    
    # Retrieval
    chunks_retrieved: int = 0
    chunks_after_rerank: int = 0
    citations_used: int = 0
    sources_used: int = 0
    
    # Quality
    max_confidence: float = 0
    avg_confidence: float = 0
    low_confidence: bool = False
    quality_check_passed: bool = True
    quality_failure_reason: str = ""
    
    # Strategy
    search_strategy_used: str = ""
    strategies_tried: List[str] = field(default_factory=list)
    corrective_retrieval_triggered: bool = False
    
    # Cache
    query_cache_hit: bool = False
    embedding_cache_hit: bool = False
    answer_cache_hit: bool = False
    
    # Errors
    error: str = ""
    error_stage: str = ""


@dataclass
class AggregateMetrics:
    """Aggregated metrics over time window."""
    window_start: str
    window_end: str
    total_queries: int = 0
    
    # Performance
    avg_total_time_ms: float = 0
    p50_total_time_ms: float = 0
    p95_total_time_ms: float = 0
    p99_total_time_ms: float = 0
    
    # Stage breakdown (avg ms)
    avg_stage_times: Dict[str, float] = field(default_factory=dict)
    
    # Quality
    low_confidence_rate: float = 0
    quality_check_fail_rate: float = 0
    corrective_retrieval_rate: float = 0
    avg_confidence: float = 0
    avg_citations: float = 0
    
    # Cache
    query_cache_hit_rate: float = 0
    embedding_cache_hit_rate: float = 0
    answer_cache_hit_rate: float = 0
    
    # Strategy distribution
    strategy_distribution: Dict[str, int] = field(default_factory=dict)
    
    # Errors
    error_rate: float = 0
    errors_by_stage: Dict[str, int] = field(default_factory=dict)


class RAGMetricsService:
    """Service for collecting and analyzing RAG metrics."""
    
    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self._metrics: List[QueryMetrics] = []
        self._current_query: Optional[QueryMetrics] = None
        self._stage_start_time: float = 0
        self._lock = asyncio.Lock()
        
        # Rolling counters for quick stats
        self._total_queries = 0
        self._total_errors = 0
        self._cache_hits = {"query": 0, "embedding": 0, "answer": 0}
        
        # Metrics file path
        self._metrics_file = Path(settings.db_path).parent / "rag_metrics.json"
        print(f"[RAGMetrics] Initializing with file: {self._metrics_file}")
        
        # Load existing metrics on startup
        self._load_metrics()
    
    def _load_metrics(self):
        """Load metrics from disk."""
        try:
            if self._metrics_file.exists():
                with open(self._metrics_file, 'r') as f:
                    data = json.load(f)
                    queries_data = data.get("queries", [])
                    self._metrics = [QueryMetrics(**m) for m in queries_data[-self.max_history:]]
                    self._total_queries = data.get("total_queries", len(self._metrics))
                    self._total_errors = data.get("total_errors", 0)
                    self._cache_hits = data.get("cache_hits", {"query": 0, "embedding": 0, "answer": 0})
                    print(f"[RAGMetrics] Loaded {len(self._metrics)} historical metrics, total_queries={self._total_queries}")
            else:
                print(f"[RAGMetrics] No metrics file found at {self._metrics_file}, starting fresh")
                # Create the file immediately
                self._save_metrics()
        except Exception as e:
            print(f"[RAGMetrics] Could not load metrics: {e}")
            import traceback
            traceback.print_exc()
    
    def _save_metrics(self):
        """Save metrics to disk (async-safe)."""
        try:
            # Ensure directory exists
            self._metrics_file.parent.mkdir(parents=True, exist_ok=True)
            
            data = {
                "queries": [asdict(m) for m in self._metrics[-self.max_history:]],
                "total_queries": self._total_queries,
                "total_errors": self._total_errors,
                "cache_hits": self._cache_hits,
                "last_updated": datetime.now().isoformat()
            }
            
            # Write to temp file first, then rename (atomic write)
            temp_file = self._metrics_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            # Atomic rename
            temp_file.replace(self._metrics_file)
            
        except Exception as e:
            print(f"[RAGMetrics] Could not save metrics: {e}")
            import traceback
            traceback.print_exc()
    
    # =========================================================================
    # Query Lifecycle
    # =========================================================================
    
    def start_query(
        self,
        query_id: str,
        notebook_id: str,
        question: str,
        query_type: str = "unknown"
    ) -> QueryMetrics:
        """Start tracking a new query."""
        self._current_query = QueryMetrics(
            query_id=query_id,
            timestamp=datetime.now().isoformat(),
            notebook_id=notebook_id,
            question_preview=question[:100],
            query_type=query_type
        )
        self._stage_start_time = time.time()
        print(f"[RAGMetrics] Started tracking query: {query_id[:8]}... type={query_type}")
        return self._current_query
    
    def start_stage(self, stage: RAGStage):
        """Mark the start of a pipeline stage."""
        self._stage_start_time = time.time()
    
    def end_stage(self, stage: RAGStage):
        """Record timing for a pipeline stage."""
        if self._current_query:
            elapsed_ms = (time.time() - self._stage_start_time) * 1000
            self._current_query.timings[stage.value] = elapsed_ms
    
    def record_retrieval(
        self,
        chunks_retrieved: int,
        chunks_after_rerank: int,
        citations_used: int,
        sources_used: int,
        max_confidence: float,
        avg_confidence: float,
        low_confidence: bool
    ):
        """Record retrieval metrics."""
        if self._current_query:
            self._current_query.chunks_retrieved = chunks_retrieved
            self._current_query.chunks_after_rerank = chunks_after_rerank
            self._current_query.citations_used = citations_used
            self._current_query.sources_used = sources_used
            self._current_query.max_confidence = max_confidence
            self._current_query.avg_confidence = avg_confidence
            self._current_query.low_confidence = low_confidence
    
    def record_strategy(
        self,
        strategy_used: SearchStrategy,
        strategies_tried: List[str]
    ):
        """Record search strategy metrics."""
        if self._current_query:
            self._current_query.search_strategy_used = strategy_used.value
            self._current_query.strategies_tried = strategies_tried
    
    def record_quality_check(self, passed: bool, reason: str = ""):
        """Record quality check result."""
        if self._current_query:
            self._current_query.quality_check_passed = passed
            self._current_query.quality_failure_reason = reason
    
    def record_corrective_retrieval(self, triggered: bool):
        """Record if corrective retrieval was triggered."""
        if self._current_query:
            self._current_query.corrective_retrieval_triggered = triggered
    
    def record_cache_hit(self, cache_type: str, hit: bool):
        """Record cache hit/miss."""
        if self._current_query:
            if cache_type == "query":
                self._current_query.query_cache_hit = hit
            elif cache_type == "embedding":
                self._current_query.embedding_cache_hit = hit
            elif cache_type == "answer":
                self._current_query.answer_cache_hit = hit
        
        if hit:
            self._cache_hits[cache_type] = self._cache_hits.get(cache_type, 0) + 1
    
    def record_error(self, error: str, stage: RAGStage):
        """Record an error."""
        if self._current_query:
            self._current_query.error = str(error)[:500]
            self._current_query.error_stage = stage.value
        self._total_errors += 1
    
    async def end_query(self, total_time_ms: Optional[float] = None) -> QueryMetrics:
        """Finish tracking the current query and save."""
        async with self._lock:
            if self._current_query:
                if total_time_ms is not None:
                    self._current_query.total_time_ms = total_time_ms
                else:
                    # Calculate from stage timings
                    self._current_query.total_time_ms = sum(self._current_query.timings.values())
                
                self._metrics.append(self._current_query)
                self._total_queries += 1
                
                # Trim history
                if len(self._metrics) > self.max_history:
                    self._metrics = self._metrics[-self.max_history:]
                
                # Save on every query to persist through restarts
                self._save_metrics()
                print(f"[RAGMetrics] Query completed: {self._current_query.query_id[:8]}... total_queries={self._total_queries}, latency={total_time_ms:.0f}ms")
                
                result = self._current_query
                self._current_query = None
                return result
            else:
                print("[RAGMetrics] WARNING: end_query called but no current query tracked")
            return None
    
    # =========================================================================
    # Analysis
    # =========================================================================
    
    def get_recent_metrics(self, count: int = 50) -> List[Dict]:
        """Get recent query metrics."""
        return [asdict(m) for m in self._metrics[-count:]]
    
    def get_aggregate_metrics(self, hours: int = 24) -> AggregateMetrics:
        """Get aggregated metrics for a time window."""
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = [
            m for m in self._metrics 
            if datetime.fromisoformat(m.timestamp) > cutoff
        ]
        
        if not recent:
            return AggregateMetrics(
                window_start=cutoff.isoformat(),
                window_end=datetime.now().isoformat()
            )
        
        # Calculate aggregates
        total = len(recent)
        times = [m.total_time_ms for m in recent if m.total_time_ms > 0]
        times_sorted = sorted(times) if times else [0]
        
        # Stage timing averages
        stage_totals = defaultdict(list)
        for m in recent:
            for stage, ms in m.timings.items():
                stage_totals[stage].append(ms)
        avg_stage_times = {
            stage: sum(times) / len(times) 
            for stage, times in stage_totals.items()
        }
        
        # Strategy distribution
        strategy_dist = defaultdict(int)
        for m in recent:
            if m.search_strategy_used:
                strategy_dist[m.search_strategy_used] += 1
        
        # Error breakdown
        errors_by_stage = defaultdict(int)
        for m in recent:
            if m.error:
                errors_by_stage[m.error_stage] += 1
        
        return AggregateMetrics(
            window_start=cutoff.isoformat(),
            window_end=datetime.now().isoformat(),
            total_queries=total,
            avg_total_time_ms=sum(times) / len(times) if times else 0,
            p50_total_time_ms=times_sorted[len(times_sorted) // 2] if times_sorted else 0,
            p95_total_time_ms=times_sorted[int(len(times_sorted) * 0.95)] if len(times_sorted) > 1 else times_sorted[0],
            p99_total_time_ms=times_sorted[int(len(times_sorted) * 0.99)] if len(times_sorted) > 1 else times_sorted[0],
            avg_stage_times=avg_stage_times,
            low_confidence_rate=sum(1 for m in recent if m.low_confidence) / total,
            quality_check_fail_rate=sum(1 for m in recent if not m.quality_check_passed) / total,
            corrective_retrieval_rate=sum(1 for m in recent if m.corrective_retrieval_triggered) / total,
            avg_confidence=sum(m.avg_confidence for m in recent) / total,
            avg_citations=sum(m.citations_used for m in recent) / total,
            query_cache_hit_rate=sum(1 for m in recent if m.query_cache_hit) / total,
            embedding_cache_hit_rate=sum(1 for m in recent if m.embedding_cache_hit) / total,
            answer_cache_hit_rate=sum(1 for m in recent if m.answer_cache_hit) / total,
            strategy_distribution=dict(strategy_dist),
            error_rate=sum(1 for m in recent if m.error) / total,
            errors_by_stage=dict(errors_by_stage)
        )
    
    def get_health_summary(self) -> Dict:
        """Get a quick health summary for dashboards/logging."""
        agg = self.get_aggregate_metrics(hours=1)
        
        # Determine health status
        issues = []
        if agg.error_rate > 0.05:
            issues.append(f"High error rate: {agg.error_rate:.1%}")
        if agg.low_confidence_rate > 0.3:
            issues.append(f"High low-confidence rate: {agg.low_confidence_rate:.1%}")
        if agg.avg_total_time_ms > 10000:
            issues.append(f"Slow queries: avg {agg.avg_total_time_ms/1000:.1f}s")
        if agg.corrective_retrieval_rate > 0.3:
            issues.append(f"High corrective retrieval rate: {agg.corrective_retrieval_rate:.1%}")
        
        status = "healthy" if not issues else "degraded" if len(issues) < 2 else "unhealthy"
        
        return {
            "status": status,
            "issues": issues,
            "queries_last_hour": agg.total_queries,
            "avg_latency_ms": round(agg.avg_total_time_ms, 1),
            "p95_latency_ms": round(agg.p95_total_time_ms, 1),
            "error_rate": round(agg.error_rate, 3),
            "low_confidence_rate": round(agg.low_confidence_rate, 3),
            "cache_hit_rates": {
                "query": round(agg.query_cache_hit_rate, 3),
                "embedding": round(agg.embedding_cache_hit_rate, 3),
                "answer": round(agg.answer_cache_hit_rate, 3)
            },
            "total_queries_all_time": self._total_queries,
            "total_errors_all_time": self._total_errors
        }
    
    def force_save(self):
        """Force save metrics to disk. Call on shutdown."""
        self._save_metrics()
        print(f"[RAGMetrics] Force saved: {len(self._metrics)} queries, total_queries={self._total_queries} to {self._metrics_file}")
    
    def print_health_report(self):
        """Print a formatted health report to console."""
        health = self.get_health_summary()
        agg = self.get_aggregate_metrics(hours=24)
        
        print("\n" + "=" * 60)
        print("RAG ENGINE HEALTH REPORT")
        print("=" * 60)
        print(f"Status: {health['status'].upper()}")
        
        if health['issues']:
            print("\n⚠️  Issues:")
            for issue in health['issues']:
                print(f"   - {issue}")
        
        print(f"\n📊 Last 24 Hours ({agg.total_queries} queries):")
        print(f"   Latency: avg={agg.avg_total_time_ms/1000:.2f}s, p95={agg.p95_total_time_ms/1000:.2f}s")
        print(f"   Quality: {(1-agg.low_confidence_rate)*100:.0f}% high confidence, {agg.avg_citations:.1f} avg citations")
        print(f"   Errors:  {agg.error_rate*100:.1f}% error rate")
        
        print("\n⚡ Cache Performance:")
        print(f"   Query cache:     {agg.query_cache_hit_rate*100:.0f}% hit rate")
        print(f"   Embedding cache: {agg.embedding_cache_hit_rate*100:.0f}% hit rate")
        print(f"   Answer cache:    {agg.answer_cache_hit_rate*100:.0f}% hit rate")
        
        if agg.strategy_distribution:
            print("\n🔍 Search Strategies:")
            for strategy, count in sorted(agg.strategy_distribution.items(), key=lambda x: -x[1]):
                pct = count / agg.total_queries * 100 if agg.total_queries > 0 else 0
                print(f"   {strategy}: {count} ({pct:.0f}%)")
        
        if agg.avg_stage_times:
            print("\n⏱️  Stage Timings (avg ms):")
            for stage, ms in sorted(agg.avg_stage_times.items(), key=lambda x: -x[1])[:5]:
                print(f"   {stage}: {ms:.0f}ms")
        
        print("=" * 60 + "\n")


# Singleton instance
rag_metrics = RAGMetricsService()
