"""RAG Health and Metrics API

Provides endpoints for monitoring RAG engine health and performance.
"""
from typing import Optional
from fastapi import APIRouter

from services.rag_metrics import rag_metrics
from services.rag_cache import embedding_cache, answer_cache
from services.entity_extractor import entity_extractor
from services.entity_graph import entity_graph
from services.community_detection import community_detector

router = APIRouter(prefix="/rag", tags=["RAG Health"])


@router.get("/health")
async def get_health():
    """Get RAG engine health summary."""
    return rag_metrics.get_health_summary()


@router.get("/metrics")
async def get_metrics(hours: int = 24):
    """Get aggregated RAG metrics for a time window."""
    agg = rag_metrics.get_aggregate_metrics(hours=hours)
    return {
        "window_start": agg.window_start,
        "window_end": agg.window_end,
        "total_queries": agg.total_queries,
        "performance": {
            "avg_total_time_ms": round(agg.avg_total_time_ms, 1),
            "p50_total_time_ms": round(agg.p50_total_time_ms, 1),
            "p95_total_time_ms": round(agg.p95_total_time_ms, 1),
            "p99_total_time_ms": round(agg.p99_total_time_ms, 1),
        },
        "stage_times": {k: round(v, 1) for k, v in agg.avg_stage_times.items()},
        "quality": {
            "low_confidence_rate": round(agg.low_confidence_rate, 3),
            "quality_check_fail_rate": round(agg.quality_check_fail_rate, 3),
            "corrective_retrieval_rate": round(agg.corrective_retrieval_rate, 3),
            "avg_confidence": round(agg.avg_confidence, 3),
            "avg_citations": round(agg.avg_citations, 2),
        },
        "cache": {
            "query_cache_hit_rate": round(agg.query_cache_hit_rate, 3),
            "embedding_cache_hit_rate": round(agg.embedding_cache_hit_rate, 3),
            "answer_cache_hit_rate": round(agg.answer_cache_hit_rate, 3),
        },
        "strategy_distribution": agg.strategy_distribution,
        "errors": {
            "error_rate": round(agg.error_rate, 3),
            "by_stage": agg.errors_by_stage,
        }
    }


@router.get("/metrics/recent")
async def get_recent_metrics(count: int = 50):
    """Get recent individual query metrics."""
    return rag_metrics.get_recent_metrics(count=count)


@router.get("/cache/stats")
async def get_cache_stats():
    """Get cache statistics."""
    return {
        "embedding_cache": embedding_cache.get_stats(),
        "answer_cache": answer_cache.get_stats(),
    }


@router.post("/cache/clear")
async def clear_caches(cache_type: str = "all"):
    """Clear caches. cache_type can be 'embedding', 'answer', or 'all'."""
    if cache_type in ("embedding", "all"):
        embedding_cache.clear()
    if cache_type in ("answer", "all"):
        answer_cache.clear()
    return {"status": "cleared", "cache_type": cache_type}


@router.get("/report")
async def get_health_report():
    """Get a detailed health report (same as console output)."""
    health = rag_metrics.get_health_summary()
    agg = rag_metrics.get_aggregate_metrics(hours=24)
    
    # Build text report
    lines = []
    lines.append("=" * 60)
    lines.append("RAG ENGINE HEALTH REPORT")
    lines.append("=" * 60)
    lines.append(f"Status: {health['status'].upper()}")
    
    if health['issues']:
        lines.append("")
        lines.append("⚠️  Issues:")
        for issue in health['issues']:
            lines.append(f"   - {issue}")
    
    lines.append("")
    lines.append(f"📊 Last 24 Hours ({agg.total_queries} queries):")
    lines.append(f"   Latency: avg={agg.avg_total_time_ms/1000:.2f}s, p95={agg.p95_total_time_ms/1000:.2f}s")
    lines.append(f"   Quality: {(1-agg.low_confidence_rate)*100:.0f}% high confidence, {agg.avg_citations:.1f} avg citations")
    lines.append(f"   Errors:  {agg.error_rate*100:.1f}% error rate")
    
    lines.append("")
    lines.append("⚡ Cache Performance:")
    lines.append(f"   Query cache:     {agg.query_cache_hit_rate*100:.0f}% hit rate")
    lines.append(f"   Embedding cache: {agg.embedding_cache_hit_rate*100:.0f}% hit rate")
    lines.append(f"   Answer cache:    {agg.answer_cache_hit_rate*100:.0f}% hit rate")
    
    if agg.strategy_distribution:
        lines.append("")
        lines.append("🔍 Search Strategies:")
        for strategy, count in sorted(agg.strategy_distribution.items(), key=lambda x: -x[1]):
            pct = count / agg.total_queries * 100 if agg.total_queries > 0 else 0
            lines.append(f"   {strategy}: {count} ({pct:.0f}%)")
    
    if agg.avg_stage_times:
        lines.append("")
        lines.append("⏱️  Stage Timings (avg ms):")
        for stage, ms in sorted(agg.avg_stage_times.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"   {stage}: {ms:.0f}ms")
    
    lines.append("=" * 60)
    
    return {
        "report": "\n".join(lines),
        "health": health,
        "metrics": {
            "total_queries": agg.total_queries,
            "avg_latency_ms": agg.avg_total_time_ms,
            "p95_latency_ms": agg.p95_total_time_ms,
        }
    }


# Entity endpoints
@router.get("/entities/{notebook_id}")
async def get_entities(notebook_id: str, entity_type: Optional[str] = None, limit: int = 50):
    """Get entities extracted from a notebook's documents."""
    entities = entity_extractor.get_entities(notebook_id, entity_type)
    return {
        "entities": [
            {
                "name": e.name,
                "type": e.type,
                "mentions": e.mentions,
                "sources": len(e.source_ids),
                "context": e.context_snippets[:2] if e.context_snippets else []
            }
            for e in entities[:limit]
        ],
        "total": len(entities)
    }


@router.get("/entities/{notebook_id}/search")
async def search_entities(notebook_id: str, query: str, limit: int = 10):
    """Search for entities by name."""
    entities = entity_extractor.search_entities(notebook_id, query, limit)
    return {
        "query": query,
        "results": [
            {
                "name": e.name,
                "type": e.type,
                "mentions": e.mentions,
                "sources": e.source_ids
            }
            for e in entities
        ]
    }


@router.get("/entities/{notebook_id}/sources")
async def get_entity_sources(notebook_id: str, entity_name: str):
    """Get source IDs that mention a specific entity."""
    sources = entity_extractor.get_related_sources(notebook_id, entity_name)
    context = entity_extractor.get_entity_context(notebook_id, entity_name)
    return {
        "entity": entity_name,
        "source_ids": sources,
        "context": context
    }


@router.post("/entities/{notebook_id}/backfill")
async def backfill_entities(notebook_id: str):
    """Backfill entities AND relationships for existing documents in a notebook.
    
    Extracts entities from all chunks, then builds relationship graph.
    """
    import lancedb
    from config import settings
    
    # Get all chunks from LanceDB
    db = lancedb.connect(str(settings.db_path))
    table_name = f"notebook_{notebook_id}"
    
    if table_name not in db.table_names():
        return {"error": "Notebook not found", "notebook_id": notebook_id}
    
    table = db.open_table(table_name)
    
    # Get all chunks (limit to reasonable amount)
    chunks = table.search([0.0] * settings.embedding_dim).limit(1000).to_list()
    
    if not chunks:
        return {"error": "No chunks found", "notebook_id": notebook_id}
    
    # Run entity backfill
    result = await entity_extractor.backfill_from_chunks(notebook_id, chunks)
    
    # Also build relationships from extracted entities
    from collections import defaultdict
    by_source = defaultdict(list)
    for chunk in chunks:
        source_id = chunk.get("source_id", "unknown")
        by_source[source_id].append(chunk)
    
    total_relationships = 0
    for source_id, source_chunks in by_source.items():
        combined_text = " ".join(c.get("text", "") for c in source_chunks)[:4000]
        entities = entity_extractor.get_entities(notebook_id)
        if len(entities) >= 2:
            entity_dicts = [{"name": e.name, "type": e.type} for e in entities[:20]]
            try:
                relationships = await entity_graph.extract_relationships(
                    text=combined_text,
                    notebook_id=notebook_id,
                    source_id=source_id,
                    entities=entity_dicts
                )
                total_relationships += len(relationships)
            except Exception as e:
                print(f"[Backfill] Relationship extraction failed for {source_id}: {e}")
    
    result["relationships_extracted"] = total_relationships
    
    return result


# Graph endpoints
@router.get("/graph/{notebook_id}/stats")
async def get_graph_stats(notebook_id: str):
    """Get entity graph statistics for a notebook."""
    return entity_graph.get_graph_stats(notebook_id)


@router.get("/graph/{notebook_id}/connected")
async def get_connected_entities(notebook_id: str, entity: str, depth: int = 2, limit: int = 20):
    """Get entities connected to a given entity."""
    connected = entity_graph.get_connected_entities(notebook_id, entity, depth, limit)
    return {
        "entity": entity,
        "connected": connected,
        "count": len(connected)
    }


@router.get("/graph/{notebook_id}/relationships")
async def get_entity_relationships(notebook_id: str, entity: str):
    """Get all relationships for an entity."""
    relationships = entity_graph.get_relationships_for_entity(notebook_id, entity)
    return {
        "entity": entity,
        "relationships": relationships,
        "count": len(relationships)
    }


@router.get("/graph/{notebook_id}/path")
async def get_path_between(notebook_id: str, entity1: str, entity2: str, max_depth: int = 4):
    """Find shortest path between two entities."""
    path = entity_graph.get_path_between_entities(notebook_id, entity1, entity2, max_depth)
    return {
        "entity1": entity1,
        "entity2": entity2,
        "path": path,
        "connected": path is not None
    }


# Community endpoints
@router.get("/communities/{notebook_id}")
async def get_communities(notebook_id: str):
    """Get all communities for a notebook."""
    communities = community_detector.get_all_communities(notebook_id)
    return {
        "communities": [
            {
                "id": c.id,
                "name": c.name,
                "entities": c.entities[:10],
                "size": c.size,
                "summary": c.summary,
                "density": round(c.density, 2)
            }
            for c in communities
        ],
        "count": len(communities)
    }


@router.post("/communities/{notebook_id}/detect")
async def detect_communities(notebook_id: str):
    """Run community detection on a notebook's entity graph."""
    communities = await community_detector.detect_communities(notebook_id, entity_graph)
    return {
        "communities_detected": len(communities),
        "notebook_id": notebook_id
    }


@router.post("/communities/{notebook_id}/{community_id}/summarize")
async def summarize_community(notebook_id: str, community_id: str):
    """Generate a summary for a specific community."""
    summary = await community_detector.generate_community_summary(
        notebook_id, community_id, entity_graph
    )
    return {
        "community_id": community_id,
        "summary": summary
    }


@router.get("/communities/{notebook_id}/for-entity")
async def get_community_for_entity(notebook_id: str, entity: str):
    """Get the community containing a specific entity."""
    community = community_detector.get_community_for_entity(notebook_id, entity)
    if not community:
        return {"entity": entity, "community": None}
    
    return {
        "entity": entity,
        "community": {
            "id": community.id,
            "name": community.name,
            "entities": community.entities,
            "summary": community.summary,
            "size": community.size
        }
    }
