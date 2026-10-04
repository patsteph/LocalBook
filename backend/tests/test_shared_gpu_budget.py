"""LB-1: the shared-GPU budget.

The case this exists for: a 48 GB MBP that also runs a ~26 GB agent brain. If
LocalBook sizes itself against the whole working set, both processes believe
they own the same memory and the machine swaps — which on Apple Silicon means
wired memory blocking Jetsam, so exhaustion panics the driver rather than
killing a process.

Per-machine, never synced (LB-12h): the mini needs 0, the MBP needs ~26. A
synced value would be wrong on at least one machine by construction.
"""

import pytest

from services import model_sizing


@pytest.fixture
def ws(monkeypatch):
    """Pin the working set so the maths is checkable, not machine-dependent."""
    def _set(value: float):
        model_sizing._CACHE["working_set"] = value
        return value
    yield _set
    model_sizing._CACHE.pop("working_set", None)


@pytest.fixture
def reserve(monkeypatch):
    def _set(value: float):
        from config import settings
        monkeypatch.setattr(settings, "external_reserve_gb", value)
        return value
    return _set


# ── the reserve ─────────────────────────────────────────────────────────────


def test_the_default_is_zero(reserve):
    """"LocalBook has the machine to itself" is the honest default, and exactly
    what was assumed before this existed."""
    from config import settings

    assert float(getattr(settings, "external_reserve_gb", 0.0)) >= 0.0


def test_the_reserve_comes_out_of_the_budget(ws, reserve):
    ws(36.0)
    reserve(0.0)
    without = model_sizing.budget_gb()

    reserve(26.0)
    with_reserve = model_sizing.budget_gb()

    assert without - with_reserve == pytest.approx(26.0, abs=0.01)


def test_a_negative_reserve_is_treated_as_zero(ws, reserve):
    ws(36.0)
    reserve(-5.0)
    assert model_sizing.external_reserve_gb() == 0.0


def test_the_reserve_is_read_per_call_not_captured(ws, reserve):
    """Changing it in Settings has to take effect without a restart."""
    ws(36.0)
    reserve(0.0)
    assert model_sizing.budget_gb() == pytest.approx(33.5, abs=0.01)
    reserve(10.0)
    assert model_sizing.budget_gb() == pytest.approx(23.5, abs=0.01)


# ── the floor, which is the part that was dangerous ─────────────────────────


def test_the_fifty_percent_floor_no_longer_overrides_a_real_reserve(ws, reserve):
    """THE bug this formula fixes.

    The floor used to be `max(ws - RESIDENT, ws * 0.5)`, which stopped small
    machines being budgeted absurdly low. With a 26 GB reserve on a 36 GiB
    working set, only ~7.5 is genuinely free while `ws * 0.5` is 18 — and the
    old `max()` would have handed back 18 and overcommitted the machine into
    swap. An explicit reserve must always win over an optimistic floor.
    """
    ws(36.0)
    reserve(26.0)

    budget = model_sizing.budget_gb()

    assert budget == pytest.approx(36.0 - model_sizing.RESIDENT_RESERVE_GB - 26.0, abs=0.01)
    assert budget < 36.0 * 0.5, "the optimistic floor overrode the reserve"


def test_the_budget_never_goes_negative(ws, reserve):
    """A reserve larger than the machine is a user error, not a crash."""
    ws(12.0)
    reserve(100.0)
    assert model_sizing.budget_gb() == 0.0


def test_no_working_set_still_returns_zero_rather_than_guessing(ws, reserve):
    ws(0.0)
    reserve(0.0)
    assert model_sizing.budget_gb() == 0.0


def test_an_explicit_fraction_still_honours_the_reserve(ws, reserve):
    """A proportional budget is still a budget on THIS Mac: memory set aside for another
    app is not ours to hand out (LB-1 open item, closed 2026-10-03 — it bypassed it)."""
    ws(36.0)
    reserve(0.0)
    assert model_sizing.budget_gb(fraction=0.5) == pytest.approx(18.0, abs=0.01)
    reserve(10.0)
    assert model_sizing.budget_gb(fraction=0.5) == pytest.approx(8.0, abs=0.01)
    reserve(26.0)
    assert model_sizing.budget_gb(fraction=0.5) == 0.0


# ── what MLX is actually told ───────────────────────────────────────────────


def test_the_mlx_memory_limit_subtracts_the_reserve(ws, reserve, monkeypatch):
    """Without this the cap describes a machine LocalBook does not have to
    itself, and both processes size against the same memory."""
    import mlx.core as mx

    ws(36.0)
    reserve(26.0)
    seen = {}
    monkeypatch.setattr(mx, "set_memory_limit",
                        lambda n: seen.__setitem__("mem", n / 1024 ** 3))
    monkeypatch.setattr(mx, "set_cache_limit",
                        lambda n: seen.__setitem__("cache", n / 1024 ** 3))

    from services.mlx_engine import mlx_engine

    monkeypatch.setattr(mlx_engine, "_mem_limit_set", False)
    mlx_engine._ensure_memory_limit()

    assert seen["mem"] == pytest.approx(36.0 * 0.90 - 26.0, abs=0.01)


def test_the_mlx_limit_never_drops_below_one_gb(ws, reserve, monkeypatch):
    """A reserve that swallows the machine must not hand MLX a zero or negative
    limit — that fails at load time with something unreadable."""
    import mlx.core as mx

    ws(12.0)
    reserve(50.0)
    seen = {}
    monkeypatch.setattr(mx, "set_memory_limit",
                        lambda n: seen.__setitem__("mem", n / 1024 ** 3))
    monkeypatch.setattr(mx, "set_cache_limit", lambda n: None)

    from services.mlx_engine import mlx_engine

    monkeypatch.setattr(mlx_engine, "_mem_limit_set", False)
    mlx_engine._ensure_memory_limit()

    assert seen["mem"] >= 1.0


def test_the_buffer_cache_is_bounded(ws, reserve, monkeypatch):
    """Unbounded, MLX holds every buffer it has ever allocated — which looks
    exactly like LocalBook hoarding memory the moment something else wants
    some."""
    import mlx.core as mx

    ws(36.0)
    reserve(0.0)
    seen = {}
    monkeypatch.setattr(mx, "set_memory_limit", lambda n: None)
    monkeypatch.setattr(mx, "set_cache_limit",
                        lambda n: seen.__setitem__("cache", n / 1024 ** 3))

    from config import settings
    from services.mlx_engine import mlx_engine

    monkeypatch.setattr(mlx_engine, "_mem_limit_set", False)
    mlx_engine._ensure_memory_limit()

    assert seen["cache"] == pytest.approx(settings.mlx_cache_limit_gb, abs=0.01)


def test_an_explicit_env_override_still_wins(ws, reserve, monkeypatch):
    """LOCALBOOK_MLX_MEMORY_LIMIT_GB is the escape hatch and must stay one."""
    import os

    import mlx.core as mx

    ws(36.0)
    reserve(26.0)
    monkeypatch.setitem(os.environ, "LOCALBOOK_MLX_MEMORY_LIMIT_GB", "8")
    seen = {}
    monkeypatch.setattr(mx, "set_memory_limit",
                        lambda n: seen.__setitem__("mem", n / 1024 ** 3))
    monkeypatch.setattr(mx, "set_cache_limit", lambda n: None)

    from services.mlx_engine import mlx_engine

    monkeypatch.setattr(mlx_engine, "_mem_limit_set", False)
    mlx_engine._ensure_memory_limit()

    assert seen["mem"] == pytest.approx(8.0, abs=0.01)


# ── /health reports it ──────────────────────────────────────────────────────


def test_health_reports_the_three_numbers(ws, reserve):
    """One number is alarming without the others: "budget 6.7 GB" on a 48 GB Mac
    reads as a bug until you can see that 26 was deliberately given away.

    The handler is called directly — constructing a TestClient over the app runs
    its whole lifespan against the production data dir.
    """
    import asyncio

    from main import health

    ws(36.0)
    reserve(26.0)
    out = asyncio.run(health())

    assert out["status"] == "healthy"
    mem = out["memory"]
    assert mem["working_set_gb"] == pytest.approx(36.0, abs=0.01)
    assert mem["external_reserve_gb"] == pytest.approx(26.0, abs=0.01)
    assert mem["resident_reserve_gb"] == model_sizing.RESIDENT_RESERVE_GB
    assert mem["budget_gb"] == pytest.approx(7.5, abs=0.01)


def test_health_stays_healthy_even_if_sizing_fails(monkeypatch):
    """It is exempt from the app token and the Tauri shell polls it for
    readiness, so anything raising here reads as "LocalBook won't start"."""
    import asyncio

    monkeypatch.setattr(
        model_sizing, "working_set_gb",
        lambda: (_ for _ in ()).throw(RuntimeError("mlx unavailable")),
    )
    from main import health

    out = asyncio.run(health())
    assert out["status"] == "healthy"
    assert "error" in out["memory"]
