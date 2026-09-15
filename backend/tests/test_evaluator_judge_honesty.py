"""The Evaluator must never report a number it did not measure.

Before 2026-09-14 every judge failure path ended in a constant:

  - `llm_judge_score`      → `return 50` on unparseable JSON, and again on any exception
  - `score_faithfulness`   → `return 50` on parse failure, transport failure, or NO CITATIONS
  - `score_semantic_similarity` → `return 50` when the embedder was unavailable
  - `rag_chat`             → `faithfulness = 60` whenever the judge matched the main model

So "the judge broke", "there was nothing to check", and "the answer was middling" were the same
number. A model whose judge output ran past `num_predict=100` and got truncated scored as
mediocre rather than unmeasured — which is how a new model (Muse Glimmer) came to look bad for
reasons that had nothing to do with the model.

These tests pin the contract: unmeasured is `None`, it stays visible in the persisted result,
and its weight is redistributed rather than invented.
"""
import asyncio

import pytest

from evaluator import scoring


# ── The parser: absent means absent ─────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "",
    "   ",
    "the answer seems fine to me",                    # prose, no verdict
    '{"score": ',                                      # truncated mid-object (the num_predict bug)
    '{"reason": "good"}',                              # right shape, missing the key
    "<think>I should score this highly",              # reasoning that never closed or concluded
])
def test_no_usable_verdict_is_none_not_fifty(raw):
    assert scoring._judge_number(raw, "score", label="t") is None


@pytest.mark.parametrize("raw,expected", [
    ('{"score": 87, "reason": "solid"}', 87),
    ('```json\n{"score": 42}\n```', 42),                        # fenced
    ('Here is my assessment:\n{"score": 15}', 15),              # preamble
    ('<think>hmm, mostly right</think>{"score": 73}', 73),      # reasoning block stripped first
    ('{"score": "64"}', 64),                                    # stringified number
    ('garbage {"score": 9} trailing', 9),                       # regex last resort
])
def test_a_real_verdict_is_read_correctly(raw, expected):
    assert scoring._judge_number(raw, "score", label="t") == expected


def test_scores_are_clamped_to_range():
    assert scoring._judge_number('{"score": 9999}', "score", label="t") == 100
    assert scoring._judge_number('{"score": -5}', "score", label="t") == 0


def test_reasoning_numbers_do_not_leak_into_the_verdict():
    """A reasoner that muses "I'd say 20" before answering 90 must score 90. Stripping the
    thinking block before parsing is what prevents the trace being read as the verdict."""
    raw = '<think>My first instinct is {"score": 20} but on reflection…</think>{"score": 90}'
    assert scoring._judge_number(raw, "score", label="t") == 90


# ── Faithfulness: nothing to judge ≠ half-faithful ──────────────────────────

def test_faithfulness_with_no_citations_is_unmeasured():
    """There is nothing to check the answer against. That is not 50% faithful."""
    assert asyncio.run(scoring.score_faithfulness("an answer", [], "judge-model")) is None


def test_faithfulness_with_no_answer_is_unmeasured():
    assert asyncio.run(scoring.score_faithfulness("", [{"text": "ctx"}], "judge-model")) is None


def test_faithfulness_survives_a_dead_judge_as_unmeasured(monkeypatch):
    """A transport failure is not a quality signal."""
    from services import llm_runtime as _lr

    async def _boom(**kwargs):
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(_lr.llm_runtime, "generate", _boom)
    got = asyncio.run(scoring.score_faithfulness("a", [{"text": "ctx"}], "judge-model"))
    assert got is None


def test_faithfulness_truncated_judge_output_is_unmeasured(monkeypatch):
    """The num_predict bug in its exact shape: the verdict got cut off mid-JSON."""
    from services import llm_runtime as _lr

    async def _truncated(**kwargs):
        return {"response": '{"faithful": '}

    monkeypatch.setattr(_lr.llm_runtime, "generate", _truncated)
    assert asyncio.run(scoring.score_faithfulness("a", [{"text": "c"}], "judge")) is None


def test_faithfulness_reads_a_real_verdict(monkeypatch):
    from services import llm_runtime as _lr

    async def _ok(**kwargs):
        return {"response": '{"faithful": 91, "reason": "supported"}'}

    monkeypatch.setattr(_lr.llm_runtime, "generate", _ok)
    assert asyncio.run(scoring.score_faithfulness("a", [{"text": "c"}], "judge")) == 91


def test_the_judge_is_given_room_to_finish(monkeypatch):
    """`num_predict=80/100` truncated any judge that reasons before answering, and the
    truncation was then scored as a mid value."""
    seen = {}
    from services import llm_runtime as _lr

    async def _capture(**kwargs):
        seen.update(kwargs)
        return {"response": '{"faithful": 50}'}

    monkeypatch.setattr(_lr.llm_runtime, "generate", _capture)
    asyncio.run(scoring.score_faithfulness("a", [{"text": "c"}], "judge"))
    assert seen["num_predict"] >= 256, "a reasoning judge cannot emit JSON in 80 tokens"


# ── Combining: redistribute, never substitute ───────────────────────────────

def test_an_unmeasured_axis_is_dropped_and_its_weight_redistributed():
    overall, detail = scoring.combine_measured({
        "correctness": (100, 0.35),
        "recall": (100, 0.25),
        "faithfulness": (None, 0.20),   # the judge could not run
        "citations": (100, 0.10),
        "speed": (100, 0.10),
    })
    # Every measured axis is 100, so the result is 100 — NOT 80, which is what substituting
    # the old flat `faithfulness = 60` produced for a perfect answer.
    assert overall == 100
    assert detail["faithfulness"] is None, "the gap must stay visible in the persisted result"
    assert detail["unmeasured"] == ["faithfulness"]
    assert detail["coverage"] == 0.8


def test_coverage_reports_how_much_was_actually_measured():
    _, detail = scoring.combine_measured({
        "a": (80, 0.5),
        "b": (None, 0.3),
        "c": (None, 0.2),
    })
    assert detail["coverage"] == 0.5
    assert detail["unmeasured"] == ["b", "c"]


def test_nothing_measured_is_zero_coverage_not_a_score():
    """Distinguishable from a genuine 0 — the caller marks the test unmeasured rather than
    failed on quality."""
    overall, detail = scoring.combine_measured({"a": (None, 0.5), "b": (None, 0.5)})
    assert overall == 0
    assert detail["coverage"] == 0.0


def test_a_measured_zero_still_counts():
    """0 is a real score and must not be mistaken for missing."""
    overall, detail = scoring.combine_measured({"a": (0, 0.5), "b": (100, 0.5)})
    assert overall == 50
    assert detail["coverage"] == 1.0
    assert detail["unmeasured"] == []
