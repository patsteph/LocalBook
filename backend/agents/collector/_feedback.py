"""FeedbackMixin — extracted from the former agents/collector.py (Wave 6 split)."""
from ._models import *  # noqa: F401,F403


class FeedbackMixin:
    async def reduce_priority_for_patterns(self, patterns: List[Dict]) -> None:
        """Reduce collection priority for ignored patterns"""
        # Extract topics/keywords from ignored items
        # Add to excluded_topics or reduce weight
        for pattern in patterns:
            topics = pattern.get("topics", [])
            for topic in topics:
                if topic not in self.config.excluded_topics:
                    self.config.excluded_topics.append(topic)
        
        if patterns:
            self._save_config()

    async def expand_focus_areas(self, search_misses: List[str]) -> None:
        """Expand focus based on search misses (user wanted X, we didn't have it)"""
        # Add search miss queries as focus areas
        for query in search_misses[:5]:
            if query not in self.config.focus_areas:
                self.config.focus_areas.append(query)
        
        if search_misses:
            self._save_config()

    async def contextualize_item(self, item: CollectedItem) -> Dict[str, Any]:
        """
        Connect new item to existing knowledge.
        Highlight what's NEW vs continuation of known story.
        Also applies delta fields directly onto the CollectedItem.
        """
        # Find related existing content in this notebook's memory
        try:
            related = await memory_store.search_archival_memory_async(
                query=item.title + " " + item.content[:500],
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=self.notebook_id,
                limit=10
            )
        except Exception as e:
            logger.debug(f"Archival search for contextualization failed (non-fatal): {e}")
            related = []
        
        # Filter to meaningfully related items (similarity > 0.3)
        related = [r for r in related if r.similarity_score > 0.3]
        
        if not related:
            # Entirely new topic — no existing knowledge
            item.is_new_topic = True
            item.knowledge_overlap = 0.0
            item.delta_summary = "New topic — not covered in existing research"
            return {
                "is_new_topic": True,
                "related_items": [],
                "delta_summary": item.delta_summary,
                "temporal_context": None,
                "knowledge_overlap": 0.0
            }
        
        # Compute knowledge_overlap from similarity scores
        max_similarity = max(r.similarity_score for r in related)
        avg_similarity = sum(r.similarity_score for r in related[:5]) / min(len(related), 5)
        knowledge_overlap = round((max_similarity * 0.6 + avg_similarity * 0.4), 2)
        
        # Extract related titles for UI display
        related_titles = []
        for r in related[:3]:
            # Extract title (first line or first 80 chars of content)
            content_text = r.entry.content if hasattr(r.entry, 'content') else str(r.entry)
            first_line = content_text.split('\n')[0][:80]
            related_titles.append(first_line)
        
        # Use LLM to identify what's specifically NEW
        related_content = "\n".join([
            f"- {r.entry.content[:200]}" for r in related[:5]
            if hasattr(r.entry, 'content')
        ])
        
        try:
            prompt = f"""Compare this NEW item against what the user already knows.

NEW ITEM:
Title: {item.title}
Content: {item.content[:800]}

EXISTING KNOWLEDGE (user already has these):
{related_content}

Respond with JSON only:
{{
    "is_new_topic": false,
    "delta_summary": "What's specifically NEW in this item that isn't in existing knowledge (one sentence)",
    "temporal_context": "How this relates chronologically to existing items (one sentence, or null if unknown)",
    "knowledge_overlap": {knowledge_overlap}
}}"""

            response = await ollama_service.generate(
                prompt=prompt,
                system="You are a research analyst identifying what's new. Respond only with valid JSON.",
                model=settings.ollama_fast_model,
                temperature=0.2
            )
            
            text = response.get("response", "")
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                result = json.loads(text[json_start:json_end])
                
                # Apply to item
                item.is_new_topic = result.get("is_new_topic", False)
                item.delta_summary = result.get("delta_summary")
                item.temporal_context = result.get("temporal_context")
                item.knowledge_overlap = float(result.get("knowledge_overlap", knowledge_overlap))
                item.related_titles = related_titles
                
                return {
                    "is_new_topic": item.is_new_topic,
                    "related_items": [r.entry.id for r in related[:3]],
                    "delta_summary": item.delta_summary,
                    "temporal_context": item.temporal_context,
                    "knowledge_overlap": item.knowledge_overlap,
                    "connects_to": related_titles
                }
        except Exception as e:
            logger.error(f"Contextualization LLM call failed: {e}")
        
        # Fallback: we know it's related but can't compute delta via LLM
        item.is_new_topic = False
        item.knowledge_overlap = knowledge_overlap
        item.related_titles = related_titles
        return {
            "is_new_topic": False,
            "related_items": [r.entry.id for r in related[:3]],
            "delta_summary": None,
            "temporal_context": None,
            "knowledge_overlap": knowledge_overlap
        }
