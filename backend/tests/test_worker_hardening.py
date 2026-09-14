"""Enrichment worker hardening — memory pressure, and the freeze watchdog.

MIGRATED 2026-08-20 from `_phase5_test.py`, which was **orphaned**: on disk, referenced by no
runner, so its 17 assertions had guarded nothing for months.

Why this matters more under MLX than it did under Ollama: Ollama held models in its own
process and expired them on a TTL, so memory pressure was partly someone else's problem. MLX
holds weights in OUR process until something evicts them, and a background job that fires
while gemma + Klein are resident is what pushes a 16 GB Mac into swap. The parking gate below
is the thing that stops that.

`monkeypatch` throughout — patching `psutil` by hand (as the original did) leaks into every
later test in the session if an assertion raises before the restore line.
"""
import asyncio

import pytest


# ── memory_pressure ─────────────────────────────────────────────────────────────

class _VM:
    def __init__(self, pct): self.percent = pct


class _SW:
    def __init__(self, used): self.used = used


@pytest.fixture
def presence_mod(monkeypatch):
    from services import presence
    yield presence


def test_high_ram_is_pressure(presence_mod, monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "virtual_memory", lambda: _VM(95.0))
    monkeypatch.setattr(psutil, "swap_memory", lambda: _SW(0))
    presence_mod._last_swap_used.update(bytes=0, ts=0.0)
    assert presence_mod.memory_pressure() is True


def test_steady_swap_is_not_pressure(presence_mod, monkeypatch):
    """Resident swap is normal on macOS. Only GROWTH means the machine is struggling —
    treating a stable 1 GB as pressure would park background work permanently."""
    import psutil
    monkeypatch.setattr(psutil, "virtual_memory", lambda: _VM(40.0))
    monkeypatch.setattr(psutil, "swap_memory", lambda: _SW(1_000_000_000))
    presence_mod._last_swap_used.update(bytes=0, ts=0.0)
    presence_mod.memory_pressure()          # prime the baseline
    assert presence_mod.memory_pressure() is False


def test_growing_swap_is_pressure(presence_mod, monkeypatch):
    import psutil
    base = 1_000_000_000
    monkeypatch.setattr(psutil, "virtual_memory", lambda: _VM(40.0))
    monkeypatch.setattr(psutil, "swap_memory", lambda: _SW(base))
    presence_mod._last_swap_used.update(bytes=0, ts=0.0)
    presence_mod.memory_pressure()          # baseline
    monkeypatch.setattr(psutil, "swap_memory", lambda: _SW(base + 200 * 1024 * 1024))
    assert presence_mod.memory_pressure() is True


def test_pressure_fails_open(presence_mod, monkeypatch):
    """A broken psutil must not park all background work forever. Fail OPEN — report no
    pressure — because the cost of a wrong 'no' is a slow moment, and the cost of a wrong
    'yes' is enrichment that never runs again."""
    import psutil

    def boom():
        raise RuntimeError("psutil exploded")

    monkeypatch.setattr(psutil, "virtual_memory", boom)
    assert presence_mod.memory_pressure() is False


# ── the worker parks, then resumes ──────────────────────────────────────────────

def test_the_worker_parks_under_pressure_and_runs_when_it_clears(monkeypatch):
    """THE behaviour this file exists for. A NIGHT job must wait while the machine is under
    memory pressure and run once it lifts — not be dropped, and not run anyway."""
    from services import enrichment_worker as ew_mod
    from services import presence
    from services.enrichment_jobs import EnrichmentJob, JobTier
    from services.enrichment_worker import EnrichmentWorker
    from services.presence import Tier

    monkeypatch.setattr(ew_mod, "_IDLE_RECHECK", 0.05, raising=False)
    monkeypatch.setattr(ew_mod, "_POLL", 0.02, raising=False)
    monkeypatch.setattr(ew_mod, "_OLLAMA_QUIET", 0.0, raising=False)

    state = {"pressure": True}
    monkeypatch.setattr(presence, "current_tier", lambda: Tier.AWAY)
    monkeypatch.setattr(presence, "is_active", lambda: False)
    monkeypatch.setattr(presence, "system_busy", lambda *a, **k: False)
    monkeypatch.setattr(presence, "memory_pressure", lambda: state["pressure"])

    async def scenario():
        ran = asyncio.Event()
        w = EnrichmentWorker()

        async def job_body():
            ran.set()

        w.enqueue(EnrichmentJob(key="t", tier=JobTier.NIGHT,
                                factory=lambda: job_body(), label="t"))
        await w.start()
        try:
            await asyncio.sleep(0.3)
            assert not ran.is_set(), "job ran despite memory pressure"
            assert w.queue_depth() == 1, "a parked job must stay queued, not be dropped"

            state["pressure"] = False
            await asyncio.wait_for(ran.wait(), timeout=2.0)
        finally:
            await w.stop()

    asyncio.run(scenario())


# ── the freeze watchdog ─────────────────────────────────────────────────────────

def test_the_watchdog_dumps_a_traceback_on_a_frozen_loop(tmp_path):
    """A frozen event loop cannot report its own freeze — that is the whole difficulty. The
    watchdog arms a C-level faulthandler timer, so the dump happens even though no Python
    callback can run. This test genuinely blocks the loop to prove it.
    """
    import os
    import time

    wd_mod = pytest.importorskip("services.loop_watchdog")
    from services.loop_watchdog import LoopWatchdog

    path = tmp_path / "freeze.trace"
    fh = open(path, "w")

    async def scenario():
        wd_mod._WATCHDOG_S = 1.0
        w = LoopWatchdog()
        w._dump_target = lambda: fh
        await w.start()
        assert w._running is True
        # Let the watchdog tick once so it ARMS the timer, then freeze the loop for real.
        await asyncio.sleep(0.05)
        time.sleep(1.6)
        fh.flush()
        os.fsync(fh.fileno())
        await w.stop()
        assert w._running is False

    asyncio.run(scenario())
    fh.close()
    dump = path.read_text()
    assert "Traceback" in dump or 'File "' in dump, f"no stack captured ({len(dump)}b)"
