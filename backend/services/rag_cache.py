"""RAG Caching Services

Provides caching for embeddings and answers to speed up RAG queries.
"""
import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import numpy as np

from config import settings


@dataclass
class CacheEntry:
    """A cached entry with metadata."""
    value: Any
    created_at: float
    hits: int = 0
    last_hit: float = 0


class EmbeddingCache:
    """LRU cache for text embeddings.
    
    Caches embeddings by text hash to avoid re-computing embeddings
    for repeated or similar texts. Uses in-memory cache with optional
    disk persistence.
    """
    
    def __init__(self, max_size: int = 10000, persist: bool = True):
        self.max_size = max_size
        self.persist = persist
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = asyncio.Lock()
        
        # Persistence
        self._cache_file = Path(settings.db_path).parent / "embedding_cache.json"
        if persist:
            self._load_cache()
    
    def _hash_text(self, text: str) -> str:
        """Create a hash key for text."""
        return hashlib.md5(text.encode('utf-8')).hexdigest()
    
    def _load_cache(self):
        """Load cache from disk."""
        try:
            if self._cache_file.exists():
                with open(self._cache_file, 'r') as f:
                    data = json.load(f)
                    for key, entry in data.get("entries", {}).items():
                        self._cache[key] = CacheEntry(
                            value=entry["value"],
                            created_at=entry["created_at"],
                            hits=entry.get("hits", 0),
                            last_hit=entry.get("last_hit", 0)
                        )
                    self._hits = data.get("total_hits", 0)
                    self._misses = data.get("total_misses", 0)
                print(f"[EmbeddingCache] Loaded {len(self._cache)} cached embeddings")
        except Exception as e:
            print(f"[EmbeddingCache] Could not load cache: {e}")
    
    def _save_cache(self):
        """Save cache to disk."""
        if not self.persist:
            return
        try:
            data = {
                "entries": {
                    k: {
                        "value": v.value,
                        "created_at": v.created_at,
                        "hits": v.hits,
                        "last_hit": v.last_hit
                    }
                    for k, v in self._cache.items()
                },
                "total_hits": self._hits,
                "total_misses": self._misses,
                "last_saved": time.time()
            }
            with open(self._cache_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[EmbeddingCache] Could not save cache: {e}")
    
    def get(self, text: str) -> Optional[List[float]]:
        """Get embedding from cache if it exists."""
        key = self._hash_text(text)
        if key in self._cache:
            entry = self._cache[key]
            entry.hits += 1
            entry.last_hit = time.time()
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self._hits += 1
            return entry.value
        self._misses += 1
        return None
    
    def put(self, text: str, embedding: List[float]):
        """Store embedding in cache."""
        key = self._hash_text(text)
        
        # Evict oldest if at capacity
        while len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)
        
        self._cache[key] = CacheEntry(
            value=embedding,
            created_at=time.time()
        )
    
    async def get_or_compute(
        self, 
        text: str, 
        compute_fn
    ) -> List[float]:
        """Get embedding from cache or compute it."""
        async with self._lock:
            cached = self.get(text)
            if cached is not None:
                return cached
        
        # Compute outside lock
        if asyncio.iscoroutinefunction(compute_fn):
            embedding = await compute_fn(text)
        else:
            embedding = compute_fn(text)
        
        async with self._lock:
            self.put(text, embedding if isinstance(embedding, list) else embedding.tolist())
            
            # Save periodically
            if (self._hits + self._misses) % 100 == 0:
                self._save_cache()
        
        return embedding
    
    async def get_or_compute_batch(
        self,
        texts: List[str],
        compute_fn
    ) -> List[List[float]]:
        """Get embeddings for multiple texts, computing only uncached ones."""
        results = [None] * len(texts)
        uncached_indices = []
        uncached_texts = []
        
        async with self._lock:
            for i, text in enumerate(texts):
                cached = self.get(text)
                if cached is not None:
                    results[i] = cached
                else:
                    uncached_indices.append(i)
                    uncached_texts.append(text)
        
        if uncached_texts:
            # Compute uncached embeddings
            if asyncio.iscoroutinefunction(compute_fn):
                computed = await compute_fn(uncached_texts)
            else:
                computed = compute_fn(uncached_texts)
            
            async with self._lock:
                for i, idx in enumerate(uncached_indices):
                    emb = computed[i]
                    emb_list = emb if isinstance(emb, list) else emb.tolist()
                    results[idx] = emb_list
                    self.put(uncached_texts[i], emb_list)
                
                # Save after batch
                self._save_cache()
        
        return results
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0
        }
    
    def clear(self):
        """Clear the cache."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
        if self.persist and self._cache_file.exists():
            self._cache_file.unlink()


class AnswerCache:
    """Semantic cache for RAG answers.
    
    Caches answers by query embedding similarity. If a new query is
    semantically similar (>92% cosine similarity) to a cached query,
    returns the cached answer instead of re-generating.
    """
    
    def __init__(
        self, 
        max_size: int = 500,
        similarity_threshold: float = 0.92,
        ttl_hours: int = 24,
        persist: bool = True
    ):
        self.max_size = max_size
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_hours * 3600
        self.persist = persist
        
        # Cache: query_hash -> (embedding, answer, citations, timestamp, hits)
        self._cache: Dict[str, Dict] = {}
        self._embeddings: Dict[str, np.ndarray] = {}  # Kept separate for fast similarity
        self._hits = 0
        self._misses = 0
        self._lock = asyncio.Lock()
        
        # Persistence
        self._cache_file = Path(settings.db_path).parent / "answer_cache.json"
        if persist:
            self._load_cache()
    
    def _load_cache(self):
        """Load cache from disk."""
        try:
            if self._cache_file.exists():
                with open(self._cache_file, 'r') as f:
                    data = json.load(f)
                    self._cache = data.get("entries", {})
                    # Reconstruct embeddings
                    for key, entry in self._cache.items():
                        if "embedding" in entry:
                            self._embeddings[key] = np.array(entry["embedding"])
                    self._hits = data.get("total_hits", 0)
                    self._misses = data.get("total_misses", 0)
                print(f"[AnswerCache] Loaded {len(self._cache)} cached answers")
        except Exception as e:
            print(f"[AnswerCache] Could not load cache: {e}")
    
    def _save_cache(self):
        """Save cache to disk."""
        if not self.persist:
            return
        try:
            # Convert embeddings to lists for JSON
            entries = {}
            for key, entry in self._cache.items():
                entries[key] = {
                    **entry,
                    "embedding": self._embeddings[key].tolist() if key in self._embeddings else []
                }
            
            data = {
                "entries": entries,
                "total_hits": self._hits,
                "total_misses": self._misses,
                "last_saved": time.time()
            }
            with open(self._cache_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[AnswerCache] Could not save cache: {e}")
    
    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
    
    def _hash_query(self, question: str, notebook_id: str) -> str:
        """Create a hash key for exact match lookup."""
        return hashlib.md5(f"{notebook_id}:{question}".encode()).hexdigest()
    
    def _is_expired(self, entry: Dict) -> bool:
        """Check if cache entry is expired."""
        return time.time() - entry.get("timestamp", 0) > self.ttl_seconds
    
    async def get(
        self, 
        question: str, 
        notebook_id: str,
        query_embedding: np.ndarray
    ) -> Optional[Dict]:
        """Get cached answer if similar query exists."""
        async with self._lock:
            # First try exact match
            exact_key = self._hash_query(question, notebook_id)
            if exact_key in self._cache:
                entry = self._cache[exact_key]
                if not self._is_expired(entry):
                    entry["hits"] = entry.get("hits", 0) + 1
                    self._hits += 1
                    print(f"[AnswerCache] Exact hit for query")
                    return {
                        "answer": entry["answer"],
                        "citations": entry["citations"],
                        "cache_type": "exact"
                    }
            
            # Try semantic similarity match
            query_emb = np.array(query_embedding) if not isinstance(query_embedding, np.ndarray) else query_embedding
            
            best_match = None
            best_similarity = 0
            
            for key, cached_emb in self._embeddings.items():
                if key not in self._cache:
                    continue
                entry = self._cache[key]
                
                # Skip expired or different notebook
                if self._is_expired(entry) or entry.get("notebook_id") != notebook_id:
                    continue
                
                similarity = self._cosine_similarity(query_emb, cached_emb)
                if similarity > best_similarity and similarity >= self.similarity_threshold:
                    best_similarity = similarity
                    best_match = (key, entry)
            
            if best_match:
                key, entry = best_match
                entry["hits"] = entry.get("hits", 0) + 1
                self._hits += 1
                print(f"[AnswerCache] Semantic hit (similarity={best_similarity:.3f})")
                return {
                    "answer": entry["answer"],
                    "citations": entry["citations"],
                    "cache_type": "semantic",
                    "similarity": best_similarity
                }
            
            self._misses += 1
            return None
    
    async def put(
        self,
        question: str,
        notebook_id: str,
        query_embedding: np.ndarray,
        answer: str,
        citations: List[Dict]
    ):
        """Store answer in cache."""
        async with self._lock:
            # Evict oldest if at capacity
            if len(self._cache) >= self.max_size:
                # Remove oldest by timestamp
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].get("timestamp", 0))
                del self._cache[oldest_key]
                if oldest_key in self._embeddings:
                    del self._embeddings[oldest_key]
            
            key = self._hash_query(question, notebook_id)
            self._cache[key] = {
                "question": question[:200],  # Store preview
                "notebook_id": notebook_id,
                "answer": answer,
                "citations": citations,
                "timestamp": time.time(),
                "hits": 0
            }
            self._embeddings[key] = np.array(query_embedding) if not isinstance(query_embedding, np.ndarray) else query_embedding
            
            # Save periodically
            if len(self._cache) % 10 == 0:
                self._save_cache()
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0,
            "similarity_threshold": self.similarity_threshold
        }
    
    def clear(self):
        """Clear the cache."""
        self._cache.clear()
        self._embeddings.clear()
        self._hits = 0
        self._misses = 0
        if self.persist and self._cache_file.exists():
            self._cache_file.unlink()


class ContextCompressor:
    """Compresses context to reduce LLM token count while preserving key information."""
    
    def __init__(self, max_tokens: int = 3000, chars_per_token: int = 4):
        self.max_tokens = max_tokens
        self.chars_per_token = chars_per_token
        self.max_chars = max_tokens * chars_per_token
    
    def compress(
        self, 
        chunks: List[str],
        confidences: Optional[List[float]] = None
    ) -> Tuple[str, int]:
        """Compress chunks to fit token budget.
        
        Returns: (compressed_context, original_char_count)
        """
        if not chunks:
            return "", 0
        
        original_chars = sum(len(c) for c in chunks)
        
        # If already under budget, return as-is
        if original_chars <= self.max_chars:
            context = "\n\n".join(f"[{i+1}] {chunk}" for i, chunk in enumerate(chunks))
            return context, original_chars
        
        # Sort by confidence if available
        if confidences and len(confidences) == len(chunks):
            indexed_chunks = list(zip(range(len(chunks)), chunks, confidences))
            indexed_chunks.sort(key=lambda x: -x[2])  # Highest confidence first
        else:
            indexed_chunks = [(i, c, 1.0) for i, c in enumerate(chunks)]
        
        # Add chunks until budget exhausted
        compressed = []
        total_chars = 0
        used_indices = set()
        
        for orig_idx, chunk, conf in indexed_chunks:
            chunk_chars = len(chunk)
            
            if total_chars + chunk_chars <= self.max_chars * 0.85:  # Leave room for numbering
                compressed.append((orig_idx, chunk))
                total_chars += chunk_chars
                used_indices.add(orig_idx)
            elif total_chars < self.max_chars * 0.7:
                # Truncate this chunk to fit
                remaining = int(self.max_chars * 0.85 - total_chars)
                truncated = chunk[:remaining] + "..."
                compressed.append((orig_idx, truncated))
                total_chars += len(truncated)
                break
            else:
                break
        
        # Sort back to original order for coherent context
        compressed.sort(key=lambda x: x[0])
        
        # Build context with original citation numbers
        context_parts = []
        for orig_idx, chunk in compressed:
            context_parts.append(f"[{orig_idx + 1}] {chunk}")
        
        context = "\n\n".join(context_parts)
        
        # Add summary note if we dropped chunks
        dropped = len(chunks) - len(compressed)
        if dropped > 0:
            context += f"\n\n[Note: {dropped} additional sources available but omitted for brevity]"
        
        print(f"[ContextCompressor] Compressed {original_chars} -> {len(context)} chars ({len(compressed)}/{len(chunks)} chunks)")
        
        return context, original_chars
    
    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for text."""
        return len(text) // self.chars_per_token


# Singleton instances
embedding_cache = EmbeddingCache(max_size=10000, persist=True)
answer_cache = AnswerCache(max_size=500, similarity_threshold=0.92, persist=True)
context_compressor = ContextCompressor(max_tokens=3000)
