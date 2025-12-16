"""
Knowledge Graph Service - Automatic bi-directional linking and concept emergence

This service:
1. Extracts concepts from documents during ingestion
2. Detects relationships between chunks/concepts
3. Runs clustering to discover emergent themes
4. Detects contradictions between sources
5. Provides graph data for visualization
"""
import json
import re
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
import threading
import lancedb
import pyarrow as pa
from sentence_transformers import SentenceTransformer
import httpx
import numpy as np

from models.knowledge_graph import (
    Concept, ConceptLink, ConceptCluster, LinkType,
    GraphNode, GraphEdge, GraphData,
    ConceptExtractionRequest, ConceptExtractionResult,
    ContradictionReport
)
from config import settings
from storage.notebook_store import notebook_store


class KnowledgeGraphService:
    """
    Manages the knowledge graph for all notebooks.
    Singleton pattern for consistent state.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self.data_dir = Path(settings.data_dir)
        self.graph_dir = self.data_dir / "knowledge_graph"
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        
        # LanceDB for concepts and embeddings
        self.db_path = self.graph_dir / "graph_db"
        self._init_db()
        
        # Embedding model (lazy loaded)
        self._embedding_model = None
        
        # LLM settings
        self.ollama_url = settings.ollama_base_url
        self.extraction_model = settings.ollama_fast_model
        
        # Clustering settings
        self.min_cluster_size = 3
        self.min_samples = 2
        
        self._initialized = True
    
    @property
    def embedding_model(self) -> SentenceTransformer:
        """Lazy load embedding model"""
        if self._embedding_model is None:
            self._embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        return self._embedding_model
    
    def get_embedding(self, text: str) -> List[float]:
        """Generate embedding for text"""
        return self.embedding_model.encode(text).tolist()
    
    def _init_db(self) -> None:
        """Initialize LanceDB tables for knowledge graph"""
        self.db = lancedb.connect(str(self.db_path))
        
        # Concepts table
        if "concepts" not in self.db.table_names():
            schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("name", pa.string()),
                pa.field("description", pa.string()),
                pa.field("source_chunk_ids", pa.string()),  # JSON array
                pa.field("source_notebook_ids", pa.string()),  # JSON array
                pa.field("frequency", pa.int32()),
                pa.field("importance", pa.float32()),
                pa.field("cluster_id", pa.string()),
                pa.field("created_at", pa.string()),
                pa.field("updated_at", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), 384)),
            ])
            self.db.create_table("concepts", schema=schema)
        
        # Links table
        if "links" not in self.db.table_names():
            schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("source_id", pa.string()),
                pa.field("target_id", pa.string()),
                pa.field("source_type", pa.string()),
                pa.field("target_type", pa.string()),
                pa.field("link_type", pa.string()),
                pa.field("strength", pa.float32()),
                pa.field("evidence", pa.string()),
                pa.field("source_notebook_id", pa.string()),
                pa.field("auto_detected", pa.bool_()),
                pa.field("verified", pa.bool_()),
                pa.field("created_at", pa.string()),
            ])
            self.db.create_table("links", schema=schema)
        
        # Clusters table
        if "clusters" not in self.db.table_names():
            schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("name", pa.string()),
                pa.field("description", pa.string()),
                pa.field("concept_ids", pa.string()),  # JSON array
                pa.field("coherence_score", pa.float32()),
                pa.field("size", pa.int32()),
                pa.field("notebook_ids", pa.string()),  # JSON array
                pa.field("created_at", pa.string()),
                pa.field("updated_at", pa.string()),
            ])
            self.db.create_table("clusters", schema=schema)
        
        # Contradictions table
        if "contradictions" not in self.db.table_names():
            schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("chunk_id_1", pa.string()),
                pa.field("chunk_id_2", pa.string()),
                pa.field("text_1", pa.string()),
                pa.field("text_2", pa.string()),
                pa.field("explanation", pa.string()),
                pa.field("severity", pa.string()),
                pa.field("notebook_ids", pa.string()),  # JSON array
                pa.field("resolved", pa.bool_()),
                pa.field("created_at", pa.string()),
            ])
            self.db.create_table("contradictions", schema=schema)
    
    # =========================================================================
    # Concept Extraction
    # =========================================================================
    
    async def extract_concepts(self, request: ConceptExtractionRequest) -> ConceptExtractionResult:
        """
        Extract concepts from text using LLM.
        Called during document ingestion.
        """
        result = ConceptExtractionResult()
        
        # Use LLM to extract concepts
        extraction_prompt = self._build_concept_extraction_prompt(request.text)
        
        try:
            extracted = await self._call_llm(extraction_prompt)
            
            if extracted:
                # Process extracted concepts
                for concept_data in extracted.get("concepts", []):
                    concept = await self._create_or_update_concept(
                        name=concept_data.get("name", ""),
                        description=concept_data.get("description"),
                        chunk_id=f"{request.source_id}_{request.chunk_index}",
                        notebook_id=request.notebook_id
                    )
                    if concept:
                        result.concepts.append(concept)
                
                # Create links between extracted concepts
                for link_data in extracted.get("relationships", []):
                    link = await self._create_link_from_extraction(
                        link_data,
                        request.notebook_id,
                        result.concepts
                    )
                    if link:
                        result.links.append(link)
        
        except Exception as e:
            print(f"Concept extraction error: {e}")
        
        return result
    
    def _build_concept_extraction_prompt(self, text: str) -> str:
        """Build prompt for concept extraction"""
        return f"""Extract key concepts and their relationships from this text.

Text: "{text[:2000]}"

Respond in JSON:
{{
    "concepts": [
        {{"name": "concept name", "description": "brief description"}}
    ],
    "relationships": [
        {{"source": "concept1", "target": "concept2", "type": "references|contradicts|expands|example_of|similar_to|precedes|causes|part_of"}}
    ]
}}

Rules:
- Extract 3-7 key concepts (nouns, named entities, important ideas)
- Only include clear relationships
- Use lowercase for concept names
- Keep descriptions under 20 words

Respond ONLY with JSON:"""
    
    async def _create_or_update_concept(
        self,
        name: str,
        description: Optional[str],
        chunk_id: str,
        notebook_id: str
    ) -> Optional[Concept]:
        """Create a new concept or update existing one"""
        if not name or len(name) < 2:
            return None
        
        name = name.lower().strip()
        
        # Check if concept exists
        table = self.db.open_table("concepts")
        existing = table.search(self.get_embedding(name)).limit(1).to_list()
        
        if existing and existing[0].get("name", "").lower() == name:
            # Update existing concept
            record = existing[0]
            chunk_ids = json.loads(record.get("source_chunk_ids", "[]"))
            notebook_ids = json.loads(record.get("source_notebook_ids", "[]"))
            
            if chunk_id not in chunk_ids:
                chunk_ids.append(chunk_id)
            if notebook_id not in notebook_ids:
                notebook_ids.append(notebook_id)
            
            # LanceDB doesn't support updates well, so we track frequency
            # In production, you'd want a proper update mechanism
            
            return Concept(
                id=record["id"],
                name=record["name"],
                description=record.get("description"),
                source_chunk_ids=chunk_ids,
                source_notebook_ids=notebook_ids,
                frequency=record.get("frequency", 1) + 1
            )
        
        # Create new concept
        concept = Concept(
            name=name,
            description=description,
            source_chunk_ids=[chunk_id],
            source_notebook_ids=[notebook_id]
        )
        
        # Store in LanceDB
        embedding = self.get_embedding(f"{name}: {description or ''}")
        record = {
            "id": concept.id,
            "name": concept.name,
            "description": concept.description or "",
            "source_chunk_ids": json.dumps(concept.source_chunk_ids),
            "source_notebook_ids": json.dumps(concept.source_notebook_ids),
            "frequency": concept.frequency,
            "importance": concept.importance,
            "cluster_id": "",
            "created_at": concept.created_at.isoformat(),
            "updated_at": concept.updated_at.isoformat(),
            "vector": embedding,
        }
        table.add([record])
        
        return concept
    
    async def _create_link_from_extraction(
        self,
        link_data: Dict,
        notebook_id: str,
        concepts: List[Concept]
    ) -> Optional[ConceptLink]:
        """Create a link from extraction data"""
        source_name = link_data.get("source", "").lower()
        target_name = link_data.get("target", "").lower()
        link_type_str = link_data.get("type", "references")
        
        # Find concept IDs
        source_concept = next((c for c in concepts if c.name == source_name), None)
        target_concept = next((c for c in concepts if c.name == target_name), None)
        
        if not source_concept or not target_concept:
            return None
        
        try:
            link_type = LinkType(link_type_str)
        except ValueError:
            link_type = LinkType.REFERENCES
        
        link = ConceptLink(
            source_id=source_concept.id,
            target_id=target_concept.id,
            link_type=link_type,
            source_notebook_id=notebook_id,
            strength=0.7  # Default strength for extracted links
        )
        
        # Store in LanceDB
        table = self.db.open_table("links")
        record = {
            "id": link.id,
            "source_id": link.source_id,
            "target_id": link.target_id,
            "source_type": link.source_type,
            "target_type": link.target_type,
            "link_type": link.link_type.value,
            "strength": link.strength,
            "evidence": link.evidence or "",
            "source_notebook_id": link.source_notebook_id or "",
            "auto_detected": link.auto_detected,
            "verified": link.verified,
            "created_at": link.created_at.isoformat(),
        }
        table.add([record])
        
        return link
    
    # =========================================================================
    # Link Detection
    # =========================================================================
    
    async def detect_links_for_chunk(
        self,
        chunk_id: str,
        chunk_text: str,
        notebook_id: str,
        max_links: int = 10
    ) -> List[ConceptLink]:
        """
        Detect links between a chunk and existing concepts/chunks.
        Called as a background job after ingestion.
        """
        links = []
        
        # Get embedding for the chunk
        chunk_embedding = self.get_embedding(chunk_text)
        
        # Find similar concepts
        concepts_table = self.db.open_table("concepts")
        similar_concepts = concepts_table.search(chunk_embedding).limit(max_links).to_list()
        
        for concept_record in similar_concepts:
            similarity = 1.0 - concept_record.get("_distance", 0.5)
            
            if similarity > 0.5:  # Threshold for creating a link
                link = ConceptLink(
                    source_id=chunk_id,
                    target_id=concept_record["id"],
                    source_type="chunk",
                    target_type="concept",
                    link_type=LinkType.REFERENCES,
                    strength=similarity,
                    source_notebook_id=notebook_id
                )
                links.append(link)
                
                # Store link
                table = self.db.open_table("links")
                record = {
                    "id": link.id,
                    "source_id": link.source_id,
                    "target_id": link.target_id,
                    "source_type": link.source_type,
                    "target_type": link.target_type,
                    "link_type": link.link_type.value,
                    "strength": link.strength,
                    "evidence": "",
                    "source_notebook_id": link.source_notebook_id or "",
                    "auto_detected": True,
                    "verified": False,
                    "created_at": link.created_at.isoformat(),
                }
                table.add([record])
        
        return links
    
    # =========================================================================
    # Contradiction Detection
    # =========================================================================
    
    async def detect_contradictions(
        self,
        chunk_id: str,
        chunk_text: str,
        notebook_id: str
    ) -> List[ContradictionReport]:
        """
        Check if a chunk contradicts existing content.
        Uses LLM to analyze potential contradictions.
        """
        contradictions = []
        
        # Find similar chunks that might contradict
        # This would search the main vector store
        # For now, we'll use a simplified approach
        
        # TODO: Implement full contradiction detection
        # This requires access to the main RAG vector store
        
        return contradictions
    
    # =========================================================================
    # Clustering
    # =========================================================================
    
    async def run_clustering(self) -> List[ConceptCluster]:
        """
        Run HDBSCAN clustering on concept embeddings to discover themes.
        Should be run periodically as a background job.
        """
        try:
            from hdbscan import HDBSCAN
        except ImportError:
            print("HDBSCAN not installed, skipping clustering")
            return []
        
        # Get all concepts with embeddings
        concepts_table = self.db.open_table("concepts")
        all_concepts = concepts_table.to_pandas()
        
        if len(all_concepts) < self.min_cluster_size:
            return []
        
        # Extract embeddings and normalize them
        # Using normalized embeddings with euclidean distance is equivalent to cosine distance
        embeddings = np.array(all_concepts["vector"].tolist())
        # Normalize embeddings to unit vectors
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1  # Avoid division by zero
        normalized_embeddings = embeddings / norms
        
        # Run HDBSCAN with euclidean metric on normalized vectors
        clusterer = HDBSCAN(
            min_cluster_size=self.min_cluster_size,
            min_samples=self.min_samples,
            metric='euclidean'  # On normalized vectors, this approximates cosine distance
        )
        cluster_labels = clusterer.fit_predict(normalized_embeddings)
        
        # Create cluster objects
        clusters = []
        unique_labels = set(cluster_labels)
        unique_labels.discard(-1)  # Remove noise label
        
        for label in unique_labels:
            mask = cluster_labels == label
            cluster_concepts = all_concepts[mask]
            
            concept_ids = cluster_concepts["id"].tolist()
            concept_names = cluster_concepts["name"].tolist()
            
            # Get notebook IDs from concepts
            notebook_ids = set()
            for nids in cluster_concepts["source_notebook_ids"]:
                notebook_ids.update(json.loads(nids))
            
            # Generate cluster name using LLM
            cluster_name = await self._generate_cluster_name(concept_names)
            
            cluster = ConceptCluster(
                name=cluster_name,
                concept_ids=concept_ids,
                size=len(concept_ids),
                notebook_ids=list(notebook_ids),
                coherence_score=float(clusterer.probabilities_[mask].mean())
            )
            clusters.append(cluster)
            
            # Store cluster
            table = self.db.open_table("clusters")
            record = {
                "id": cluster.id,
                "name": cluster.name,
                "description": cluster.description or "",
                "concept_ids": json.dumps(cluster.concept_ids),
                "coherence_score": cluster.coherence_score,
                "size": cluster.size,
                "notebook_ids": json.dumps(cluster.notebook_ids),
                "created_at": cluster.created_at.isoformat(),
                "updated_at": cluster.updated_at.isoformat(),
            }
            table.add([record])
            
            # Update concepts with cluster ID
            # (LanceDB doesn't support updates well, so this is simplified)
        
        return clusters
    
    async def _generate_cluster_name(self, concept_names: List[str]) -> str:
        """Generate a name for a cluster using LLM"""
        try:
            prompt = f"""These concepts are related: {', '.join(concept_names[:10])}

What theme or topic connects them? Respond with just a 2-4 word name:"""
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.extraction_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.3, "num_predict": 20}
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return result.get("response", "").strip()[:50]
        except Exception as e:
            print(f"Cluster naming error: {e}")
        
        return f"Theme: {concept_names[0]}"
    
    # =========================================================================
    # Graph Queries
    # =========================================================================
    
    async def get_graph_data(
        self,
        notebook_id: Optional[str] = None,
        center_node_id: Optional[str] = None,
        depth: int = 2,
        include_clusters: bool = True,
        min_link_strength: float = 0.3
    ) -> GraphData:
        """
        Get graph data for visualization.
        """
        nodes = []
        edges = []
        clusters = []
        
        # Get notebooks for colors
        notebooks = await notebook_store.list()
        notebook_colors = {n["id"]: n.get("color", "#3B82F6") for n in notebooks}
        
        # Get concepts
        concepts_table = self.db.open_table("concepts")
        if notebook_id:
            # Filter by notebook - need to check JSON array
            all_concepts = concepts_table.to_pandas()
            concepts_df = all_concepts[
                all_concepts["source_notebook_ids"].apply(
                    lambda x: notebook_id in json.loads(x)
                )
            ]
        else:
            concepts_df = concepts_table.to_pandas()
        
        # Create nodes from concepts
        for _, row in concepts_df.iterrows():
            notebook_ids = json.loads(row["source_notebook_ids"])
            primary_notebook = notebook_ids[0] if notebook_ids else None
            
            # Extract source IDs from chunk IDs (format: sourceId_chunkIndex)
            source_chunk_ids = json.loads(row.get("source_chunk_ids", "[]"))
            source_ids = list(set(cid.split("_")[0] for cid in source_chunk_ids if "_" in cid))
            
            node = GraphNode(
                id=row["id"],
                label=row["name"],
                type="concept",
                color=notebook_colors.get(primary_notebook, "#3B82F6"),
                size=min(2.0, 0.5 + row["frequency"] * 0.1),
                notebook_id=primary_notebook,
                metadata={
                    "description": row.get("description", ""),
                    "frequency": row["frequency"],
                    "importance": row["importance"],
                    "source_ids": source_ids,
                    "notebook_ids": notebook_ids
                }
            )
            nodes.append(node)
        
        # Get links
        links_table = self.db.open_table("links")
        links_df = links_table.to_pandas()
        
        if notebook_id:
            links_df = links_df[links_df["source_notebook_id"] == notebook_id]
        
        links_df = links_df[links_df["strength"] >= min_link_strength]
        
        # Create edges from links
        node_ids = {n.id for n in nodes}
        for _, row in links_df.iterrows():
            # Only include edges where both nodes exist
            if row["source_id"] in node_ids and row["target_id"] in node_ids:
                # Check if cross-notebook
                source_node = next((n for n in nodes if n.id == row["source_id"]), None)
                target_node = next((n for n in nodes if n.id == row["target_id"]), None)
                is_cross_notebook = (
                    source_node and target_node and
                    source_node.notebook_id != target_node.notebook_id
                )
                
                edge = GraphEdge(
                    id=row["id"],
                    source=row["source_id"],
                    target=row["target_id"],
                    label=row["link_type"],
                    strength=row["strength"],
                    dashed=is_cross_notebook
                )
                edges.append(edge)
        
        # Get clusters if requested
        if include_clusters:
            clusters_table = self.db.open_table("clusters")
            clusters_df = clusters_table.to_pandas()
            
            for _, row in clusters_df.iterrows():
                cluster_notebook_ids = json.loads(row["notebook_ids"])
                if notebook_id and notebook_id not in cluster_notebook_ids:
                    continue
                
                cluster = ConceptCluster(
                    id=row["id"],
                    name=row["name"],
                    description=row.get("description"),
                    concept_ids=json.loads(row["concept_ids"]),
                    coherence_score=row["coherence_score"],
                    size=row["size"],
                    notebook_ids=cluster_notebook_ids
                )
                clusters.append(cluster)
        
        return GraphData(nodes=nodes, edges=edges, clusters=clusters)
    
    async def get_connections_for_source(
        self,
        source_id: str,
        notebook_id: str,
        limit: int = 20
    ) -> Dict[str, Any]:
        """
        Get connections for a specific source document.
        Used for the "connections panel" in the UI.
        """
        connections = {
            "related_sources": [],
            "concepts": [],
            "clusters": []
        }
        
        # Get concepts mentioned in this source
        concepts_table = self.db.open_table("concepts")
        all_concepts = concepts_table.to_pandas()
        
        source_concepts = all_concepts[
            all_concepts["source_chunk_ids"].apply(
                lambda x: any(source_id in cid for cid in json.loads(x))
            )
        ]
        
        for _, row in source_concepts.iterrows():
            connections["concepts"].append({
                "id": row["id"],
                "name": row["name"],
                "frequency": row["frequency"]
            })
        
        # Find related sources through shared concepts
        related_sources = {}
        for _, concept_row in source_concepts.iterrows():
            chunk_ids = json.loads(concept_row["source_chunk_ids"])
            for chunk_id in chunk_ids:
                other_source_id = chunk_id.split("_")[0]
                if other_source_id != source_id:
                    if other_source_id not in related_sources:
                        related_sources[other_source_id] = 0
                    related_sources[other_source_id] += 1
        
        # Sort by connection strength
        sorted_related = sorted(
            related_sources.items(),
            key=lambda x: x[1],
            reverse=True
        )[:limit]
        
        connections["related_sources"] = [
            {"source_id": sid, "shared_concepts": count}
            for sid, count in sorted_related
        ]
        
        return connections
    
    # =========================================================================
    # Stats
    # =========================================================================
    
    async def get_stats(self, notebook_id: Optional[str] = None) -> Dict[str, Any]:
        """Get knowledge graph statistics, optionally filtered by notebook"""
        try:
            concepts_table = self.db.open_table("concepts")
            links_table = self.db.open_table("links")
            clusters_table = self.db.open_table("clusters")
            
            if notebook_id:
                # Filter by notebook
                concepts_df = concepts_table.to_pandas()
                concepts_count = len(concepts_df[
                    concepts_df["source_notebook_ids"].apply(
                        lambda x: notebook_id in json.loads(x)
                    )
                ])
                
                links_df = links_table.to_pandas()
                links_count = len(links_df[links_df["source_notebook_id"] == notebook_id])
                
                clusters_df = clusters_table.to_pandas()
                clusters_count = len(clusters_df[
                    clusters_df["notebook_ids"].apply(
                        lambda x: notebook_id in json.loads(x)
                    )
                ])
                
                return {
                    "concepts": concepts_count,
                    "links": links_count,
                    "clusters": clusters_count,
                }
            else:
                return {
                    "concepts": concepts_table.count_rows(),
                    "links": links_table.count_rows(),
                    "clusters": clusters_table.count_rows(),
                }
        except Exception:
            return {"concepts": 0, "links": 0, "clusters": 0}
    
    # =========================================================================
    # LLM Helper
    # =========================================================================
    
    async def _call_llm(self, prompt: str) -> Optional[Dict]:
        """Call LLM and parse JSON response"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.extraction_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 500}
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    text = result.get("response", "")
                    
                    # Parse JSON from response
                    json_match = re.search(r'\{[\s\S]*\}', text)
                    if json_match:
                        return json.loads(json_match.group())
        except Exception as e:
            print(f"LLM call error: {e}")
        
        return None


# Singleton instance
knowledge_graph_service = KnowledgeGraphService()
