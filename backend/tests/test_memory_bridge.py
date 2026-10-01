"""LB-4: the memory bridge — a companion reads and writes LocalBook's memory.

Real stores on `tmp_path`: recall SQLite, archival LanceDB and its FTS index,
core JSON. A FRESH `MemoryStore` per test — the singleton captures its paths at
construction, so relying on conftest's data-dir swap alone would reach whatever
store was built first. Embeddings are a deterministic bag-of-words stand-in, so
vector search means something without loading the real model; the LLM is
faked wherever extraction or summaries run.
"""

import asyncio
import hashlib
import math

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import companions as svc


def _embed(text: str, dim: int):
    v = [0.0] * dim
    for w in text.lower().split():
        h = int(hashlib.md5(w.strip(".,!?").encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@pytest.fixture
def mem(tmp_path, monkeypatch):
    from config import settings
    from storage import memory_store as ms

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    # Core memory lives in localbook.db `documents` (LB-12 D1): the Database
    # singleton must follow this test's data dir too, or rows leak between tests.
    import importlib

    import storage.database as _db
    importlib.reload(_db)
    from services import migration_ledger
    migration_ledger.run_pending(_db.Database().get_connection(), tmp_path)
    monkeypatch.setattr(ms.MemoryStore, "_instance", None)
    store = ms.MemoryStore()
    monkeypatch.setattr(store, "get_embedding", lambda text: _embed(text, settings.embedding_dim))
    monkeypatch.setattr(ms, "memory_store", store)
    import services.memory_agent as ma
    monkeypatch.setattr(ma, "memory_store", store)

    jobs = []
    from services.enrichment_worker import enrichment_worker
    monkeypatch.setattr(enrichment_worker, "enqueue", lambda job: jobs.append(job))
    store.jobs = jobs
    return store


def _run(coro):
    return asyncio.run(coro)


def _fts_rows(store, ids):
    conn = store._get_recall_connection()
    try:
        q = f"SELECT COUNT(*) FROM archival_fts WHERE memory_id IN ({','.join('?' * len(ids))})"
        return conn.execute(q, list(ids)).fetchone()[0]
    finally:
        conn.close()


def _fake_extraction(monkeypatch, facts=None, long_term=None):
    from services.memory_agent import memory_agent

    async def fake(prompt):
        return {"user_facts": facts or [], "topics_mentioned": [], "entities_mentioned": [],
                "should_remember_long_term": long_term}

    monkeypatch.setattr(memory_agent, "_call_llm_for_extraction", fake)


# ── sync-turn ───────────────────────────────────────────────────────────────


def test_a_turn_is_stored_once_however_often_it_is_sent(mem):
    from services import memory_bridge
    from storage import companion_memory as cm

    first = memory_bridge.sync_turn("jocasta", "s1", "I moved to Lisbon", "Noted!", "2026-09-30T10:00:00Z")
    again = memory_bridge.sync_turn("jocasta", "s1", "I moved to Lisbon", "Noted!", "2026-09-30T10:00:00Z")

    assert first["stored"] == {"user": True, "assistant": True}
    assert again["stored"] == {"user": False, "assistant": False}
    assert len(cm.session_turns("jocasta", "s1")) == 2
    assert len(mem.jobs) == 1            # the retry queued nothing either


def test_extraction_is_queued_not_run_inline(mem, monkeypatch):
    from services import memory_bridge
    from services.memory_agent import memory_agent

    called = []
    monkeypatch.setattr(memory_agent, "_call_llm_for_extraction",
                        lambda p: called.append(p) or None)
    memory_bridge.sync_turn("jocasta", "s1", "hello", "", 1727690000)
    assert called == []
    job = mem.jobs[0]
    assert job.key == "mem-extract:jocasta:s1:1727690000" and job.tier.name == "DAYDREAM"


def test_extracted_facts_are_tagged_to_the_companion(mem, monkeypatch):
    from services import memory_bridge

    _fake_extraction(monkeypatch, facts=[{"key": "home city", "value": "Lisbon",
                                          "category": "user_fact", "importance": "high"}],
                     long_term="The user moved to Lisbon in September")
    memory_bridge.sync_turn("jocasta", "s1", "I moved to Lisbon", "Noted!", "2026-09-30T10:00:01Z")
    _run(mem.jobs[0].factory())

    core = mem.load_core_memory().entries
    assert [(e.key, e.source_conversation_id) for e in core] == [("home city", "companion:jocasta:s1")]
    table = mem.archival_db.open_table("archival_memories").to_pandas()
    assert table["namespace"].tolist() == ["companion:jocasta"]
    conn = mem._get_recall_connection()
    try:   # extraction did not store the turn a second time
        assert conn.execute("SELECT COUNT(*) FROM recall_entries").fetchone()[0] == 2
    finally:
        conn.close()


def test_bad_ids_are_refused(mem):
    from services import memory_bridge
    from storage.companion_memory import CompanionMemoryError

    with pytest.raises(CompanionMemoryError):
        memory_bridge.sync_turn("jocasta", "s1' OR 1=1 --", "x", "y", "2026-09-30T10:00:00Z")
    with pytest.raises(CompanionMemoryError):
        memory_bridge.sync_turn("Jocasta Evil", "s1", "x", "y", "2026-09-30T10:00:00Z")


# ── one shared memory ───────────────────────────────────────────────────────


def _companion_memory(mem, text="The user moved to Lisbon in September"):
    from models.memory import ArchivalMemoryEntry
    from storage import companion_memory as cm

    entry = ArchivalMemoryEntry(content=text, content_type="extracted_fact",
                                source_type="user_stated",
                                source_id=cm.conversation_id("jocasta", "s1"))
    mem.add_archival_memory(entry, namespace=cm.namespace("jocasta"))
    return entry


def test_localbook_chat_can_recall_what_a_companion_learned(mem):
    """The user's choice: one memory. SYSTEM search sees companion:* rows."""
    entry = _companion_memory(mem)
    found = mem.search_archival_memory("where did the user move Lisbon")
    assert entry.id in [r.entry.id for r in found]


def test_prefetch_brings_every_tier_inside_the_budget(mem):
    from models.memory import CoreMemoryEntry
    from services import memory_bridge

    mem.add_core_memory(CoreMemoryEntry(key="name", value="Pat", category="user_fact",
                                        importance="critical", source_type="user_stated"))
    _companion_memory(mem)
    memory_bridge.sync_turn("jocasta", "s1", "Book a table in Lisbon", "Done", "2026-09-30T10:00:01Z")

    out = _run(memory_bridge.prefetch("jocasta", "Lisbon move", "s1", k=5, char_budget=2000))
    s = out["sections"]
    assert s["core"][0]["text"] == "name: Pat" and s["core"][0]["source"] == "localbook"
    assert s["archival"] and s["archival"][0]["source"] == "companion:jocasta"
    assert any("Book a table" in r["text"] for r in s["recall"])
    assert out["chars"] <= 2000 and out["text"].startswith("name: Pat")


def test_prefetch_never_exceeds_a_small_budget(mem):
    from services import memory_bridge

    for i in range(20):
        memory_bridge.sync_turn("jocasta", "s1", f"message number {i} " * 5, "", f"2026-09-30T10:{i:02d}:00Z")
    out = _run(memory_bridge.prefetch("jocasta", "message", "s1", char_budget=150))
    assert 0 < out["chars"] <= 150 and len(out["text"]) <= 150


# ── forgetting ──────────────────────────────────────────────────────────────


def test_purge_removes_everything_the_companion_wrote_and_nothing_else(mem, monkeypatch):
    from models.memory import ArchivalMemoryEntry, CoreMemoryEntry
    from services import memory_bridge
    from storage import companion_memory as cm

    mine = ArchivalMemoryEntry(content="LocalBook's own memory about notebooks",
                               content_type="x", source_type="system")
    mem.add_archival_memory(mine)
    mem.add_core_memory(CoreMemoryEntry(key="name", value="Pat", category="user_fact",
                                        source_type="user_stated"))
    _fake_extraction(monkeypatch, facts=[{"key": "home city", "value": "Lisbon",
                                          "category": "user_fact", "importance": "high"}])
    memory_bridge.sync_turn("jocasta", "s1", "I moved to Lisbon", "ok", "2026-09-30T10:00:01Z")
    _run(mem.jobs[0].factory())
    theirs = _companion_memory(mem)
    assert _fts_rows(mem, [theirs.id]) == 1

    out = cm.purge("jocasta")

    assert out["recall"] == 2 and out["archival"] == 1 and out["fts"] == 1 and out["core"] == 1
    assert _fts_rows(mem, [theirs.id]) == 0
    assert mem.archival_db.open_table("archival_memories").to_pandas()["id"].tolist() == [mine.id]
    assert [e.key for e in mem.load_core_memory().entries] == ["name"]
    assert cm.session_turns("jocasta", "s1") == []


def test_deleting_a_notebook_no_longer_leaks_keyword_rows(mem):
    from models.memory import ArchivalMemoryEntry

    e = ArchivalMemoryEntry(content="notebook memory", content_type="x", source_type="system",
                            source_notebook_id="nb1")
    mem.add_archival_memory(e)
    assert _fts_rows(mem, [e.id]) == 1
    mem.delete_notebook_memories("nb1")
    assert _fts_rows(mem, [e.id]) == 0


# ── session-end ─────────────────────────────────────────────────────────────


def test_session_end_summarises_into_the_companion_namespace(mem, monkeypatch):
    from models.memory import ConversationSummary
    from services import memory_bridge
    from services.memory_agent import memory_agent

    assert memory_bridge.session_end("jocasta", "s1") == {"queued": False, "turns": 0}
    memory_bridge.sync_turn("jocasta", "s1", "Plan the Lisbon trip", "Sure", "2026-09-30T10:00:01Z")
    mem.jobs.clear()

    async def fake_summary(entries):
        from datetime import datetime
        return (ConversationSummary(conversation_id=entries[0].conversation_id,
                                    summary="Planning a Lisbon trip", key_points=["dates"],
                                    start_time=datetime.utcnow(), end_time=datetime.utcnow(),
                                    message_count=len(entries)), [])

    monkeypatch.setattr(memory_agent, "_summarize_conversation", fake_summary)
    assert memory_bridge.session_end("jocasta", "s1") == {"queued": True, "turns": 2}
    _run(mem.jobs[0].factory())
    table = mem.archival_db.open_table("archival_memories").to_pandas()
    assert table["namespace"].tolist() == ["companion:jocasta"]
    assert "Lisbon" in table["content"].iloc[0]


# ── routes ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(mem):
    from api import memory_bridge as routes

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _key(scopes=("memory",)):
    return svc.issue_companion_key("jocasta", list(scopes))


def test_routes_need_a_key_with_memory_scope(client):
    body = {"query": "x", "session_id": "s1"}
    assert client.post("/memory/prefetch", json=body).status_code == 401
    r = client.post("/memory/prefetch", json=body,
                    headers={"Authorization": f"Bearer {_key(('llm',))}"})
    assert r.status_code == 403 and "'memory'" in r.json()["detail"]
    r = client.post("/memory/prefetch", json=body, headers={"Authorization": f"Bearer {_key()}"})
    assert r.status_code == 200 and "sections" in r.json()


def test_sync_turn_then_prefetch_round_trip_over_http(client):
    h = {"Authorization": f"Bearer {_key()}"}
    r = client.post("/memory/sync-turn", headers=h,
                    json={"session_id": "s9", "user": "My dog is called Biscuit",
                          "assistant": "Lovely", "ts": "2026-09-30T12:00:00Z"})
    assert r.status_code == 200 and r.json()["stored"]["user"] is True
    r = client.post("/memory/prefetch", headers=h, json={"query": "dog Biscuit", "session_id": "s9"})
    assert "Biscuit" in r.json()["text"]


def test_the_key_decides_whose_memory_it_is(client):
    """A companion cannot write under another's tag: the id comes from the key."""
    from storage import companion_memory as cm

    other = svc.issue_companion_key("meeting-notes", ["memory"])
    client.post("/memory/sync-turn", headers={"Authorization": f"Bearer {other}"},
                json={"session_id": "s1", "user": "hi", "assistant": "", "ts": "2026-09-30T10:00:00Z"})
    assert cm.session_turns("meeting-notes", "s1") and not cm.session_turns("jocasta", "s1")


def test_only_the_three_bridge_paths_skip_the_app_token():
    from utils.auth_middleware import EXEMPT_PATHS, EXEMPT_PREFIXES

    assert {"/memory/prefetch", "/memory/sync-turn", "/memory/session-end"} <= EXEMPT_PATHS
    assert not any(p.startswith("/memory") for p in EXEMPT_PREFIXES)
    assert "/memory/core" not in EXEMPT_PATHS


# ── MCP tools ───────────────────────────────────────────────────────────────


def _tools():
    from services import mcp_server

    async def _get():
        return await mcp_server.build_server().get_tools()
    return asyncio.run(_get())


def _as(scopes):
    from services import mcp_server
    from services.companion_keys import CompanionIdentity

    mcp_server._set_caller(CompanionIdentity(companion_id="jocasta", scopes=scopes))


def test_memory_tools_refuse_an_mcp_only_key(mem):
    _as(("mcp",))
    tools = _tools()
    assert "error" in _run(tools["memory_search"].fn(query="x"))
    assert "error" in _run(tools["memory_add"].fn(text="remember this"))
    assert "archival_memories" not in mem.archival_db.table_names() or \
        mem.archival_db.open_table("archival_memories").count_rows() == 0


def test_memory_add_then_search_through_mcp(mem):
    _as(("mcp", "memory"))
    tools = _tools()
    added = _run(tools["memory_add"].fn(text="The user prefers window seats on trains"))
    assert added["stored"] and added["tag"] == "companion:jocasta"
    found = _run(tools["memory_search"].fn(query="window seats trains"))
    assert any("window seats" in a["text"] for a in found["sections"]["archival"])


def test_a_ts_that_is_not_a_time_is_a_400(client):
    r = client.post("/memory/sync-turn", headers={"Authorization": f"Bearer {_key()}"},
                    json={"session_id": "s1", "user": "hi", "ts": "yesterday-ish"})
    assert r.status_code == 400
