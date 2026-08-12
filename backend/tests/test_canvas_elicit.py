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


# ── P4 idle orphan-surfacing (canvas_idle_research._surface_top_orphan / _research_top_orphan) ──
from services import canvas_idle_research as cir


def _layout(nodes):
    return {"nodes": nodes, "edges": []}


def _orphan(node_id, title, **kw):
    n = {"id": node_id, "title": title, "topic_id": None, "ref_type": "source", "snapshot": {}}
    n.update(kw)
    return n


def test_surface_picks_orphan_with_genuine_partner():
    layout = _layout([
        _orphan("orphan1", "reinforcement learning"),
        _orphan("assigned1", "policy gradients", topic_id="T1"),  # already assigned → the partner
    ])
    candidates = [{"a_node": "orphan1", "b_node": "assigned1", "score": 0.8, "signal": "related"}]
    pick = cir._surface_top_orphan(layout, candidates)
    assert pick is not None
    orphan, partner, query = pick
    assert orphan["id"] == "orphan1" and partner == "assigned1"
    assert query == "reinforcement learning"


def test_surface_skips_assigned_topicnode_and_already_surfaced():
    layout = _layout([
        _orphan("assigned", "x", topic_id="T1"),                       # not an orphan (assigned)
        _orphan("topicnode", "Group", ref_type="topic"),              # topic-group node, never an orphan
        _orphan("surfaced", "y", snapshot={"research_insight": "old"}),  # already surfaced
        _orphan("partner", "p", topic_id="T1"),
    ])
    candidates = [
        {"a_node": "assigned", "b_node": "partner", "score": 0.9},
        {"a_node": "topicnode", "b_node": "partner", "score": 0.9},
        {"a_node": "surfaced", "b_node": "partner", "score": 0.9},
    ]
    assert cir._surface_top_orphan(layout, candidates) is None


def test_surface_requires_genuine_partner_and_score():
    # Both endpoints orphans (no assigned partner) → skip; and a below-threshold pair → skip.
    layout = _layout([_orphan("o1", "t"), _orphan("o2", "u"), _orphan("a1", "p", topic_id="T1")])
    assert cir._surface_top_orphan(
        layout, [{"a_node": "o1", "b_node": "o2", "score": 0.9}]) is None
    assert cir._surface_top_orphan(
        layout, [{"a_node": "o1", "b_node": "a1", "score": 0.5}]) is None  # < IDLE_MIN_SCORE


def test_research_top_orphan_stashes_insight_and_edge(monkeypatch):
    import asyncio
    layout = _layout([
        _orphan("orphan1", "reinforcement learning"),
        _orphan("assigned1", "policy gradients", topic_id="T1"),
    ])
    candidates = [{"a_node": "orphan1", "b_node": "assigned1", "score": 0.8}]

    class _Result:
        title, snippet, url = "RL explained", "a short overview", "http://x/rl"
    async def _search(q, nb, max_results=3):
        return [_Result()]
    monkeypatch.setattr("services.research_engine.research_engine.web_search", _search)

    patches, edges = [], []
    monkeypatch.setattr("storage.canvas_layout_store.patch_node_snapshot",
                        lambda nb, nid, patch: patches.append((nid, patch)) or True)
    monkeypatch.setattr("storage.canvas_layout_store.upsert_edge",
                        lambda nb, edge: edges.append(edge) or {"id": "e"})

    ok = asyncio.run(cir._research_top_orphan("nb1", layout, candidates))
    assert ok is True
    # insight stashed on the ORPHAN node (drives the frontend chip)
    assert patches and patches[0][0] == "orphan1" and "research_insight" in patches[0][1]
    # a `researched` edge points orphan → genuine partner
    assert edges and edges[0]["source"] == "orphan1" and edges[0]["target"] == "assigned1"
    assert edges[0]["state"] == "researched"


def test_research_top_orphan_no_results_is_noop(monkeypatch):
    import asyncio
    layout = _layout([_orphan("orphan1", "t"), _orphan("assigned1", "p", topic_id="T1")])
    candidates = [{"a_node": "orphan1", "b_node": "assigned1", "score": 0.8}]
    async def _empty(q, nb, max_results=3):
        return []
    monkeypatch.setattr("services.research_engine.research_engine.web_search", _empty)
    wrote = []
    monkeypatch.setattr("storage.canvas_layout_store.patch_node_snapshot",
                        lambda *a, **k: wrote.append(1) or True)
    assert asyncio.run(cir._research_top_orphan("nb1", layout, candidates)) is False
    assert not wrote  # nothing stashed when research yields nothing
