"""Retrieval metrics — the numbers the RAG work will be judged by.

These decide whether four long-deferred changes ship (`query: ` prefix, overcollect floor, PDF
page metadata, unembedded notes), so they are pinned against hand-computed values rather than
against whatever the implementation happens to return. Pure functions: no model, no index, no
network, so they run in CI.
"""
import math

import pytest

from evaluator import retrieval_metrics as rm


# ── hit@k ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("relevance,k,expected", [
    ([1, 0, 0, 0, 0], 5, 1.0),
    ([0, 0, 0, 0, 1], 5, 1.0),
    ([0, 0, 0, 0, 0, 1], 5, 0.0),     # rank 6 — outside the top 5 the app uses
    ([0, 0, 0, 0, 0, 1], 20, 1.0),    # …but retrievable, which is a different problem
    ([], 5, 0.0),
    ([1], 0, 0.0),
])
def test_hit_at_k(relevance, k, expected):
    assert rm.hit_at_k(relevance, k) == expected


def test_the_top5_top20_distinction_is_the_point():
    """'Cannot find it' and 'finds it but buries it' need different fixes — an embedding
    problem versus a ranking one. A metric that collapses them hides which."""
    buried = [0] * 10 + [1] + [0] * 9
    assert rm.hit_at_k(buried, 5) == 0.0
    assert rm.hit_at_k(buried, 20) == 1.0
    assert rm.first_relevant_rank(buried) == 11


# ── recall@k ────────────────────────────────────────────────────────────────

def test_recall_counts_all_relevant_not_just_the_first():
    relevance = [1, 0, 1, 0, 1]
    assert rm.recall_at_k(relevance, 5) == 1.0
    assert rm.recall_at_k(relevance, 3) == pytest.approx(2 / 3)


def test_recall_against_a_known_total_is_not_flattered_by_what_was_retrieved():
    """Retrieving 1 of 5 relevant chunks and ranking it first is NOT perfect recall. Without
    an explicit total the metric can only measure what it already found."""
    relevance = [1, 0, 0]
    assert rm.recall_at_k(relevance, 3) == 1.0                      # implicit total = 1
    assert rm.recall_at_k(relevance, 3, total_relevant=5) == 0.2    # the honest number


def test_recall_with_nothing_relevant_is_zero_not_an_error():
    assert rm.recall_at_k([0, 0, 0], 3) == 0.0


# ── nDCG ────────────────────────────────────────────────────────────────────

def test_ndcg_rewards_ranking_the_answer_first():
    """hit@5 cannot tell rank 1 from rank 5, but the context builder has a token budget — a
    gold chunk at rank 5 may never reach the model."""
    first = rm.ndcg_at_k([1, 0, 0, 0, 0], 10)
    fifth = rm.ndcg_at_k([0, 0, 0, 0, 1], 10)
    assert first == 1.0
    assert 0 < fifth < first


def test_ndcg_matches_hand_computed_values():
    # One relevant item at rank 2: DCG = 1/log2(3); ideal = 1/log2(2) = 1.
    expected = (1 / math.log2(3)) / 1.0
    assert rm.ndcg_at_k([0, 1, 0], 10) == pytest.approx(expected)

    # Two relevant at ranks 1 and 3: DCG = 1/log2(2) + 1/log2(4) = 1 + 0.5
    # ideal (both at 1 and 2) = 1/log2(2) + 1/log2(3)
    dcg = 1.0 + 1 / math.log2(4)
    idcg = 1.0 + 1 / math.log2(3)
    assert rm.ndcg_at_k([1, 0, 1], 10) == pytest.approx(dcg / idcg)


def test_ndcg_is_zero_when_nothing_is_relevant():
    assert rm.ndcg_at_k([0, 0, 0], 10) == 0.0


def test_ndcg_respects_the_cutoff():
    """A relevant item outside k contributes nothing."""
    assert rm.ndcg_at_k([0, 0, 0, 1], 3) == 0.0


# ── MRR ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("relevance,expected", [
    ([1, 0, 0], 1.0),
    ([0, 1, 0], 0.5),
    ([0, 0, 1], pytest.approx(1 / 3)),
    ([0, 0, 0], 0.0),
])
def test_mrr(relevance, expected):
    assert rm.mrr(relevance) == expected


def test_first_relevant_rank_is_none_when_missed():
    assert rm.first_relevant_rank([0, 0, 0]) is None


# ── Marker matching ─────────────────────────────────────────────────────────

def test_any_of_markers_match():
    chunks = [{"text": "irrelevant"}, {"text": "produces 1024-dimensional dense vectors"}]
    rel = rm.relevance_from_markers(chunks, ["568M parameters", "1024-dimensional dense vectors"])
    assert rel == [0.0, 1.0]


def test_matching_searches_parent_text_and_snippet():
    """A marker phrase can straddle a chunk boundary — hitting the enclosing paragraph is a
    legitimate retrieval, not a near-miss. Mirrors scoring.score_context_recall."""
    chunks = [{"text": "fragment", "parent_text": "… produces 1024-dimensional dense vectors …"}]
    assert rm.relevance_from_markers(chunks, ["1024-dimensional dense vectors"]) == [1.0]

    chunks = [{"text": "fragment", "snippet": "568M parameters"}]
    assert rm.relevance_from_markers(chunks, ["568M parameters"]) == [1.0]


def test_matching_is_case_insensitive():
    chunks = [{"text": "Produces 1024-Dimensional Dense Vectors"}]
    assert rm.relevance_from_markers(chunks, ["1024-dimensional dense vectors"]) == [1.0]


def test_no_markers_means_nothing_is_relevant():
    """Rather than silently marking everything relevant, which would turn an unlabelled case
    into a perfect score."""
    assert rm.relevance_from_markers([{"text": "anything"}], []) == [0.0]


# ── The gold set itself ─────────────────────────────────────────────────────

def test_every_gold_marker_exists_verbatim_in_the_corpus():
    """A marker that appears nowhere in the source can never be retrieved, so a miss would
    mean the LABEL was wrong rather than retrieval failing — the harness would be measuring
    its own fixtures."""
    import json
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "evaluator"
    src = (root / "test_content" / "generate_test_files.py").read_text()
    corpus = " ".join(
        re.findall(r'"""(.*?)"""', src, re.S) + re.findall(r'"([^"\n]{10,})"', src)
    ).lower()

    gold = json.loads((root / "test_fixtures" / "retrieval_gold.json").read_text())
    missing = [
        (q["id"], m)
        for q in gold["questions"]
        for m in q["markers"]
        if m.lower() not in corpus
    ]
    assert not missing, f"gold markers not present in the corpus: {missing}"


def test_gold_cases_are_well_formed():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "evaluator"
    gold = json.loads((root / "test_fixtures" / "retrieval_gold.json").read_text())
    questions = gold["questions"]
    assert len(questions) >= 20, "a handful of pairs cannot resolve a retrieval change"

    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids)), "duplicate gold ids"
    for q in questions:
        assert q.get("question", "").strip(), f"{q['id']} has no question"
        assert q.get("markers"), f"{q['id']} has no markers"


# ── The runner's verdict logic ──────────────────────────────────────────────
#
# Exercised against a stubbed search so the top-5 / top-20 / miss distinction is pinned without
# needing a live index. The live call itself (search_chunks_async → LanceDB) is NOT covered
# here — only the scoring built on top of it.

import asyncio


def _stub_search(ranked_texts):
    """Return a fake search_chunks_async yielding chunks in the given order."""
    async def _search(notebook_id, query, top_k=20):
        return [{"text": t} for t in ranked_texts[:top_k]]
    return _search


def _run_runner(monkeypatch, ranked_texts, gold_questions):
    from services import rag_engine as _re
    from evaluator.test_runners import retrieval

    monkeypatch.setattr(_re.rag_engine, "search_chunks_async", _stub_search(ranked_texts))
    monkeypatch.setattr(retrieval, "_load_gold", lambda: gold_questions)
    return asyncio.run(retrieval.run("nb", {}, "combo", "hw"))


GOLD = [{"id": "q1", "question": "where is the fact?", "markers": ["THE FACT"]}]


def test_a_gold_chunk_in_the_top_five_scores_full(monkeypatch):
    results = _run_runner(monkeypatch, ["noise", "THE FACT here", "noise"], GOLD)
    assert results[0].overall_score == 100
    assert results[0].passed is True
    assert results[0].sub_scores["first_relevant_rank"] == 2


def test_a_buried_chunk_gets_partial_credit_and_says_where(monkeypatch):
    """Rank 11 means retrieval CAN find it but the top-k cut hides it — a ranking fix, not an
    embedding one. Scoring it zero would erase that distinction."""
    ranked = ["noise"] * 10 + ["THE FACT"] + ["noise"] * 9
    results = _run_runner(monkeypatch, ranked, GOLD)
    assert results[0].overall_score == 60
    assert results[0].sub_scores["first_relevant_rank"] == 11
    assert results[0].sub_scores["hit_at_5"] == 0.0
    assert results[0].sub_scores["hit_at_20"] == 1.0
    assert "rank 11" in results[0].failure_reason


def test_a_complete_miss_scores_zero_and_names_the_problem(monkeypatch):
    results = _run_runner(monkeypatch, ["noise"] * 20, GOLD)
    assert results[0].overall_score == 0
    assert results[0].passed is False
    assert results[0].sub_scores["first_relevant_rank"] is None
    assert "never surfaced it" in results[0].failure_reason


def test_an_unlabelled_case_is_skipped_not_failed(monkeypatch):
    """A case awaiting a human label must not depress the score — same distinction Phase 1
    fixed for field_edges."""
    bad = [{"id": "q2", "question": "", "markers": []}]
    results = _run_runner(monkeypatch, ["anything"], bad)
    assert results[0].skipped is True
    assert results[0].overall_score == 0


def test_no_gold_pairs_skips_the_category(monkeypatch):
    results = _run_runner(monkeypatch, ["anything"], [])
    assert len(results) == 1 and results[0].skipped is True
