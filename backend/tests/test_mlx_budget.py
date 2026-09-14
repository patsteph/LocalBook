"""Resident budget — don't exceed the ceiling in the first place.

Stage 3.2, the other half of 3.1. Unload gives us the *ability* to reclaim; this decides *when*,
before a load rather than after the machine is already swapping.

Measured context (2026-08-19): MLX peaked at 10.18 GB — 86 % of Apple's 11.84 GiB working set —
and every run swapped. The budget is 75 % of the working set (8.88 GB here), and counts the
incoming model's KV at its DEPLOYED context because weights alone under-count the model that
actually hurts: phi is the small model by weight (2.01 vs 4.79 GiB) and the expensive one by KV
(2.00 vs 0.25 GiB at 16k), since it has 32 full-attention layers with 8 kv-heads against gemma's
7 with 2.
"""
import asyncio

import pytest

from services import model_sizing as ms
from services.mlx_engine import mlx_engine

GEMMA = "mlx-community/gemma-4-e4b-it-4bit"
PHI = "mlx-community/Phi-4-mini-instruct-4bit"
ARCTIC = "mlx-community/snowflake-arctic-embed-l-v2.0-bf16"


@pytest.fixture(autouse=True)
def clean():
    yield
    for m in (GEMMA, PHI, ARCTIC, "fake/a", "fake/b"):
        mlx_engine._resident.pop(m, None)
        mlx_engine._embed_resident.pop(m, None)
        mlx_engine._last_used.pop(m, None)
        mlx_engine._model_locks.pop(m, None)


def test_the_fast_model_is_the_expensive_one_at_context():
    """The finding that inverts the old sizing assumption — pinned so it cannot regress."""
    g_kv = ms.kv_cache_gb(ms.load_config(GEMMA), 16384)
    p_kv = ms.kv_cache_gb(ms.load_config(PHI), 16384)
    assert ms.exact_weight_gb(PHI) < ms.exact_weight_gb(GEMMA)   # phi is smaller...
    assert p_kv > g_kv * 5                                        # ...and far costlier at ctx


def test_resident_cost_is_exact_not_estimated():
    mlx_engine._resident[GEMMA] = ("m", "t")
    mlx_engine._embed_resident[ARCTIC] = ("m", "t")
    cost = mlx_engine._resident_cost_gb()
    expected = ms.exact_weight_gb(GEMMA) + ms.exact_weight_gb(ARCTIC)
    assert abs(cost - expected) < 0.01


def test_budget_comes_from_the_working_set():
    b = mlx_engine._budget_gb()
    assert b > 0
    assert abs(b - ms.working_set_gb() * 0.75) < 0.01


def test_no_eviction_when_the_incoming_model_fits():
    mlx_engine._embed_resident[ARCTIC] = ("m", "t")
    asyncio.run(mlx_engine._make_room_for(GEMMA))     # 1.06 + 6.00 = 7.06 < 8.88
    assert ARCTIC in mlx_engine._embed_resident, "evicted when there was room"


def test_eviction_fires_when_the_incoming_model_does_not_fit():
    """gemma + arctic resident (5.85), phi incoming needs 4.41 → 10.26 > 8.88."""
    mlx_engine._resident[GEMMA] = ("m", "t")
    mlx_engine._embed_resident[ARCTIC] = ("m", "t")
    mlx_engine._last_used[GEMMA] = 1.0            # older → evicted first
    mlx_engine._last_used[ARCTIC] = 99.0
    asyncio.run(mlx_engine._make_room_for(PHI))
    assert GEMMA not in mlx_engine._resident, "LRU victim should have been evicted"
    assert ARCTIC in mlx_engine._embed_resident, "recently-used model should survive"


def test_an_unknown_model_never_triggers_eviction():
    """Evicting on a guess is worse than not evicting: the old estimator reported 0.00 GB for
    arctic, which the fit check read as 'fits'. Unknown means do nothing."""
    mlx_engine._resident[GEMMA] = ("m", "t")
    asyncio.run(mlx_engine._make_room_for("mlx-community/not-on-this-disk"))
    assert GEMMA in mlx_engine._resident


def test_the_incoming_model_is_never_its_own_victim():
    mlx_engine._resident[PHI] = ("m", "t")
    mlx_engine._last_used[PHI] = 1.0
    asyncio.run(mlx_engine._make_room_for(PHI))
    assert PHI in mlx_engine._resident


def test_a_busy_model_survives_the_budget_sweep():
    """The 3.1 safety property must hold under budget pressure too — a live generation
    outranks reclaiming memory."""
    mlx_engine._resident[GEMMA] = ("m", "t")
    mlx_engine._embed_resident[ARCTIC] = ("m", "t")
    mlx_engine._last_used[GEMMA] = 1.0

    async def scenario():
        lock = mlx_engine._model_locks.setdefault(GEMMA, asyncio.Lock())
        await lock.acquire()
        try:
            await mlx_engine._make_room_for(PHI)
        finally:
            lock.release()

    asyncio.run(scenario())
    assert GEMMA in mlx_engine._resident, "a busy model must not be evicted, even under pressure"
