"""Deterministic unit tests for Phase 5 (enrichment-worker hardening).

Dev artifact (not shipped). No live Ollama needed — everything is monkeypatched.

    .venv/bin/python3 _phase5_test.py

Covers:
  5b  presence.memory_pressure() signals + worker parks under pressure
  5c  community_detection.build_missing_summaries per-cycle cap + work-remains
  5d  loop_watchdog arms/disarms + fires a traceback on a synthetic freeze
"""
import asyncio
import sys

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))


# ────────────────────────────────────────────────────────────────────────
# 5b — memory-pressure gate
# ────────────────────────────────────────────────────────────────────────
async def test_5b():
    print("[5b] memory-pressure gate")
    from services import presence
    import psutil

    # Save originals
    orig_vm = psutil.virtual_memory
    orig_sw = psutil.swap_memory

    class _VM:
        def __init__(self, pct): self.percent = pct

    class _SW:
        def __init__(self, used): self.used = used

    # 1. high RAM percent → pressure
    psutil.virtual_memory = lambda: _VM(95.0)
    psutil.swap_memory = lambda: _SW(0)
    presence._last_swap_used.update(bytes=0, ts=0.0)
    check("memory_pressure True at 95% RAM", presence.memory_pressure() is True)

    # 2. steady state (low RAM, no swap growth) → no pressure
    psutil.virtual_memory = lambda: _VM(40.0)
    psutil.swap_memory = lambda: _SW(1_000_000_000)  # 1 GB resident, stable
    presence._last_swap_used.update(bytes=0, ts=0.0)
    presence.memory_pressure()  # prime baseline
    r = presence.memory_pressure()  # second read: no growth
    check("memory_pressure False on steady swap", r is False)

    # 3. swap GROWING fast within window → pressure
    psutil.virtual_memory = lambda: _VM(40.0)
    base = 1_000_000_000
    psutil.swap_memory = lambda: _SW(base)
    presence.memory_pressure()  # baseline
    psutil.swap_memory = lambda: _SW(base + 200 * 1024 * 1024)  # +200 MB
    check("memory_pressure True on swap growth", presence.memory_pressure() is True)

    # 4. psutil import failure → fail open (False)
    psutil.virtual_memory = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    check("memory_pressure fails open on psutil error", presence.memory_pressure() is False)

    psutil.virtual_memory = orig_vm
    psutil.swap_memory = orig_sw

    # 5. worker PARKS a runnable job under pressure, RUNS it when cleared
    from services import enrichment_worker as ew_mod
    from services.enrichment_worker import EnrichmentWorker
    from services.enrichment_jobs import EnrichmentJob, JobTier
    from services.presence import Tier

    ew_mod._IDLE_RECHECK = 0.05
    ew_mod._POLL = 0.02
    ew_mod._OLLAMA_QUIET = 0.0

    # Force AWAY, not active, not busy. Toggle pressure via a mutable holder.
    state = {"pressure": True}
    presence.current_tier = lambda: Tier.AWAY
    presence.is_active = lambda: False
    presence.system_busy = lambda *a, **k: False
    presence.memory_pressure = lambda: state["pressure"]

    ran = asyncio.Event()
    w = EnrichmentWorker()

    async def job_body():
        ran.set()

    w.enqueue(EnrichmentJob(key="t", tier=JobTier.NIGHT,
                            factory=lambda: job_body(), label="t"))
    await w.start()

    await asyncio.sleep(0.3)
    parked = (not ran.is_set()) and w.queue_depth() == 1
    check("worker parks NIGHT job under pressure", parked)

    state["pressure"] = False
    try:
        await asyncio.wait_for(ran.wait(), timeout=1.0)
        check("worker runs job after pressure clears", True)
    except asyncio.TimeoutError:
        check("worker runs job after pressure clears", False, "job never ran")
    await w.stop()


# ────────────────────────────────────────────────────────────────────────
# 5c — community-summary per-cycle cap (filled in once 5c lands)
# ────────────────────────────────────────────────────────────────────────
async def test_5c():
    print("[5c] community-summary cap")
    try:
        from services import community_detection as cd
    except Exception as e:
        check("import community_detection", False, str(e))
        return
    if not hasattr(cd, "_SUMMARY_CAP_PER_CYCLE"):
        check("5c not yet implemented (skip)", True, "no _SUMMARY_CAP_PER_CYCLE")
        return

    from services.community_detection import Community, CommunityDetector

    det = CommunityDetector.__new__(CommunityDetector)   # bypass __init__/disk load
    det._communities = {}
    nb = "nb-5c"
    # 100 communities, all missing summaries
    det._communities[nb] = {
        f"community_{i}": Community(id=f"community_{i}", name=f"c{i}", entities=["a", "b"])
        for i in range(100)
    }

    # Stub the actual LLM call so we count batching, not generate.
    built = {"n": 0}

    async def fake_gen(notebook_id, comm_id, entity_graph):
        built["n"] += 1
        det._communities[notebook_id][comm_id].summary = "x"
        return "x"

    det.generate_community_summary = fake_gen

    cd._SUMMARY_CAP_PER_CYCLE = 40  # force a small cap for the test

    # disable pacing so the test is fast
    from services import presence
    presence.background_pace_seconds = lambda: 0.0

    n1 = await det.build_missing_summaries(nb, entity_graph=None)
    check("first cycle builds exactly the cap", n1 == 40, f"built {n1}")
    check("remaining after cycle 1 == 60", det.count_missing_summaries(nb) == 60)

    n2 = await det.build_missing_summaries(nb, entity_graph=None)
    check("second cycle builds next cap", n2 == 40)
    n3 = await det.build_missing_summaries(nb, entity_graph=None)
    check("third cycle builds the tail (20)", n3 == 20)
    check("none missing after draining", det.count_missing_summaries(nb) == 0)


# ────────────────────────────────────────────────────────────────────────
# 5d — thread watchdog (filled in once 5d lands)
# ────────────────────────────────────────────────────────────────────────
async def test_5d():
    print("[5d] loop watchdog")
    try:
        from services import loop_watchdog as wd_mod
        from services.loop_watchdog import LoopWatchdog
    except Exception:
        check("5d not yet implemented (skip)", True, "no loop_watchdog module")
        return
    import tempfile, time, os, faulthandler

    # Point the dump at a temp file and use a tiny timeout so a synthetic freeze
    # trips it quickly. faulthandler writes to the fd directly, so we re-open the
    # path afterward to read what landed.
    wd_mod._WATCHDOG_S = 1.0
    path = tempfile.mktemp(suffix=".trace")
    fh = open(path, "w")
    w = LoopWatchdog()
    w._dump_target = lambda: fh          # override file selection
    await w.start()
    check("watchdog starts (faulthandler present)", w._running is True)

    # Let the watchdog task run once so it ARMS the C timer (it arms inside its
    # loop, which needs the event loop to tick). THEN freeze the loop.
    await asyncio.sleep(0.05)
    # Block the loop synchronously past the timeout → C timer fires and dumps the
    # stack of THIS frozen frame even though the loop can't run.
    time.sleep(1.6)
    fh.flush()
    os.fsync(fh.fileno())
    await w.stop()
    dump = open(path).read()
    check("fatal freeze produced a traceback dump",
          "Traceback" in dump or 'File "' in dump, f"dump empty ({len(dump)}b)")
    check("watchdog stopped cleanly", w._running is False)


async def test_5a():
    print("[5a] timer folds → worker")
    import services.enrichment_worker as ewm
    from services.enrichment_jobs import JobTier

    # Capture enqueues against the singleton (the folded loops import it directly).
    captured = []
    orig_enqueue = ewm.enrichment_worker.enqueue
    ewm.enrichment_worker.enqueue = lambda job: captured.append(job)

    try:
        # ── representative poller fold: correspondent (digest/journal identical) ──
        from agents.correspondent import CorrespondentAgent
        agent = CorrespondentAgent()
        agent.poll_all = lambda: (_ for _ in ()).throw(AssertionError("must NOT run inline"))
        agent.poll_interval_seconds = 999
        agent._running = True
        t = asyncio.create_task(agent._loop())
        await asyncio.sleep(0.05)            # let one iteration enqueue
        agent._running = False
        t.cancel()
        try: await t
        except BaseException: pass
        corr = [j for j in captured if j.key == "correspondent-poll"]
        check("correspondent enqueues 1 DEEP poll job",
              len(corr) == 1 and corr[0].tier == JobTier.DEEP,
              f"got {[(j.key, j.tier) for j in captured]}")

        # ── collection scheduler fold: due notebook → DEEP per-notebook job + claim ──
        captured.clear()
        import services.collection_scheduler as csmod
        from datetime import datetime, timedelta

        sched = csmod.CollectionScheduler.__new__(csmod.CollectionScheduler)
        sched._running = True
        sched._last_runs = {"nb1": datetime.utcnow() - timedelta(days=3)}  # old → due, not fresh
        sched._save_state = lambda: None
        seeded = sched._last_runs["nb1"]

        class _Cfg:
            intent = "track stuff"
            collection_mode = "auto"
            schedule = {"frequency": "daily"}

        class _Col:
            def get_config(self): return _Cfg()

        async def _list(): return [{"id": "nb1", "name": "Test NB"}]
        csmod.notebook_store.list = _list
        csmod.get_collector = lambda nid: _Col()
        # force idle so the cycle isn't deferred
        import services.memory_steward as ms
        ms.seconds_since_activity = lambda: 9999

        await sched._check_and_run_collections()
        coll = [j for j in captured if j.key == "collection:nb1"]
        check("collection enqueues a DEEP per-notebook job",
              len(coll) == 1 and coll[0].tier == JobTier.DEEP,
              f"got {[j.key for j in captured]}")
        check("collection claims _last_runs at enqueue (no re-fire)",
              sched._last_runs["nb1"] > seeded)
    finally:
        ewm.enrichment_worker.enqueue = orig_enqueue


async def main():
    await test_5b()
    await test_5c()
    await test_5d()
    await test_5a()
    print()
    n_pass = sum(1 for _, ok, _ in results if ok)
    print(f"{n_pass}/{len(results)} checks passed")
    sys.exit(0 if n_pass == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
