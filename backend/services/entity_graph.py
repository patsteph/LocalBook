"""Entity Graph Service

Extracts and stores relationships between entities for Graph RAG.
Enables "who is connected to what" queries through graph traversal.
"""
import asyncio
import json
import re
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import httpx

from config import settings


@dataclass
class EntityRelationship:
    """A relationship between two entities."""
    source_entity: str
    source_type: str
    relationship: str  # e.g., "works_with", "reported", "mentioned_with"
    target_entity: str
    target_type: str
    strength: float = 1.0  # How strong/frequent the relationship
    context_snippets: List[str] = field(default_factory=list)
    source_ids: List[str] = field(default_factory=list)


@dataclass 
class EntityNode:
    """A node in the entity graph."""
    name: str
    entity_type: str
    connections: Dict[str, float] = field(default_factory=dict)  # entity_key -> strength
    total_mentions: int = 0


class EntityGraph:
    """Graph of entity relationships for a notebook."""
    
    def __init__(self):
        # notebook_id -> {relationship_key -> EntityRelationship}
        self._relationships: Dict[str, Dict[str, EntityRelationship]] = {}
        # notebook_id -> {entity_key -> EntityNode}
        self._nodes: Dict[str, Dict[str, EntityNode]] = {}
        self._graph_file = Path(settings.db_path).parent / "entity_graph.json"
        self._lock = asyncio.Lock()
        self._load_graph()
    
    def _load_graph(self):
        """Load graph from disk."""
        try:
            if self._graph_file.exists():
                with open(self._graph_file, 'r') as f:
                    data = json.load(f)
                    for nb_id, rels in data.get("relationships", {}).items():
                        self._relationships[nb_id] = {
                            k: EntityRelationship(**v) for k, v in rels.items()
                        }
                    for nb_id, nodes in data.get("nodes", {}).items():
                        self._nodes[nb_id] = {
                            k: EntityNode(**v) for k, v in nodes.items()
                        }
                print(f"[EntityGraph] Loaded graph for {len(self._relationships)} notebooks")
        except Exception as e:
            print(f"[EntityGraph] Could not load graph: {e}")
    
    def _save_graph(self):
        """Save graph to disk."""
        try:
            data = {
                "relationships": {
                    nb_id: {k: asdict(v) for k, v in rels.items()}
                    for nb_id, rels in self._relationships.items()
                },
                "nodes": {
                    nb_id: {k: asdict(v) for k, v in nodes.items()}
                    for nb_id, nodes in self._nodes.items()
                }
            }
            with open(self._graph_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[EntityGraph] Could not save graph: {e}")
    
    def _entity_key(self, name: str, entity_type: str) -> str:
        """Create unique key for entity."""
        return f"{entity_type}:{name.lower().strip()}"
    
    def _relationship_key(self, source: str, target: str, rel_type: str) -> str:
        """Create unique key for relationship."""
        # Normalize order for undirected relationships
        if source > target:
            source, target = target, source
        return f"{source}|{rel_type}|{target}"
    
    async def extract_relationships(
        self,
        text: str,
        notebook_id: str,
        source_id: str,
        entities: List[Dict]  # List of {name, type} dicts
    ) -> List[EntityRelationship]:
        """Extract relationships between entities in text.
        
        Uses co-occurrence and LLM extraction for relationship detection.
        """
        if len(entities) < 2:
            return []
        
        relationships = []
        
        # Method 1: Co-occurrence based relationships
        # Entities mentioned in the same sentence/chunk are related
        co_occurrences = self._extract_co_occurrences(text, entities)
        relationships.extend(co_occurrences)
        
        # Method 2: LLM-based relationship extraction for richer context
        if len(text) > 200 and len(entities) >= 2:
            llm_relationships = await self._extract_with_llm(text, entities)
            relationships.extend(llm_relationships)
        
        # Store relationships
        if relationships:
            await self._store_relationships(notebook_id, source_id, relationships)
        
        return relationships
    
    def _extract_co_occurrences(
        self,
        text: str,
        entities: List[Dict]
    ) -> List[EntityRelationship]:
        """Extract relationships based on co-occurrence."""
        relationships = []
        text_lower = text.lower()
        
        # Split into sentences
        sentences = re.split(r'[.!?\n]', text)
        
        for sentence in sentences:
            sentence_lower = sentence.lower()
            if len(sentence_lower) < 20:
                continue
            
            # Find entities in this sentence
            entities_in_sentence = []
            for entity in entities:
                name = entity.get("name", "").lower()
                if name and name in sentence_lower:
                    entities_in_sentence.append(entity)
            
            # Create co-occurrence relationships
            for i, e1 in enumerate(entities_in_sentence):
                for e2 in entities_in_sentence[i+1:]:
                    rel = EntityRelationship(
                        source_entity=e1["name"],
                        source_type=e1.get("type", "unknown"),
                        relationship="mentioned_with",
                        target_entity=e2["name"],
                        target_type=e2.get("type", "unknown"),
                        strength=0.5,
                        context_snippets=[sentence.strip()[:200]]
                    )
                    relationships.append(rel)
        
        return relationships
    
    async def _extract_with_llm(
        self,
        text: str,
        entities: List[Dict]
    ) -> List[EntityRelationship]:
        """Use LLM to extract semantic relationships."""
        # Limit text and entities for prompt
        text_sample = text[:2000]
        entity_names = [e["name"] for e in entities[:10]]
        
        prompt = f"""Extract relationships between these entities from the text.

Entities: {', '.join(entity_names)}

Text:
{text_sample}

For each relationship found, output JSON with:
- source: entity name
- relationship: verb/action (e.g., "works_with", "reported", "manages", "created", "discussed")
- target: entity name

Output as JSON array. Example:
[{{"source": "Chris", "relationship": "conducted", "target": "demo"}}, {{"source": "Q1", "relationship": "contains", "target": "7 demos"}}]

JSON (only output relationships you find, empty array if none):"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_fast_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 300, "temperature": 0.2}
                    }
                )
                
                if response.status_code != 200:
                    return []
                
                result = response.json().get("response", "")
                
                # Extract JSON array
                match = re.search(r'\[.*?\]', result, re.DOTALL)
                if match:
                    raw_rels = json.loads(match.group())
                    relationships = []
                    
                    # Build entity type lookup
                    type_lookup = {e["name"].lower(): e.get("type", "unknown") for e in entities}
                    
                    for item in raw_rels:
                        if not isinstance(item, dict):
                            continue
                        source = item.get("source", "")
                        target = item.get("target", "")
                        rel_type = item.get("relationship", "related_to")
                        
                        if source and target:
                            rel = EntityRelationship(
                                source_entity=source,
                                source_type=type_lookup.get(source.lower(), "unknown"),
                                relationship=rel_type,
                                target_entity=target,
                                target_type=type_lookup.get(target.lower(), "unknown"),
                                strength=0.8,
                                context_snippets=[text_sample[:150]]
                            )
                            relationships.append(rel)
                    
                    return relationships
                
                return []
                
        except Exception as e:
            print(f"[EntityGraph] LLM extraction failed: {e}")
            return []
    
    async def _store_relationships(
        self,
        notebook_id: str,
        source_id: str,
        relationships: List[EntityRelationship]
    ):
        """Store relationships in graph."""
        async with self._lock:
            if notebook_id not in self._relationships:
                self._relationships[notebook_id] = {}
            if notebook_id not in self._nodes:
                self._nodes[notebook_id] = {}
            
            for rel in relationships:
                # Update or create relationship
                key = self._relationship_key(
                    rel.source_entity.lower(),
                    rel.target_entity.lower(),
                    rel.relationship
                )
                
                if key in self._relationships[notebook_id]:
                    existing = self._relationships[notebook_id][key]
                    existing.strength = min(1.0, existing.strength + 0.1)
                    if source_id not in existing.source_ids:
                        existing.source_ids.append(source_id)
                    for snippet in rel.context_snippets:
                        if snippet not in existing.context_snippets:
                            existing.context_snippets.append(snippet)
                    existing.context_snippets = existing.context_snippets[:5]
                else:
                    rel.source_ids = [source_id]
                    self._relationships[notebook_id][key] = rel
                
                # Update nodes
                for entity_name, entity_type in [
                    (rel.source_entity, rel.source_type),
                    (rel.target_entity, rel.target_type)
                ]:
                    node_key = self._entity_key(entity_name, entity_type)
                    if node_key not in self._nodes[notebook_id]:
                        self._nodes[notebook_id][node_key] = EntityNode(
                            name=entity_name,
                            entity_type=entity_type
                        )
                    node = self._nodes[notebook_id][node_key]
                    node.total_mentions += 1
                    
                    # Add connection
                    other_entity = rel.target_entity if entity_name == rel.source_entity else rel.source_entity
                    other_type = rel.target_type if entity_name == rel.source_entity else rel.source_type
                    other_key = self._entity_key(other_entity, other_type)
                    node.connections[other_key] = node.connections.get(other_key, 0) + rel.strength
            
            # Save periodically
            if len(self._relationships[notebook_id]) % 10 == 0:
                self._save_graph()
    
    # =========================================================================
    # Graph Query Methods
    # =========================================================================
    
    def get_connected_entities(
        self,
        notebook_id: str,
        entity_name: str,
        max_depth: int = 2,
        limit: int = 20
    ) -> List[Dict]:
        """Get entities connected to a given entity.
        
        Performs breadth-first traversal up to max_depth.
        """
        if notebook_id not in self._nodes:
            return []
        
        # Find the starting node
        start_key = None
        for key, node in self._nodes[notebook_id].items():
            if node.name.lower() == entity_name.lower():
                start_key = key
                break
        
        if not start_key:
            return []
        
        # BFS traversal
        visited = {start_key}
        queue = [(start_key, 0)]  # (node_key, depth)
        connected = []
        
        while queue and len(connected) < limit:
            current_key, depth = queue.pop(0)
            
            if depth > 0:  # Don't include starting node
                node = self._nodes[notebook_id][current_key]
                connected.append({
                    "name": node.name,
                    "type": node.entity_type,
                    "depth": depth,
                    "connection_strength": node.connections.get(start_key, 0)
                })
            
            if depth < max_depth:
                current_node = self._nodes[notebook_id].get(current_key)
                if current_node:
                    for neighbor_key in current_node.connections:
                        if neighbor_key not in visited and neighbor_key in self._nodes[notebook_id]:
                            visited.add(neighbor_key)
                            queue.append((neighbor_key, depth + 1))
        
        # Sort by connection strength
        connected.sort(key=lambda x: (-x["connection_strength"], x["depth"]))
        
        return connected
    
    def get_relationships_for_entity(
        self,
        notebook_id: str,
        entity_name: str
    ) -> List[Dict]:
        """Get all relationships involving an entity."""
        if notebook_id not in self._relationships:
            return []
        
        entity_lower = entity_name.lower()
        results = []
        
        for rel in self._relationships[notebook_id].values():
            if rel.source_entity.lower() == entity_lower or rel.target_entity.lower() == entity_lower:
                results.append({
                    "source": rel.source_entity,
                    "relationship": rel.relationship,
                    "target": rel.target_entity,
                    "strength": rel.strength,
                    "context": rel.context_snippets[0] if rel.context_snippets else ""
                })
        
        # Sort by strength
        results.sort(key=lambda x: -x["strength"])
        
        return results
    
    def get_path_between_entities(
        self,
        notebook_id: str,
        entity1: str,
        entity2: str,
        max_depth: int = 4
    ) -> Optional[List[str]]:
        """Find shortest path between two entities."""
        if notebook_id not in self._nodes:
            return None
        
        # Find start and end nodes
        start_key = end_key = None
        for key, node in self._nodes[notebook_id].items():
            if node.name.lower() == entity1.lower():
                start_key = key
            if node.name.lower() == entity2.lower():
                end_key = key
        
        if not start_key or not end_key:
            return None
        
        # BFS to find path
        visited = {start_key}
        queue = [(start_key, [entity1])]
        
        while queue:
            current_key, path = queue.pop(0)
            
            if current_key == end_key:
                return path
            
            if len(path) > max_depth:
                continue
            
            current_node = self._nodes[notebook_id].get(current_key)
            if current_node:
                for neighbor_key in current_node.connections:
                    if neighbor_key not in visited and neighbor_key in self._nodes[notebook_id]:
                        visited.add(neighbor_key)
                        neighbor_node = self._nodes[notebook_id][neighbor_key]
                        queue.append((neighbor_key, path + [neighbor_node.name]))
        
        return None
    
    def get_graph_stats(self, notebook_id: str) -> Dict:
        """Get statistics about the entity graph."""
        if notebook_id not in self._nodes:
            return {"nodes": 0, "relationships": 0}
        
        nodes = len(self._nodes.get(notebook_id, {}))
        relationships = len(self._relationships.get(notebook_id, {}))
        
        # Get most connected entities
        top_entities = []
        for key, node in self._nodes.get(notebook_id, {}).items():
            top_entities.append({
                "name": node.name,
                "type": node.entity_type,
                "connections": len(node.connections)
            })
        top_entities.sort(key=lambda x: -x["connections"])
        
        return {
            "nodes": nodes,
            "relationships": relationships,
            "top_entities": top_entities[:10]
        }
    
    def get_context_for_query(
        self,
        notebook_id: str,
        entities: List[str],
        max_context: int = 500
    ) -> str:
        """Get graph context to add to LLM prompt.
        
        Returns relationship information for entities in the query.
        """
        if notebook_id not in self._relationships:
            return ""
        
        parts = []
        
        for entity_name in entities[:3]:
            rels = self.get_relationships_for_entity(notebook_id, entity_name)[:3]
            if rels:
                for rel in rels:
                    parts.append(f"- {rel['source']} {rel['relationship']} {rel['target']}")
        
        if parts:
            context = "ENTITY RELATIONSHIPS:\n" + "\n".join(parts[:5]) + "\n\n"
            return context[:max_context]
        
        return ""


# Singleton instance
entity_graph = EntityGraph()
