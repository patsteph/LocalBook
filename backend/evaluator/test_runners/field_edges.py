"""Field-edge regression runner — promoted daily-use near-misses as permanent cases.

Modeled on `intent_classify.py`. Loads cases from
`evaluator/test_fixtures/field_edge_cases.json` (data, not code) that the QS
promoter (`services/field_edge_promoter.promote_signal_to_eval_case`) appends to
when a recurring MISROUTE clears the promotion thresholds. Each case re-runs the
LIVE intent classifier and asserts the corrected intent, so the next model swap
re-tests the exact edge that bit a user.

When no cases have been promoted yet (the committed default), the runner emits a
single SKIPPED result so the category is excluded from the weighted average
rather than scored as a zero. Cases missing a query or a corrected
`expected_intent` label are likewise skipped (they need a human label) instead of
failing.
"""
import time
from datetime import datetime

from evaluator.models import EvalResult


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Run all promoted field-edge cases against the live intent classifier."""
    from services.field_edge_promoter import load_field_edge_cases

    cases = load_field_edge_cases()

    # No promotions yet → skip the whole category (excluded from the weighted avg).
    if not cases:
        skipped = EvalResult(
            test_id="field_edges_empty",
            category="field_edges",
            test_name="Field Edges: (no promoted cases yet)",
            model_combo=combo_name,
            hardware_fingerprint=hw_fingerprint,
            timestamp=datetime.utcnow().isoformat(),
        )
        skipped.mark_skipped("No promoted field-edge cases yet")
        print("[EVAL-FIELDEDGE] no promoted cases — category skipped")
        return [skipped]

    from services.intent_classifier import classify_intent
    from config import settings

    _fast_model = getattr(settings, "ollama_fast_model", "") or getattr(settings, "ollama_model", "")

    results: list[EvalResult] = []
    correct = 0
    scored = 0

    for i, case in enumerate(cases):
        name = case.get("name") or f"case_{i}"
        query = (case.get("query") or case.get("message") or "").strip()
        expected = (case.get("expected_intent") or "").strip()

        result = EvalResult(
            test_id=f"field_edges_{name}",
            category="field_edges",
            test_name=f"Field Edge: {name}",
            model_combo=combo_name,
            hardware_fingerprint=hw_fingerprint,
            timestamp=datetime.utcnow().isoformat(),
        )
        result.stamp_provider(_fast_model)

        # Unlabeled auto-promoted case — skip (needs a human corrected label).
        if not query or not expected:
            result.mark_skipped("case missing query or expected_intent (needs human label)")
            results.append(result)
            continue

        try:
            start = time.time()
            classified = await classify_intent(
                message=query,
                agent_type=case.get("agent", "studio"),
            )
            elapsed = (time.time() - start) * 1000
            result.total_time_ms = elapsed

            actual = classified.get("intent", "")
            result.actual_output_preview = f"Expected: {expected}, Got: {actual}"
            result.input_chars = len(query)

            is_correct = actual == expected
            if is_correct:
                correct += 1
            scored += 1

            result.accuracy_score = 100 if is_correct else 0
            speed_score = 100 if elapsed < 3000 else max(0, int(100 - (elapsed - 3000) / 200))
            result.overall_score = int(result.accuracy_score * 0.70 + speed_score * 0.30)
            result.passed = is_correct
            if not is_correct:
                result.failure_reason = f"Expected '{expected}' but got '{actual}'"
        except Exception as e:
            result.passed = False
            result.failure_reason = str(e)[:200]
            result.overall_score = 0

        results.append(result)

    print(f"[EVAL-FIELDEDGE] {correct}/{scored} corrected edges held")
    return results
