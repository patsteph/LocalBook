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
    """Get a single embedding from Ollama synchronously (legacy / rarely used).

    Kept for non-async callers that embed one string; bulk paths use the batched
    helpers below. Uses /api/embed (input list of one) for consistency.
    """
    import requests
    response = requests.post(
        f"{settings.ollama_base_url}/api/embed",
        json={"model": settings.embedding_model, "input": text},
        timeout=60,
    )
    response.raise_for_status()
    embs = response.json().get("embeddings") or []
    return embs[0] if embs else []


def _get_ollama_embeddings_batch_sync(texts: List[str]) -> List[List[float]]:
    """Get embeddings for many texts in ONE /api/embed call per sub-batch.

    P0a (2026-06-26): Ollama's /api/embed accepts a list ``input`` and returns all
    vectors in a single response, so we no longer loop one HTTP request per text
    (the old behaviour produced the thousands-of-calls flood). Sync path retained
    only for non-async callers; async contexts must use ``encode_async``. A failed
    or shape-mismatched sub-batch falls back to zero vectors (logged) so retrieval
    gaps stay visible rather than silently corrupting the index.
    """
    import requests
    if not texts:
        return []
    zero = [0.0] * settings.embedding_dim
    batch = 64
    out: List[List[float]] = []
    for start in range(0, len(texts), batch):
        sub = texts[start:start + batch]
        try:
            response = requests.post(
                f"{settings.ollama_base_url}/api/embed",
                json={"model": settings.embedding_model, "input": sub},
                timeout=120,
            )
            response.raise_for_status()
            embs = response.json().get("embeddings") or []
            if len(embs) == len(sub):
                out.extend(e if (e and len(e) == settings.embedding_dim) else zero for e in embs)
            else:
                print(f"[RAG] ⚠️ sync embed shape mismatch {len(embs)}≠{len(sub)} — zero-filling")
                out.extend(zero for _ in sub)
        except Exception as e:
            print(f"[RAG] ⚠️ sync batch embed failed for slice @{start}: {e}")
            out.extend(zero for _ in sub)
    return out


def encode(texts: Union[str, List[str]]) -> np.ndarray:
    """Encode texts to embeddings (compatible with SentenceTransformer interface).

    This is the primary sync encoding entry point. All callers
    (rag_engine.encode, external services) route through here.

    WARNING: this blocks. In an async context use ``encode_async`` instead — a
    sync embed on the event loop is what froze the loop on 2026-06-26.
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
    """Get embedding from Ollama asynchronously (via the canonical service)."""
    from services.ollama_service import ollama_service
    data = await ollama_service.embed(text, timeout=60.0)
    embs = data.get("embeddings") or []
    if embs:
        return embs[0]
    return data.get("embedding", [])  # legacy single-vector shape


async def _get_ollama_embeddings_batch_async(texts: List[str], max_concurrent: int = 10) -> List[List[float]]:
    """Embed many texts with the fewest round-trips, yielding to foreground work.

    P0a (2026-06-26): replaced the per-chunk fan-out (one HTTP call per chunk →
    thousands per big ingest, which monopolised Ollama and froze the loop) with one
    batched ``/api/embed`` call per sub-batch via ``ollama_service.embed_batch``. We
    still ``await_background_clearance()`` between sub-batches so a bulk/background
    ingest yields to any active FOREGROUND op (deadlock-proof: a no-op when this runs
    inside a foreground task tree, e.g. a chat's own embed). ``max_concurrent`` is
    retained for signature compatibility; batching now bounds the request count.
    """
    if not texts:
        return []
    from services.ollama_service import ollama_service
    from services.memory_steward import await_background_clearance

    zero = [0.0] * settings.embedding_dim
    batch = 64
    results: List[List[float]] = []
    for start in range(0, len(texts), batch):
        await await_background_clearance()
        sub = texts[start:start + batch]
        embs = await ollama_service.embed_batch(sub, timeout=60.0, max_batch=batch)
        if len(embs) == len(sub):
            results.extend(e if (e and len(e) == settings.embedding_dim) else zero for e in embs)
        else:
            results.extend(zero for _ in sub)
    return results


async def encode_async(texts: Union[str, List[str]]) -> np.ndarray:
    """Async encode texts to embeddings using batched processing.

    Use this instead of encode() in async contexts — one batched call per 64 texts
    instead of one blocking call per text.
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
