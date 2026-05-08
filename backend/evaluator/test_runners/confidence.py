"""Confidence-scoring calibration test runner.

Verifies services/scan_pipeline._compute_confidence behaves correctly on
fixed inputs with known [unclear] markers. Pure-function test — no LLM
call involved, so this should pass instantly on every combo. If it
fails, _compute_confidence has a bug, not the model.

Test cases cover:
  - Empty input (should return 1.0 — no penalty for empty)
  - All-clean text (1.0)
  - One [unclear] in 10 words (0.9)
  - All [unclear] (0.0 floor)
  - Structured-only input (no prose, e.g. just a table) — should not
    falsely penalise.

Identical to apples-to-apples — no model variation, but the test still
runs per-combo so a regression in _compute_confidence shows up in every
report alike.
"""

import time
from datetime import datetime

from evaluator.models import EvalResult


# Each case: (input_text, expected_confidence, tolerance, name).
# Tolerance is symmetric — output must be within ±tolerance of expected.
_CASES = [
    ("", 1.0, 0.05, "empty"),
    ("This is a perfectly clean sentence with no unclear markers.", 1.0, 0.05, "clean"),
    # 10 words, 1 unclear → 1 - 1/10 = 0.9
    ("one two three four [unclear] six seven eight nine ten", 0.9, 0.05, "one_in_ten"),
    # 4 words, 4 unclear-ish (every word is [unclear]) → 1 - 4/4 = 0.0
    ("[unclear] [unclear] [unclear] [unclear]", 0.0, 0.05, "all_unclear"),
    # Structured table — no prose, no unclear → 1.0 (clean signal).
    ("| Item | Qty |\n|---|---|\n| Apple | 5 |", 1.0, 0.05, "structured_only"),
]


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Run all confidence-calibration cases. One EvalResult per case."""
    from config import settings
    from services.scan_pipeline import _compute_confidence

    results: list[EvalResult] = []

    for text, expected, tol, name in _CASES:
        result = EvalResult(
            test_id=f"confidence_{name}",
            category="confidence",
            test_name=f"Confidence Calibration: {name}",
            model_combo=combo_name,
            hardware_fingerprint=hw_fingerprint,
            timestamp=datetime.utcnow().isoformat(),
        )
        result.stamp_provider(settings.ollama_model)

        try:
            start = time.time()
            actual = _compute_confidence(text)
            elapsed = (time.time() - start) * 1000
            result.total_time_ms = elapsed

            delta = abs(actual - expected)
            within = delta <= tol
            result.sub_scores = {
                "input_chars": len(text),
                "expected": expected,
                "actual": round(actual, 3),
                "delta": round(delta, 3),
                "tolerance": tol,
            }
            result.actual_output_preview = (
                f"input='{text[:60]}…' expected={expected:.3f} actual={actual:.3f} "
                f"delta={delta:.3f}"
            )
            # Score: 100 if within tolerance, decay linearly.
            if within:
                result.overall_score = 100
                result.passed = True
            else:
                # Past tolerance: scale down. delta=1 → 0.
                result.overall_score = max(0, int(100 * (1 - delta)))
                result.passed = False
                result.failure_reason = (
                    f"_compute_confidence({name}) returned {actual:.3f}, "
                    f"expected {expected:.3f} (±{tol})"
                )
            print(
                f"[EVAL-CONF] {name}: expected={expected:.2f} actual={actual:.2f} "
                f"score={result.overall_score}"
            )
        except Exception as e:
            result.passed = False
            result.failure_reason = str(e)[:200]
            result.overall_score = 0
            print(f"[EVAL-CONF] {name} FAILED: {e}")

        results.append(result)

    return results
