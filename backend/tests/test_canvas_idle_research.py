"""CI-pure tests for the Journey Canvas idle-research pass (2.2.0 Walk W5).

Two layers, no live model / KG / network / production writes:
  1. The PURE selection core (`select_new_curator_edges`, `best_partner_for`).
  2. The async orchestration (`enrich_notebook`, `run_idle_pass`) driven against an
     in-memory sqlite canvas layout with the candidate engine + research monkeypatched.

`settings.data_dir` is redirected to a tmp dir (the dev venv points it at PRODUCTION
data) — belt-and-suspenders even though this suite never touches disk-backed stores.
"""
import asyncio
import sqlite3

import pytest

from services import canvas_idle_research as ir


# ── Pure: select_new_curator_edges ───────────────────────────────────────────────────
def test_select_picks_strong_new_pairs_only():
    cands = [
        {"a_node": "n1", "b_node": "n2", "score": 0.9, "signal": "embed"},
        {"a_node": "n2", "b_node": "n3", "score": 0.7, "signal": "concept"},
        {"a_node": "n1", "b_node": "n3", "score": 0.5, "signal": "embed"},  # below IDLE bar
    ]
    out = ir.select_new_curator_edges(cands, [], ["n1", "n2", "n3"])
    assert [(e["source"], e["target"]) for e in out] == [("n1", "n2"), ("n2", "n3")]
    assert all(e["state"] == "curator" for e in out)
    assert out[0]["meta"]["origin"] == "idle_research"
    assert out[0]["label"] == "embed"


def test_select_skips_already_edged_either_direction():
    cands = [{"a_node": "n1", "b_node": "n2", "score": 0.9, "signal": "embed"}]
    # existing edge is n2->n1 (reversed) — must still be treated as already connected.
    out = ir.select_new_curator_edges(cands, [{"source": "n2", "target": "n1"}], ["n1", "n2"])
    assert out == []


def test_select_drops_vanished_nodes_and_respects_cap():
    cands = [
        {"a_node": "n1", "b_node": "gone", "score": 0.99, "signal": "embed"},  # node vanished
        {"a_node": "n1", "b_node": "n2", "score": 0.9, "signal": "embed"},
        {"a_node": "n2", "b_node": "n3", "score": 0.85, "signal": "embed"},
        {"a_node": "n3", "b_node": "n4", "score": 0.8, "signal": "embed"},
    ]
    out = ir.select_new_curator_edges(cands, [], ["n1", "n2", "n3", "n4"], max_new=2)
    assert len(out) == 2
    assert ("n1", "gone") not in [(e["source"], e["target"]) for e in out]


# ── Pure: best_partner_for ───────────────────────────────────────────────────────────
def test_best_partner_returns_highest_scoring_neighbour():
    cands = [
        {"a_node": "q", "b_node": "s1", "score": 0.7, "signal": "embed"},
        {"a_node": "s2", "b_node": "q", "score": 0.92, "signal": "embed"},
        {"a_node": "x", "b_node": "y", "score": 0.99, "signal": "embed"},  # unrelated to q
    ]
    assert ir.best_partner_for("q", cands) == "s2"


def test_best_partner_none_when_no_qualifying_neighbour():
    cands = [{"a_node": "q", "b_node": "s1", "score": 0.4, "signal": "embed"}]  # below bar
    assert ir.best_partner_for("q", cands, min_score=0.65) is None
    assert ir.best_partner_for("q", []) is None


# ── Async: enrich_notebook draws curator edges from candidates ───────────────────────
@pytest.fixture()
def mem_layout(monkeypatch, tmp_path):
    from config import settings
    # data_dir must stay a Path — stores do `settings.data_dir / "file.json"`.
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)

    from storage import canvas_layout_store as cl
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cl._ensure_schema(conn)
    monkeypatch.setattr(cl, "_get_conn", lambda: conn)
    return cl


def _seed_two_node_layout(cl, nb):
    cl.save_layout(nb, [
        {"id": "n1", "x": 0, "y": 0, "kind": "chat_turn", "ref_type": "exploration_query",
         "ref_id": "q1", "title": "What is RAG?",
         "snapshot": {"id": "a", "type": "markdown", "payload": "**Q:** What is RAG?"}},
        {"id": "n2", "x": 1, "y": 0, "kind": "chat_turn", "ref_type": "exploration_query",
         "ref_id": "q2", "title": "How does reranking work?",
         "snapshot": {"id": "b", "type": "markdown", "payload": "**Q:** reranking"}},
    ], [])


def test_enrich_notebook_persists_curator_edges(monkeypatch, mem_layout):
    cl = mem_layout
    _seed_two_node_layout(cl, "nb1")

    async def fake_candidates(nb, nodes):
        return [{"a_node": "n1", "b_node": "n2", "score": 0.9, "signal": "embed"}]
    monkeypatch.setattr(
        "services.canvas_candidates.compute_candidates", fake_candidates
    )

    res = asyncio.run(ir.enrich_notebook("nb1", allow_research=False))
    assert res["edges_drawn"] == 1
    edges = cl.get_layout("nb1")["edges"]
    assert len(edges) == 1 and edges[0]["state"] == "curator"

    # Idempotent: a second pass draws nothing new (pair already edged).
    res2 = asyncio.run(ir.enrich_notebook("nb1", allow_research=False))
    assert res2["edges_drawn"] == 0
    assert len(cl.get_layout("nb1")["edges"]) == 1


def test_enrich_notebook_skips_when_too_few_nodes(monkeypatch, mem_layout):
    cl = mem_layout
    cl.save_layout("nb2", [{"id": "n1", "x": 0, "y": 0, "kind": "chat_turn"}], [])
    called = {"n": 0}

    async def fake_candidates(nb, nodes):
        called["n"] += 1
        return []
    monkeypatch.setattr("services.canvas_candidates.compute_candidates", fake_candidates)

    res = asyncio.run(ir.enrich_notebook("nb2", allow_research=False))
    assert res == {"edges_drawn": 0, "researched": False}
    assert called["n"] == 0  # short-circuited before touching the candidate engine


# ── Async: gap research writes a researched edge with meta.insight ───────────────────
def test_research_top_gap_writes_researched_edge(monkeypatch, mem_layout):
    cl = mem_layout
    _seed_two_node_layout(cl, "nb3")

    async def fake_candidates(nb, nodes):
        # n1 (a gap question) has a genuine latent partner n2.
        return [{"a_node": "n1", "b_node": "n2", "score": 0.9, "signal": "embed"}]
    monkeypatch.setattr("services.canvas_candidates.compute_candidates", fake_candidates)

    async def fake_journey(nb, limit=200):
        return {"queries": [{"id": "q1", "query": "What is RAG?",
                             "answer_preview": "couldn't find that in your documents",
                             "confidence": 0.2}]}
    monkeypatch.setattr("storage.exploration_store.exploration_store.get_journey", fake_journey)

    class _R:
        title = "RAG explained"
        url = "https://example.com/rag"
        snippet = "Retrieval-augmented generation combines search with generation."

    async def fake_web_search(query, notebook_id, max_results=3):
        return [_R()]
    monkeypatch.setattr("services.research_engine.research_engine.web_search", fake_web_search)

    res = asyncio.run(ir.enrich_notebook("nb3", allow_research=True))
    assert res["researched"] is True
    edges = cl.get_layout("nb3")["edges"]
    researched = [e for e in edges if e["state"] == "researched"]
    assert len(researched) == 1
    assert "RAG explained" in researched[0]["meta"]["insight"]
    assert researched[0]["meta"]["url"] == "https://example.com/rag"


# ── Async: run_idle_pass orchestration + preemption ──────────────────────────────────
def test_run_idle_pass_iterates_layout_notebooks(monkeypatch, mem_layout):
    cl = mem_layout
    _seed_two_node_layout(cl, "nbA")

    async def fake_list():
        return [{"id": "nbA"}, {"id": "nbEmpty"}]
    monkeypatch.setattr("storage.notebook_store.notebook_store.list", fake_list)

    async def fake_candidates(nb, nodes):
        return [{"a_node": "n1", "b_node": "n2", "score": 0.9, "signal": "embed"}]
    monkeypatch.setattr("services.canvas_candidates.compute_candidates", fake_candidates)

    # Force idle + no pacing so the pass runs deterministically & fast.
    monkeypatch.setattr("services.presence.current_tier", lambda: __import__(
        "services.presence", fromlist=["Tier"]).Tier.LONG_IDLE)
    monkeypatch.setattr("services.presence.is_active", lambda: False)
    monkeypatch.setattr("services.presence.background_pace_seconds", lambda: 0.0)

    summary = asyncio.run(ir.run_idle_pass(worker=None, allow_research=False))
    assert summary["notebooks"] == 1          # only nbA has ≥2 nodes
    assert summary["edges_drawn"] == 1
    assert summary["researched"] == 0


def test_run_idle_pass_preempts_when_user_active(monkeypatch, mem_layout):
    cl = mem_layout
    _seed_two_node_layout(cl, "nbA")

    async def fake_list():
        return [{"id": "nbA"}]
    monkeypatch.setattr("storage.notebook_store.notebook_store.list", fake_list)
    monkeypatch.setattr("services.presence.is_active", lambda: True)  # user is back

    called = {"n": 0}

    async def fake_candidates(nb, nodes):
        called["n"] += 1
        return []
    monkeypatch.setattr("services.canvas_candidates.compute_candidates", fake_candidates)

    summary = asyncio.run(ir.run_idle_pass(worker=None, allow_research=False))
    assert summary["notebooks"] == 0
    assert called["n"] == 0  # bailed before enriching any notebook
