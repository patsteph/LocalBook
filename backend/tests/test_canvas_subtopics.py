"""CI-pure tests for Canvas Phase 2 — stable/accretive sub-topics.

Pure `cluster` (hand vectors), the topics store internals (in-memory sqlite), and the
`assign_and_persist` orchestrator with embeddings + LLM + store all faked (no I/O).
"""
import asyncio
import sqlite3

import pytest

from services import canvas_subtopics as cs


def test_cluster_assigns_seeds_and_orphans():
    vecs = [[1, 0, 0.02], [0, 1, 0.0], [0, 1, 0.02], [0, 0, 1]]
    r = cs.cluster(vecs, [{"id": "T1", "centroid": [1, 0, 0]}])
    assigned = {i for t in r for i in t["members"]}
    assert any(t["id"] == "T1" and t["members"] == [0] for t in r)   # accretes to existing
    assert any(t["is_new"] and sorted(t["members"]) == [1, 2] for t in r)  # new pair topic
    assert 3 not in assigned                                          # lone thread = orphan


def test_cluster_is_stable_on_rerun():
    vecs = [[1, 0, 0.02], [0, 1, 0.0], [0, 1, 0.02]]
    r1 = cs.cluster(vecs, [{"id": "T1", "centroid": [1, 0, 0]}])
    existing = [{"id": t["id"] or "N", "centroid": t["centroid"]} for t in r1]
    r2 = cs.cluster(vecs, existing)
    assert all(not t["is_new"] for t in r2)   # no thrash: everything reuses existing topics


def test_cluster_no_embedding_is_orphan():
    r = cs.cluster([[], [0, 0, 0]], [{"id": "T1", "centroid": [1, 0, 0]}])
    assert all(0 not in t["members"] for t in r)   # empty vec never assigned


# ── topics store (in-memory) ──────────────────────────────────────────────────
def _mem_conn():
    from storage import canvas_topics_store as ts
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ts._ensure_schema(c)
    return c


def test_topics_store_roundtrip_and_delete_missing():
    from storage import canvas_topics_store as ts
    c = _mem_conn()
    tid = ts._upsert_topic(c, "nb", {"id": "t1", "title": "Pipeline", "synthesis": "s",
                                     "centroid": [0.1, 0.2], "member_count": 3, "x": 10, "y": 20})
    assert tid == "t1"
    got = ts._list_topics(c, "nb")
    assert len(got) == 1 and got[0]["title"] == "Pipeline" and got[0]["centroid"] == [0.1, 0.2]
    assert got[0]["x"] == 10 and got[0]["collapsed"] is True
    # Update preserves geometry when not supplied.
    ts._upsert_topic(c, "nb", {"id": "t1", "title": "Pipeline v2", "centroid": [0.3], "member_count": 4})
    got = ts._list_topics(c, "nb")
    assert got[0]["title"] == "Pipeline v2" and got[0]["x"] == 10   # x preserved
    # delete_missing drops topics not in keep set.
    ts._upsert_topic(c, "nb", {"id": "t2", "title": "Other", "centroid": [1.0]})
    ts._delete_missing(c, "nb", ["t1"])
    assert [t["id"] for t in ts._list_topics(c, "nb")] == ["t1"]


def test_layout_topics_and_threads():
    from services import canvas_layout_topics as lt
    topics = [{"id": "t1", "title": "Pipeline", "synthesis": "s", "member_count": 3},
              {"id": "t2", "title": "Calendar", "synthesis": "s2", "member_count": 1}]
    nodes = [
        {"id": "n1", "kind": "chat_turn", "title": "Q", "topic_id": "t1", "created_at": "2026-08-01"},
        {"id": "n2", "kind": "artifact", "title": "Pod", "topic_id": "t1", "created_at": "2026-08-03"},
        {"id": "n3", "kind": "artifact", "title": "Quiz", "topic_id": "t1", "created_at": "2026-08-02"},
        {"id": "n4", "kind": "chat_turn", "title": "C", "topic_id": "t2", "created_at": "2026-08-01"},
        {"id": "n5", "kind": "chat_turn", "title": "orphan", "topic_id": None, "created_at": "2026-08-04"},
    ]
    out = lt.layout_topics_and_threads(topics, nodes)
    groups = {n["id"]: n for n in out if n["kind"] == "topic"}
    assert "topic:t1" in groups and groups["topic:t1"]["snapshot"]["type"] == "json:topic-card"
    assert groups["topic:t1"]["width"] > groups["topic:t2"]["width"]   # sized to member count
    t1 = sorted([n for n in out if n.get("parent_id") == "topic:t1"], key=lambda x: (x["y"], x["x"]))
    assert [n["id"] for n in t1] == ["n1", "n3", "n2"]                 # time-ordered inside the card
    orphan = [n for n in out if n["id"] == "n5"][0]
    assert not orphan.get("parent_id")                                # orphan stands alone


def test_assign_and_persist_stamps_topic_ids(monkeypatch):
    from storage import canvas_topics_store as ts
    from services.ollama_service import ollama_service

    store: list = []
    monkeypatch.setattr(ts, "list_topics", lambda nb: list(store))
    monkeypatch.setattr(ts, "upsert_topic", lambda nb, t: (store.append(dict(t)) or t["id"]))
    monkeypatch.setattr(ts, "delete_missing", lambda nb, keep: None)

    async def _fake_embed(texts):
        # node 0,1 similar (a topic); node 2 distinct (orphan)
        return [[1.0, 0.0], [0.98, 0.02], [0.0, 1.0]]
    monkeypatch.setattr(ollama_service, "embed_batch", _fake_embed)

    async def _fake_title(titles):
        return ("Synth Topic", "one line")
    monkeypatch.setattr(cs, "_title_synthesis", _fake_title)

    nodes = [
        {"id": "n0", "title": "A", "snapshot": {"type": "markdown", "payload": "alpha"}},
        {"id": "n1", "title": "B", "snapshot": {"type": "markdown", "payload": "alpha-ish"}},
        {"id": "n2", "title": "C", "snapshot": {"type": "markdown", "payload": "zeta"}},
    ]
    surviving = asyncio.run(cs.assign_and_persist("nb", nodes))
    # n0+n1 share one topic; n2 is an orphan (no topic_id).
    assert nodes[0]["topic_id"] and nodes[0]["topic_id"] == nodes[1]["topic_id"]
    assert not nodes[2].get("topic_id")
    assert len(surviving) == 1 and surviving[0]["title"] == "Synth Topic"
    assert surviving[0]["member_count"] == 2
