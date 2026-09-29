"""LB-2: the MCP server at /mcp.

Covers the guards rather than the retrieval: every tool is a thin wrapper, so
testing that `search_notebooks` finds the right passages would be testing
`cross_notebook_search` in the wrong file. What is LB-2's own is who may call,
what they may call, how much they may ask for, and what gets written down.

Tools are exercised through the registered FastMCP functions, with the caller
contextvar set the way the middleware sets it, and the underlying services
faked. The middleware itself is driven as a raw ASGI app.
"""

import asyncio
import json

import pytest

from services import companion_audit, companion_keys, mcp_server
from services.companion_keys import CompanionIdentity


@pytest.fixture
def store(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


@pytest.fixture
def caller(monkeypatch):
    """Pretend the middleware authenticated a companion holding `mcp`."""
    identity = CompanionIdentity(companion_id="jocasta", scopes=("mcp",))
    mcp_server._set_caller(identity)
    return identity


@pytest.fixture
def tools():
    async def _get():
        server = mcp_server.build_server()
        return await server.get_tools()

    return asyncio.run(_get())


def _call(tools, name, **kwargs):
    return asyncio.run(tools[name].fn(**kwargs))


# ── the tools exist and say they are read-only ──────────────────────────────


def test_the_v1_tools_are_registered(tools):
    assert {
        "list_notebooks",
        "search_notebooks",
        "ask_notebook",
        "get_source",
        "web_search",
        "fetch_page",
    } <= set(tools)


# The only tool that is not read-only. Kept as a constant so adding a second
# write tool has to be a deliberate edit here, not a silent test pass.
WRITE_TOOLS = {"propose_note"}


def test_every_tool_declares_read_only_explicitly(tools):
    """An agent deciding whether it may call something unattended should read a
    `False`, not infer one from a missing annotation."""
    for name, tool in tools.items():
        assert tool.annotations is not None, name
        expected = name not in WRITE_TOOLS
        assert tool.annotations.readOnlyHint is expected, name


def test_propose_note_is_the_only_write_tool(tools):
    """Writes are proposals (LB-2). If this list grows, it should hurt."""
    writes = {n for n, t in tools.items()
              if t.annotations and t.annotations.readOnlyHint is False}
    assert writes == WRITE_TOOLS


def test_proposing_is_not_marked_destructive(tools):
    """It adds to a review queue; it changes nothing the user already has."""
    assert tools["propose_note"].annotations.destructiveHint is False


# ── auth: loopback, key, scope ──────────────────────────────────────────────


def _run_asgi(app, *, client=("127.0.0.1", 5000), headers=None):
    """Drive the ASGI middleware directly and collect its response."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "client": client,
        "headers": headers or [],
    }
    asyncio.run(app(scope, receive, send))
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, (json.loads(body) if body else {})


class _Reached(Exception):
    pass


def _guarded(passthrough=None):
    async def inner(scope, receive, send):
        raise _Reached()

    return mcp_server.CompanionAuthMiddleware(passthrough or inner)


def test_a_non_loopback_client_is_refused(store):
    status, body = _run_asgi(_guarded(), client=("192.168.1.50", 5000))
    assert status == 403
    assert "this machine only" in body["detail"]


def test_no_key_is_a_401(store):
    status, body = _run_asgi(_guarded())
    assert status == 401
    assert "Companions" in body["detail"]


def test_a_wrong_key_is_a_401(store):
    status, _ = _run_asgi(
        _guarded(), headers=[(b"authorization", b"Bearer lb-not-a-key")]
    )
    assert status == 401


def test_a_key_without_the_mcp_scope_is_a_403(store):
    """The meeting recorder holds `llm`. It must not reach the notebooks."""
    key = companion_keys.issue("meeting-notes", ["llm"])
    status, body = _run_asgi(
        _guarded(), headers=[(b"authorization", f"Bearer {key}".encode())]
    )
    assert status == 403
    assert "mcp" in body["detail"]
    assert "meeting-notes" in body["detail"]


def test_a_403_for_a_missing_scope_is_audited(store, monkeypatch):
    rows = []
    monkeypatch.setattr(companion_audit, "record", lambda **kw: rows.append(kw))
    key = companion_keys.issue("meeting-notes", ["llm"])
    _run_asgi(_guarded(), headers=[(b"authorization", f"Bearer {key}".encode())])
    assert rows and rows[0]["outcome"] == companion_audit.OUTCOME_DENIED


def test_a_scoped_key_gets_through_and_sets_the_caller(store):
    key = companion_keys.issue("jocasta", ["mcp"])
    seen = {}

    async def capture(scope, receive, send):
        # Read it HERE: the caller is a contextvar, and asyncio.run copies the
        # context, so it is gone again by the time the test regains control.
        seen["caller"] = mcp_server.current_caller()

    app = mcp_server.CompanionAuthMiddleware(capture)
    _run_asgi(app, headers=[(b"authorization", f"Bearer {key}".encode())])

    assert seen["caller"].companion_id == "jocasta"
    assert seen["caller"].has("mcp")


def test_a_revoked_key_stops_working(store):
    key = companion_keys.issue("jocasta", ["mcp"])
    companion_keys.revoke("jocasta")
    status, _ = _run_asgi(
        _guarded(), headers=[(b"authorization", f"Bearer {key}".encode())]
    )
    assert status == 401


# ── bounds ──────────────────────────────────────────────────────────────────


def test_k_is_clamped_to_a_ceiling():
    assert mcp_server._bounded_k(10_000, 8) == mcp_server.MAX_K
    assert mcp_server._bounded_k(0, 8) == 1
    assert mcp_server._bounded_k(-5, 8) == 1
    assert mcp_server._bounded_k(None, 8) == 8
    assert mcp_server._bounded_k("nonsense", 8) == 8
    assert mcp_server._bounded_k(12, 8) == 12


def test_max_chars_is_clamped_to_a_ceiling():
    assert mcp_server._bounded_chars(10_000_000) == mcp_server.MAX_CHARS
    assert mcp_server._bounded_chars(1) == 200
    assert mcp_server._bounded_chars(None) == mcp_server.DEFAULT_CHARS


def test_search_cannot_be_asked_for_the_whole_corpus(store, caller, tools, monkeypatch):
    seen = {}

    class FakeSearch:
        async def search(self, query, notebook_ids=None, top_k=5):
            seen["top_k"] = top_k
            return {"results": [], "notebooks_searched": 0}

    monkeypatch.setattr("services.cross_notebook_search.cross_notebook_search", FakeSearch())
    _call(tools, "search_notebooks", query="x", k=100_000)
    assert seen["top_k"] == mcp_server.MAX_K


# ── get_source pagination ───────────────────────────────────────────────────


def _fake_source_store(monkeypatch, source):
    class FakeStore:
        async def get(self, source_id):
            return source if source_id == "s1" else None

    monkeypatch.setattr("storage.source_store.source_store", FakeStore())


def test_get_source_pages_through_a_long_document(store, caller, tools, monkeypatch):
    _fake_source_store(monkeypatch, {"id": "s1", "title": "Long", "content": "A" * 25_000})

    first = _call(tools, "get_source", source_id="s1", max_chars=10_000)
    assert len(first["text"]) == 10_000
    assert first["total_chars"] == 25_000
    assert first["next_offset"] == 10_000

    last = _call(tools, "get_source", source_id="s1", offset=20_000, max_chars=10_000)
    assert len(last["text"]) == 5_000
    assert last["next_offset"] is None


def test_get_source_on_a_missing_id_is_an_error_not_a_crash(store, caller, tools, monkeypatch):
    _fake_source_store(monkeypatch, {"id": "s1", "content": "x"})
    assert "error" in _call(tools, "get_source", source_id="nope")


# ── busy handling ───────────────────────────────────────────────────────────


def test_ask_notebook_returns_busy_when_the_model_is_held(store, caller, tools, monkeypatch):
    async def held(model):
        return False

    monkeypatch.setattr(mcp_server, "_model_is_available", held)
    out = _call(tools, "ask_notebook", question="what did I save about X?")
    assert out["status"] == "busy"
    assert out["retry_after"] == mcp_server.BUSY_RETRY_AFTER


def test_a_busy_answer_is_audited_as_busy(store, caller, tools, monkeypatch):
    rows = []
    monkeypatch.setattr(companion_audit, "record", lambda **kw: rows.append(kw))

    async def held(model):
        return False

    monkeypatch.setattr(mcp_server, "_model_is_available", held)
    _call(tools, "ask_notebook", question="x")
    assert rows[-1]["outcome"] == companion_audit.OUTCOME_BUSY
    assert rows[-1]["tool"] == "ask_notebook"


def test_the_busy_probe_yields_to_the_person_at_the_machine(store, monkeypatch):
    """A companion must acquire the lane at BACKGROUND priority, never ahead of
    foreground chat."""
    seen = {}

    class FakeLane:
        async def acquire(self, priority):
            seen["priority"] = priority
            return True

        def release(self):
            seen["released"] = True

    monkeypatch.setattr("services.llm_runtime._semaphore_for_model", lambda m: FakeLane())
    from services.llm_runtime import PRIORITY_BACKGROUND

    assert asyncio.run(mcp_server._model_is_available("gemma")) is True
    assert seen["priority"] == PRIORITY_BACKGROUND
    assert seen["released"] is True


def test_a_probe_that_times_out_reports_busy(store, monkeypatch):
    class StuckLane:
        async def acquire(self, priority):
            await asyncio.sleep(10)

        def release(self):
            pass

    monkeypatch.setattr("services.llm_runtime._semaphore_for_model", lambda m: StuckLane())
    monkeypatch.setattr(mcp_server, "BUSY_TIMEOUT_SECONDS", 0.05)
    assert asyncio.run(mcp_server._model_is_available("gemma")) is False


# ── fetch_page is SSRF-guarded ──────────────────────────────────────────────


def test_fetch_page_refuses_localbooks_own_api(store, caller, tools):
    out = _call(tools, "fetch_page", url="http://127.0.0.1:8000/notebooks")
    assert "refused" in out["error"]
    assert "loopback" in out["error"]


def test_a_refused_fetch_is_audited_as_denied(store, caller, tools, monkeypatch):
    rows = []
    monkeypatch.setattr(companion_audit, "record", lambda **kw: rows.append(kw))
    _call(tools, "fetch_page", url="file:///etc/passwd")
    assert rows[-1]["outcome"] == companion_audit.OUTCOME_DENIED


def test_fetch_page_never_reaches_the_scraper_when_refused(store, caller, tools, monkeypatch):
    reached = []

    class Boom:
        async def scrape_urls(self, urls):
            reached.append(urls)
            return [{}]

    monkeypatch.setattr("services.web_scraper.web_scraper", Boom())
    _call(tools, "fetch_page", url="http://192.168.1.1/admin")
    assert reached == []


# ── the audit row ───────────────────────────────────────────────────────────


def test_a_successful_call_writes_one_audit_row(store, caller, tools, monkeypatch):
    rows = []
    monkeypatch.setattr(companion_audit, "record", lambda **kw: rows.append(kw))

    class FakeNotebooks:
        async def list(self):
            return [{"id": "nb1", "title": "Work", "source_count": 3}]

    monkeypatch.setattr("storage.notebook_store.notebook_store", FakeNotebooks())
    out = _call(tools, "list_notebooks")

    assert out["notebooks"][0]["title"] == "Work"
    assert len(rows) == 1
    assert rows[0]["companion_id"] == "jocasta"
    assert rows[0]["tool"] == "list_notebooks"
    assert rows[0]["outcome"] == companion_audit.OUTCOME_OK


def test_a_raising_tool_still_writes_a_row(store, caller, tools, monkeypatch):
    rows = []
    monkeypatch.setattr(companion_audit, "record", lambda **kw: rows.append(kw))

    class Exploding:
        async def list(self):
            raise RuntimeError("store is down")

    monkeypatch.setattr("storage.notebook_store.notebook_store", Exploding())
    with pytest.raises(RuntimeError):
        _call(tools, "list_notebooks")

    assert rows[-1]["outcome"] == companion_audit.OUTCOME_ERROR
    assert "store is down" in rows[-1]["detail"]


def test_arguments_are_hashed_never_stored(store, caller, tools, monkeypatch):
    """A tool call carries the user's own questions. They must not accumulate in
    a plaintext log that LB-11 does not cover and LB-10 will back up.

    Asserted on the PERSISTED row, not on the call into `record` — `record` is
    handed the arguments precisely so it can hash them, so intercepting it would
    test the wrong layer and pass while the database filled with plaintext.
    """
    companion_audit.purge()
    secret = "what did the oncologist say about the biopsy"

    async def held(model):
        return False

    monkeypatch.setattr(mcp_server, "_model_is_available", held)
    _call(tools, "ask_notebook", question=secret)

    rows = companion_audit.recent("jocasta")
    assert rows, "the call was not audited at all"
    blob = json.dumps(rows[0], default=str)
    assert secret not in blob
    assert "oncologist" not in blob
    assert len(rows[0]["args_hash"]) == 32
    assert rows[0]["args_preview"] == "notebook_id,question,top_k"
    assert rows[0]["outcome"] == companion_audit.OUTCOME_BUSY
    companion_audit.purge()


# ── the audit store itself ──────────────────────────────────────────────────


def test_the_audit_log_round_trips(store):
    companion_audit.purge()
    companion_audit.record(
        companion_id="jocasta", tool="ask_notebook",
        args={"question": "a private question"}, ms=42,
    )
    rows = companion_audit.recent("jocasta")
    assert rows[0]["tool"] == "ask_notebook"
    assert rows[0]["ms"] == 42
    assert "a private question" not in json.dumps(rows[0])
    assert rows[0]["args_preview"] == "question"
    companion_audit.purge("jocasta")


def test_the_same_call_hashes_the_same_regardless_of_key_order():
    assert companion_audit.hash_args({"a": 1, "b": 2}) == companion_audit.hash_args({"b": 2, "a": 1})
    assert companion_audit.hash_args({"a": 1}) != companion_audit.hash_args({"a": 2})


def test_purging_one_companion_leaves_the_other(store):
    companion_audit.purge()
    companion_audit.record(companion_id="jocasta", tool="t")
    companion_audit.record(companion_id="meeting-notes", tool="t")
    companion_audit.purge("jocasta")
    left = {r["companion_id"] for r in companion_audit.recent()}
    assert left == {"meeting-notes"}
    companion_audit.purge()


def test_an_audit_failure_does_not_fail_the_call(store, monkeypatch):
    """The log must never become a new way for the feature to break."""
    monkeypatch.setattr(
        companion_audit, "ensure_table",
        lambda: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    companion_audit.record(companion_id="jocasta", tool="t")  # must not raise


def test_the_bundle_collects_fastmcps_binary_dependencies():
    """Found the hard way, 2026-09-29: the first bundled run failed with
    `No module named 'lupa.lua51'` and MCP did not start.

    fastmcp -> pydocket -> fakeredis -> lupa, and lupa.lua51 is a compiled
    .so submodule. PyInstaller's static analysis does not follow that, so
    --collect-all=fastmcp alone is not enough. Everything imported fine in the
    venv and failed only in the bundle — which is why this assertion exists
    next to tests that actually import fastmcp.
    """
    from pathlib import Path

    import fastmcp  # the functional half: it must really import

    assert fastmcp.__version__

    build_sh = Path(__file__).resolve().parents[1] / "build_backend.sh"
    contents = build_sh.read_text()
    for flag in ("--collect-all=fastmcp", "--collect-all=mcp",
                 "--collect-all=fakeredis", "--collect-all=lupa"):
        assert flag in contents, f"{flag} missing from build_backend.sh"


# ── curator / queue / digest ────────────────────────────────────────────────


def test_curator_insights_does_not_mark_anything_surfaced(store, caller, tools, monkeypatch):
    """An agent glancing at an insight is not the user having seen it. If this
    ever starts marking, the morning brief quietly stops showing things."""
    calls = []

    class FakeBrain:
        def get_active_insights(self, limit):
            calls.append(("insights", limit))
            return [{"id": 1, "text": "you keep returning to X"}]

        def get_unsurfaced_reflections(self, limit):
            return [{"id": 9, "text": "a question worth asking"}]

        def mark_insight_surfaced(self, insight_id):
            calls.append(("MARKED", insight_id))

    monkeypatch.setattr("services.curator_brain.curator_brain", FakeBrain())
    out = _call(tools, "curator_insights", k=5)

    assert out["insights"][0]["text"] == "you keep returning to X"
    assert out["reflections"][0]["id"] == 9
    assert not any(c[0] == "MARKED" for c in calls)


def test_curator_insights_is_bounded(store, caller, tools, monkeypatch):
    seen = {}

    class FakeBrain:
        def get_active_insights(self, limit):
            seen["limit"] = limit
            return []

        def get_unsurfaced_reflections(self, limit):
            return []

    monkeypatch.setattr("services.curator_brain.curator_brain", FakeBrain())
    _call(tools, "curator_insights", k=99_999)
    assert seen["limit"] == mcp_server.MAX_K


def test_approval_queue_is_read_only_and_bounded(store, caller, tools, monkeypatch):
    """Approving stays in the UI — taking a source into the corpus is the
    user's decision, not an agent's."""
    class FakeCollector:
        def get_pending_approvals(self):
            return [{"id": f"i{i}"} for i in range(30)]

        def get_expiring_soon(self, days):
            return [{"id": "i0"}]

    monkeypatch.setattr("agents.collector.get_collector", lambda nb: FakeCollector())
    out = _call(tools, "approval_queue", notebook_id="nb1", k=5)

    assert len(out["pending"]) == 5
    assert out["total"] == 30
    assert out["truncated"] is True
    assert out["expiring_soon"] == 1
    assert not hasattr(FakeCollector, "approve")


def test_todays_digest_for_one_notebook_and_for_all(store, caller, tools, monkeypatch):
    class FakeBrain:
        def get_digest(self, notebook_id):
            return {"notebook_id": notebook_id, "summary": "s"} if notebook_id == "nb1" else None

        def get_all_digests(self):
            return [{"notebook_id": "nb1"}, {"notebook_id": "nb2"}]

    monkeypatch.setattr("services.curator_brain.curator_brain", FakeBrain())
    assert _call(tools, "todays_digest", notebook_id="nb1")["digest"]["summary"] == "s"
    assert "error" in _call(tools, "todays_digest", notebook_id="missing")
    assert len(_call(tools, "todays_digest")["digests"]) == 2


# ── propose_note: the one write path ────────────────────────────────────────


class _RecordingCollector:
    def __init__(self, outcome="queued"):
        self.outcome = outcome
        self.items = []

    async def _add_to_approval_queue(self, item):
        self.items.append(item)
        return self.outcome


def test_a_proposal_is_queued_never_added_directly(store, caller, tools, monkeypatch):
    """The whole contract: an agent cannot put anything into the corpus."""
    collector = _RecordingCollector("queued")
    monkeypatch.setattr("agents.collector.get_collector", lambda nb: collector)

    out = _call(tools, "propose_note", notebook_id="nb1",
                title="A thought", body="the body of it")

    assert out["outcome"] == "queued"
    assert "waiting for the user" in out["detail"]
    assert len(collector.items) == 1


def test_a_proposal_goes_through_the_same_queue_as_a_discovered_article(
    store, caller, tools, monkeypatch
):
    """It must not bypass Curator pre-triage — `_add_to_approval_queue` is
    where that happens, so the proposal has to enter there."""
    collector = _RecordingCollector()
    monkeypatch.setattr("agents.collector.get_collector", lambda nb: collector)
    _call(tools, "propose_note", notebook_id="nb1", title="T", body="B")
    assert collector.items, "the proposal skipped the approval queue entirely"


def test_a_proposal_is_attributed_to_the_companion(store, caller, tools, monkeypatch):
    """Whoever reviews the queue needs to know an agent proposed it, rather
    than it being laundered in as a web find."""
    collector = _RecordingCollector()
    monkeypatch.setattr("agents.collector.get_collector", lambda nb: collector)

    _call(tools, "propose_note", notebook_id="nb1", title="T", body="B")

    assert "jocasta" in collector.items[0].source_name
    assert collector.items[0].source_type == "manual"


def test_an_explicit_source_is_kept(store, caller, tools, monkeypatch):
    collector = _RecordingCollector()
    monkeypatch.setattr("agents.collector.get_collector", lambda nb: collector)
    _call(tools, "propose_note", notebook_id="nb1", title="T", body="B",
          source="from the Tuesday call")
    assert collector.items[0].source_name == "from the Tuesday call"


def test_a_curator_rejection_is_reported_and_audited_as_denied(
    store, caller, tools, monkeypatch
):
    rows = []
    monkeypatch.setattr(companion_audit, "record", lambda **kw: rows.append(kw))
    monkeypatch.setattr("agents.collector.get_collector",
                        lambda nb: _RecordingCollector("rejected"))

    out = _call(tools, "propose_note", notebook_id="nb1", title="T", body="B")

    assert out["outcome"] == "rejected"
    assert "Curator declined" in out["detail"]
    assert rows[-1]["outcome"] == companion_audit.OUTCOME_DENIED


def test_an_empty_proposal_is_refused_before_reaching_the_queue(
    store, caller, tools, monkeypatch
):
    collector = _RecordingCollector()
    monkeypatch.setattr("agents.collector.get_collector", lambda nb: collector)

    assert "error" in _call(tools, "propose_note", notebook_id="nb1", title="", body="B")
    assert "error" in _call(tools, "propose_note", notebook_id="nb1", title="T", body="   ")
    assert collector.items == []


def test_the_proposal_body_is_not_stored_in_the_audit_log(
    store, caller, tools, monkeypatch
):
    """A proposal can carry anything the agent read. Only the argument NAMES
    and a hash go in the log."""
    companion_audit.purge()
    monkeypatch.setattr("agents.collector.get_collector", lambda nb: _RecordingCollector())

    secret = "the confidential thing from the meeting"
    _call(tools, "propose_note", notebook_id="nb1", title="T", body=secret)

    rows = companion_audit.recent("jocasta")
    assert secret not in json.dumps(rows[0], default=str)
    assert "body" not in (rows[0]["args_preview"] or "")
    companion_audit.purge()


# ── the mount, end to end ───────────────────────────────────────────────────
#
# Unit tests above drive the middleware and the tool functions separately. This
# section proves they work MOUNTED, with the MCP app's own lifespan running —
# the integration the plan warned about, where a mounted app accepts a
# connection and then fails at runtime because its session manager never
# started. A minimal host app is used rather than main.app on purpose: booting
# LocalBook would run its startup tasks against the production data dir.


@pytest.fixture
def mounted(store):
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    mcp_server._app = None          # a fresh server per test
    mcp_server._mcp = None

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_server.lifespan_context(app):
            yield

    host = FastAPI(lifespan=lifespan)
    host.mount("/mcp", mcp_server.get_app())
    with TestClient(host) as client:
        yield client

    mcp_server._app = None
    mcp_server._mcp = None


def _initialize(client, key):
    return client.post(
        "/mcp/",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
    )


def test_the_mounted_endpoint_rejects_an_unauthenticated_call(mounted):
    r = mounted.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401


def test_the_mounted_endpoint_rejects_a_key_without_the_mcp_scope(mounted, store):
    key = companion_keys.issue("meeting-notes", ["llm"])
    r = _initialize(mounted, key)
    assert r.status_code == 403


def test_an_mcp_client_can_initialize_and_list_the_tools(mounted, store):
    """The plan's 'done when': the tools are listable over the real transport.

    If the MCP app's lifespan had not been chained into the host's, this is
    where it would fail — the session manager would never have started.
    """
    key = companion_keys.issue("jocasta", ["mcp"])

    r = _initialize(mounted, key)
    assert r.status_code == 200, r.text
    assert "LocalBook" in r.text

    session = r.headers.get("mcp-session-id")
    assert session, "no session id — the session manager is not running"

    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "mcp-session-id": session,
    }
    mounted.post("/mcp/", headers=headers,
                 json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    listed = mounted.post("/mcp/", headers=headers,
                          json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed.status_code == 200, listed.text
    for tool in ("list_notebooks", "search_notebooks", "ask_notebook",
                 "get_source", "web_search", "fetch_page"):
        assert tool in listed.text, f"{tool} not listed"
