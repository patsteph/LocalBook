"""Instruction following test runner — tests format compliance and constraints."""

import time
from datetime import datetime
from evaluator.models import EvalResult
from evaluator import scoring


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Run instruction following tests: format compliance + negative constraints."""
    from services.rag_engine import rag_engine
    from config import settings

    results = []
    queries = config["queries"]

    # ── Test 1: Numbered list format ─────────────────────────────────────
    q = queries["instruction_format"]
    result = EvalResult(
        test_id="instruction_numbered_list",
        category="instruction_follow",
        test_name="Numbered List Format Compliance",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result.stamp_provider(settings.ollama_model)

    try:
        start = time.time()
        response = await rag_engine.query(
            notebook_id=notebook_id,
            question=q["question"],
            top_k=4,
        )
        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed

        answer = response.answer if hasattr(response, 'answer') else response.get("answer", "")
        result.output_chars = len(answer)
        result.actual_output_preview = answer[:500]

        format_score = scoring.score_format_compliance(answer, q["expected_format"])
        result.format_score = format_score
        result.overall_score = format_score
        result.passed = format_score >= 50

        if format_score < 50:
            result.failure_reason = f"Format compliance {format_score}% < 50%"

        print(f"[EVAL-INSTRUCT] Numbered list: format={format_score}, {elapsed:.0f}ms")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-INSTRUCT] Numbered list FAILED: {e}")

    results.append(result)

    # ── Test 2: Paragraph constraint ─────────────────────────────────────
    q = queries["instruction_constraint"]
    result = EvalResult(
        test_id="instruction_paragraph_constraint",
        category="instruction_follow",
        test_name="Paragraph + Negative Constraint",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result.stamp_provider(settings.ollama_model)

    try:
        start = time.time()
        response = await rag_engine.query(
            notebook_id=notebook_id,
            question=q["question"],
            top_k=4,
        )
        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed

        answer = response.answer if hasattr(response, 'answer') else response.get("answer", "")
        result.output_chars = len(answer)
        result.actual_output_preview = answer[:500]

        format_score = scoring.score_format_compliance(answer, q["expected_format"])
        result.format_score = format_score
        result.overall_score = format_score
        result.passed = format_score >= 50

        if format_score < 50:
            result.failure_reason = f"Format compliance {format_score}% < 50%"

        print(f"[EVAL-INSTRUCT] Paragraph constraint: format={format_score}, {elapsed:.0f}ms")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-INSTRUCT] Paragraph constraint FAILED: {e}")

    results.append(result)
    return results
