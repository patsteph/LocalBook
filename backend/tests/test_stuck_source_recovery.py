"""The net under an interrupted capture has to actually catch.

2026-09-25: `/browser/capture` now answers before the RAG ingest runs, so the user is told
"captured" while the work is still in flight in the backend. Browsing away cannot touch
that — it runs in the backend process, not the browser — but a backend restart, a
watchdog kill or an app quit mid-enrichment CAN, and then the source sits in
`processing` with the user believing it landed. `stuck_source_recovery` is the net for
exactly that, and it was dead in two independent ways:

1. `check_and_recover` read `source_store._load_data()` — the JSON path — while
   `settings.use_sqlite` defaults to True. On this machine `sources.json` was last
   written in January; every source created since lives in `localbook.db` and was
   invisible to the sweep.
2. Every status write called `source_store.update(source_id, patch)` — two positional
   args, unawaited — against `async def update(self, notebook_id, source_id, updates)`.
   That raises TypeError at call time, which `_recover_source` caught and reported as a
   generic failure.

Fault 1 was masking fault 2, and the masking mattered: with only fault 2, a stuck source
WOULD be re-ingested by `_ingest_content` and then fail to flip status — so the next
sweep five minutes later would re-ingest it again, duplicating its chunks every time.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def stuck(monkeypatch):
    """A source stuck in `processing` for an hour, with its text already stored.

    That text is the thing that makes recovery possible at all: /browser/capture writes
    `content` into the source record BEFORE it responds, precisely so an interrupted
    enrichment can be finished later rather than lost.
    """
    from storage.source_store import source_store
    from services import stuck_source_recovery as ssr

    ingested = []

    async def fake_ingest(notebook_id, source_id, content, title, source):
        ingested.append({"source_id": source_id, "chars": len(content)})
        return 12

    monkeypatch.setattr(ssr.stuck_source_recovery, "_ingest_content", fake_ingest)

    async def no_chunks(notebook_id, source_id):
        return 0

    monkeypatch.setattr(ssr.stuck_source_recovery, "_count_chunks_in_db", no_chunks)

    # Everything a capture writes is stamped utcnow(); with a 0-minute threshold a source
    # created a moment ago is eligible, which is what makes the UTC-vs-local fault visible
    # without having to age anything. `test_a_recent_capture_is_left_alone` keeps the real
    # threshold and covers the other direction.
    monkeypatch.setattr(ssr, "STUCK_THRESHOLD_MINUTES", 0)

    async def setup():
        from storage.notebook_store import notebook_store
        nb = await notebook_store.create(title="Stuck NB")
        src = await source_store.create(
            notebook_id=nb["id"],
            filename="An Interrupted Capture",
            metadata={
                "type": "web", "format": "web", "status": "processing",
                "url": "https://example.com/interrupted",
                "title": "An Interrupted Capture",
                "content": "the article text that was stored before the response " * 40,
                "chunks": 0,
            },
        )
        return src["id"]

    source_id = asyncio.run(setup())
    return {"source_id": source_id, "ingested": ingested, "store": source_store}


def test_the_sweep_sees_sources_in_the_real_store(stuck):
    """Fault 1: it read the JSON file while production writes SQLite."""
    from services.stuck_source_recovery import stuck_source_recovery

    result = asyncio.run(stuck_source_recovery.check_and_recover())

    assert result["stuck_found"] >= 1, (
        "the sweep found nothing — it is reading a store the app does not write to, so "
        "an interrupted capture would sit in `processing` forever")


def test_an_interrupted_capture_gets_finished(stuck):
    """Fault 2: the status write had the wrong arity and was never awaited, so a
    re-ingested source stayed `processing` — and got re-ingested on every later sweep."""
    from services.stuck_source_recovery import stuck_source_recovery

    result = asyncio.run(stuck_source_recovery.check_and_recover())
    assert result["recovered"] >= 1, f"recovery reported: {result['details']}"

    src = asyncio.run(stuck["store"].get(stuck["source_id"]))
    assert src["status"] == "completed", (
        "the source is still `processing` after a successful re-ingest — the next sweep "
        "will ingest it AGAIN, duplicating its chunks every five minutes")
    assert src["chunks"] == 12
    mine = [i for i in stuck["ingested"] if i["source_id"] == stuck["source_id"]]
    assert len(mine) == 1


def test_a_second_sweep_does_not_ingest_again(stuck):
    """The duplication hazard, asserted directly."""
    from services.stuck_source_recovery import stuck_source_recovery

    asyncio.run(stuck_source_recovery.check_and_recover())
    asyncio.run(stuck_source_recovery.check_and_recover())

    mine = [i for i in stuck["ingested"] if i["source_id"] == stuck["source_id"]]
    assert len(mine) == 1, (
        f"ingested {len(stuck['ingested'])} times — each sweep re-chunks the same source "
        f"into LanceDB")


def test_a_source_with_no_content_is_marked_failed_not_left_hanging(monkeypatch):
    from storage.source_store import source_store
    from services import stuck_source_recovery as ssr
    from services.stuck_source_recovery import stuck_source_recovery

    monkeypatch.setattr(ssr, "STUCK_THRESHOLD_MINUTES", 0)

    async def setup():
        from storage.notebook_store import notebook_store
        nb = await notebook_store.create(title="Empty NB")
        src = await source_store.create(
            notebook_id=nb["id"],
            filename="Nothing To Ingest",
            metadata={
                "type": "web", "format": "web", "status": "processing",
                "title": "Nothing To Ingest", "content": "", "chunks": 0,
            },
        )
        return src["id"]

    source_id = asyncio.run(setup())
    asyncio.run(stuck_source_recovery.check_and_recover())

    src = asyncio.run(source_store.get(source_id))
    assert src["status"] == "failed"
    assert src.get("error")


def test_a_recent_capture_is_left_alone(monkeypatch):
    """A capture whose enrichment is still legitimately running must not be touched."""
    from storage.source_store import source_store
    from services.stuck_source_recovery import stuck_source_recovery

    async def setup():
        from storage.notebook_store import notebook_store
        nb = await notebook_store.create(title="Fresh NB")
        src = await source_store.create(
            notebook_id=nb["id"],
            filename="Still Working",
            metadata={
                "type": "web", "format": "web", "status": "processing",
                "title": "Still Working", "content": "text " * 100, "chunks": 0,
            },
        )
        return src["id"]

    source_id = asyncio.run(setup())
    asyncio.run(stuck_source_recovery.check_and_recover())

    src = asyncio.run(source_store.get(source_id))
    assert src["status"] == "processing", "the sweep grabbed a capture that was still running"


def test_the_startup_pass_does_not_wait_for_deep_idle(monkeypatch):
    """A source still `processing` at startup cannot have a task running for it — whatever
    owned it died with the previous process. That is the watchdog-restart-mid-capture case,
    and since the capture already told the user "captured", it should not wait on ~120s of
    continuous idle (DEEP) from someone who is actively browsing."""
    import asyncio as aio
    from services import stuck_source_recovery as ssr
    from services.enrichment_jobs import JobTier

    enqueued = []
    monkeypatch.setattr("services.enrichment_worker.enrichment_worker.enqueue",
                        lambda job: enqueued.append(job))
    # Collapse the startup delay and stop after the first cadence tick.
    sleeps = []

    async def fake_sleep(secs):
        sleeps.append(secs)
        if len(sleeps) >= 2:            # startup sleep, then the cadence wait
            ssr.stuck_source_recovery._running = False

    monkeypatch.setattr(aio, "sleep", fake_sleep)

    ssr.stuck_source_recovery._running = True
    aio.run(ssr.stuck_source_recovery._background_loop())

    assert enqueued, "the loop enqueued nothing"
    assert enqueued[0].tier == JobTier.DAYDREAM, (
        f"the startup pass runs at {enqueued[0].tier!r} — it needs the tier that a brief "
        f"pause satisfies, or an interrupted capture waits on a long idle")
    assert enqueued[0].key == "stuck-source-recovery"
    # The periodic sweep stays DEEP — it is speculative, unlike the startup one.
    assert any(j.tier == JobTier.DEEP for j in enqueued[1:]), \
        f"tiers enqueued: {[j.tier for j in enqueued]}"


def test_the_sweep_never_runs_inline(monkeypatch):
    """One traffic cop: recovery goes through the presence-gated worker so it can never
    collide with a foreground capture (S1/C7). Asserted by watching the sweep NOT be
    called while the loop runs — a grep of the source would prove nothing about that."""
    import asyncio as aio
    from services import stuck_source_recovery as ssr

    enqueued = []
    monkeypatch.setattr("services.enrichment_worker.enrichment_worker.enqueue",
                        lambda job: enqueued.append(job))

    ran = []

    async def tripwire():
        ran.append(True)

    monkeypatch.setattr(ssr.stuck_source_recovery, "check_and_recover", tripwire)

    sleeps = []

    async def fake_sleep(secs):
        sleeps.append(secs)
        if len(sleeps) >= 2:
            ssr.stuck_source_recovery._running = False

    monkeypatch.setattr(aio, "sleep", fake_sleep)

    ssr.stuck_source_recovery._running = True
    aio.run(ssr.stuck_source_recovery._background_loop())

    assert enqueued, "nothing was enqueued"
    assert ran == [], "the loop ran the sweep itself instead of handing it to the worker"

    # ...and the job it handed over really is the sweep: run one factory and see it fire.
    aio.run(enqueued[0].factory())
    assert ran == [True]


def test_a_recovery_tells_the_ui(stuck, monkeypatch):
    """A recovery no surface hears about is half a recovery: the source becomes searchable
    while the notebook's source-count badge and its source list keep showing the
    pre-recovery state until the window is reloaded."""
    import asyncio as aio
    from services.stuck_source_recovery import stuck_source_recovery

    pushed = []

    async def fake_notify(payload):
        pushed.append(payload)

    monkeypatch.setattr("api.constellation_ws.notify_source_updated", fake_notify)

    aio.run(stuck_source_recovery.check_and_recover())

    mine = [p for p in pushed if p["source_id"] == stuck["source_id"]]
    assert mine, f"recovery completed silently — the UI cannot know (pushed: {pushed})"
    frame = mine[0]
    assert frame["status"] == "completed"
    assert frame["source_id"] == stuck["source_id"]
    assert frame["chunks"] == 12
    assert frame["notebook_id"]


def test_a_broken_socket_does_not_fail_the_recovery(stuck, monkeypatch):
    """The push is a courtesy; the re-ingest is the product."""
    import asyncio as aio
    from services.stuck_source_recovery import stuck_source_recovery

    async def explode(payload):
        raise RuntimeError("no clients, socket closed")

    monkeypatch.setattr("api.constellation_ws.notify_source_updated", explode)

    result = aio.run(stuck_source_recovery.check_and_recover())
    assert result["recovered"] >= 1, f"a socket error failed the recovery: {result['details']}"
    src = aio.run(stuck["store"].get(stuck["source_id"]))
    assert src["status"] == "completed"
