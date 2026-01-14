"""Community Detection Service

Detects communities of related entities using graph clustering.
Enables holistic "tell me everything about X" queries by grouping
related entities and generating community summaries.

Based on Microsoft GraphRAG approach using Leiden algorithm.
"""
import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import httpx

from config import settings


@dataclass
class Community:
    """A community of related entities."""
    id: str
    name: str  # Auto-generated descriptive name
    entities: List[str]  # Entity names in this community
    summary: str = ""  # LLM-generated summary of the community
    keywords: List[str] = field(default_factory=list)
    size: int = 0
    density: float = 0.0  # How interconnected the community is
    source_ids: List[str] = field(default_factory=list)  # Sources that mention this community


class CommunityDetector:
    """Detects and manages entity communities."""
    
    def __init__(self):
        # notebook_id -> {community_id -> Community}
        self._communities: Dict[str, Dict[str, Community]] = {}
        # notebook_id -> {entity_name -> community_id}
        self._entity_to_community: Dict[str, Dict[str, str]] = {}
        self._community_file = Path(settings.db_path).parent / "communities.json"
        self._lock = asyncio.Lock()
        self._load_communities()
    
    def _load_communities(self):
        """Load communities from disk."""
        try:
            if self._community_file.exists():
                with open(self._community_file, 'r') as f:
                    data = json.load(f)
                    for nb_id, comms in data.get("communities", {}).items():
                        self._communities[nb_id] = {
                            k: Community(**v) for k, v in comms.items()
                        }
                    self._entity_to_community = data.get("entity_to_community", {})
                print(f"[CommunityDetector] Loaded communities for {len(self._communities)} notebooks")
        except Exception as e:
            print(f"[CommunityDetector] Could not load communities: {e}")
    
    def _save_communities(self):
        """Save communities to disk."""
        try:
            data = {
                "communities": {
                    nb_id: {k: asdict(v) for k, v in comms.items()}
                    for nb_id, comms in self._communities.items()
                },
                "entity_to_community": self._entity_to_community
            }
            with open(self._community_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[CommunityDetector] Could not save communities: {e}")
    
    def _simple_community_detection(
        self,
        nodes: Dict[str, Set[str]]  # entity -> connected entities
    ) -> List[Set[str]]:
        """Simple connected components + modularity-based community detection.
        
        Fallback when networkx/igraph not available.
        Uses label propagation-like approach.
        """
        if not nodes:
            return []
        
        # Start with connected components
        visited = set()
        communities = []
        
        def bfs_component(start: str) -> Set[str]:
            component = set()
            queue = [start]
            while queue:
                node = queue.pop(0)
                if node in component:
                    continue
                component.add(node)
                for neighbor in nodes.get(node, set()):
                    if neighbor not in component and neighbor in nodes:
                        queue.append(neighbor)
            return component
        
        for node in nodes:
            if node not in visited:
                component = bfs_component(node)
                visited.update(component)
                
                # Split large components using edge density
                if len(component) > 10:
                    sub_communities = self._split_by_density(component, nodes)
                    communities.extend(sub_communities)
                else:
                    communities.append(component)
        
        return communities
    
    def _split_by_density(
        self,
        component: Set[str],
        nodes: Dict[str, Set[str]],
        min_size: int = 3
    ) -> List[Set[str]]:
        """Split a large component into denser sub-communities."""
        # Find nodes with highest connectivity (hubs)
        node_degrees = {}
        for node in component:
            degree = len(nodes.get(node, set()) & component)
            node_degrees[node] = degree
        
        # Sort by degree
        sorted_nodes = sorted(node_degrees.items(), key=lambda x: -x[1])
        
        # Greedily form communities around high-degree nodes
        assigned = set()
        communities = []
        
        for hub, _ in sorted_nodes:
            if hub in assigned:
                continue
            
            # Create community with hub and unassigned neighbors
            community = {hub}
            for neighbor in nodes.get(hub, set()):
                if neighbor in component and neighbor not in assigned:
                    community.add(neighbor)
            
            if len(community) >= min_size:
                communities.append(community)
                assigned.update(community)
        
        # Add remaining nodes to nearest community
        for node in component:
            if node not in assigned:
                # Find community with most connections
                best_comm = None
                best_overlap = 0
                for comm in communities:
                    overlap = len(nodes.get(node, set()) & comm)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_comm = comm
                
                if best_comm:
                    best_comm.add(node)
                else:
                    communities.append({node})
        
        return communities
    
    async def detect_communities(
        self,
        notebook_id: str,
        entity_graph  # EntityGraph instance
    ) -> List[Community]:
        """Detect communities from the entity graph.
        
        Args:
            notebook_id: Notebook to process
            entity_graph: EntityGraph instance with nodes and relationships
            
        Returns: List of detected communities
        """
        # Build adjacency from entity graph
        if notebook_id not in entity_graph._nodes:
            return []
        
        nodes_data = entity_graph._nodes[notebook_id]
        adjacency: Dict[str, Set[str]] = {}
        
        for key, node in nodes_data.items():
            adjacency[node.name] = set()
            for conn_key in node.connections:
                if conn_key in nodes_data:
                    adjacency[node.name].add(nodes_data[conn_key].name)
        
        if not adjacency:
            return []
        
        # Detect communities
        raw_communities = self._simple_community_detection(adjacency)
        
        # Convert to Community objects
        communities = []
        async with self._lock:
            if notebook_id not in self._communities:
                self._communities[notebook_id] = {}
            if notebook_id not in self._entity_to_community:
                self._entity_to_community[notebook_id] = {}
            
            for i, members in enumerate(raw_communities):
                if len(members) < 2:
                    continue
                
                comm_id = f"comm_{notebook_id[:8]}_{i}"
                
                # Calculate density
                total_edges = sum(
                    len(adjacency.get(m, set()) & members)
                    for m in members
                ) // 2
                max_edges = len(members) * (len(members) - 1) // 2
                density = total_edges / max_edges if max_edges > 0 else 0
                
                # Get source IDs from entity graph
                source_ids = set()
                for entity_name in members:
                    for key, node in nodes_data.items():
                        if node.name == entity_name:
                            # Get sources from relationships
                            for rel in entity_graph._relationships.get(notebook_id, {}).values():
                                if rel.source_entity == entity_name or rel.target_entity == entity_name:
                                    source_ids.update(rel.source_ids)
                
                community = Community(
                    id=comm_id,
                    name=f"Community {i+1}",  # Will be updated by summary
                    entities=list(members),
                    size=len(members),
                    density=density,
                    source_ids=list(source_ids)[:20]
                )
                
                communities.append(community)
                self._communities[notebook_id][comm_id] = community
                
                # Update entity -> community mapping
                for entity in members:
                    self._entity_to_community[notebook_id][entity.lower()] = comm_id
            
            self._save_communities()
        
        print(f"[CommunityDetector] Detected {len(communities)} communities in notebook {notebook_id[:8]}")
        
        return communities
    
    async def generate_community_summary(
        self,
        notebook_id: str,
        community_id: str,
        entity_graph
    ) -> str:
        """Generate an LLM summary for a community."""
        if notebook_id not in self._communities:
            return ""
        if community_id not in self._communities[notebook_id]:
            return ""
        
        community = self._communities[notebook_id][community_id]
        
        # Gather relationship context
        relationships = []
        for entity in community.entities[:10]:
            rels = entity_graph.get_relationships_for_entity(notebook_id, entity)
            for rel in rels[:3]:
                relationships.append(f"{rel['source']} {rel['relationship']} {rel['target']}")
        
        if not relationships:
            return ""
        
        prompt = f"""Summarize this group of related entities in 2-3 sentences.

Entities: {', '.join(community.entities[:15])}

Relationships found:
{chr(10).join(relationships[:10])}

Provide:
1. A short descriptive name for this group (3-5 words)
2. A summary of what connects these entities

Format:
NAME: [group name]
SUMMARY: [2-3 sentence summary]"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_fast_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 150, "temperature": 0.3}
                    }
                )
                
                if response.status_code != 200:
                    return ""
                
                result = response.json().get("response", "")
                
                # Parse name and summary
                lines = result.strip().split('\n')
                name = ""
                summary = ""
                
                for line in lines:
                    if line.startswith("NAME:"):
                        name = line.replace("NAME:", "").strip()
                    elif line.startswith("SUMMARY:"):
                        summary = line.replace("SUMMARY:", "").strip()
                
                if name:
                    community.name = name
                if summary:
                    community.summary = summary
                
                # Extract keywords
                community.keywords = [e for e in community.entities[:5]]
                
                self._save_communities()
                
                return summary
                
        except Exception as e:
            print(f"[CommunityDetector] Summary generation failed: {e}")
            return ""
    
    def get_community_for_entity(
        self,
        notebook_id: str,
        entity_name: str
    ) -> Optional[Community]:
        """Get the community that contains an entity."""
        if notebook_id not in self._entity_to_community:
            return None
        
        comm_id = self._entity_to_community[notebook_id].get(entity_name.lower())
        if not comm_id:
            return None
        
        return self._communities.get(notebook_id, {}).get(comm_id)
    
    def get_all_communities(self, notebook_id: str) -> List[Community]:
        """Get all communities for a notebook."""
        return list(self._communities.get(notebook_id, {}).values())
    
    def get_community_context(
        self,
        notebook_id: str,
        entity_names: List[str],
        max_context: int = 400
    ) -> str:
        """Get community context for entities mentioned in a query.
        
        Used to add holistic context to LLM prompts.
        """
        if notebook_id not in self._communities:
            return ""
        
        seen_communities = set()
        context_parts = []
        
        for entity in entity_names[:3]:
            community = self.get_community_for_entity(notebook_id, entity)
            if community and community.id not in seen_communities:
                seen_communities.add(community.id)
                if community.summary:
                    context_parts.append(f"- {community.name}: {community.summary}")
                else:
                    context_parts.append(f"- {community.name}: includes {', '.join(community.entities[:5])}")
        
        if context_parts:
            return "RELATED TOPICS:\n" + "\n".join(context_parts[:3]) + "\n\n"
        
        return ""
    
    def is_holistic_query(self, query: str) -> bool:
        """Detect if a query is asking for holistic/overview information.
        
        Holistic queries benefit from community-level context.
        """
        holistic_patterns = [
            "tell me about",
            "everything about",
            "overview of",
            "what do you know about",
            "summarize",
            "all about",
            "related to",
            "connected to",
            "who is involved",
            "what's the story",
        ]
        
        query_lower = query.lower()
        return any(pattern in query_lower for pattern in holistic_patterns)


# Singleton instance
community_detector = CommunityDetector()
