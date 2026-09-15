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


# ── The fail floor: a mean cannot answer "is anything broken?" ───────────────
#
# The 2026-09-14 release run reported 87.7 (B+) while a category sat at the bottom of the
# table. Two gates are needed because they catch different things: regression-vs-baseline is
# blind to anything already broken AT the baseline, and the weighted mean dissolves a single
# dead capability into an otherwise healthy average.

from evaluator.run import blocking_failures, evaluate_regression


def test_a_failed_capability_blocks_even_with_a_high_overall():
    summary = {
        "overall_score": 87.7,
        "feature_parity": [
            {"category": "rag_chat", "feature": "RAG Chat Q&A", "verdict": "pass", "score": 91},
            {"category": "structured_json", "feature": "Structured JSON", "verdict": "fail", "score": 12},
        ],
    }
    blockers = blocking_failures(summary)
    assert [b["category"] for b in blockers] == ["structured_json"]
    assert blockers[0]["score"] == 12


def test_a_skipped_capability_is_never_a_blocker():
    """A feature that is not part of this combo has not failed — conflating the two is what
    made `field_edges: 0 (F)` look like a broken model when it had nothing to run."""
    summary = {"feature_parity": [
        {"category": "vision", "feature": "Vision", "verdict": "not_applicable", "score": 0},
    ]}
    assert blocking_failures(summary) == []


def test_degraded_is_not_blocking():
    """Degraded means weaker, not broken — it must not stop a release on its own."""
    summary = {"feature_parity": [
        {"category": "streaming", "feature": "Streaming", "verdict": "degraded", "score": 55},
    ]}
    assert blocking_failures(summary) == []


def test_no_parity_data_blocks_nothing():
    assert blocking_failures({}) == []
    assert blocking_failures(None) == []


def test_the_two_gates_are_independent():
    """A capability broken since the baseline shows NO regression — the drop is zero — which
    is exactly the case the floor exists to catch."""
    baseline = {"overall_score": 87.7}
    is_reg, drop = evaluate_regression(baseline, 87.7, 5.0)
    assert is_reg is False and drop == 0.0

    summary = {"feature_parity": [
        {"category": "structured_json", "feature": "Structured JSON", "verdict": "fail", "score": 5},
    ]}
    assert blocking_failures(summary), "the floor must catch what the regression gate cannot"


# ── Re-baselining: a scoring change is not a quality regression ──────────────

from evaluator.run import scoring_changed
from evaluator.scoring import SCORING_VERSION


def test_a_scoring_change_re_baselines_instead_of_reporting_a_regression():
    """Phase 0/1 moved the number by design — faithfulness stopped contributing a flat 60 and
    four categories started counting. Comparing across that boundary is meaningless in both
    directions: it can invent a regression that is really a fix, or hide a real one behind a
    scoring change that happened to raise the average."""
    assert scoring_changed({"overall_score": 87.3, "scoring_version": 1}, 2) is True
    assert scoring_changed({"overall_score": 87.3, "scoring_version": 2}, 2) is False


def test_a_baseline_written_before_versioning_is_treated_as_v1():
    """Every run persisted before 2026-09-14 carries no scoring_version — which is exactly
    what version 1 means."""
    assert scoring_changed({"overall_score": 87.3}, 2) is True
    assert scoring_changed({"overall_score": 87.3}, 1) is False


def test_no_baseline_is_not_a_scoring_change():
    assert scoring_changed(None, SCORING_VERSION) is False
    assert scoring_changed({}, SCORING_VERSION) is False


# ── Weights and runners must not drift apart ────────────────────────────────

def test_every_runner_category_is_weighted_and_mapped():
    """The bug this prevents: six runners executed and contributed NOTHING to the overall,
    because `compute_overall_score` only sums categories present in `category_weights`. They
    cost full runtime and implied coverage that did not exist. `field_edges` was worse — absent
    from the weights AND from the feature map, so it had no verdict either.

    A new runner must be weighted (it counts) or deliberately excluded here (with a reason).
    """
    import json
    from pathlib import Path
    from evaluator import feature_parity

    root = Path(__file__).resolve().parents[1] / "evaluator"
    weights = json.loads((root / "test_fixtures" / "eval_config.json").read_text())
    weights = weights["scoring"]["category_weights"]

    runners = {p.stem for p in (root / "test_runners").glob("*.py")
               if p.stem != "__init__"}
    # `ingestion` is scored as a phase rather than emitting a literal category= string.
    known_unweighted: set = set()

    unweighted = runners - set(weights) - known_unweighted
    assert not unweighted, (
        f"runner(s) execute but score nothing: {sorted(unweighted)} — weight them in "
        f"eval_config.json or document why they are excluded"
    )

    unmapped = runners - set(feature_parity._CATEGORY_TO_FEATURE) - known_unweighted
    assert not unmapped, (
        f"runner(s) have no feature-parity verdict: {sorted(unmapped)} — the fail floor "
        f"cannot see them"
    )
