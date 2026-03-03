"""
RAG Embeddings — Embedding generation via Ollama or SentenceTransformer.

Extracted from rag_engine.py Phase 1. Owns all embedding model management,
sync/async encoding, and batch processing with retry logic.

External callers continue to use rag_engine.encode() — RAGEngine delegates here.
"""
import asyncio
import time
from typing import List, Optional, Tuple, Union

import httpx
import numpy as np

from config import settings


# ─── Lazy-loaded model state ────────────────────────────────────────────────────

_embedding_model = None  # SentenceTransformer fallback (lazy)
_use_ollama = settings.use_ollama_embeddings


# ─── Model Loading ──────────────────────────────────────────────────────────────

def get_embedding_model():
    """Lazy load SentenceTransformer embedding model (fallback when Ollama not used)."""
    global _embedding_model
    if _use_ollama:
        return None
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(settings.embedding_model)
    return _embedding_model


def load_embedding_model():
    """Force load the embedding model (used for warmup)."""
    if _use_ollama:
        # For Ollama, warmup is handled by model_warmup.py
        return None
    return get_embedding_model()


# ─── Sync Embedding ─────────────────────────────────────────────────────────────

def _get_ollama_embedding_sync(text: str) -> List[float]:
    """Get embedding from Ollama synchronously."""
    import requests
    response = requests.post(
        f"{settings.ollama_base_url}/api/embeddings",
        json={
            "model": settings.embedding_model,
            "prompt": text
        },
        timeout=60
    )
    result = response.json()
    return result.get("embedding", [])


def _get_ollama_embeddings_batch_sync(texts: List[str]) -> List[List[float]]:
    """Get embeddings for multiple texts from Ollama.
    
    Uses sequential processing with exponential backoff retry.
    Logs failures for monitoring - zero vectors indicate retrieval gaps.
    """
    embeddings = []
    failed_chunks = []
    
    for i, text in enumerate(texts):
        embedding = None
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                embedding = _get_ollama_embedding_sync(text)
                if embedding and len(embedding) == settings.embedding_dim:
                    break  # Success
                
                # Empty or wrong dimension - retry
                if attempt < max_retries - 1:
                    wait_time = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                    print(f"[RAG] Empty embedding for chunk {i}, retry {attempt + 1}/{max_retries} in {wait_time}s...")
                    time.sleep(wait_time)
                    
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 0.5 * (2 ** attempt)
                    print(f"[RAG] Embedding failed for chunk {i} (attempt {attempt + 1}): {e}")
                    time.sleep(wait_time)
                else:
                    print(f"[RAG] ⚠️ EMBEDDING FAILED after {max_retries} attempts for chunk {i}: {e}")
        
        if embedding and len(embedding) == settings.embedding_dim:
            embeddings.append(embedding)
        else:
            # Last resort: zero vector (will be logged for later repair)
            embeddings.append([0.0] * settings.embedding_dim)
            failed_chunks.append(i)
    
    if failed_chunks:
        print(f"[RAG] ⚠️ {len(failed_chunks)} chunks got zero vectors (indices: {failed_chunks[:5]}{'...' if len(failed_chunks) > 5 else ''})")
    
    return embeddings


def encode(texts: Union[str, List[str]]) -> np.ndarray:
    """Encode texts to embeddings (compatible with SentenceTransformer interface).
    
    This is the primary sync encoding entry point. All callers
    (rag_engine.encode, external services) route through here.
    """
    if isinstance(texts, str):
        texts = [texts]
    
    if _use_ollama:
        embeddings = _get_ollama_embeddings_batch_sync(texts)
        return np.array(embeddings)
    else:
        model = get_embedding_model()
        return model.encode(texts)


# ─── Async Embedding ────────────────────────────────────────────────────────────

async def _get_ollama_embedding(text: str) -> List[float]:
    """Get embedding from Ollama asynchronously."""
    timeout = httpx.Timeout(60.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{settings.ollama_base_url}/api/embeddings",
            json={
                "model": settings.embedding_model,
                "prompt": text
            }
        )
        result = response.json()
        return result.get("embedding", [])


async def _get_ollama_embeddings_batch_async(texts: List[str], max_concurrent: int = 10) -> List[List[float]]:
    """Get embeddings for multiple texts in parallel using asyncio.gather.
    
    This is 10-20x faster than sequential processing for large batches.
    Uses semaphore to limit concurrent requests and avoid overwhelming Ollama.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    timeout = httpx.Timeout(60.0)
    
    async def get_single_embedding(text: str, index: int) -> Tuple[int, List[float]]:
        async with semaphore:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        f"{settings.ollama_base_url}/api/embeddings",
                        json={"model": settings.embedding_model, "prompt": text}
                    )
                    result = response.json()
                    embedding = result.get("embedding", [])
                    if not embedding:
                        print(f"[RAG] Empty embedding for chunk {index}, using zero vector")
                        return (index, [0.0] * settings.embedding_dim)
                    return (index, embedding)
            except Exception as e:
                print(f"[RAG] Embedding failed for chunk {index}: {e}")
                return (index, [0.0] * settings.embedding_dim)
    
    # Run all embedding requests in parallel
    tasks = [get_single_embedding(text, i) for i, text in enumerate(texts)]
    results = await asyncio.gather(*tasks)
    
    # Sort by index to preserve order
    results.sort(key=lambda x: x[0])
    return [emb for _, emb in results]


async def encode_async(texts: Union[str, List[str]]) -> np.ndarray:
    """Async encode texts to embeddings using parallel processing.
    
    Use this instead of encode() in async contexts for 10-20x speedup.
    """
    if isinstance(texts, str):
        texts = [texts]
    
    if _use_ollama:
        embeddings = await _get_ollama_embeddings_batch_async(texts)
        return np.array(embeddings)
    else:
        # Fallback to sync for sentence-transformers
        model = get_embedding_model()
        return model.encode(texts)


# ─── Utilities ──────────────────────────────────────────────────────────────────

def get_current_embedding_dim() -> int:
    """Get the dimension of the current embedding model."""
    test_embedding = encode("test")[0]
    return len(test_embedding)
