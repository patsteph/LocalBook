"""Knowledge Graph API endpoints"""
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from models.knowledge_graph import (
    GraphData, GraphNode, GraphEdge, ConceptCluster,
    ConceptExtractionRequest, ConceptExtractionResult
)
from services.knowledge_graph import knowledge_graph_service


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
# Graph Data Endpoints
# =============================================================================

@router.post("/query", response_model=GraphData)
async def query_graph(params: GraphQueryParams):
    """
    Query the knowledge graph for visualization.
    Returns nodes, edges, and clusters.
    """
    notebook_id = None if params.cross_notebook else params.notebook_id
    
    graph_data = await knowledge_graph_service.get_graph_data(
        notebook_id=notebook_id,
        center_node_id=params.center_node_id,
        depth=params.depth,
        include_clusters=params.include_clusters,
        min_link_strength=params.min_link_strength
    )
    
    return graph_data


@router.get("/notebook/{notebook_id}", response_model=GraphData)
async def get_notebook_graph(
    notebook_id: str,
    include_clusters: bool = True,
    min_link_strength: float = 0.3
):
    """Get the knowledge graph for a specific notebook"""
    return await knowledge_graph_service.get_graph_data(
        notebook_id=notebook_id,
        include_clusters=include_clusters,
        min_link_strength=min_link_strength
    )


@router.get("/all", response_model=GraphData)
async def get_full_graph(
    include_clusters: bool = True,
    min_link_strength: float = 0.3
):
    """Get the complete knowledge graph across all notebooks"""
    return await knowledge_graph_service.get_graph_data(
        notebook_id=None,
        include_clusters=include_clusters,
        min_link_strength=min_link_strength
    )


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
    Returns related sources, shared concepts, and cluster memberships.
    """
    connections = await knowledge_graph_service.get_connections_for_source(
        source_id=source_id,
        notebook_id=notebook_id,
        limit=limit
    )
    return connections


# =============================================================================
# Concept Management
# =============================================================================

@router.post("/extract", response_model=ConceptExtractionResult)
async def extract_concepts(request: ConceptExtractionRequest):
    """
    Extract concepts from text.
    Usually called automatically during document ingestion.
    """
    result = await knowledge_graph_service.extract_concepts(request)
    return result


@router.get("/concepts")
async def list_concepts(
    notebook_id: Optional[str] = None,
    limit: int = 100
):
    """List all concepts, optionally filtered by notebook"""
    graph_data = await knowledge_graph_service.get_graph_data(
        notebook_id=notebook_id,
        include_clusters=False
    )
    
    concepts = [
        {
            "id": node.id,
            "name": node.label,
            "notebook_id": node.notebook_id,
            "size": node.size,
            "metadata": node.metadata
        }
        for node in graph_data.nodes
        if node.type == "concept"
    ][:limit]
    
    return {"concepts": concepts}


@router.get("/concepts/search")
async def search_concepts(query: str, limit: int = 20):
    """Search concepts by name"""
    # Get all concepts and filter by name
    graph_data = await knowledge_graph_service.get_graph_data(include_clusters=False)
    
    query_lower = query.lower()
    matching = [
        {
            "id": node.id,
            "name": node.label,
            "notebook_id": node.notebook_id
        }
        for node in graph_data.nodes
        if query_lower in node.label.lower()
    ][:limit]
    
    return {"concepts": matching}


# =============================================================================
# Clustering
# =============================================================================

@router.post("/cluster")
async def run_clustering(background_tasks: BackgroundTasks):
    """
    Trigger clustering to discover emergent themes.
    Runs as a background task.
    """
    background_tasks.add_task(knowledge_graph_service.run_clustering)
    return {"message": "Clustering started", "status": "running"}


@router.get("/clusters")
async def list_clusters(notebook_id: Optional[str] = None):
    """List all concept clusters"""
    graph_data = await knowledge_graph_service.get_graph_data(
        notebook_id=notebook_id,
        include_clusters=True
    )
    
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
    Returns deduplicated clusters with their associated concept names for display.
    """
    graph_data = await knowledge_graph_service.get_graph_data(
        notebook_id=notebook_id,
        include_clusters=True
    )
    
    # Build concept lookup
    concept_lookup = {node.id: node.label for node in graph_data.nodes if node.type == "concept"}
    
    # Deduplicate clusters by name - merge concepts from clusters with same name
    merged_themes: Dict[str, Dict[str, Any]] = {}
    for cluster in graph_data.clusters:
        name = (cluster.name or "").strip().lower()
        if not name:
            continue
            
        if name not in merged_themes:
            merged_themes[name] = {
                "id": cluster.id,
                "name": cluster.name,
                "description": cluster.description,
                "concept_ids": set(cluster.concept_ids),
                "coherence_score": cluster.coherence_score
            }
        else:
            # Merge concepts from duplicate cluster
            merged_themes[name]["concept_ids"].update(cluster.concept_ids)
            # Keep higher coherence score
            if cluster.coherence_score > merged_themes[name]["coherence_score"]:
                merged_themes[name]["coherence_score"] = cluster.coherence_score
                merged_themes[name]["description"] = cluster.description
    
    # Build final themes list
    themes = []
    for theme_data in merged_themes.values():
        concept_ids = list(theme_data["concept_ids"])
        # Only include concepts that have names (filter out orphaned IDs)
        concept_names = [concept_lookup[cid] for cid in concept_ids if cid in concept_lookup][:10]
        if not concept_names:
            continue  # Skip themes with no resolvable concepts
        
        # Clean theme name - remove trailing punctuation and quotes
        theme_name = (theme_data["name"] or "").rstrip('.,;:!?').strip('"\'')
        
        themes.append({
            "id": theme_data["id"],
            "name": theme_name,
            "description": theme_data["description"],
            "concepts": concept_names,
            "concept_count": len([cid for cid in concept_ids if cid in concept_lookup]),
            "coherence_score": theme_data["coherence_score"]
        })
    
    # Sort by concept count (most to least) and limit
    themes = sorted(themes, key=lambda t: t["concept_count"], reverse=True)[:limit]
    
    # Also get top standalone concepts not in clusters
    clustered_concept_ids = set()
    for c in graph_data.clusters:
        clustered_concept_ids.update(c.concept_ids)
    
    top_concepts = [
        {"id": node.id, "name": node.label, "size": node.size}
        for node in sorted(graph_data.nodes, key=lambda n: n.size, reverse=True)
        if node.type == "concept" and node.id not in clustered_concept_ids
    ][:20]
    
    return {
        "notebook_id": notebook_id,
        "themes": themes,
        "theme_count": len(themes),
        "top_concepts": top_concepts,
        "total_concepts": len([n for n in graph_data.nodes if n.type == "concept"])
    }


# =============================================================================
# Stats
# =============================================================================

@router.get("/stats", response_model=GraphStatsResponse)
async def get_graph_stats(notebook_id: Optional[str] = None):
    """Get knowledge graph statistics, optionally filtered by notebook"""
    stats = await knowledge_graph_service.get_stats(notebook_id=notebook_id)
    return GraphStatsResponse(**stats)


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
    graph_data = await knowledge_graph_service.get_graph_data(
        notebook_id=notebook_id,
        min_link_strength=min_strength
    )
    
    edges = graph_data.edges
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
    Build/rebuild the knowledge graph for a notebook by extracting concepts from all sources.
    Useful for existing notebooks that don't have graph data yet.
    """
    from storage.source_store import source_store
    from services.rag_engine import rag_service
    from api.constellation_ws import notify_build_progress, notify_build_complete
    
    # Get all sources for this notebook
    sources = await source_store.list(notebook_id)
    
    if not sources:
        raise HTTPException(status_code=404, detail="No sources found in notebook")
    
    async def extract_all():
        total_concepts = 0
        total_chunks = 0
        processed_chunks = 0
        
        print(f"[KG Build] Starting build for notebook {notebook_id} with {len(sources)} sources")
        
        # First, count total chunks to process
        sources_with_content = 0
        for source in sources:
            content = source.get("content", "")
            if content:
                sources_with_content += 1
                chunks = rag_service._chunk_text(content)
                total_chunks += len([c for i, c in enumerate(chunks) if i % 3 == 0])
            else:
                print(f"[KG Build] Source {source.get('filename', 'unknown')} has no content field")
        
        print(f"[KG Build] Found {sources_with_content} sources with content, {total_chunks} chunks to process")
        
        if total_chunks == 0:
            print(f"[KG Build] No chunks to process - aborting")
            await notify_build_complete()
            return
        
        for source in sources:
            content = source.get("content", "")
            if not content:
                continue
            
            # Chunk the content
            chunks = rag_service._chunk_text(content)
            
            # Extract concepts (every 3rd chunk for speed)
            for i, chunk in enumerate(chunks):
                if i % 3 != 0:
                    continue
                
                request = ConceptExtractionRequest(
                    text=chunk,
                    source_id=source["id"],
                    chunk_index=i,
                    notebook_id=notebook_id
                )
                
                result = await knowledge_graph_service.extract_concepts(request)
                total_concepts += len(result.concepts)
                processed_chunks += 1
                
                # Send progress update
                progress = (processed_chunks / total_chunks * 100) if total_chunks > 0 else 0
                await notify_build_progress({
                    "notebook_id": notebook_id,
                    "progress": round(progress, 1),
                    "concepts_found": total_concepts,
                    "chunks_processed": processed_chunks,
                    "total_chunks": total_chunks
                })
        
        print(f"[KG] Built graph for notebook {notebook_id}: {total_concepts} concepts extracted")
        await notify_build_complete()
    
    background_tasks.add_task(extract_all)
    
    return {
        "message": f"Building graph for {len(sources)} sources",
        "status": "running",
        "sources": len(sources)
    }


@router.delete("/reset/{notebook_id}")
async def reset_knowledge_graph(notebook_id: str):
    """
    Reset the knowledge graph for a notebook.
    Clears all concepts, links, and clusters to allow a fresh rebuild.
    """
    try:
        # Clear concepts for this notebook
        concepts_cleared = await knowledge_graph_service.clear_notebook_data(notebook_id)
        
        return {
            "success": True,
            "message": f"Knowledge graph reset for notebook {notebook_id}",
            "concepts_cleared": concepts_cleared
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
