"""The Jocasta contract (READFIRST/ArchitectureDocs/LocalBook contract.md), row by row.

Tool names, argument names and response shapes are what Jocasta's code already
sends and parses. A change that breaks one of these breaks Jocasta — so the
rows are pinned here, one test each, rather than implied by the LB-2/3/4 tests.
"""

import asyncio
import inspect
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import companion_keys, mcp_server
from services import companions as svc
from services.companion_keys import CompanionIdentity

CONTRACT_TOOLS = {
    "todays_digest": [], "curator_insights": ["since", "k"], "events_since": ["cursor", "kinds"],
    "search_notebooks": ["query", "notebook_ids", "k"], "get_source": ["source_id", "offset", "max_chars"],
    "propose_note": ["notebook_id", "title", "body", "source"], "web_search": ["query", "n"],
    "fetch_page": ["url"], "start_research": ["topic", "notebook_id"], "get_job": ["job_id"],
    "memory_search": ["query", "k"], "ask_notebook": ["question", "notebook_id"],
    "list_notebooks": [], "get_note": ["note_id"], "list_recent_notes": ["n"],
    "approval_queue": ["status", "k"], "youtube_search": [], "scholarly_search": [],
    "memory_add": ["text", "category"],
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


@pytest.fixture
def tools():
    async def _get():
        return await mcp_server.build_server().get_tools()
    return asyncio.run(_get())


def _as(*scopes):
    mcp_server._set_caller(CompanionIdentity(companion_id="jocasta", scopes=tuple(scopes)))


def _call(tools, name, **kw):
    return asyncio.run(tools[name].fn(**kw))


# ── MCP: all 19 tools, by name and argument name ────────────────────────────


def test_all_nineteen_contract_tools_exist_with_their_argument_names(tools):
    assert len(CONTRACT_TOOLS) == 19
    for name, args in CONTRACT_TOOLS.items():
        assert name in tools, f"missing tool {name}"
        params = inspect.signature(tools[name].fn).parameters
        for a in args:
            assert a in params, f"{name} lacks argument {a!r}"


def test_mcp_is_served_at_exactly_slash_mcp_without_a_redirect(store):
    host = FastAPI()
    host.mount("/mcp", mcp_server.get_app())
    host.add_middleware(mcp_server.ExactMountPath, path="/mcp")
    c = TestClient(host, follow_redirects=False)
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
               headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401 and "detail" in r.json()     # reached the app, no 307


# ── events_since ────────────────────────────────────────────────────────────


def test_events_since_has_the_contract_shape(monkeypatch):
    from services import event_feed

    monkeypatch.setattr(event_feed, "_read_curator",
                        lambda after, limit, kinds: [{"source": "curator", "id": 3, "ts": "2026-09-30T10:00",
                                                      "kind": "insight_found", "notebook_id": "nb1",
                                                      "actor": "curator", "payload": {"title": "A pattern"}}])
    monkeypatch.setattr(event_feed, "_read_activity",
                        lambda after, limit, kinds: [{"source": "activity", "id": 3, "ts": "2026-09-30T10:01",
                                                      "kind": "source_added", "notebook_id": "nb1",
                                                      "actor": "user", "payload": {"source_id": "s9", "filename": "x.pdf"}}])
    out = event_feed.events_since(None)
    assert out["next_cursor"] and out["next_cursor"] == out["cursor"]
    ids = [e["id"] for e in out["events"]]
    assert ids == ["curator:3", "activity:3"]                 # same numeric id, distinct events
    e = out["events"][1]
    assert {"id", "kind", "ts", "ref", "summary"} <= set(e)
    assert e["ref"] == "s9" and "source added" in e["summary"] and "x.pdf" in e["summary"]


def test_events_need_the_events_scope(tools, monkeypatch):
    from services import event_feed

    monkeypatch.setattr(event_feed, "events_since", lambda **kw: {"events": [], "next_cursor": "b0.a0"})
    _as("mcp")
    assert "error" in _call(tools, "events_since")
    _as("mcp", "events")
    assert _call(tools, "events_since")["next_cursor"] == "b0.a0"


# ── memory ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mem(tmp_path, monkeypatch):
    from tests.test_memory_bridge import mem as _mem_fixture  # noqa: F401 — reuse its setup
    from config import settings
    from storage import memory_store as ms
    from tests.test_memory_bridge import _embed

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(ms.MemoryStore, "_instance", None)
    store = ms.MemoryStore()
    monkeypatch.setattr(store, "get_embedding", lambda text: _embed(text, settings.embedding_dim))
    monkeypatch.setattr(ms, "memory_store", store)
    from services.enrichment_worker import enrichment_worker
    monkeypatch.setattr(enrichment_worker, "enqueue", lambda job: None)
    return store


@pytest.fixture
def client(mem):
    from api import memory_bridge as routes

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _key():
    return svc.issue_companion_key("jocasta", ["llm", "mcp", "audio", "memory", "events"])


def test_prefetch_returns_items_kind_and_text_best_first(client):
    h = {"Authorization": f"Bearer {_key()}"}
    client.post("/memory/sync-turn", headers=h,
                json={"session_id": "s1", "user": "My sister is called Ana", "assistant": "Noted",
                      "ts": 1759230000, "source": "jocasta"})
    out = client.post("/memory/prefetch", headers=h,
                      json={"query": "sister Ana", "session_id": "s1", "k": 8, "char_budget": 2000}).json()
    assert out["items"] and all({"kind", "text"} <= set(i) for i in out["items"])
    assert any("Ana" in i["text"] for i in out["items"])


def test_memory_search_via_prefetch_with_the_search_session(client):
    r = client.post("/memory/prefetch", headers={"Authorization": f"Bearer {_key()}"},
                    json={"query": "anything", "session_id": "search", "k": 8, "char_budget": 8000})
    assert r.status_code == 200 and "items" in r.json()


def test_memory_add_endpoint_takes_content_category_source(client, mem):
    r = client.post("/memory/add", headers={"Authorization": f"Bearer {_key()}"},
                    json={"content": "Prefers window seats", "category": "preference", "source": "jocasta"})
    assert r.status_code == 200 and r.json()["tag"] == "companion:jocasta"
    rows = mem.archival_db.open_table("archival_memories").to_pandas()
    assert rows["namespace"].tolist() == ["companion:jocasta"]


def test_memory_add_is_exempt_from_the_app_token_by_exact_path():
    from utils.auth_middleware import EXEMPT_PATHS

    assert "/memory/add" in EXEMPT_PATHS and "/memory/core" not in EXEMPT_PATHS


def test_mcp_memory_add_takes_text_and_category(tools, mem):
    _as("mcp", "memory")
    out = _call(tools, "memory_add", text="Allergic to peanuts", category="health")
    assert out["stored"] and out["category"] == "health"


# ── the other tools' contract behaviour ─────────────────────────────────────


def test_approval_queue_takes_a_status_and_refuses_unknown_ones(tools):
    _as("mcp")
    assert "error" in _call(tools, "approval_queue", status="approved")


def test_research_jobs_are_persisted_and_resume_after_a_restart(store, monkeypatch):
    import importlib

    import storage.database as db
    importlib.reload(db)
    from services import research_jobs

    job = research_jobs.create("battery chemistry", None, "jocasta")
    assert job["status"] == "queued"

    research_jobs._set(job["id"], status="running", attempts=1)    # the app stopped mid-job
    assert research_jobs.resume_interrupted() == [job["id"]]
    assert research_jobs.get(job["id"])["status"] == "queued"
    research_jobs._set(job["id"], status="running", attempts=2)    # and again
    assert research_jobs.resume_interrupted() == []
    assert research_jobs.get(job["id"])["status"] == "error"


def test_scholarly_search_rejects_an_unknown_source(tools):
    _as("mcp")
    assert "error" in _call(tools, "scholarly_search", query="x", source="scihub")


# ── /health and speech ──────────────────────────────────────────────────────


def test_health_has_resident_budget_reserve_and_codec_at_the_top_level():
    import main

    out = asyncio.run(main.health())
    for key in ("resident_gb", "budget_gb", "reserve_gb", "codec_ok"):
        assert key in out, key
    assert isinstance(out["resident_gb"], (int, float))


def test_speech_accepts_openai_model_names_and_pcm():
    from api.openai_audio import SpeechRequest
    from services import audio_codec

    req = SpeechRequest(model="gpt-4o-mini-tts", input="hi", voice="bf_emma", response_format="pcm")
    assert req.response_format in audio_codec.FORMATS
    from services.audio_llm import KOKORO_VOICES
    assert "bf_emma" in KOKORO_VOICES


def test_start_research_launches_on_the_event_loop_and_finishes(tools, store, monkeypatch):
    """create() runs in a worker thread; launching from there would need a loop
    it does not have. The tool launches on the loop — this drives it end to end."""
    import importlib

    import storage.database as db
    importlib.reload(db)
    from services import research_engine as re_mod
    from services import research_jobs

    async def fake_dive(query, notebook_id, filters=None, on_status=None):
        return [re_mod.ResearchResult(id="r1", title="T", url="https://x", snippet="s")]

    monkeypatch.setattr(re_mod.research_engine, "deep_dive", fake_dive)
    _as("mcp")

    async def go():
        started = await tools["start_research"].fn(topic="battery chemistry")
        for _ in range(50):
            job = await tools["get_job"].fn(job_id=started["job_id"])
            if job["status"] == "done":
                return job
            await asyncio.sleep(0.02)
        return job

    job = asyncio.run(go())
    assert job["status"] == "done" and job["results"][0]["url"] == "https://x"
