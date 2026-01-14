"""Entity Extraction Service

Extracts named entities from documents during ingestion.
Stores entities separately for entity-aware retrieval.
"""
import asyncio
import json
import re
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import httpx

from config import settings


@dataclass
class Entity:
    """A named entity extracted from text."""
    name: str
    type: str  # person, company, location, product, date, metric
    mentions: int = 1
    source_ids: List[str] = None
    context_snippets: List[str] = None
    
    def __post_init__(self):
        if self.source_ids is None:
            self.source_ids = []
        if self.context_snippets is None:
            self.context_snippets = []


class EntityExtractor:
    """Extracts and stores entities from documents."""
    
    def __init__(self):
        self._entities: Dict[str, Dict[str, Entity]] = {}  # notebook_id -> {entity_key -> Entity}
        self._entity_file = Path(settings.db_path).parent / "entities.json"
        self._lock = asyncio.Lock()
        self._load_entities()
    
    def _load_entities(self):
        """Load entities from disk."""
        try:
            if self._entity_file.exists():
                with open(self._entity_file, 'r') as f:
                    data = json.load(f)
                    for notebook_id, entities in data.items():
                        self._entities[notebook_id] = {
                            k: Entity(**v) for k, v in entities.items()
                        }
                print(f"[EntityExtractor] Loaded entities for {len(self._entities)} notebooks")
        except Exception as e:
            print(f"[EntityExtractor] Could not load entities: {e}")
    
    def _save_entities(self):
        """Save entities to disk."""
        try:
            data = {
                nb_id: {k: asdict(v) for k, v in entities.items()}
                for nb_id, entities in self._entities.items()
            }
            with open(self._entity_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[EntityExtractor] Could not save entities: {e}")
    
    def _entity_key(self, name: str, entity_type: str) -> str:
        """Create a unique key for an entity."""
        return f"{entity_type}:{name.lower().strip()}"
    
    async def extract_from_text(
        self,
        text: str,
        notebook_id: str,
        source_id: str,
        use_llm: bool = True
    ) -> List[Entity]:
        """Extract entities from text.
        
        Args:
            text: Text to extract entities from
            notebook_id: Notebook ID for storage
            source_id: Source ID for tracking
            use_llm: Whether to use LLM for extraction (slower but more accurate)
            
        Returns: List of extracted entities
        """
        if use_llm:
            entities = await self._extract_with_llm(text)
        else:
            entities = self._extract_with_regex(text)
        
        if not entities:
            return []
        
        # Store entities
        async with self._lock:
            if notebook_id not in self._entities:
                self._entities[notebook_id] = {}
            
            stored_entities = []
            for entity in entities:
                key = self._entity_key(entity.name, entity.type)
                
                if key in self._entities[notebook_id]:
                    # Update existing entity
                    existing = self._entities[notebook_id][key]
                    existing.mentions += entity.mentions
                    if source_id not in existing.source_ids:
                        existing.source_ids.append(source_id)
                    # Add context snippets (limit to 5)
                    for snippet in entity.context_snippets[:2]:
                        if snippet not in existing.context_snippets:
                            existing.context_snippets.append(snippet)
                    existing.context_snippets = existing.context_snippets[:5]
                    stored_entities.append(existing)
                else:
                    # Add new entity
                    entity.source_ids = [source_id]
                    self._entities[notebook_id][key] = entity
                    stored_entities.append(entity)
            
            # Save periodically
            if len(self._entities[notebook_id]) % 10 == 0:
                self._save_entities()
        
        print(f"[EntityExtractor] Extracted {len(stored_entities)} entities from source {source_id[:8]}")
        return stored_entities
    
    async def _extract_with_llm(self, text: str) -> List[Entity]:
        """Use LLM to extract entities."""
        # Limit text to avoid token overflow
        text_sample = text[:4000] if len(text) > 4000 else text
        
        prompt = f"""Extract named entities from this text. Output ONLY valid JSON.

Text:
{text_sample}

Extract entities of these types:
- person: Names of people
- company: Company/organization names
- location: Places, cities, countries
- product: Product names, tools, software
- date: Specific dates, time periods, quarters (Q1 2025, etc.)
- metric: Numbers with context (revenue, count, percentage)

For each entity, provide:
- name: The entity name (normalized, e.g., "Chris Norman" not "Chris")
- type: One of the types above
- context: A brief phrase showing how it's used

Output as JSON array:
[{{"name": "...", "type": "...", "context": "..."}}]

JSON:"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_fast_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 500, "temperature": 0.2}
                    }
                )
                
                if response.status_code != 200:
                    return self._extract_with_regex(text)
                
                result = response.json().get("response", "")
                
                # Extract JSON array
                match = re.search(r'\[.*?\]', result, re.DOTALL)
                if match:
                    raw_entities = json.loads(match.group())
                    entities = []
                    
                    for item in raw_entities:
                        if isinstance(item, dict) and "name" in item and "type" in item:
                            entity = Entity(
                                name=item["name"],
                                type=item["type"],
                                mentions=1,
                                context_snippets=[item.get("context", "")]
                            )
                            entities.append(entity)
                    
                    return entities
                
                return self._extract_with_regex(text)
                
        except Exception as e:
            print(f"[EntityExtractor] LLM extraction failed: {e}")
            return self._extract_with_regex(text)
    
    def _extract_with_regex(self, text: str) -> List[Entity]:
        """Fast regex-based entity extraction (fallback)."""
        entities = []
        
        # Person names (capitalized words, 2-3 parts)
        person_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b'
        for match in re.finditer(person_pattern, text):
            name = match.group(1)
            # Skip common non-names
            if name.lower() not in ['the company', 'the project', 'new york', 'los angeles']:
                context_start = max(0, match.start() - 30)
                context_end = min(len(text), match.end() + 30)
                context = text[context_start:context_end].strip()
                entities.append(Entity(
                    name=name,
                    type="person",
                    context_snippets=[context]
                ))
        
        # Dates and quarters
        date_patterns = [
            (r'\b(Q[1-4]\s*(?:FY\s*)?\d{4})\b', "date"),  # Q1 2025, Q1 FY 2025
            (r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})\b', "date"),
            (r'\b(\d{1,2}/\d{1,2}/\d{2,4})\b', "date"),
        ]
        
        for pattern, entity_type in date_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                entities.append(Entity(
                    name=match.group(1),
                    type=entity_type,
                    context_snippets=[text[max(0, match.start()-20):min(len(text), match.end()+20)]]
                ))
        
        # Metrics (numbers with context)
        metric_pattern = r'\b(\d+(?:,\d{3})*(?:\.\d+)?)\s*(percent|%|dollars?|\$|demos?|meetings?|calls?|revenue)\b'
        for match in re.finditer(metric_pattern, text, re.IGNORECASE):
            full_match = match.group(0)
            entities.append(Entity(
                name=full_match,
                type="metric",
                context_snippets=[text[max(0, match.start()-30):min(len(text), match.end()+30)]]
            ))
        
        # Deduplicate by name
        seen = set()
        unique_entities = []
        for e in entities:
            key = (e.name.lower(), e.type)
            if key not in seen:
                seen.add(key)
                unique_entities.append(e)
        
        return unique_entities
    
    def get_entities(
        self,
        notebook_id: str,
        entity_type: Optional[str] = None
    ) -> List[Entity]:
        """Get all entities for a notebook."""
        if notebook_id not in self._entities:
            return []
        
        entities = list(self._entities[notebook_id].values())
        
        if entity_type:
            entities = [e for e in entities if e.type == entity_type]
        
        # Sort by mentions (most mentioned first)
        entities.sort(key=lambda e: -e.mentions)
        
        return entities
    
    def search_entities(
        self,
        notebook_id: str,
        query: str,
        limit: int = 10
    ) -> List[Entity]:
        """Search entities by name."""
        if notebook_id not in self._entities:
            return []
        
        query_lower = query.lower()
        matches = []
        
        for entity in self._entities[notebook_id].values():
            if query_lower in entity.name.lower():
                matches.append(entity)
        
        # Sort by relevance (exact match first, then by mentions)
        matches.sort(key=lambda e: (
            0 if e.name.lower() == query_lower else 1,
            -e.mentions
        ))
        
        return matches[:limit]
    
    def get_related_sources(
        self,
        notebook_id: str,
        entity_name: str
    ) -> List[str]:
        """Get source IDs that mention an entity."""
        if notebook_id not in self._entities:
            return []
        
        # Find entity
        for entity in self._entities[notebook_id].values():
            if entity.name.lower() == entity_name.lower():
                return entity.source_ids
        
        return []
    
    def get_entity_context(
        self,
        notebook_id: str,
        entity_name: str
    ) -> str:
        """Get context snippets for an entity as a single string."""
        if notebook_id not in self._entities:
            return ""
        
        for entity in self._entities[notebook_id].values():
            if entity.name.lower() == entity_name.lower():
                return " ... ".join(entity.context_snippets)
        
        return ""
    
    def delete_source_entities(self, notebook_id: str, source_id: str):
        """Remove a source from all entity references."""
        if notebook_id not in self._entities:
            return
        
        to_delete = []
        for key, entity in self._entities[notebook_id].items():
            if source_id in entity.source_ids:
                entity.source_ids.remove(source_id)
                # Delete entity if no sources left
                if not entity.source_ids:
                    to_delete.append(key)
        
        for key in to_delete:
            del self._entities[notebook_id][key]
        
        self._save_entities()
    
    # =========================================================================
    # Backfill Methods
    # =========================================================================
    
    async def backfill_from_chunks(
        self,
        notebook_id: str,
        chunks: List[Dict]
    ) -> Dict:
        """Backfill entities from existing chunks in vector DB.
        
        Args:
            notebook_id: Notebook to backfill
            chunks: List of chunks with 'text', 'source_id', 'filename'
            
        Returns: Stats about extraction
        """
        from collections import defaultdict
        
        # Group chunks by source
        by_source = defaultdict(list)
        for chunk in chunks:
            source_id = chunk.get("source_id", "unknown")
            by_source[source_id].append(chunk)
        
        total_entities = 0
        sources_processed = 0
        
        for source_id, source_chunks in by_source.items():
            # Combine text from all chunks (limit to 8000 chars)
            combined_text = " ".join(c.get("text", "") for c in source_chunks)[:8000]
            
            if len(combined_text) < 100:
                continue
            
            # Extract entities
            entities = await self.extract_from_text(
                text=combined_text,
                notebook_id=notebook_id,
                source_id=source_id,
                use_llm=len(combined_text) > 500
            )
            
            total_entities += len(entities)
            sources_processed += 1
            
            # Yield control periodically
            if sources_processed % 5 == 0:
                import asyncio
                await asyncio.sleep(0.1)
        
        self._save_entities()
        
        print(f"[EntityExtractor] Backfill complete: {total_entities} entities from {sources_processed} sources")
        
        return {
            "entities_extracted": total_entities,
            "sources_processed": sources_processed,
            "notebook_id": notebook_id
        }
    
    # =========================================================================
    # Entity-Aware Retrieval Methods
    # =========================================================================
    
    def find_entities_in_query(
        self,
        notebook_id: str,
        query: str
    ) -> List[Entity]:
        """Find known entities mentioned in a query.
        
        Returns entities from the notebook that appear in the query text.
        Used to boost retrieval for entity-specific questions.
        """
        if notebook_id not in self._entities:
            return []
        
        query_lower = query.lower()
        found = []
        
        for entity in self._entities[notebook_id].values():
            entity_name_lower = entity.name.lower()
            
            # Check for entity name in query
            if entity_name_lower in query_lower:
                found.append(entity)
            # Also check first name for person entities
            elif entity.type == "person" and " " in entity.name:
                first_name = entity.name.split()[0].lower()
                if len(first_name) > 2 and first_name in query_lower.split():
                    found.append(entity)
        
        # Sort by mentions (most mentioned entities first)
        found.sort(key=lambda e: -e.mentions)
        
        return found
    
    def get_entity_source_boost(
        self,
        notebook_id: str,
        query: str
    ) -> Dict[str, float]:
        """Get source boost scores based on entity mentions in query.
        
        Returns: {source_id: boost_score} where boost_score is 0.0-1.0
        Higher scores mean the source is more likely relevant.
        """
        entities = self.find_entities_in_query(notebook_id, query)
        
        if not entities:
            return {}
        
        source_scores = {}
        
        for entity in entities:
            # Weight by entity mention count (normalized)
            weight = min(1.0, entity.mentions / 10.0)
            
            for source_id in entity.source_ids:
                if source_id in source_scores:
                    source_scores[source_id] = min(1.0, source_scores[source_id] + weight * 0.3)
                else:
                    source_scores[source_id] = weight * 0.5
        
        return source_scores
    
    def get_entity_context_for_query(
        self,
        notebook_id: str,
        query: str,
        max_entities: int = 3
    ) -> str:
        """Get entity context to add to LLM prompts.
        
        Returns a string with relevant entity information that can
        be prepended to the context for better answers.
        """
        entities = self.find_entities_in_query(notebook_id, query)[:max_entities]
        
        if not entities:
            return ""
        
        parts = []
        for entity in entities:
            context = " | ".join(entity.context_snippets[:2]) if entity.context_snippets else ""
            if context:
                parts.append(f"- {entity.name} ({entity.type}): {context}")
            else:
                parts.append(f"- {entity.name} ({entity.type}): mentioned {entity.mentions} times")
        
        if parts:
            return "KNOWN ENTITIES:\n" + "\n".join(parts) + "\n\n"
        
        return ""
    
    def boost_results_by_entity(
        self,
        notebook_id: str,
        query: str,
        results: List[Dict],
        boost_factor: float = 0.15
    ) -> List[Dict]:
        """Boost search results that contain entities mentioned in query.
        
        Modifies rerank_score or adds entity_boost field to results.
        Returns results sorted by boosted score.
        """
        source_boosts = self.get_entity_source_boost(notebook_id, query)
        
        if not source_boosts:
            return results
        
        entities = self.find_entities_in_query(notebook_id, query)
        entity_names = [e.name.lower() for e in entities]
        
        for result in results:
            source_id = result.get("source_id", "")
            text_lower = result.get("text", "").lower()
            
            # Source-level boost
            source_boost = source_boosts.get(source_id, 0)
            
            # Text-level boost (entity appears in this specific chunk)
            text_boost = 0
            for name in entity_names:
                if name in text_lower:
                    text_boost += boost_factor
            
            # Combined boost
            total_boost = min(0.3, source_boost * boost_factor + text_boost)
            
            # Apply boost to existing score
            if "rerank_score" in result:
                result["entity_boost"] = total_boost
                result["boosted_score"] = result["rerank_score"] + total_boost
            else:
                result["entity_boost"] = total_boost
                result["boosted_score"] = total_boost
        
        # Sort by boosted score
        results.sort(key=lambda r: -r.get("boosted_score", r.get("rerank_score", 0)))
        
        boosted_count = sum(1 for r in results if r.get("entity_boost", 0) > 0)
        if boosted_count > 0:
            print(f"[EntityExtractor] Boosted {boosted_count} results for {len(entities)} entities")
        
        return results


# Singleton instance
entity_extractor = EntityExtractor()
