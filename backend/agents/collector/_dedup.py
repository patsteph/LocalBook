"""DedupMixin — extracted from the former agents/collector.py (Wave 6 split)."""
from ._models import *  # noqa: F401,F403


class DedupMixin:
    def _content_similarity(self, content1: str, content2: str) -> float:
        """Quick content similarity check using word overlap"""
        words1 = set(content1.lower().split()[:50])
        words2 = set(content2.lower().split()[:50])
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0

    def _generate_content_hash(self, content: str) -> str:
        """Generate hash for exact duplicate detection"""
        normalized = content.lower().strip()
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    async def _find_semantic_duplicate(self, item: CollectedItem, threshold: float = 0.92) -> Optional[str]:
        """Find near-duplicate via embedding similarity"""
        try:
            # Search existing Collector memories for this notebook
            results = await memory_store.search_archival_memory_async(
                query=item.title + " " + item.content[:500],
                namespace=AgentNamespace.COLLECTOR,
                notebook_id=self.notebook_id,
                limit=5
            )
            
            for r in results:
                if r.similarity_score >= threshold:
                    return r.entry.id
        except Exception as e:
            logger.debug(f"Semantic duplicate check failed (non-fatal): {e}")
        
        return None
