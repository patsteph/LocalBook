"""LB-12 visibility: progress you can watch, a Stop that is safe, synced sources
that become searchable, and Macs found without typing an address.

2026-10-01, the first real mini ⇄ MBP sync: it worked, but the user was blind
(one long request behind a spinner), the MBP's synced notebook was never indexed
on the mini (the old re-index trusted the other Mac's chunk count), and pairing
took ~10 clicks.
"""

import asyncio
import time

import pytest

from services.sync import discovery, engine, indexer, progress
from tests.sync_harness import Mac, pull, template


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    progress._reset_for_tests()
    yield
    progress._reset_for_tests()


@pytest.fixture
def macs(tmp_path_factory):
    root = tmp_path_factory.mktemp("syncp")
    tmpl = template(tmp_path_factory.getbasetemp())
    return lambda *names: [Mac(root, n, tmpl) for n in names]


# ── progress ────────────────────────────────────────────────────────────────


def test_a_run_reports_phase_counts_and_finishes():
    run = progress.begin("initiated", "initiated", ["connect", "receive", "send"], name="MacBook Pro")
    run.step("connect")
    run.step("receive", total=538, unit="changes")
    run.advance(340)
    snap = progress.snapshot()
    d = snap["runs"][0]
    assert snap["running"] and d["label"] == "Receiving changes"
    assert (d["done"], d["total"], d["unit"]) == (340, 538, "changes")
    assert [p["state"] for p in d["phases"]] == ["done", "current", "pending"]
    assert progress.summary() == {"running": True, "label": "Syncing with MacBook Pro… 63%"}
    run.finish(result={"received": {"sources": 23}})
    assert progress.snapshot()["running"] is False
    assert progress.summary()["running"] is False


def test_stop_is_cooperative_and_only_for_this_macs_runs():
    mine = progress.begin("initiated", "initiated", ["receive"])
    theirs = progress.incoming("dev-b", "mini")
    assert progress.cancel() is True
    with pytest.raises(progress.Cancelled):
        mine.check()
    theirs.check()                                   # the other Mac drives that one


def test_an_incoming_run_closes_once_the_other_mac_goes_quiet(monkeypatch):
    run = progress.incoming("dev-b", "mini")
    run.step("receive", unit="changes")
    run.advance(5)
    monkeypatch.setattr(progress, "INCOMING_IDLE", 0)
    time.sleep(0.01)
    snap = progress.snapshot()
    assert snap["running"] is False and snap["runs"][0]["result"] == {"done": 5}


# ── the denominators ────────────────────────────────────────────────────────


def test_pending_counts_what_a_pull_will_move(macs):
    a, b = macs("A", "B")
    a.notebook("nb1")
    for i in range(9):
        a.source(f"s{i}", "nb1", f"text {i}")
    n = engine.pending(a.rep, engine.vv(b.conn))
    first = engine.export(a.rep, engine.vv(b.conn), limit=4)
    assert n == first["remaining"] == 10
    pull(b, a)
    assert engine.pending(a.rep, engine.vv(b.conn)) == 0


# ── what has to be (re-)indexed ─────────────────────────────────────────────


def test_apply_reports_sources_whose_text_changed_or_that_were_deleted(macs):
    a, b = macs("A", "B")
    a.notebook("nb1")
    a.source("s1", "nb1", "first text")
    a.source("s2", "nb1", "other")
    pull(b, a)
    a.sql("UPDATE sources SET content='edited text' WHERE id='s1'")
    a.sql("UPDATE sources SET notes='a note' WHERE id='s2'")       # not the text
    page = engine.export(a.rep, engine.vv(b.conn))
    rep = engine.apply(b.rep, page)
    assert rep.get("reindex") == ["s1"]
    a.sql("DELETE FROM sources WHERE id='s2'")
    rep = engine.apply(b.rep, engine.export(a.rep, engine.vv(b.conn)))
    assert rep.get("unindex") == ["s2"]


def _fake_sources(monkeypatch, rows, indexed):
    monkeypatch.setattr(indexer, "_sources", lambda: rows)
    monkeypatch.setattr(indexer, "_indexed_ids", lambda nb: set(indexed.get(nb, ())))


def _src(sid, nb="nb1", chunks=5, status="completed"):
    return {"id": sid, "notebook_id": nb, "filename": f"{sid}.pdf", "type": "document",
            "chunks": chunks, "status": status, "summary": "S"}


def test_a_synced_source_claiming_chunks_but_missing_here_is_indexed(monkeypatch):
    """The bug: the other Mac's chunk count came along, so the old re-index skipped it."""
    _fake_sources(monkeypatch, [_src("here"), _src("synced"), _src("never-indexed", chunks=0)],
                  {"nb1": {"here"}})
    work = indexer.plan()
    assert [s["id"] for s in work["index"]] == ["synced"]


def test_changed_text_is_reindexed_and_deleted_sources_unindexed(monkeypatch):
    _fake_sources(monkeypatch, [_src("s1"), _src("s2")], {"nb1": {"s1", "s2"}})
    indexer.note({"tables": {"sources": {"changed": 2}}, "reindex": ["s1"], "unindex": ["gone"]})
    work = indexer.plan()
    assert [s["id"] for s in work["index"]] == ["s1"] and work["unindex"] == ["gone"]


class _FakeRag:
    def __init__(self):
        self.ingested, self.deleted = [], []

    async def delete_source(self, nb, sid):
        self.deleted.append(sid)
        return True

    async def ingest_document(self, **kw):
        assert kw["deferred"] is True        # topic model + entities wait for sustained idle
        self.ingested.append((kw["source_id"], kw["enable_hyde"], kw["precomputed_summary"]))
        return {}


class _FakeSources:
    async def get_content(self, nb, sid):
        return {"content": f"text of {sid}"}


@pytest.fixture
def fake_rag(monkeypatch):
    import services.rag_engine as re_mod
    import storage.source_store as ss_mod

    rag = _FakeRag()
    monkeypatch.setattr(re_mod, "rag_engine", rag)
    monkeypatch.setattr(ss_mod, "source_store", _FakeSources())
    monkeypatch.setattr(indexer, "_tables", lambda: ["nb1"])
    return rag


def test_indexing_reports_progress_uses_no_llm_and_clears_pending(monkeypatch, fake_rag):
    _fake_sources(monkeypatch, [_src("a"), _src("b")], {})
    indexer.note({"reindex": ["a"]})
    run = progress.begin("index", "index", ["index"])
    out = asyncio.run(indexer.run_into(run))
    assert out == {"indexed": 2, "failed": 0, "removed": 0}
    assert all(h is False and summ == "S" for _, h, summ in fake_rag.ingested)   # no HyDE, no summary call
    d = progress.snapshot()["runs"][0]
    assert (d["done"], d["total"], d["unit"]) == (2, 2, "sources")
    from services.sync import store
    assert store.get(indexer._PENDING)["reindex"] == []


def test_stopping_indexing_keeps_the_rest_for_next_time(monkeypatch, fake_rag):
    _fake_sources(monkeypatch, [_src("a"), _src("b"), _src("c")], {})
    indexer.note({"reindex": ["a", "b", "c"]})
    run = progress.begin("index", "index", ["index"])
    orig = run.advance

    def advance_then_stop(*a, **k):
        orig(*a, **k)
        run.cancel_requested = True
    run.advance = advance_then_stop
    with pytest.raises(progress.Cancelled):
        asyncio.run(indexer.run_into(run))
    from services.sync import store
    assert store.get(indexer._PENDING)["reindex"] == ["b", "c"]


# ── discovery ───────────────────────────────────────────────────────────────

BROWSE = """Browsing for _localbook._tcp.local
DATE: ---Thu 01 Oct 2026---
10:41:20.108  ...STARTING...
Timestamp     A/R    Flags  if Domain               Service Type         Instance Name
10:41:20.110  Add        3   1 local.               _localbook._tcp.     Patrick’s Mac mini
10:41:20.110  Add        2  15 local.               _localbook._tcp.     Patrick’s Mac mini
10:41:20.111  Add        2  15 local.               _localbook._tcp.     Work MacBook Pro
"""
LOOKUP = """Lookup Patrick’s Mac mini._localbook._tcp.local
10:41:22.116  Patrick’s\\032Mac\\032mini._localbook._tcp.local. can be reached at Patricks-Mac-mini.local.:47600 (interface 15) Flags: 1
 id=11debbf6001033f4
"""


def test_discovery_parses_dns_sd():
    assert discovery.parse_browse(BROWSE) == ["Patrick’s Mac mini", "Work MacBook Pro"]
    assert discovery.parse_lookup(LOOKUP) == {"hostname": "Patricks-Mac-mini.local", "port": 47600,
                                              "device_id": "11debbf6001033f4"}
    assert discovery.parse_lookup("nothing here") is None


# ── runs in the background ──────────────────────────────────────────────────


def test_start_sync_returns_at_once_and_the_run_finishes(monkeypatch):
    from services.sync import service, store

    store.conn().execute("INSERT INTO devices (device_id, name, cert_pem, fingerprint, host, port, role, mode) "
                         "VALUES ('dev-b', 'MacBook Pro', 'x', 'f', '10.0.0.2', 47600, 'joiner', 'live')")

    async def fake_sync(device_id, user_initiated=False, run=None):
        run.step("connect")
        run.step("receive", total=3, unit="changes")
        run.advance(3)
        await asyncio.sleep(0.05)
        return {"main": {"in": {"conflicts": 1, "tables": {"sources": {"in": 3, "changed": 3}}},
                         "out": {"tables": {}}},
                "blobs": {"fetched": 0}, "index": {"indexed": 3}}

    monkeypatch.setattr(service, "sync_with", fake_sync)

    async def go():
        t0 = time.time()
        out = service.start_run("dev-b")
        assert time.time() - t0 < 0.05 and out["run_id"]
        with pytest.raises(ValueError, match="already running"):
            service.start_run("dev-b")
        for _ in range(100):
            if not progress.active("initiated"):
                break
            await asyncio.sleep(0.02)
    asyncio.run(go())
    d = progress.snapshot()["runs"][0]
    assert d["running"] is False and d["error"] is None
    assert d["result"] == {"received": {"sources": 3}, "sent": {}, "conflicts": 1,
                           "files": {"fetched": 0}, "index": {"indexed": 3}}


# ── a notebook deleted on another Mac leaves nothing behind here ─────────────


def test_a_synced_notebook_delete_reports_what_it_took_with_it(macs):
    a, b = macs("A", "B")
    a.notebook("nb1")
    a.source("s1", "nb1")
    a.sql("INSERT INTO audio_generations (audio_id, notebook_id, status, created_at, updated_at) "
          "VALUES ('pod1', 'nb1', 'completed', 'now', 'now')")
    pull(b, a)
    a.sql("DELETE FROM notebooks WHERE id='nb1'")          # the app cascades sources + audio
    removed = {"removed_notebooks": [], "removed_audio": [], "unindex": []}
    for _ in range(20):
        page = engine.export(a.rep, engine.vv(b.conn), limit=7)
        rep = engine.apply(b.rep, page)
        for k in removed:
            removed[k] += rep.get(k, [])
        if not page["more"]:
            break
    assert removed == {"removed_notebooks": ["nb1"], "removed_audio": ["pod1"], "unindex": ["s1"]}


def test_cleanup_removes_only_what_the_deleted_rows_named(tmp_path, monkeypatch):
    from config import settings
    from services import rag_storage
    from services.sync import peer

    dropped = []
    monkeypatch.setattr(rag_storage, "drop_notebook_table", lambda nb: dropped.append(nb) or True)
    (tmp_path / "audio").mkdir()
    for name in ("pod1.wav", "pod1_speech.wav", "other.wav"):
        (tmp_path / "audio" / name).write_bytes(b"x")
    (tmp_path / "notebooks" / "nb1").mkdir(parents=True)
    (tmp_path / "notebooks" / "nb1" / "collector.yaml").write_text("x")
    (tmp_path / "notebooks" / "nb2").mkdir()
    from storage.database import get_db
    get_db()                                                   # schema in the temp dir
    peer._clean_removed({"removed_notebooks": ["nb1"], "removed_audio": ["pod1"]})
    assert sorted(p.name for p in (tmp_path / "audio").iterdir()) == ["other.wav"]
    assert not (tmp_path / "notebooks" / "nb1").exists() and (tmp_path / "notebooks" / "nb2").exists()
    assert dropped == ["nb1"]
