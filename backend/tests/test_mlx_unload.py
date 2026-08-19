"""MLXEngine.unload — the fix for weights that were never released.

MEASURED JUSTIFICATION (2026-08-19 engine A/B): an evaluation run ended holding 7.64 GB of MLX
weights, exactly gemma 4.793 + phi 2.010 + arctic 1.058 GiB, because nothing ever cleared
`_resident`. Confirmed three ways — the memory sampler trace, the arithmetic, and quitting the
app freeing precisely 7.3 GB. On a 16 GB machine that is most of the working set, and it is why
six pipelines call `memory_steward.free_for_pipeline` expecting an unload that did not exist.

These tests use a fake resident entry rather than a real model: the point under test is the
lock discipline and the bookkeeping, and loading gemma in CI would be absurd. The real free was
verified live — arctic unloaded and `mx.get_active_memory()` fell 1.058 → 0.0 GB.
"""
import asyncio

import pytest

from services.mlx_engine import mlx_engine


@pytest.fixture(autouse=True)
def clean_resident():
    """Never leave a fake entry behind — other tests share this singleton."""
    yield
    mlx_engine._resident.pop("fake/model", None)
    mlx_engine._embed_resident.pop("fake/embed", None)
    mlx_engine._model_locks.pop("fake/model", None)


def test_unload_removes_the_entry_and_reports_success():
    mlx_engine._resident["fake/model"] = ("model", "tokenizer")
    assert asyncio.run(mlx_engine.unload("fake/model")) is True
    assert "fake/model" not in mlx_engine._resident


def test_unloading_something_not_loaded_is_a_no_op():
    """Short-circuit before touching Metal: freeing nothing still costs a round trip."""
    assert asyncio.run(mlx_engine.unload("never/loaded")) is False


def test_embed_models_unload_too():
    """The embedder is held in a SEPARATE dict; an unload that only checked `_resident`
    would silently leave 1.058 GB behind."""
    mlx_engine._embed_resident["fake/embed"] = ("model", "tokenizer")
    assert asyncio.run(mlx_engine.unload("fake/embed")) is True
    assert "fake/embed" not in mlx_engine._embed_resident


def test_a_busy_model_is_SKIPPED_not_freed_mid_generation():
    """THE safety property. Freeing weights under a live stream is a crash; skipping an
    eviction is a missed optimisation. When in doubt, skip."""
    mlx_engine._resident["fake/model"] = ("model", "tokenizer")

    async def scenario():
        lock = mlx_engine._model_locks.setdefault("fake/model", asyncio.Lock())
        await lock.acquire()                      # simulate a generation in flight
        try:
            return await mlx_engine.unload("fake/model", wait=0.05)
        finally:
            lock.release()

    assert asyncio.run(scenario()) is False
    assert "fake/model" in mlx_engine._resident, "a busy model must NOT be evicted"


def test_unload_all_respects_keep():
    mlx_engine._resident["fake/model"] = ("m", "t")
    mlx_engine._embed_resident["fake/embed"] = ("m", "t")
    freed = asyncio.run(mlx_engine.unload_all(keep=["fake/embed"]))
    assert "fake/model" in freed
    assert "fake/embed" not in freed
    assert "fake/embed" in mlx_engine._embed_resident


def test_unload_all_with_nothing_loaded_returns_empty():
    assert asyncio.run(mlx_engine.unload_all()) == []


def test_resident_reports_what_is_held():
    """Without this the resident budget and any eviction sweep are unfalsifiable — the MLX
    twin of Ollama's /api/ps."""
    mlx_engine._resident["fake/model"] = ("m", "t")
    r = mlx_engine.resident()
    assert "fake/model" in r["text"]
    assert "active_gb" in r and "peak_gb" in r
