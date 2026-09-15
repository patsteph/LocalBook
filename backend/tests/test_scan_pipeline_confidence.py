"""`scan_pipeline._compute_confidence` calibration.

Moved out of the Evaluator on 2026-09-14. It had been a test runner
(`evaluator/test_runners/confidence.py`) scored as a per-model category, but it makes no model
call at all — it imports `EvalResult` and calls one pure function. Its own docstring said so:
*"Pure-function test — no LLM call involved, so this should pass instantly on every combo. If
it fails, `_compute_confidence` has a bug, not the model."*

Running it there cost minutes of a 15–30 minute model evaluation to re-answer a question that
does not depend on the model, and reported the result as if it were a property of the model.
Here it runs in milliseconds, on every commit, where a regression in the function is actually
attributable.

Cases carried over verbatim from the runner's `_CASES`.
"""
import pytest

from services.scan_pipeline import _compute_confidence


# (input_text, expected_confidence, tolerance, name) — symmetric ±tolerance.
CASES = [
    ("", 1.0, 0.05, "empty"),
    ("This is a perfectly clean sentence with no unclear markers.", 1.0, 0.05, "clean"),
    # 10 words, 1 unclear → 1 - 1/10 = 0.9
    ("one two three four [unclear] six seven eight nine ten", 0.9, 0.05, "one_in_ten"),
    # every word unclear → 1 - 4/4 = 0.0
    ("[unclear] [unclear] [unclear] [unclear]", 0.0, 0.05, "all_unclear"),
    # Structured table — no prose, no unclear markers → clean signal.
    ("| Item | Qty |\n|---|---|\n| Apple | 5 |", 1.0, 0.05, "structured_only"),
]


@pytest.mark.parametrize("text,expected,tol,name", CASES, ids=[c[3] for c in CASES])
def test_confidence_calibration(text, expected, tol, name):
    actual = _compute_confidence(text)
    assert abs(actual - expected) <= tol, (
        f"_compute_confidence({name}) returned {actual:.3f}, expected {expected:.3f} (±{tol})"
    )


def test_confidence_stays_in_range():
    """A confidence outside 0..1 would corrupt every downstream comparison that treats it as a
    fraction, and nothing else checks the bound."""
    for text, _, _, _ in CASES:
        value = _compute_confidence(text)
        assert 0.0 <= value <= 1.0, f"out of range: {value}"


def test_more_unclear_markers_never_raises_confidence():
    """Monotonicity — the property the fixed cases above only sample. If adding an [unclear]
    marker can increase confidence, the metric is not measuring what its name claims."""
    base = "one two three four five six seven eight nine ten"
    previous = _compute_confidence(base)
    words = base.split()
    for i in range(len(words)):
        words[i] = "[unclear]"
        current = _compute_confidence(" ".join(words))
        assert current <= previous + 1e-9, (
            f"confidence rose from {previous:.3f} to {current:.3f} after adding an "
            f"[unclear] marker"
        )
        previous = current
