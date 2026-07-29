"""CI-pure unit tests for the headless Evaluator's regression decision (2.2.0 Wave 1).

Covers evaluate_regression() without touching the live eval — importing evaluator.run
is light (the heavy evaluator_service import is lazy inside _run).
"""
from evaluator.run import evaluate_regression


def test_no_baseline_is_never_regression():
    assert evaluate_regression(None, 80.0, 5.0) == (False, None)
    assert evaluate_regression({}, 80.0, 5.0) == (False, None)


def test_missing_overall_score_is_never_regression():
    assert evaluate_regression({"overall_grade": "B"}, 80.0, 5.0) == (False, None)


def test_drop_within_threshold_is_ok():
    is_reg, drop = evaluate_regression({"overall_score": 82.0}, 80.0, 5.0)
    assert is_reg is False and drop == 2.0


def test_drop_exactly_threshold_is_ok():
    # regression is a drop STRICTLY greater than threshold
    is_reg, drop = evaluate_regression({"overall_score": 85.0}, 80.0, 5.0)
    assert is_reg is False and drop == 5.0


def test_drop_beyond_threshold_is_regression():
    is_reg, drop = evaluate_regression({"overall_score": 90.0}, 80.0, 5.0)
    assert is_reg is True and drop == 10.0


def test_improvement_is_not_regression():
    is_reg, drop = evaluate_regression({"overall_score": 70.0}, 80.0, 5.0)
    assert is_reg is False and drop == -10.0
