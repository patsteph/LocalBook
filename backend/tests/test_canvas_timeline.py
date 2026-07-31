"""CI-pure tests for the Journey Canvas unified timeline read-layer (2.2.0 Walk).

Merges three capture stores (exploration_store / activity_ledger / curator_brain)
into one newest-first, de-duplicated feed. These tests monkeypatch the three read
seams with tiny fixtures — NO real store access, NO production data touched — and
lock the merge/order/de-dup/fail-open contract.
"""
import asyncio

from services import canvas_timeline as ct


# --- fixture helpers -------------------------------------------------------

def _patch(monkeypatch, *, exploration=None, ledger=None, brain=None):
    """Replace the three store-read seams with in-memory fixtures."""
    async def _fake_exploration(_nb, _limit):
        return exploration if exploration is not None else {}

    monkeypatch.setattr(ct, "_read_exploration_journey", _fake_exploration)
    monkeypatch.setattr(ct, "_read_ledger_events", lambda _nb, _limit: ledger or [])
    monkeypatch.setattr(ct, "_read_brain_events", lambda _nb, _limit: brain or [])


def _run(limit=200):
    return asyncio.run(ct.build_timeline("nb1", limit))


# --- empty -----------------------------------------------------------------

def test_all_empty_returns_empty(monkeypatch):
    _patch(monkeypatch)
    assert _run() == []


def test_blank_notebook_id_short_circuits(monkeypatch):
    _patch(monkeypatch, ledger=[{"kind": "source_added", "ts": "t", "payload": {"source_id": "s1"}}])
    assert asyncio.run(ct.build_timeline("", 200)) == []


# --- merge + shape ---------------------------------------------------------

def test_merges_all_three_stores_into_uniform_shape(monkeypatch):
    _patch(
        monkeypatch,
        exploration={"queries": [
            {"id": "q1", "query": "What is X?", "topics": ["alpha"],
             "sources_used": ["s1"], "confidence": 0.8, "timestamp": "2026-01-01T00:00:00"},
        ]},
        ledger=[{"kind": "query_ran", "ts": "2026-01-02T00:00:00", "actor": "user",
                 "payload": {"query": "later q"}}],
        brain=[{"action": "morning_brief", "ts": "2026-01-03T00:00:00", "actor": "@curator",
                "intent": "morning_brief", "outcome": "success", "payload": {}}],
    )
    tl = _run()
    assert len(tl) == 3
    # every entry has the uniform shape
    for e in tl:
        assert set(e.keys()) == {"ts", "kind", "source", "title", "ref_type", "ref_id", "meta"}
    by_src = {e["source"]: e for e in tl}
    assert by_src["exploration"]["ref_type"] == "exploration_query" and by_src["exploration"]["ref_id"] == "q1"
    assert by_src["exploration"]["kind"] == "chat_query"
    assert by_src["exploration"]["meta"]["topics"] == ["alpha"]
    assert by_src["ledger"]["kind"] == "query_ran" and by_src["ledger"]["title"] == "later q"
    assert by_src["brain"]["source"] == "brain" and by_src["brain"]["meta"]["actor"] == "@curator"


# --- ordering --------------------------------------------------------------

def test_merge_is_newest_first(monkeypatch):
    _patch(
        monkeypatch,
        exploration={"queries": [{"id": "q1", "query": "a", "timestamp": "2026-01-01T00:00:00"}]},
        ledger=[{"kind": "query_ran", "ts": "2026-06-01T00:00:00", "payload": {}}],
        brain=[{"action": "x", "ts": "2026-03-01T00:00:00", "payload": {}}],
    )
    ts = [e["ts"] for e in _run()]
    assert ts == sorted(ts, reverse=True)
    assert ts[0] == "2026-06-01T00:00:00"  # ledger newest
    assert ts[-1] == "2026-01-01T00:00:00"  # exploration oldest


def test_limit_truncates_after_sort(monkeypatch):
    _patch(monkeypatch, ledger=[
        {"kind": "query_ran", "ts": f"2026-01-{d:02d}T00:00:00", "payload": {}} for d in range(1, 11)
    ])
    tl = _run(limit=3)
    assert len(tl) == 3
    assert [e["ts"] for e in tl] == ["2026-01-10T00:00:00", "2026-01-09T00:00:00", "2026-01-08T00:00:00"]


# --- de-dup ----------------------------------------------------------------

def test_source_add_in_both_ledger_and_brain_dedups_to_one(monkeypatch):
    # Same source_id recorded in BOTH stores -> one entry, ledger wins (priority).
    _patch(
        monkeypatch,
        ledger=[{"kind": "source_added", "ts": "2026-01-05T00:00:00", "actor": "user",
                 "payload": {"source_id": "SRC-9", "filename": "Paper.pdf"}}],
        brain=[{"action": "source_ingested", "ts": "2026-01-05T00:00:01", "actor": "@collector",
                "payload": {"source_id": "SRC-9", "filename": "Paper.pdf"}}],
    )
    tl = _run()
    src_entries = [e for e in tl if e["ref_type"] == "source" and e["ref_id"] == "SRC-9"]
    assert len(src_entries) == 1
    assert src_entries[0]["source"] == "ledger"  # ledger preferred on collision


def test_distinct_sources_not_deduped(monkeypatch):
    _patch(
        monkeypatch,
        ledger=[{"kind": "source_added", "ts": "t1", "payload": {"source_id": "A"}}],
        brain=[{"action": "source_ingested", "ts": "t2", "payload": {"source_id": "B"}}],
    )
    refs = sorted(e["ref_id"] for e in _run() if e["ref_type"] == "source")
    assert refs == ["A", "B"]


def test_non_ref_events_never_collide_across_stores(monkeypatch):
    # query_ran in ledger and a brain query event with same ts stay separate
    # (fallback key includes the source discriminator).
    _patch(
        monkeypatch,
        ledger=[{"kind": "query_ran", "ts": "2026-01-01T00:00:00", "payload": {}}],
        brain=[{"action": "query_ran", "ts": "2026-01-01T00:00:00", "payload": {}}],
    )
    assert len(_run()) == 2


# --- fail-open -------------------------------------------------------------

def test_one_store_raising_still_yields_the_others(monkeypatch):
    # A store lane that RAISES must not sink the merge — the other two survive.
    def _boom(_nb, _limit):
        raise RuntimeError("store exploded")

    async def _ok_exploration(_nb, _limit):
        return {"queries": [{"id": "q1", "query": "a", "timestamp": "2026-01-01T00:00:00"}]}

    monkeypatch.setattr(ct, "_read_exploration_journey", _ok_exploration)
    monkeypatch.setattr(ct, "_read_ledger_events", _boom)  # this lane raises
    monkeypatch.setattr(ct, "_read_brain_events", lambda _nb, _limit: [
        {"action": "x", "ts": "2026-02-01T00:00:00", "payload": {}}
    ])

    tl = _run()
    assert {e["source"] for e in tl} == {"exploration", "brain"}
    assert all(e["kind"] for e in tl)


def test_real_seams_are_fail_open(monkeypatch):
    # Force the underlying store imports/reads to blow up and confirm each real
    # seam returns its empty sentinel rather than raising.
    import services.activity_ledger as al

    monkeypatch.setattr(al, "recent_events", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ct._read_ledger_events("nb1", 10) == []
