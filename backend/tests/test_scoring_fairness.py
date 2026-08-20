"""A thinking model must not be scored on its reasoning.

MIGRATED 2026-08-20 from `_scoring_fairness_test.py` (was run via a subprocess wrapper).

THE BUG THIS GUARDS: every deterministic scorer used to see the raw output, so a model that
emits `<think>…</think>` before its answer was judged on the reasoning too — banned keywords
found inside the thinking, word limits blown by it, JSON "missing" because it sat after it.
That penalises reasoning models specifically, which makes any cross-model comparison a
comparison of output format rather than answer quality.

Still relevant post-cutover: gemma and phi both emit reasoning under some prompts, and the
stop-sequence profile that suppresses it is advisory, not a guarantee.
"""
import pytest

from evaluator import scoring
from evaluator.run_profile import RunProfile

# The same answer, from a thinking model and a plain one. Every score below must match.
THINK = "<think>The user wants Paris. Let me recall French geography.</think>Paris"
CLEAN = "Paris"


@pytest.fixture(autouse=True)
def _clear_profile():
    """The active profile is module-level state; a leak changes later tests' scores."""
    yield
    scoring.clear_active_run_profile()


def test_a_thinking_answer_scores_the_same_as_a_clean_one():
    assert scoring.score_must_contain(CLEAN, ["Paris"]) == 100
    assert scoring.score_must_contain(THINK, ["Paris"]) == 100


def test_a_banned_word_inside_the_reasoning_does_not_count():
    """'geography' appears ONLY in the think block. Penalising it scores the model on words
    it never said to the user."""
    out = scoring.score_format_compliance(
        "<think>France geography lesson</think>Here is the answer.",
        {"banned_keywords": ["geography"]})
    assert out == 100


@pytest.mark.parametrize("raw,expected", [
    ('<think>plan the shape</think>{"answer": 42}', 100),
    ('<think>reasoning</think>Here you go:\n```json\n{"x":1}\n```', 90),   # extraction needed
    ("<think>I cannot</think>sorry no json", 0),
])
def test_json_validity_looks_past_the_reasoning(raw, expected):
    score, _ = scoring.score_json_validity(raw)
    assert score == expected


def test_word_limits_are_judged_on_the_answer_only():
    """A 200-word reasoning block in front of a 5-word answer must not fail a 6-word limit."""
    long_think = "<think>" + ("blah " * 200) + "</think>Short and sweet answer."
    assert scoring.score_format_compliance(long_think, {"max_word_count": 6}) == 100
    assert scoring.score_output_length(long_think, max_words=6) == 100


def test_citations_survive_de_thinking():
    assert scoring.score_has_citations("<think>cite it</think>The sky is blue [1].", 1) == 100


def test_stripping_happens_with_or_without_an_active_profile():
    """The profile carries the filter list, but a scorer with no profile set must still strip —
    otherwise scores silently depend on whether a run happened to register one."""
    rp = RunProfile(model="x", thinking_capable=True, normalize_filters=["strip_thinking"])
    scoring.set_active_run_profile(rp)
    assert scoring.score_must_contain(THINK, ["Paris"]) == 100

    scoring.clear_active_run_profile()
    assert scoring.score_must_contain(THINK, ["Paris"]) == 100
    assert scoring.score_must_contain(CLEAN, ["Paris"]) == 100
