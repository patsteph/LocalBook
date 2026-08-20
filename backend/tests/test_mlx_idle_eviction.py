"""Idle eviction — the piece that actually moves the end-of-session memory.

Stage 3.10. Measured (2026-08-19): with unload (3.1) and load-time budgeting (3.2) both shipped,
an evaluation run STILL ended holding 7.636 GB — exactly gemma + phi + arctic. My prediction that
those two would fix it was wrong, and the instrumentation showed why:

  · `free_for_pipeline` is called by scan/multimodal/audio/video/chat — the evaluator never calls it
  · the budget only acts when something is LOADING, and a session ends after work, not before it

So nothing ever released a cold model. Warming kept hot models resident and there was no opposite
force. This is that force.

The guards matter more than the sweep: evicting at the wrong moment costs a ~20 s gemma reload,
which is worse than holding the memory.
"""
import asyncio
import time

import pytest

from config import settings
from services import model_warmup as mw
from services.mlx_engine import mlx_engine

FAKE = "fake/idle-model"


@pytest.fixture
def resident_fake(monkeypatch):
    """A fake resident entry + MLX engines on, without loading a real model."""
    keep_model = getattr(settings, "embedding_model", "")
    settings.embedding_model = FAKE
    mlx_engine._embed_resident[FAKE] = ("m", "t")
    # A sweep must never fire while the user is active — pin that off for the test.
    monkeypatch.setattr("services.presence.system_busy", lambda *a, **k: False)
    yield
    mlx_engine._embed_resident.pop(FAKE, None)
    mlx_engine._model_locks.pop(FAKE, None)
    settings.embedding_model = keep_model


def test_a_recently_used_model_is_kept(resident_fake):
    """Warming and evicting read the SAME stamps, so they cannot disagree about what is hot."""
    mw._last_embedding_use = time.time()
    asyncio.run(mw._evict_idle_mlx())
    assert FAKE in mlx_engine._embed_resident


def test_an_idle_model_is_evicted(resident_fake):
    mw._last_embedding_use = time.time() - (mw.MODEL_IDLE_TIMEOUT + 60)
    asyncio.run(mw._evict_idle_mlx())
    assert FAKE not in mlx_engine._embed_resident


def test_nothing_happens_when_the_user_is_active(resident_fake, monkeypatch):
    """THE most important guard. A reload costs ~20 s for gemma; paying that because a sweep
    fired mid-session is worse than holding the memory."""
    monkeypatch.setattr("services.presence.system_busy", lambda *a, **k: True)
    mw._last_embedding_use = time.time() - (mw.MODEL_IDLE_TIMEOUT + 60)
    asyncio.run(mw._evict_idle_mlx())
    assert FAKE in mlx_engine._embed_resident, "must not evict while the system is busy"


def test_a_busy_model_survives_even_when_idle(resident_fake):
    """The 3.1 lock guard still applies — a generation in flight outranks the sweep."""
    mw._last_embedding_use = time.time() - (mw.MODEL_IDLE_TIMEOUT + 60)

    async def scenario():
        lock = mlx_engine._model_locks.setdefault(FAKE, asyncio.Lock())
        await lock.acquire()
        try:
            await mw._evict_idle_mlx()
        finally:
            lock.release()

    asyncio.run(scenario())
    assert FAKE in mlx_engine._embed_resident


def test_a_resident_no_role_points_at_is_dead_weight_and_goes(resident_fake, monkeypatch):
    """Replaces the old "no role is on MLX" early-out, which the engine collapse removed.

    A resident model that no configured role names — the previous embedder after a Locker
    swap, for instance — has no usage stamp, so it reads as infinitely idle and is evicted.
    That is the intended outcome, not an accident of `stamps.get(m) or 0`: holding weights
    nothing can route to is the exact waste this sweep exists to end."""
    monkeypatch.setattr(settings, "embedding_model", "some/other-model", raising=False)
    asyncio.run(mw._evict_idle_mlx())
    assert FAKE not in mlx_engine._embed_resident


def test_never_raises_with_nothing_loaded(monkeypatch):
    monkeypatch.setattr("services.presence.system_busy", lambda *a, **k: False)
    asyncio.run(mw._evict_idle_mlx())        # must not throw
