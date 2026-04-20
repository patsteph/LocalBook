"""Intent classification test runner — tests accuracy of intent detection."""

import time
from datetime import datetime
from evaluator.models import EvalResult


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Run intent classification tests against known expected intents."""
    from services.intent_classifier import classify_intent
    from services.ollama_client import ollama_client
    from config import settings

    tests = config.get("intent_classification_tests", [])
    if not tests:
        return []

    # Intent classification uses the configured fast model via ollama_client
    _fast_model = getattr(settings, "ollama_fast_model", "") or getattr(settings, "ollama_model", "")

    results = []
    correct = 0
    total_time = 0

    for i, test in enumerate(tests):
        result = EvalResult(
            test_id=f"intent_classify_{i}",
            category="intent_classify",
            test_name=f"Intent: {test['message'][:40]}...",
            model_combo=combo_name,
            hardware_fingerprint=hw_fingerprint,
            timestamp=datetime.utcnow().isoformat(),
        )
        result.stamp_provider(_fast_model)

        try:
            start = time.time()
            classified = await classify_intent(
                message=test["message"],
                agent_type=test.get("agent", "studio"),
                ollama_client=ollama_client,
            )
            elapsed = (time.time() - start) * 1000
            result.total_time_ms = elapsed
            total_time += elapsed

            actual_intent = classified.get("intent", "")
            expected_intent = test["expected_intent"]
            result.actual_output_preview = f"Expected: {expected_intent}, Got: {actual_intent}"
            result.input_chars = len(test["message"])

            is_correct = actual_intent == expected_intent
            if is_correct:
                correct += 1

            result.accuracy_score = 100 if is_correct else 0
            speed_score = 100 if elapsed < 3000 else max(0, int(100 - (elapsed - 3000) / 200))

            result.overall_score = int(result.accuracy_score * 0.70 + speed_score * 0.30)
            result.passed = is_correct

            if not is_correct:
                result.failure_reason = f"Expected '{expected_intent}' but got '{actual_intent}'"

        except Exception as e:
            result.passed = False
            result.failure_reason = str(e)[:200]
            result.overall_score = 0

        results.append(result)

    accuracy = (correct / len(tests) * 100) if tests else 0
    avg_time_ms = (total_time / len(tests)) if tests else 0
    print(f"[EVAL-INTENT] {correct}/{len(tests)} correct ({accuracy:.0f}%), avg {avg_time_ms:.0f}ms")

    return results
