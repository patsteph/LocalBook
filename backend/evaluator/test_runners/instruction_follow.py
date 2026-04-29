"""Instruction following test runner — IFEval-style verifiable constraints.

Implements 4 of the 9 IFEval constraint categories (Google, 2023):
- detectable_format: numbered lists, paragraph counts
- detectable_format_combination: structure + negative constraints
- length_keyword_combination: word limits + required keywords
- keyword_exclusion: banned keyword constraint (hardest category)

Each test uses deterministic regex-based scoring (no LLM judge) for
reproducibility, matching the IFEval methodology.
"""

import time
from datetime import datetime
from evaluator.models import EvalResult
from evaluator import scoring


# Test configurations: (config_key, test_id, test_name)
INSTRUCTION_TESTS = [
    ("instruction_format", "instruction_numbered_list", "Numbered List Format"),
    ("instruction_constraint", "instruction_paragraph_constraint", "Paragraph + Negative Constraint"),
    ("instruction_word_limit", "instruction_word_limit", "Word Limit + Required Keywords"),
    ("instruction_keyword_exclusion", "instruction_keyword_exclusion", "Banned Keyword Exclusion"),
]


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Run IFEval-style instruction following tests."""
    from services.rag_engine import rag_engine
    from config import settings

    results = []
    queries = config["queries"]

    for config_key, test_id, test_name in INSTRUCTION_TESTS:
        q = queries.get(config_key)
        if not q:
            # Skip silently if test not configured (allows incremental rollout)
            continue

        result = EvalResult(
            test_id=test_id,
            category="instruction_follow",
            test_name=test_name,
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
            
            # Surface the IFEval category for dashboard grouping
            result.sub_scores = {
                "category": q["expected_format"].get("category", "unknown"),
                "compliance": format_score,
            }

            if format_score < 50:
                result.failure_reason = f"Format compliance {format_score}% < 50%"

            print(f"[EVAL-INSTRUCT] {test_name}: score={format_score}, {elapsed:.0f}ms")

        except Exception as e:
            result.passed = False
            result.failure_reason = str(e)[:200]
            result.overall_score = 0
            print(f"[EVAL-INSTRUCT] {test_name} FAILED: {e}")

        results.append(result)

    return results
