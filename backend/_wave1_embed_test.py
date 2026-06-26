"""Deterministic unit checks for the Wave 1 P0 embedding fix (no live model).

Run:  cd backend && python3 _wave1_embed_test.py

Verifies:
  1. ollama_service.embed_batch issues ONE /api/embed call per <=max_batch slice,
     sends `input` as a LIST, preserves order/count, returns correct dim.
  2. rag_embeddings.encode_async sub-batches via embed_batch (not per-text),
     returns (N, dim), yields between batches.
"""
import asyncio
import sys

import numpy as np

from config import settings
from services.ollama_service import ollama_service
from services import rag_embeddings

DIM = settings.embedding_dim
PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


class _Resp:
    def __init__(self, n):
        self._n = n

    def raise_for_status(self):
        pass

    def json(self):
        return {"embeddings": [[0.1] * DIM for _ in range(self._n)]}


async def test_embed_batch_single_call():
    print("embed_batch — one /api/embed call per slice")
    calls = []

    class FakeClient:
        async def post(self, url, json=None, timeout=None):
            calls.append(json)
            n = len(json["input"]) if isinstance(json["input"], list) else 1
            return _Resp(n)

    orig = ollama_service._get_client
    ollama_service._get_client = lambda: FakeClient()
    try:
        out = await ollama_service.embed_batch([f"t{i}" for i in range(100)], max_batch=64)
    finally:
        ollama_service._get_client = orig

    check("returns one vector per input (100)", len(out) == 100)
    check("sends `input` as a list", bool(calls) and isinstance(calls[0]["input"], list))
    check("collapses 100 texts -> 2 calls (64+36)", len(calls) == 2)
    check("first slice carries 64 texts", bool(calls) and len(calls[0]["input"]) == 64)
    check("vectors have correct dim", all(len(v) == DIM for v in out))


async def test_encode_async_batches():
    print("encode_async — sub-batches via embed_batch, no per-text fan-out")
    seen = []

    async def fake_embed_batch(texts, **kw):
        seen.append(len(texts))
        return [[0.2] * DIM for _ in texts]

    orig_eb = ollama_service.embed_batch
    orig_use = rag_embeddings._use_ollama
    ollama_service.embed_batch = fake_embed_batch
    rag_embeddings._use_ollama = True

    import services.memory_steward as ms
    orig_clear = getattr(ms, "await_background_clearance", None)

    async def _noop():
        return None

    ms.await_background_clearance = _noop
    try:
        arr = await rag_embeddings.encode_async([f"x{i}" for i in range(150)])
    finally:
        ollama_service.embed_batch = orig_eb
        rag_embeddings._use_ollama = orig_use
        if orig_clear is not None:
            ms.await_background_clearance = orig_clear

    check("returns (150, dim)", arr.shape == (150, DIM))
    check("sub-batches 150 -> [64, 64, 22]", seen == [64, 64, 22])
    check("made batched calls (not 150 per-text)", len(seen) == 3)


async def main():
    print("Wave 1 — P0 embedding fix unit checks\n")
    await test_embed_batch_single_call()
    await test_encode_async_batches()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
