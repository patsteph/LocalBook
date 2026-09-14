"""A skipped test is NO DATA — never a zero.

MIGRATED 2026-08-20 from `evaluator/_eval_smoke.py` (ran via a subprocess wrapper). Its
capability and stamp_provider checks were dropped as duplicates of
`test_capability_slotting.py` and `test_eval_provenance.py`; the skip semantics below were
unique.

This is the same family of bug as the phase-timeout fix: scoring a category the model was
never eligible for as 0 doesn't just lower the number, it makes two runs incomparable — an
18-point swing appeared in a real A/B from exactly this, and it looked like an engine quality
difference rather than a scoring artefact.
"""
from evaluator import scoring
from evaluator.models import EvalResult


def _skipped(test_id="b", reason="not applicable"):
    r = EvalResult(test_id=test_id, category="x", test_name="n")
    r.mark_skipped(reason)
    return r


def test_a_skip_is_not_a_failure():
    r = _skipped("t", "no vision support")
    assert r.skipped is True
    assert r.skip_reason == "no vision support"
    assert r.passed is True, "a model cannot fail a test it was never eligible for"
    assert r.overall_score == 0


def test_a_category_score_is_the_mean_of_what_actually_RAN():
    """(80 + 0) / 2 = 40 would report a capable model as mediocre because one sub-test did
    not apply to it."""
    passed = EvalResult(test_id="a", category="x", test_name="n", overall_score=80, passed=True)
    score, _ = scoring.compute_category_score([passed, _skipped()])
    assert score == 80.0, score


def test_an_entirely_skipped_category_scores_zero_as_a_MARKER():
    """0 here means 'no data', and the summary layer filters it out. The distinction only
    holds because `compute_overall_score` excludes skipped categories — see below."""
    score, _ = scoring.compute_category_score([_skipped("c")])
    assert score == 0.0


def test_the_overall_score_excludes_skipped_categories():
    """THE one that matters for comparing runs. Including a skipped category as 0 drags the
    overall down by an amount that depends on which categories a model was eligible for —
    so two runs of different models are no longer measuring the same thing."""
    weights = {"a": 10, "vision": 10}
    with_skip = {"a": 90.0, "vision": 0.0}
    without_skip = {"a": 90.0}
    incl, _ = scoring.compute_overall_score(with_skip, weights)
    excl, _ = scoring.compute_overall_score(without_skip, weights)
    assert incl < excl, (incl, excl)
