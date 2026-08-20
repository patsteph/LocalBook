"""Deterministic unit checks for the embedding contract (no live model).

Run:  cd backend && python3 _wave1_embed_test.py

Verifies:
  1. llm_runtime.embed_batch embeds the whole list in ONE in-process call, preserves
     order/count/dim, and RAISES rather than zero-filling when no engine can serve it.
  2. rag_embeddings.encode_async sub-batches via embed_batch (not per-text),
     returns (N, dim), yields between batches.

Check 1 originally pinned the Ollama HTTP batching contract — one /api/embed call per
<=max_batch slice, `input` sent as a list. The v2.3.0 cutover deleted that transport, so it
now pins what replaced it. The `max_batch` slicing it used to verify existed to cap HTTP
round-trips; in-process there are none to cap.
"""
import asyncio
import sys

import numpy as np

from config import settings
from services.llm_runtime import llm_runtime
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


async def test_embed_batch_single_call():
    print("embed_batch — one in-process call, and it RAISES rather than zero-filling")
    calls = []

    async def _fake_mlx(texts):
        calls.append(len(texts))
        return [[0.1] * DIM for _ in texts]

    orig_mlx = llm_runtime._mlx_embed_or_none
    llm_runtime._mlx_embed_or_none = _fake_mlx
    try:
        out = await llm_runtime.embed_batch([f"t{i}" for i in range(100)], max_batch=64)
    finally:
        llm_runtime._mlx_embed_or_none = orig_mlx

    check("returns one vector per input (100)", len(out) == 100)
    check("embeds all 100 in ONE call (no HTTP slicing)", calls == [100])
    check("vectors have correct dim", all(len(v) == DIM for v in out))

    # THE property that matters. A zero vector is unretrievable forever and is written into
    # LanceDB; with no fallback engine left, returning zeros would silently poison the index.
    async def _no_engine(_texts):
        return None

    llm_runtime._mlx_embed_or_none = _no_engine
    try:
        await llm_runtime.embed_batch(["a", "b"])
        check("raises when no engine can serve it", False)
    except RuntimeError:
        check("raises when no engine can serve it", True)
    except Exception:
        check("raises when no engine can serve it", False)
    finally:
        llm_runtime._mlx_embed_or_none = orig_mlx

    llm_runtime._mlx_embed_or_none = _no_engine
    try:
        await llm_runtime.embed("q")
        check("single embed raises too", False)
    except RuntimeError:
        check("single embed raises too", True)
    except Exception:
        check("single embed raises too", False)
    finally:
        llm_runtime._mlx_embed_or_none = orig_mlx


async def test_encode_async_batches():
    print("encode_async — sub-batches via embed_batch, no per-text fan-out")
    seen = []

    async def fake_embed_batch(texts, **kw):
        seen.append(len(texts))
        return [[0.2] * DIM for _ in texts]

    orig_eb = llm_runtime.embed_batch
    orig_use = rag_embeddings._use_ollama
    llm_runtime.embed_batch = fake_embed_batch
    rag_embeddings._use_ollama = True

    import services.memory_steward as ms
    orig_clear = getattr(ms, "await_background_clearance", None)

    async def _noop():
        return None

    ms.await_background_clearance = _noop
    try:
        arr = await rag_embeddings.encode_async([f"x{i}" for i in range(150)])
    finally:
        llm_runtime.embed_batch = orig_eb
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
