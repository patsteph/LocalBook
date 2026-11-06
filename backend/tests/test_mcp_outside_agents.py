"""MCP for outside agents (Claude Code, Claude Desktop, ...) — 2026-11-06.

What this adds on top of LB-2 (tests/test_mcp_server.py):

  * Host and Origin checks, the MCP spec's DNS-rebinding defence.
  * `all_notebooks`: searching or asking across every notebook at once is its
    own grant. Keys that held `mcp` before it existed get it once on upgrade.
  * `search_notebook` and `list_notes`, the one-notebook tools.
"""

import asyncio
import json

import pytest

from services import companion_keys, mcp_server
from services.companion_keys import CompanionIdentity


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


def _as(scopes):
    mcp_server._set_caller(CompanionIdentity(companion_id="claude-code", scopes=tuple(scopes)))


def _call(tools, name, **kwargs):
    return asyncio.run(tools[name].fn(**kwargs))


# ── Host / Origin ───────────────────────────────────────────────────────────


class _Reached(Exception):
    pass


def _status(headers):
    async def inner(scope, receive, send):
        raise _Reached()

    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/", "client": ("127.0.0.1", 5000),
             "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    try:
        asyncio.run(mcp_server.CompanionAuthMiddleware(inner)(scope, receive, send))
    except _Reached:
        return "reached"
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "[::1]:8000", "localhost"])
def test_loopback_host_headers_get_through_to_the_key_check(store, host):
    assert _status({"host": host}) == 401          # past the Host check, stopped at the key


@pytest.mark.parametrize("host", ["evil.example:8000", "192.168.1.5:8000", "localhost.evil.com"])
def test_a_rebound_host_header_is_refused(store, host):
    assert _status({"host": host}) == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "file://", "http://192.168.1.5"])
def test_a_web_page_origin_is_refused(store, origin):
    assert _status({"host": "127.0.0.1:8000", "origin": origin}) == 403


@pytest.mark.parametrize("origin", ["http://localhost:1420", "tauri://localhost", "http://127.0.0.1:8000"])
def test_localbooks_own_origins_are_allowed(store, origin):
    assert _status({"host": "127.0.0.1:8000", "origin": origin}) == 401


def test_a_rebound_origin_is_refused_even_with_a_valid_key(store):
    key = companion_keys.issue("claude-code", ["mcp"])
    assert _status({"host": "127.0.0.1:8000", "origin": "https://evil.example",
                    "authorization": f"Bearer {key}"}) == 403


def test_a_valid_key_with_no_origin_reaches_the_server(store):
    key = companion_keys.issue("claude-code", ["mcp"])
    assert _status({"host": "127.0.0.1:8000", "authorization": f"Bearer {key}"}) == "reached"


# ── the all_notebooks scope ─────────────────────────────────────────────────


def test_all_notebooks_is_a_scope_that_can_be_issued(store):
    key = companion_keys.issue("claude-code", ["mcp", "all_notebooks"])
    assert companion_keys.verify(key).has("all_notebooks")


def test_a_new_mcp_key_does_not_get_all_notebooks_by_itself(store):
    key = companion_keys.issue("claude-code", ["mcp"])
    assert not companion_keys.verify(key).has("all_notebooks")


def test_upgrading_a_v1_store_keeps_existing_mcp_keys_working_across_notebooks(store):
    """Jocasta searched across notebooks before the scope existed; the upgrade
    must not take that away. A key without `mcp` gains nothing."""
    jocasta = companion_keys.issue("jocasta", ["mcp", "events"])
    recorder = companion_keys.issue("meeting-notes", ["llm"])
    path = store / companion_keys.STORE_FILE
    data = json.loads(path.read_text())
    data["version"] = 1
    for rec in data["companions"].values():
        rec["scopes"] = [s for s in rec["scopes"] if s != "all_notebooks"]
    path.write_text(json.dumps(data))

    assert companion_keys.verify(jocasta).scopes == ("mcp", "events", "all_notebooks")
    assert companion_keys.verify(recorder).scopes == ("llm",)
    assert json.loads(path.read_text())["version"] == companion_keys.STORE_VERSION

    # Once only: a scope the user later removes is not re-added on the next load.
    companion_keys.issue("jocasta", ["mcp"])
    assert companion_keys.list_keys()[0]["scopes"] == ["mcp"]


def _fake_cross_search(monkeypatch):
    seen = {}

    class FakeSearch:
        async def search(self, query, notebook_ids=None, top_k=5):
            seen["ids"] = notebook_ids
            return {"results": [], "notebooks_searched": len(notebook_ids or [1, 2])}

    monkeypatch.setattr("services.cross_notebook_search.cross_notebook_search", FakeSearch())
    return seen


def test_search_across_every_notebook_needs_all_notebooks(store, tools, monkeypatch):
    seen = _fake_cross_search(monkeypatch)
    _as(["mcp"])
    out = _call(tools, "search_notebooks", query="x")
    assert "all_notebooks" in out["error"]
    assert "ids" not in seen                       # never searched

    out = _call(tools, "search_notebooks", query="x", notebook_ids=["a", "b"])
    assert "all_notebooks" in out["error"]


def test_search_of_one_named_notebook_needs_only_mcp(store, tools, monkeypatch):
    seen = _fake_cross_search(monkeypatch)
    _as(["mcp"])
    out = _call(tools, "search_notebooks", query="x", notebook_ids=["a"])
    assert "error" not in out and seen["ids"] == ["a"]


def test_search_across_every_notebook_works_with_the_scope(store, tools, monkeypatch):
    seen = _fake_cross_search(monkeypatch)
    _as(["mcp", "all_notebooks"])
    assert "error" not in _call(tools, "search_notebooks", query="x")
    assert seen["ids"] is None


def test_ask_with_no_notebook_needs_all_notebooks_and_says_so_before_waiting(store, tools, monkeypatch):
    from storage.notebook_store import notebook_store

    async def books():
        return [{"id": "n1"}, {"id": "n2"}]

    probed = []

    async def probe(model):
        probed.append(model)
        return True

    monkeypatch.setattr(notebook_store, "list", books)
    monkeypatch.setattr(mcp_server, "_model_is_available", probe)
    _as(["mcp"])
    out = _call(tools, "ask_notebook", question="q")
    assert "all_notebooks" in out["error"] and "notebook_id" in out["hint"]
    assert probed == []                            # refused without the busy probe


def test_ask_with_no_notebook_and_only_one_notebook_needs_only_mcp(store, tools, monkeypatch):
    from services.rag_engine import rag_engine
    from storage.notebook_store import notebook_store

    async def books():
        return [{"id": "only"}]

    async def free(model):
        return True

    async def query(notebook_id, question, top_k):
        return {"answer": f"from {notebook_id}", "sources": []}

    monkeypatch.setattr(notebook_store, "list", books)
    monkeypatch.setattr(mcp_server, "_model_is_available", free)
    monkeypatch.setattr(rag_engine, "query", query)
    _as(["mcp"])
    out = _call(tools, "ask_notebook", question="q")
    assert out == {"answer": "from only", "sources": [], "notebook_id": "only"}


# ── search_notebook ─────────────────────────────────────────────────────────


class _Table:
    def __init__(self, rows):
        self.rows = rows

    def count_rows(self):
        return len(self.rows)


def _fake_retrieval(monkeypatch, rows, *, reranker=True):
    import numpy as np

    from services.rag_engine import rag_engine
    from storage.notebook_store import notebook_store

    calls = {"tables": [], "reranked": False}

    async def get(nid):
        return {"id": nid, "title": "Leadership"} if nid == "n1" else None

    async def encode_async(text, is_query=False):
        return np.zeros((1, 4))

    def get_table(nid):
        calls["tables"].append(nid)
        return _Table(rows)

    def hybrid(query, table, emb, k=12):
        calls["pool"] = k
        return list(table.rows)[:k]

    def rerank(query, docs, top_k=5):
        calls["reranked"] = True
        return list(reversed(docs))[:top_k]

    monkeypatch.setattr(notebook_store, "get", get)
    monkeypatch.setattr(rag_engine, "encode_async", encode_async)
    monkeypatch.setattr(rag_engine, "_get_table", get_table)
    monkeypatch.setattr(rag_engine, "_hybrid_search", hybrid)
    monkeypatch.setattr(rag_engine, "rerank", rerank)
    monkeypatch.setattr(rag_engine, "_use_reranker", reranker, raising=False)
    return calls


def _row(i, text="t"):
    return {"source_id": f"s{i}", "filename": f"doc{i}.md", "text": text, "chunk_index": i,
            "source_type": "md", "_distance": 0.1 * i}


def test_search_notebook_on_an_unknown_id_never_touches_the_index(store, tools, monkeypatch):
    """rag_storage.get_table CREATES a table for an unseen id."""
    calls = _fake_retrieval(monkeypatch, [])
    _as(["mcp"])
    out = _call(tools, "search_notebook", notebook_id="typo", query="x")
    assert "no notebook" in out["error"]
    assert calls["tables"] == []


def test_search_notebook_overcollects_then_reranks_like_the_chat(store, tools, monkeypatch):
    from config import settings

    calls = _fake_retrieval(monkeypatch, [_row(i) for i in range(30)])
    _as(["mcp"])
    out = _call(tools, "search_notebook", notebook_id="n1", query="delegation", k=3)
    assert calls["pool"] == max(3, settings.retrieval_overcollect)
    assert calls["reranked"] is True
    assert len(out["results"]) == 3
    assert out["notebook_title"] == "Leadership"
    assert set(out["results"][0]) >= {"source_id", "title", "text", "score", "chunk_index"}


def test_search_notebook_without_the_reranker_takes_the_hybrid_order(store, tools, monkeypatch):
    calls = _fake_retrieval(monkeypatch, [_row(i) for i in range(10)], reranker=False)
    _as(["mcp"])
    out = _call(tools, "search_notebook", notebook_id="n1", query="x", k=2)
    assert calls["pool"] == 2 and calls["reranked"] is False
    assert [r["source_id"] for r in out["results"]] == ["s0", "s1"]


def test_search_notebook_bounds_k_and_passage_length(store, tools, monkeypatch):
    from services import mcp_tools_more

    _fake_retrieval(monkeypatch, [_row(i, "x" * 50_000) for i in range(80)], reranker=False)
    _as(["mcp"])
    out = _call(tools, "search_notebook", notebook_id="n1", query="x", k=10_000)
    assert len(out["results"]) == mcp_server.MAX_K
    assert len(out["results"][0]["text"]) == mcp_tools_more.PASSAGE_CHARS
    assert out["results"][0]["truncated"] is True


def test_search_notebook_skips_the_placeholder_row(store, tools, monkeypatch):
    rows = [{"source_id": "placeholder", "text": ""}, _row(1)]
    _fake_retrieval(monkeypatch, rows, reranker=False)
    _as(["mcp"])
    out = _call(tools, "search_notebook", notebook_id="n1", query="x")
    assert [r["source_id"] for r in out["results"]] == ["s1"]


def test_search_notebook_on_an_empty_notebook_is_empty_not_an_error(store, tools, monkeypatch):
    _fake_retrieval(monkeypatch, [])
    _as(["mcp"])
    assert _call(tools, "search_notebook", notebook_id="n1", query="x")["results"] == []


# ── list_notes ──────────────────────────────────────────────────────────────


def test_list_notes_lists_one_notebook_with_previews(store, tools, monkeypatch):
    from storage.note_store import note_store

    async def for_nb(nid):
        assert nid == "n1"
        return [{"id": f"note{i}", "title": f"T{i}", "content_markdown": "y" * 500,
                 "updated_at": f"2026-11-0{i}"} for i in range(5, 0, -1)]

    monkeypatch.setattr(note_store, "list_for_notebook", for_nb)
    _as(["mcp"])
    out = _call(tools, "list_notes", notebook_id="n1", n=2)
    assert out["total"] == 5
    assert [x["id"] for x in out["notes"]] == ["note5", "note4"]
    assert len(out["notes"][0]["preview"]) == 200


# ── over the real transport ────────────────────────────────────────────────


@pytest.fixture
def mounted(store):
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    mcp_server._app = None
    mcp_server._mcp = None

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_server.lifespan_context(app):
            yield

    host = FastAPI(lifespan=lifespan)
    host.mount("/mcp", mcp_server.get_app())
    host.add_middleware(mcp_server.ExactMountPath, path="/mcp")
    with TestClient(host, base_url="http://127.0.0.1:8000") as client:
        yield client
    mcp_server._app = None
    mcp_server._mcp = None


def _init_body():
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                       "clientInfo": {"name": "claude-code", "version": "1"}}}


def _headers(key, **extra):
    return {"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json", **extra}


def test_claude_code_style_client_lists_the_one_notebook_tools(mounted, store):
    key = companion_keys.issue("claude-code", ["mcp"])
    r = mounted.post("/mcp", headers=_headers(key), json=_init_body())
    assert r.status_code == 200, r.text
    session = r.headers["mcp-session-id"]
    h = _headers(key, **{"mcp-session-id": session, "mcp-protocol-version": "2025-11-25"})
    mounted.post("/mcp", headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    listed = mounted.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed.status_code == 200
    for tool in ("search_notebook", "list_notes", "list_notebooks", "list_sources", "get_source",
                 "get_note"):
        assert f'"{tool}"' in listed.text, tool


def test_a_browser_page_cannot_reach_the_transport_even_with_a_key(mounted, store):
    key = companion_keys.issue("claude-code", ["mcp"])
    r = mounted.post("/mcp", headers=_headers(key, Origin="https://evil.example"), json=_init_body())
    assert r.status_code == 403
