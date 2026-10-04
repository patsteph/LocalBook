"""The shared GPU budget covers the image model too (LB-1 done-when, 2026-10-03).

The image model loaded outside the budget — never counted, never evicted, unload_after
ignored — and a model that didn't fit was loaded anyway. An image requested during a
chat answer could push the Mac into swap. Model-agnostic: ids here are placeholders.
"""
import asyncio
import time

import pytest

from services import mlx_engine as me

SIZES = {"chat": 5.0, "fast": 2.0, "img": 4.0}


@pytest.fixture
def engine(monkeypatch):
    e = me.MLXEngine()
    monkeypatch.setattr(e, "_budget_gb", lambda: 10.0)
    monkeypatch.setattr("services.model_sizing.exact_weight_gb", lambda m: SIZES.get(m))
    monkeypatch.setattr("services.model_sizing.load_config", lambda m: None)

    async def fake_unload(model_id, wait=2.0):
        e._resident.pop(model_id, None)
        return True
    monkeypatch.setattr(e, "unload", fake_unload)
    return e


def _external(e, mid, busy=lambda: False):
    released = []

    async def release():
        released.append(mid)
    e.register_external(mid, SIZES[mid], release, busy)
    return released


def test_the_image_model_counts_against_the_budget(engine):
    _external(engine, "img")
    assert engine._resident_cost_gb() == pytest.approx(4.0)


def test_an_idle_image_model_is_evicted_to_make_room(engine):
    released = _external(engine, "img")
    engine._resident["fast"] = object(); engine._last_used["fast"] = time.monotonic()
    ok = asyncio.run(engine._make_room_for("chat"))            # 4 + 2 + 5×1.2 > 10
    assert ok and released == ["img"] and "img" not in engine._external


def test_a_busy_image_model_is_never_freed_and_the_load_is_refused(engine):
    released = _external(engine, "img", busy=lambda: True)
    engine._resident["fast"] = object()
    engine._resident["fast2"] = object()
    SIZES["fast2"] = 2.0
    ok = asyncio.run(engine._make_room_for("chat", need_gb=7.0, wait_s=0))
    assert ok is False and released == []                     # refused, not swapped


def test_room_is_waited_for_while_a_busy_model_finishes(engine):
    state = {"busy": True}
    released = _external(engine, "img", busy=lambda: state["busy"])

    async def go():
        async def finish():
            await asyncio.sleep(1.2)
            state["busy"] = False
        asyncio.get_running_loop().create_task(finish())
        return await engine._make_room_for("chat", need_gb=7.0, wait_s=5)
    assert asyncio.run(go()) is True and released == ["img"]


def test_a_model_that_would_be_alone_still_loads(engine):
    assert asyncio.run(engine._make_room_for("chat", need_gb=50.0)) is True


def test_unload_after_releases_the_image_model(monkeypatch):
    from services import visual_diffusion as vd
    svc = vd.KleinDiffusionService() if hasattr(vd, "KleinDiffusionService") else vd.klein_diffusion.__class__()
    released = []

    async def fake_gen(prompt, **kw):
        return vd.DiffusionResult(success=True)

    async def fake_release():
        released.append(1)
    monkeypatch.setattr(svc, "_generate_mflux", fake_gen)
    monkeypatch.setattr(svc, "release", fake_release)
    cap = type("C", (), {"klein_model": "img"})()
    asyncio.run(svc.generate("x", capability=cap, unload_after=True))
    asyncio.run(svc.generate("x", capability=cap, unload_after=False))
    assert released == [1]


def test_a_reserve_makes_a_big_mac_swap_before_an_image(monkeypatch):
    from services import visual_capability as vc
    monkeypatch.setattr("services.model_sizing.working_set_gb", lambda: 35.5)    # a 48 GB Mac
    monkeypatch.setattr("services.model_sizing.external_reserve_gb", lambda: 0.0)
    from services.model_sizing import budget_gb
    assert budget_gb() >= vc.BUDGET_CONCURRENT_GB
    monkeypatch.setattr("services.model_sizing.external_reserve_gb", lambda: 26.0)
    assert budget_gb() < vc.BUDGET_SWAP_GB                      # reserve 26 → swap-strict
