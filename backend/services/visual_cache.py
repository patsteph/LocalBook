"""
Visual Classification Cache - Pre-compute visual types during query for instant visual generation.

When user asks a question, we analyze the answer content in the background and cache:
- Extracted structure (themes, entities, relationships, etc.)
- Suggested visual type
- Key items for the visual

When user clicks "Create Visual", we check cache first - if hit, instant response.
Cache expires after TTL or after N other queries (LRU-style).
"""
import asyncio
import time
import hashlib
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from collections import OrderedDict


@dataclass
class VisualClassification:
    """Cached visual classification result."""
    query: str
    answer_preview: str  # First 500 chars of answer for matching
    visual_type: str
    suggested_template: str
    key_items: List[str]
    title: str
    structure: Dict[str, Any]  # Raw extracted structure
    created_at: float = field(default_factory=time.time)
    notebook_id: str = ""
    # Phase 4: Multi-visual support
    secondary_types: List[str] = field(default_factory=list)  # Additional visual types detected
    has_multiple_structures: bool = False  # True if content has themes AND timeline, etc.


class VisualClassificationCache:
    """TTL + LRU cache for visual classifications.
    
    - Expires entries after TTL seconds
    - Also expires oldest entries when max_entries exceeded
    - Key is hash of (notebook_id, query, answer_preview)
    """
    
    def __init__(self, ttl_seconds: int = 1800, max_entries: int = 50):
        self.ttl_seconds = ttl_seconds  # 30 minutes default
        self.max_entries = max_entries
        self._cache: OrderedDict[str, VisualClassification] = OrderedDict()
        self._lock = asyncio.Lock()
    
    def _make_key(self, notebook_id: str, query: str, answer_preview: str) -> str:
        """Create cache key from notebook, query, and answer preview."""
        content = f"{notebook_id}:{query}:{answer_preview[:200]}"
        return hashlib.md5(content.encode()).hexdigest()
    
    async def get(self, notebook_id: str, query: str, answer_preview: str) -> Optional[VisualClassification]:
        """Get cached classification if exists and not expired."""
        async with self._lock:
            key = self._make_key(notebook_id, query, answer_preview)
            
            if key not in self._cache:
                return None
            
            entry = self._cache[key]
            
            # Check TTL
            if time.time() - entry.created_at > self.ttl_seconds:
                del self._cache[key]
                return None
            
            # Move to end (LRU touch)
            self._cache.move_to_end(key)
            return entry
    
    async def set(self, classification: VisualClassification) -> None:
        """Store classification in cache."""
        async with self._lock:
            key = self._make_key(
                classification.notebook_id,
                classification.query,
                classification.answer_preview
            )
            
            # Evict oldest if at capacity
            while len(self._cache) >= self.max_entries:
                self._cache.popitem(last=False)
            
            self._cache[key] = classification
            self._cache.move_to_end(key)
    
    async def get_by_notebook(self, notebook_id: str) -> Optional[VisualClassification]:
        """Get most recent classification for a notebook (for quick access)."""
        async with self._lock:
            # Find most recent entry for this notebook
            for key in reversed(self._cache):
                entry = self._cache[key]
                if entry.notebook_id == notebook_id:
                    # Check TTL
                    if time.time() - entry.created_at > self.ttl_seconds:
                        del self._cache[key]
                        continue
                    return entry
            return None
    
    async def is_ready(self, notebook_id: str) -> dict:
        """Check if cache has a valid entry for notebook. Returns status dict."""
        async with self._lock:
            for key in reversed(self._cache):
                entry = self._cache[key]
                if entry.notebook_id == notebook_id:
                    # Check TTL
                    if time.time() - entry.created_at > self.ttl_seconds:
                        return {"ready": False, "reason": "expired"}
                    # Check if themes exist
                    themes = entry.structure.get("themes", [])
                    if len(themes) >= 2:
                        return {
                            "ready": True, 
                            "theme_count": len(themes),
                            "age_seconds": int(time.time() - entry.created_at)
                        }
                    return {"ready": False, "reason": "no_themes"}
            return {"ready": False, "reason": "not_found"}
    
    async def clear_notebook(self, notebook_id: str) -> int:
        """Clear all cached entries for a notebook."""
        async with self._lock:
            keys_to_delete = [
                k for k, v in self._cache.items() 
                if v.notebook_id == notebook_id
            ]
            for key in keys_to_delete:
                del self._cache[key]
            return len(keys_to_delete)
    
    async def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        async with self._lock:
            now = time.time()
            keys_to_delete = [
                k for k, v in self._cache.items()
                if now - v.created_at > self.ttl_seconds
            ]
            for key in keys_to_delete:
                del self._cache[key]
            return len(keys_to_delete)
    
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        now = time.time()
        valid_count = sum(
            1 for v in self._cache.values()
            if now - v.created_at <= self.ttl_seconds
        )
        return {
            "total_entries": len(self._cache),
            "valid_entries": valid_count,
            "expired_entries": len(self._cache) - valid_count,
            "max_entries": self.max_entries,
            "ttl_seconds": self.ttl_seconds,
        }


# Singleton instance
visual_cache = VisualClassificationCache(ttl_seconds=1800, max_entries=50)
