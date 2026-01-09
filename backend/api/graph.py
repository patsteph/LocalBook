"""Knowledge Graph API endpoints

v0.6.5: Now uses BERTopic for topic modeling instead of custom concept extraction.
Topics are discovered automatically from document chunks with two-stage naming:
1. Instant c-TF-IDF names at ingestion
2. Background LLM enhancement for more readable names
"""
import asyncio
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from models.knowledge_graph import (
    GraphData, GraphNode, GraphEdge, ConceptCluster,
    ConceptExtractionRequest, ConceptExtractionResult
)
# NOTE: knowledge_graph_service is legacy (1395 lines) - replaced by BERTopic
# Keeping services/knowledge_graph.py for now as migration_manager references it
from services.topic_modeling import topic_modeling_service


router = APIRouter(prefix="/graph", tags=["knowledge-graph"])


# =============================================================================
# Request/Response Models
# =============================================================================

class GraphQueryParams(BaseModel):
    notebook_id: Optional[str] = None
    center_node_id: Optional[str] = None
    depth: int = 2
    include_clusters: bool = True
    min_link_strength: float = 0.3
    cross_notebook: bool = False


class ConnectionsResponse(BaseModel):
    related_sources: List[Dict[str, Any]]
    concepts: List[Dict[str, Any]]
    clusters: List[Dict[str, Any]]


class GraphStatsResponse(BaseModel):
    concepts: int
    links: int
    clusters: int


# =============================================================================
# Graph Data Endpoints (v0.6.5: Uses BERTopic topics)
# =============================================================================

# Color palette for topics
TOPIC_COLORS = [
    "#8B5CF6",  # Purple
    "#3B82F6",  # Blue
    "#10B981",  # Green
    "#F59E0B",  # Amber
    "#EF4444",  # Red
    "#EC4899",  # Pink
    "#6366F1",  # Indigo
    "#14B8A6",  # Teal
    "#F97316",  # Orange
    "#84CC16",  # Lime
]


async def _build_graph_from_topics(notebook_id: Optional[str] = None) -> GraphData:
    """Build GraphData from BERTopic topics for visualization."""
    topics = await topic_modeling_service.get_topics(notebook_id)
    
    nodes = []
    edges = []
    clusters = []
    
    # Create a node for each topic
    for i, topic in enumerate(topics):
        color = TOPIC_COLORS[i % len(TOPIC_COLORS)]
        
        # Main topic node
        node = GraphNode(
            id=topic.id,
            label=topic.display_name,
            type="topic",
            size=max(10, min(50, topic.document_count * 3)),
            color=color,
            metadata={
                "topic_id": topic.topic_id,
                "document_count": topic.document_count,
                "keywords": [kw for kw, _ in topic.keywords[:5]],
                "enhanced": topic.enhanced_name is not None
            }
        )
        nodes.append(node)
        
        # Create keyword nodes for each topic
        for j, (keyword, weight) in enumerate(topic.keywords[:5]):
            kw_id = f"{topic.id}_kw_{j}"
            kw_node = GraphNode(
                id=kw_id,
                label=keyword,
                type="keyword",
                size=max(5, min(20, weight * 200)),
                color=color,
                metadata={"weight": weight, "parent_topic": topic.id}
            )
            nodes.append(kw_node)
            
            # Edge from topic to keyword
            edge = GraphEdge(
                id=f"edge_{topic.id}_{kw_id}",
                source=topic.id,
                target=kw_id,
                label="has_keyword",
                strength=weight,
                dashed=False
            )
            edges.append(edge)
        
        # Create cluster entry
        cluster = ConceptCluster(
            id=topic.id,
            name=topic.display_name,
            description=", ".join([kw for kw, _ in topic.keywords[:5]]),
            concept_ids=[f"{topic.id}_kw_{j}" for j in range(min(5, len(topic.keywords)))],
            size=topic.document_count,
            coherence_score=0.8,
            notebook_ids=topic.notebook_ids
        )
        clusters.append(cluster)
    
    # Add edges between topics that share keywords
    for i, topic1 in enumerate(topics):
        kw1 = set(kw for kw, _ in topic1.keywords[:10])
        for j, topic2 in enumerate(topics[i+1:], i+1):
            kw2 = set(kw for kw, _ in topic2.keywords[:10])
            shared = kw1 & kw2
            if len(shared) >= 2:
                edge = GraphEdge(
                    id=f"edge_{topic1.id}_{topic2.id}",
                    source=topic1.id,
                    target=topic2.id,
                    label="related",
                    strength=len(shared) / 10,
                    dashed=True
                )
                edges.append(edge)
    
    return GraphData(nodes=nodes, edges=edges, clusters=clusters)


@router.post("/query", response_model=GraphData)
async def query_graph(params: GraphQueryParams):
    """
    Query the knowledge graph for visualization.
    v0.6.5: Returns BERTopic topics as nodes with keyword children.
    """
    notebook_id = None if params.cross_notebook else params.notebook_id
    return await _build_graph_from_topics(notebook_id)


@router.get("/notebook/{notebook_id}", response_model=GraphData)
async def get_notebook_graph(
    notebook_id: str,
    include_clusters: bool = True,
    min_link_strength: float = 0.3
):
    """Get the knowledge graph for a specific notebook"""
    return await _build_graph_from_topics(notebook_id)


@router.get("/all", response_model=GraphData)
async def get_full_graph(
    include_clusters: bool = True,
    min_link_strength: float = 0.3
):
    """Get the complete knowledge graph across all notebooks"""
    return await _build_graph_from_topics(None)


@router.get("/notebook/{notebook_id}/with-insights", response_model=GraphData)
async def get_notebook_graph_with_insights(notebook_id: str):
    """
    Get knowledge graph with insight nodes (contradictions, gaps).
    
    Adds special node types:
    - type="conflict": Red nodes for detected contradictions
    - type="gap": Yellow nodes for knowledge gaps (topics mentioned but not covered)
    """
    from services.contradiction_detector import contradiction_detector
    
    # Get base graph
    graph = await _build_graph_from_topics(notebook_id)
    
    # Get contradictions
    report = await contradiction_detector.get_cached_report(notebook_id)
    
    if report and report.contradictions:
        for contra in report.contradictions:
            if contra.dismissed:
                continue
            
            # Create conflict node
            conflict_node = GraphNode(
                id=f"conflict_{contra.id}",
                label=f"⚠️ {contra.contradiction_type.title()} Conflict",
                type="conflict",
                size=25,
                color="#EF4444",  # Red
                metadata={
                    "explanation": contra.explanation,
                    "severity": contra.severity,
                    "source_a": contra.claim_a.source_name,
                    "source_b": contra.claim_b.source_name,
                    "claim_a": contra.claim_a.text,
                    "claim_b": contra.claim_b.text,
                    "resolution_hint": contra.resolution_hint,
                }
            )
            graph.nodes.append(conflict_node)
            
            # Try to link to related topic nodes
            for node in graph.nodes:
                if node.type == "topic":
                    # Check if topic keywords relate to the conflict
                    keywords = node.metadata.get("keywords", [])
                    claim_words = set((contra.claim_a.text + " " + contra.claim_b.text).lower().split())
                    if any(kw.lower() in claim_words for kw in keywords):
                        edge = GraphEdge(
                            id=f"edge_conflict_{contra.id}_{node.id}",
                            source=f"conflict_{contra.id}",
                            target=node.id,
                            label="conflicts_with",
                            strength=0.8,
                            color="#EF4444",
                            dashed=False
                        )
                        graph.edges.append(edge)
                        break  # Just link to first matching topic
    
    return graph


# =============================================================================
# Source Connections
# =============================================================================

@router.get("/connections/{source_id}")
async def get_source_connections(
    source_id: str,
    notebook_id: str,
    limit: int = 20
):
    """
    Get connections for a specific source document.
    v0.6.5: Returns topics associated with this source.
    """
    topics = await topic_modeling_service.get_topics_for_source(source_id)
    return {
        "source_id": source_id,
        "topics": [t.to_dict() for t in topics[:limit]],
        "related_sources": [],  # Could be expanded later
        "clusters": []
    }


# =============================================================================
# Concept Management
# =============================================================================

@router.post("/extract")
async def extract_concepts(request: ConceptExtractionRequest):
    """
    Extract concepts from text.
    v0.6.5: Deprecated - BERTopic handles topic discovery automatically.
    This endpoint is kept for backwards compatibility but does nothing.
    """
    return {"concepts": [], "links": [], "message": "v0.6.5: Use BERTopic topic modeling instead"}


@router.get("/concepts")
async def list_concepts(
    notebook_id: Optional[str] = None,
    limit: int = 100
):
    """List all concepts (now topics and keywords)"""
    # v0.6.5: Return topics and their keywords as "concepts"
    graph_data = await _build_graph_from_topics(notebook_id)
    
    concepts = [
        {
            "id": node.id,
            "name": node.label,
            "type": node.type,
            "size": node.size,
            "metadata": node.metadata
        }
        for node in graph_data.nodes
    ][:limit]
    
    return {"concepts": concepts}


@router.get("/concepts/search")
async def search_concepts(query: str, limit: int = 20):
    """Search topics and keywords by name"""
    # v0.6.5: Search in BERTopic topics
    results = await topic_modeling_service.find_topics(query)
    
    matching = [
        {
            "topic_id": topic_id,
            "score": score
        }
        for topic_id, score in results[:limit]
    ]
    
    return {"results": matching}


# =============================================================================
# Clustering
# =============================================================================

@router.post("/cluster")
async def run_clustering(background_tasks: BackgroundTasks):
    """
    Trigger clustering to discover emergent themes.
    v0.6.5: Deprecated - BERTopic handles clustering automatically.
    Use /build/{notebook_id} to rebuild topics instead.
    """
    return {"message": "v0.6.5: Clustering is automatic. Use Rebuild Topics instead.", "status": "deprecated"}


@router.get("/clusters")
async def list_clusters(notebook_id: Optional[str] = None):
    """List all topic clusters"""
    # v0.6.5: Return BERTopic topics as clusters
    graph_data = await _build_graph_from_topics(notebook_id)
    
    return {
        "clusters": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "size": c.size,
                "coherence_score": c.coherence_score,
                "concept_ids": c.concept_ids,
                "notebook_ids": c.notebook_ids
            }
            for c in graph_data.clusters
        ]
    }


@router.get("/themes/{notebook_id}")
async def get_notebook_themes(notebook_id: str, limit: int = 10):
    """
    Get key themes discovered in a notebook.
    v0.6.5: Now uses BERTopic topics instead of custom concept clusters.
    Returns topics with their keywords for display in ThemesPanel.
    """
    # Get topics from BERTopic service
    topics = await topic_modeling_service.get_topics(notebook_id)
    
    # Build themes list from topics
    themes = []
    for topic in topics[:limit]:
        # Get keyword names for display
        keywords = [kw for kw, _ in topic.keywords[:10]]
        
        themes.append({
            "id": topic.id,
            "name": topic.display_name,
            "description": ", ".join(keywords[:5]),
            "concepts": keywords,  # Keywords serve as "concepts" for click-to-chat
            "concept_count": topic.document_count,
            "coherence_score": 0.8,  # BERTopic doesn't provide this directly
            "topic_id": topic.topic_id,
            "enhanced": topic.enhanced_name is not None
        })
    
    # Get stats
    stats = await topic_modeling_service.get_stats(notebook_id)
    
    return {
        "notebook_id": notebook_id,
        "themes": themes,
        "theme_count": len(themes),
        "top_concepts": [],  # No longer used - topics contain keywords
        "total_concepts": stats.get("total_documents", 0)
    }


# =============================================================================
# Stats
# =============================================================================

@router.get("/stats", response_model=GraphStatsResponse)
async def get_graph_stats(notebook_id: Optional[str] = None):
    """Get knowledge graph statistics, optionally filtered by notebook"""
    # v0.6.5: Use BERTopic stats
    stats = await topic_modeling_service.get_stats(notebook_id)
    graph_data = await _build_graph_from_topics(notebook_id)
    return GraphStatsResponse(
        concepts=len([n for n in graph_data.nodes if n.type == "topic"]),
        links=len(graph_data.edges),
        clusters=len(graph_data.clusters)
    )


# =============================================================================
# Link Management
# =============================================================================

@router.get("/links")
async def list_links(
    notebook_id: Optional[str] = None,
    link_type: Optional[str] = None,
    min_strength: float = 0.0,
    limit: int = 100
):
    """List all links in the knowledge graph"""
    # v0.6.5: Use BERTopic graph
    graph_data = await _build_graph_from_topics(notebook_id)
    
    edges = [e for e in graph_data.edges if e.strength >= min_strength]
    if link_type:
        edges = [e for e in edges if e.label == link_type]
    
    return {
        "links": [
            {
                "id": e.id,
                "source": e.source,
                "target": e.target,
                "type": e.label,
                "strength": e.strength,
                "cross_notebook": e.dashed
            }
            for e in edges[:limit]
        ]
    }


@router.get("/link-types")
async def get_link_types():
    """Get available link types"""
    from models.knowledge_graph import LinkType
    return {
        "types": [
            {"value": lt.value, "label": lt.value.replace("_", " ").title()}
            for lt in LinkType
        ]
    }


@router.post("/build/{notebook_id}")
async def build_graph_for_notebook(notebook_id: str, background_tasks: BackgroundTasks):
    """
    Build/rebuild topics for a notebook.
    v0.6.5: Processes all sources through BERTopic to discover topics.
    """
    import threading
    from storage.source_store import source_store
    from services.rag_engine import rag_engine
    from api.constellation_ws import notify_build_progress, notify_build_complete
    
    def run_rebuild_sync():
        """Synchronous wrapper to run async rebuild in a new event loop."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(rebuild_topics_async())
        finally:
            loop.close()
    
    async def rebuild_topics_async():
        try:
            print(f"[TopicModel] Starting rebuild for notebook {notebook_id}")
            
            # Get all sources for this notebook
            sources = await source_store.list(notebook_id)
            if not sources:
                print(f"[TopicModel] No sources found for notebook {notebook_id}")
                await notify_build_complete()
                return
            
            print(f"[TopicModel] Found {len(sources)} sources to process")
            
            await notify_build_progress({
                "notebook_id": notebook_id,
                "progress": 5,
                "status": f"Collecting {len(sources)} sources..."
            })
            
            # STEP 1: Collect ALL chunks and embeddings first
            all_chunks = []
            all_embeddings = []
            chunk_metadata = []  # Track which source each chunk belongs to
            
            for i, source in enumerate(sources):
                content = source.get("content", "")
                if not content or len(content) < 100:
                    print(f"[TopicModel] Skipping source {i+1}: no content or too short")
                    continue
                
                source_id = source.get("id", "")
                filename = source.get('filename', 'unknown')
                print(f"[TopicModel] Chunking source {i+1}/{len(sources)}: {filename}")
                
                # Chunk the content
                chunks = rag_engine._chunk_text(content)
                if not chunks:
                    print(f"[TopicModel] No chunks generated for source {i+1}")
                    continue
                
                # Generate embeddings for chunks
                embeddings = rag_engine.encode(chunks)
                
                # Collect
                all_chunks.extend(chunks)
                all_embeddings.append(embeddings)
                for chunk in chunks:
                    chunk_metadata.append({"source_id": source_id, "notebook_id": notebook_id})
                
                progress = int(5 + (i + 1) / len(sources) * 40)
                await notify_build_progress({
                    "notebook_id": notebook_id,
                    "progress": progress,
                    "status": f"Chunked {i + 1}/{len(sources)} sources ({len(all_chunks)} chunks)"
                })
                await asyncio.sleep(0.05)
            
            if not all_chunks:
                print(f"[TopicModel] No chunks collected from any source")
                await notify_build_complete()
                return
            
            # Combine embeddings
            import numpy as np
            combined_embeddings = np.vstack(all_embeddings) if all_embeddings else None
            
            print(f"[TopicModel] Collected {len(all_chunks)} chunks, fitting BERTopic...")
            
            await notify_build_progress({
                "notebook_id": notebook_id,
                "progress": 50,
                "status": f"Discovering topics from {len(all_chunks)} chunks..."
            })
            
            # STEP 2: Fit BERTopic on ALL documents at once
            result = await topic_modeling_service.fit_all(
                texts=all_chunks,
                embeddings=combined_embeddings,
                metadata=chunk_metadata,
                notebook_id=notebook_id
            )
            
            print(f"[TopicModel] BERTopic fit complete: {result}")
            
            await notify_build_progress({
                "notebook_id": notebook_id,
                "progress": 95,
                "status": f"Found {result.get('topics_found', 0)} topics"
            })
            
            # Get final stats
            stats = await topic_modeling_service.get_stats(notebook_id)
            print(f"[TopicModel] Rebuild complete: {stats}")
            
            await notify_build_progress({
                "notebook_id": notebook_id,
                "progress": 100,
                "topics_found": stats.get("total_topics", 0)
            })
            
            print(f"[TopicModel] Built topics for notebook {notebook_id}: {stats}")
            await notify_build_complete()
            
        except Exception as e:
            import traceback
            print(f"[TopicModel] Rebuild error: {e}")
            traceback.print_exc()
            await notify_build_complete()
    
    # Use threading for reliable background execution in bundled app
    thread = threading.Thread(target=run_rebuild_sync, daemon=True)
    thread.start()
    
    return {
        "message": "Building topics from sources",
        "status": "running"
    }


@router.delete("/reset/{notebook_id}")
async def reset_knowledge_graph(notebook_id: str):
    """
    Reset the knowledge graph for a notebook.
    v0.6.5: Clears topic model data for the notebook.
    """
    try:
        # Clear topics (BERTopic doesn't support per-notebook reset easily,
        # so we just rebuild from scratch)
        await topic_modeling_service.reset()
        
        return {
            "success": True,
            "message": f"Topic model reset for notebook {notebook_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Topic-specific endpoints (v0.6.5)
# =============================================================================

@router.get("/topics/{notebook_id}")
async def get_topics(notebook_id: str):
    """
    Get all topics for a notebook.
    v0.6.5: Returns BERTopic-discovered topics.
    """
    topics = await topic_modeling_service.get_topics(notebook_id)
    return {
        "notebook_id": notebook_id,
        "topics": [t.to_dict() for t in topics],
        "count": len(topics)
    }


@router.get("/topic/{topic_id}")
async def get_topic(topic_id: int):
    """
    Get details for a specific topic.
    """
    topic = await topic_modeling_service.get_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic.to_dict()


@router.get("/topic-stats")
async def get_topic_stats(notebook_id: Optional[str] = None):
    """
    Get topic modeling statistics.
    """
    stats = await topic_modeling_service.get_stats(notebook_id)
    return stats
