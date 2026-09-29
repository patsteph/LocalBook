"""The extension's capture must answer before it does any model work.

2026-09-25: `POST /browser/capture` ran the whole pipeline — trafilatura, curator
scoring, the page summary (on the MAIN model, with 12k chars of prompt), the RAG ingest
and auto-tagging — BEFORE responding, so scraping a long article held the connection for
minutes behind a spinner. `api/web.py::quick_add` has done the opposite for a long time.
This module pins the new shape; `api/browser.py` had no tests at all before it, which is
how it drifted that far.

⚠️ Why these call the endpoint function instead of going through TestClient:
Starlette runs background tasks as part of the response lifecycle, so `client.post()`
does not return until they have finished. "No model work at response time" asserted
through TestClient would pass no matter what the code did — a test that proves nothing.
Calling the handler hands us the real `BackgroundTasks` object, so we can assert what is
IN it, and then run it deliberately as a second step.
"""
import asyncio
import uuid

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeSourceStore:
    def __init__(self):
        self.sources = {}

    async def create(self, notebook_id, filename, metadata):
        s = dict(metadata)
        s.setdefault("id", str(uuid.uuid4()))
        s["notebook_id"] = notebook_id
        s.setdefault("filename", filename)
        self.sources[s["id"]] = s
        return s

    async def update(self, notebook_id, source_id, patch):
        self.sources[source_id].update(patch)

    async def get(self, source_id):
        return self.sources.get(source_id)

    async def list(self, notebook_id):
        return [s for s in self.sources.values() if s.get("notebook_id") == notebook_id]


class FakeTool:
    """Stands in for a langchain @tool — callers use `.ainvoke({...})`."""
    def __init__(self, result, counter, key):
        self._result, self._counter, self._key = result, counter, key

    async def ainvoke(self, args):
        self._counter[self._key] += 1
        return self._result


@pytest.fixture()
def ctx(monkeypatch):
    calls = {"ingest": 0, "curator": 0, "tag": 0, "summary": 0}
    notifications = []
    jobs = []
    store = FakeSourceStore()
    ingest_raises = {"boom": False}

    monkeypatch.setattr("storage.source_store.source_store", store)

    async def fake_ingest(**kw):
        calls["ingest"] += 1
        if ingest_raises["boom"]:
            raise RuntimeError("lancedb is on fire")
        return {"chunks": 7}

    monkeypatch.setattr("services.rag_engine.rag_engine.ingest_document", fake_ingest)

    async def fake_score(**kw):
        calls["curator"] += 1
        return {
            "relevance_score": 0.91,
            "topics": ["local-first", "rag"],
            "entities": ["LocalBook"],
            "importance": "high",
        }

    monkeypatch.setattr("agents.curator.curator.score_user_item", fake_score)

    async def fake_tag(*a, **k):
        calls["tag"] += 1

    monkeypatch.setattr("services.auto_tagger.auto_tagger.tag_source_in_notebook", fake_tag)
    monkeypatch.setattr(
        "agents.tools.summarize_page_tool",
        FakeTool({"summary": "A summary.", "key_concepts": ["c1"]}, calls, "summary"),
    )

    async def fake_notify(payload):
        notifications.append(dict(payload))

    monkeypatch.setattr("api.browser.notify_source_updated", fake_notify)
    monkeypatch.setattr("api.browser.log_document_captured", lambda *a, **k: None)
    monkeypatch.setattr(
        "services.rag_metrics.rag_metrics.record_token_savings", lambda *a, **k: None)

    def fake_enqueue(job):
        # Snapshot the source's status AT ENQUEUE TIME — the image pass appends to the
        # document the ingest creates, so it must never be queued before that lands.
        src = store.sources.get(job.key.split(":")[-1], {})
        jobs.append((job, src.get("status")))

    monkeypatch.setattr("services.enrichment_worker.enrichment_worker.enqueue", fake_enqueue)

    return {"store": store, "calls": calls, "notifications": notifications,
            "jobs": jobs, "ingest_raises": ingest_raises}


def _request(**over):
    from api.browser import PageCaptureRequest
    body = {
        "url": "https://example.com/a-long-article",
        "title": "A Long Article",
        "content": " ".join(["word"] * 400),
        "notebook_id": "nb-1",
        "html_content": ("<html><body><article><p>" + ("word " * 400)
                         + "</p></article></body></html>"),
        "capture_type": "full_page",
    }
    body.update(over)
    return PageCaptureRequest(**body)


def _capture(request=None):
    """Await the handler with a real BackgroundTasks, exactly as the route does."""
    from api.browser import capture_page

    async def flow():
        tasks = BackgroundTasks()
        resp = await capture_page(request if request is not None else _request(), tasks)
        return resp, tasks

    return asyncio.run(flow())


def _run_background(tasks):
    """Run what the server runs once the response is on the wire."""
    asyncio.run(tasks())


# ── the regression that matters ──────────────────────────────────────────────

def test_capture_answers_before_any_model_work(ctx):
    from api.browser import _finish_web_capture_background

    resp, tasks = _capture()

    assert resp.success is True
    assert resp.status == "processing"
    assert resp.source_id
    assert resp.word_count > 0

    # The point of the whole change: nothing needing a model has run yet.
    assert ctx["calls"] == {"ingest": 0, "curator": 0, "tag": 0, "summary": 0}, (
        "capture responded only after doing model work — that is the minutes-long "
        "spinner this change exists to remove")
    assert ctx["store"].sources[resp.source_id]["status"] == "processing"

    # ...and it is all queued, exactly once.
    assert len(tasks.tasks) == 1
    assert tasks.tasks[0].func is _finish_web_capture_background


def test_the_background_pass_completes_the_source(ctx):
    resp, tasks = _capture()
    _run_background(tasks)

    src = ctx["store"].sources[resp.source_id]
    assert src["status"] == "completed"
    assert src["chunks"] == 7
    assert src["topics"] == ["local-first", "rag"]
    assert src["importance"] == "high"
    assert src["summary"] == "A summary."
    assert src["key_concepts"] == ["c1"]
    assert ctx["calls"] == {"ingest": 1, "curator": 1, "tag": 1, "summary": 1}


def test_the_image_pass_is_queued_after_the_ingest(ctx):
    """It appends to the document the ingest creates. Queued at request time — as it was
    before — that append could race the ingest it depends on."""
    _, tasks = _capture()
    _run_background(tasks)

    assert len(ctx["jobs"]) == 1
    job, status_when_queued = ctx["jobs"][0]
    assert job.label == "web-images"
    assert job.key.startswith("web-images:nb-1:")
    assert status_when_queued == "completed", (
        "the image pass was queued before the ingest finished")
    # A thunk, not a live coroutine — the worker cancels and re-runs jobs.
    assert callable(job.factory)


def test_a_failed_ingest_is_visible_not_stuck(ctx):
    """The one outcome this must never produce is a source sitting in `processing`."""
    ctx["ingest_raises"]["boom"] = True
    resp, tasks = _capture()
    _run_background(tasks)

    src = ctx["store"].sources[resp.source_id]
    assert src["status"] == "failed"
    assert "lancedb is on fire" in src["error"]
    assert any(n.get("status") == "failed" for n in ctx["notifications"])
    # Enrichment is skipped once the ingest fails — no point scoring an unindexed source.
    assert ctx["calls"]["curator"] == 0
    assert ctx["jobs"] == []


def test_an_enrichment_failure_leaves_the_source_usable(ctx, monkeypatch):
    """A summary is decoration; a searchable source is the product."""
    async def boom(**kw):
        raise RuntimeError("no model today")

    monkeypatch.setattr("agents.curator.curator.score_user_item", boom)
    resp, tasks = _capture()
    _run_background(tasks)

    src = ctx["store"].sources[resp.source_id]
    assert src["status"] == "completed"
    assert src["chunks"] == 7


def test_content_too_short_is_rejected_in_the_request(ctx):
    """The only failure the user must see immediately stays synchronous."""
    resp, tasks = _capture(_request(content="three words only", html_content=None))

    assert resp.success is False
    assert "too short" in (resp.error or "")
    assert tasks.tasks == []
    assert ctx["store"].sources == {}


# ── content loss ─────────────────────────────────────────────────────────────

def test_the_longer_extraction_wins(ctx):
    """trafilatura used to win on any result over 100 chars. So a long page whose HTML
    had been truncated by the extension's size cap could be ingested PARTIAL while the
    fuller text sat unused in `request.content`."""
    full_text = " ".join(f"sentence{i} of the real article" for i in range(200))
    truncated_html = "<html><body><article><p>only the opening paragraph survived</p>"

    resp, _ = _capture(_request(content=full_text, html_content=truncated_html))

    stored = ctx["store"].sources[resp.source_id]["content"]
    assert "sentence199" in stored, "the partial HTML extraction was preferred"
    assert resp.word_count > 200


def test_trafilatura_still_wins_when_it_extracts_more(ctx):
    """The other direction: the backend's extractor is the better one when it has the
    whole page, and that is why it is there at all."""
    rich_html = ("<html><body><article>"
                 + "".join(f"<p>Paragraph {i} with a real sentence in it.</p>"
                           for i in range(60))
                 + "</article></body></html>")

    resp, _ = _capture(_request(
        content="a thin innerText fallback of about ten words here", html_content=rich_html))

    stored = ctx["store"].sources[resp.source_id]["content"]
    assert "Paragraph 59" in stored
    assert resp.word_count > 100


# ── selection capture got the same treatment ─────────────────────────────────

def test_selection_capture_also_answers_first(ctx):
    from api.browser import capture_selection, SelectionCaptureRequest, \
        _finish_web_capture_background

    async def flow():
        tasks = BackgroundTasks()
        resp = await capture_selection(SelectionCaptureRequest(
            url="https://example.com/a",
            title="A Page",
            selected_text="A deliberately highlighted sentence worth keeping.",
            notebook_id="nb-1",
        ), tasks)
        return resp, tasks

    resp, tasks = asyncio.run(flow())
    assert resp.status == "processing"
    assert ctx["calls"]["curator"] == 0
    assert len(tasks.tasks) == 1
    assert tasks.tasks[0].func is _finish_web_capture_background

    _run_background(tasks)
    src = ctx["store"].sources[resp.source_id]
    assert src["status"] == "completed"
    # A highlight carries double curator weight and needs no summary of its own.
    assert tasks.tasks[0].kwargs["user_weight_bonus"] == 2.0
    assert tasks.tasks[0].kwargs["summarize"] is False
    assert ctx["calls"]["summary"] == 0
    assert ctx["jobs"] == [], "a selection has no page HTML, so no image pass"


# ── the endpoints the extension now calls ────────────────────────────────────

@pytest.fixture()
def client(ctx):
    from api import browser as browser_api
    app = FastAPI()
    app.include_router(browser_api.router)
    return TestClient(app)


def test_exists_answers_without_the_notebook(client, ctx):
    """Replaces a GET of /sources/{notebook}, which returns every source's FULL TEXT."""
    ctx["store"].sources["s1"] = {
        "id": "s1", "notebook_id": "nb-1", "url": "https://example.com/a/",
        "title": "Already Here", "status": "completed", "content": "x" * 100_000,
    }

    r = client.get("/browser/exists",
                   params={"notebook_id": "nb-1", "url": "https://example.com/a/"})
    assert r.status_code == 200
    assert r.json() == {"exists": True, "source_id": "s1", "title": "Already Here",
                        "status": "completed"}

    # Normalised match: a cleaned URL still finds a source stored with the raw one.
    r = client.get("/browser/exists",
                   params={"notebook_id": "nb-1", "url": "https://example.com/a#section"})
    assert r.json()["exists"] is True

    r = client.get("/browser/exists",
                   params={"notebook_id": "nb-1", "url": "https://example.com/never-seen"})
    assert r.json() == {"exists": False}

    # Wrong notebook is not a hit.
    r = client.get("/browser/exists",
                   params={"notebook_id": "nb-2", "url": "https://example.com/a/"})
    assert r.json() == {"exists": False}


def test_capture_status_is_cheap_and_scoped(client, ctx):
    ctx["store"].sources["s2"] = {
        "id": "s2", "notebook_id": "nb-1", "title": "T", "status": "completed",
        "chunks": 4, "word_count": 900, "topics": ["a"], "key_concepts": ["b"],
        "summary": "S", "content": "x" * 50_000,
    }

    r = client.get("/browser/capture-status/nb-1/s2")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["chunks"] == 4
    assert body["topics"] == ["a"]
    assert body["summary_present"] is True
    # Polled endpoint: it must not carry the document.
    assert "content" not in body and "html" not in body

    assert client.get("/browser/capture-status/nb-1/nope").status_code == 404
    # A source in another notebook is not readable through this notebook's path.
    assert client.get("/browser/capture-status/nb-2/s2").status_code == 404
