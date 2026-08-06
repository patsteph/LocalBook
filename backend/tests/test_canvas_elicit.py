"""CI-pure tests for the P4 orphan intent-elicitation loop (POST /canvas/elicit).

Mounts the canvas router on a bare FastAPI app; both stores share one in-memory sqlite; the
embed call + the enrichment enqueue are stubbed so nothing touches the model or the worker.
Verifies: intent is stored, a matching orphan JOINS the nearest sub-topic, suggestions are
returned, a research job is enqueued (coalescing key), and the validation/404 paths.
"""
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from storage import canvas_layout_store as cl
from storage import canvas_topics_store as ts
from services import canvas_subtopics
from services.ollama_service import ollama_service
from api import canvas as canvas_api


@pytest.fixture()
def ctx(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cl._ensure_schema(conn)
    ts._ensure_schema(conn)
    monkeypatch.setattr(cl, "_get_conn", lambda: conn)
    monkeypatch.setattr(ts, "_get_conn", lambda: conn)

    # Deterministic embedding: the elicited thread embeds to [1, 0] (aligned with topic "T1").
    async def _fake_embed(texts, *a, **k):
        return [[1.0, 0.0] for _ in texts]
    monkeypatch.setattr(ollama_service, "embed_batch", _fake_embed)

    # Capture enqueued research jobs instead of running the worker.
    jobs = []
    monkeypatch.setattr("services.enrichment_worker.enrichment_worker.enqueue",
                        lambda job: jobs.append(job))

    app = FastAPI()
    app.include_router(canvas_api.router)
    client = TestClient(app)
    return client, jobs


def _seed_orphan(client):
    client.put("/canvas/layout/nb1", json={
        "nodes": [{"id": "n1", "x": 0, "y": 0, "kind": "chat_turn", "title": "orphan thread"}],
        "edges": [],
    })


def test_elicit_joins_nearest_topic(ctx):
    client, jobs = ctx
    _seed_orphan(client)
    ts.upsert_topic("nb1", {"id": "T1", "title": "Alpha", "synthesis": "s",
                            "centroid": [1.0, 0.0], "member_count": 2})

    r = client.post("/canvas/elicit/nb1/n1", json={"intent": "how vaccines train immunity"})
    assert r.status_code == 200
    body = r.json()

    # (b) joined the aligned topic + returned it as the top suggestion
    assert body["assigned_topic_id"] == "T1"
    assert body["suggestions"] and body["suggestions"][0]["id"] == "T1"

    # (a) intent stored + node no longer an orphan in the returned layout
    node = next(n for n in body["layout"]["nodes"] if n["id"] == "n1")
    assert node["intent"] == "how vaccines train immunity"
    assert node["topic_id"] == "T1" and node["parent_id"] == "T1"

    # topic absorbed the thread (member_count bumped, centroid still aligned)
    t1 = next(t for t in ts.list_topics("nb1") if t["id"] == "T1")
    assert t1["member_count"] == 3

    # (c) exactly one AWAY-gated research job enqueued, keyed for coalescing
    assert len(jobs) == 1
    assert jobs[0].key == "elicit-research:nb1:n1"
    assert jobs[0].tier == __import__("services.enrichment_jobs", fromlist=["JobTier"]).JobTier.NIGHT


def test_elicit_no_topics_stays_orphan_but_stores_intent(ctx):
    client, jobs = ctx
    _seed_orphan(client)  # no topics exist yet

    r = client.post("/canvas/elicit/nb1/n1", json={"intent": "quantum error correction"})
    assert r.status_code == 200
    body = r.json()
    assert body["assigned_topic_id"] is None
    assert body["suggestions"] == []
    node = next(n for n in body["layout"]["nodes"] if n["id"] == "n1")
    assert node["intent"] == "quantum error correction"
    assert node["topic_id"] is None  # a lone thread can't seed a topic → stays an orphan
    assert len(jobs) == 1  # research still enqueued


def test_elicit_validation_and_missing_node(ctx):
    client, _ = ctx
    _seed_orphan(client)
    assert client.post("/canvas/elicit/nb1/n1", json={"intent": "   "}).status_code == 422
    assert client.post("/canvas/elicit/nb1/ghost", json={"intent": "x"}).status_code == 404


def test_reassign_one_below_threshold_no_join(ctx, monkeypatch):
    client, _ = ctx
    # Orthogonal centroid → cosine 0 < ASSIGN_THRESHOLD → suggestion returned but no join.
    ts.upsert_topic("nb1", {"id": "T2", "title": "Beta", "synthesis": "",
                            "centroid": [0.0, 1.0], "member_count": 4})
    node = {"id": "nX", "title": "orphan", "snapshot": {}}
    import asyncio
    res = asyncio.run(canvas_subtopics.reassign_one("nb1", node, extra_text="unrelated"))
    assert res["topic_id"] is None
    assert res["suggestions"] and res["suggestions"][0]["id"] == "T2"
    assert ts.list_topics("nb1")[0]["member_count"] == 4  # unchanged
