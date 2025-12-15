"""Knowledge Graph models for bi-directional linking and concept emergence"""
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
import uuid


class LinkType(str, Enum):
    """Types of relationships between concepts/chunks"""
    REFERENCES = "references"       # A mentions B
    CONTRADICTS = "contradicts"     # A disagrees with B
    EXPANDS = "expands"             # A provides more detail on B
    EXAMPLE_OF = "example_of"       # A is an example of B
    SIMILAR_TO = "similar_to"       # A is semantically similar to B
    PRECEDES = "precedes"           # A comes before B (temporal)
    CAUSES = "causes"               # A leads to B
    PART_OF = "part_of"             # A is a component of B


class Concept(BaseModel):
    """
    A concept extracted from documents.
    Concepts are the nodes in the knowledge graph.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str                         # The concept name/label
    description: Optional[str] = None # Brief description
    
    # Source tracking
    source_chunk_ids: List[str] = Field(default_factory=list)  # Chunks that mention this
    source_notebook_ids: List[str] = Field(default_factory=list)  # Notebooks containing this
    
    # Metadata
    frequency: int = 1                # How often it appears
    importance: float = 0.5           # Computed importance (0-1)
    
    # Clustering
    cluster_id: Optional[str] = None  # Which cluster this belongs to
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Embedding stored separately in LanceDB


class ConceptLink(BaseModel):
    """
    A link between two concepts or chunks.
    Links are the edges in the knowledge graph.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    # Source and target (can be concept IDs or chunk IDs)
    source_id: str
    target_id: str
    source_type: str = "concept"      # "concept" or "chunk"
    target_type: str = "concept"      # "concept" or "chunk"
    
    # Relationship
    link_type: LinkType
    strength: float = Field(default=0.5, ge=0.0, le=1.0)  # How strong the connection
    
    # Evidence
    evidence: Optional[str] = None    # Text supporting this link
    source_notebook_id: Optional[str] = None
    
    # Metadata
    auto_detected: bool = True        # Was this auto-detected or user-created
    verified: bool = False            # Has user verified this link
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ConceptCluster(BaseModel):
    """
    A cluster of related concepts (emergent theme).
    Discovered via HDBSCAN clustering on concept embeddings.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str                         # LLM-generated name for the cluster
    description: Optional[str] = None # LLM-generated description
    
    # Members
    concept_ids: List[str] = Field(default_factory=list)
    
    # Metadata
    coherence_score: float = 0.0      # How coherent the cluster is
    size: int = 0                     # Number of concepts
    
    # Source notebooks
    notebook_ids: List[str] = Field(default_factory=list)
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class GraphNode(BaseModel):
    """A node in the graph visualization"""
    id: str
    label: str
    type: str                         # "concept", "chunk", "cluster"
    color: Optional[str] = None       # Notebook color or type color
    size: float = 1.0                 # Node size based on importance
    notebook_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """An edge in the graph visualization"""
    id: str
    source: str
    target: str
    label: str                        # Link type
    strength: float = 0.5
    color: Optional[str] = None
    dashed: bool = False              # True for cross-notebook links


class GraphData(BaseModel):
    """Complete graph data for visualization"""
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)
    clusters: List[ConceptCluster] = Field(default_factory=list)


# =============================================================================
# Request/Response Models
# =============================================================================

class ConceptExtractionRequest(BaseModel):
    """Request to extract concepts from text"""
    text: str
    source_id: str
    notebook_id: str
    chunk_index: int = 0


class ConceptExtractionResult(BaseModel):
    """Result of concept extraction"""
    concepts: List[Concept] = Field(default_factory=list)
    links: List[ConceptLink] = Field(default_factory=list)


class LinkDetectionRequest(BaseModel):
    """Request to detect links between chunks"""
    chunk_id: str
    notebook_id: str
    max_links: int = 10


class GraphQueryRequest(BaseModel):
    """Request to query the knowledge graph"""
    notebook_id: Optional[str] = None  # None for cross-notebook
    center_node_id: Optional[str] = None  # Start from specific node
    depth: int = 2                     # How many hops from center
    include_clusters: bool = True
    min_link_strength: float = 0.3


class ContradictionReport(BaseModel):
    """A detected contradiction between sources"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    chunk_id_1: str
    chunk_id_2: str
    text_1: str
    text_2: str
    explanation: str                   # Why these contradict
    severity: str = "medium"           # "low", "medium", "high"
    notebook_ids: List[str] = Field(default_factory=list)
    resolved: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
