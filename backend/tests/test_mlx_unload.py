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


# ── Gate metrics (2026-08-19) ───────────────────────────────────────────────────

def test_gate_thresholds_discriminate_between_real_runs():
    """A gate that fails everything is not a gate.

    The memory-metrics recommendation, implemented literally, aborted all SEVEN recorded runs
    including both Ollama baselines. Two flaws, both found by validating against the real data:
      · it recommended a single-dip floor (min_available >= 0.75 GB) while its own analysis
        said a single-dip criterion cannot discriminate — every run of both engines touches
        0.29-0.74 GB at some instant;
      · it compared MLX commitment to the WORKING SET, but MLX is pinned at `mlx_engine`'s own
        set_memory_limit (90% of the working set), so "abort above 90%" aborts on the ceiling
        we configured. Circular, and guaranteed to fire.
    """
    from evaluator.memory_sampler import _verdict

    # A healthy Ollama run (real numbers from 1690b60a-88b).
    assert _verdict(0.43, 0.3, 2.0, 0.46)["level"] == "pass"
    # A genuinely bad run — 75s continuously under 1.5 GB (507b0c49-12c).
    assert _verdict(0.43, 6.0, 75.5, 0.52)["level"] == "abort"
    # The worst MLX run — 22.8% of samples under pressure (759c52b5-3ff).
    assert _verdict(0.29, 22.8, 15.4, 1.0)["level"] == "abort"
    # A healthy MLX run pinned at its own limit: WARN, not abort — the cap is what prevents
    # the danger, so sitting on it is informative rather than dangerous.
    assert _verdict(0.56, 8.9, 14.2, 1.0)["level"] == "warn"


def test_a_single_dip_never_gates():
    """Every run of both engines dips below 0.75 GB. If that gated, nothing would ever pass."""
    from evaluator.memory_sampler import _verdict

    v = _verdict(0.10, 1.0, 2.0, 0.50)
    assert v["level"] == "pass"
    assert any("[info]" in r for r in v["reasons"]), "the dip should still be REPORTED"
