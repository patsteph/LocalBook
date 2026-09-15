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

    # 3 samples in the full tier, 1 in smoke. Each sample is a full RAG generation (20-40s),
    # so this is the difference between a trustworthy number and a quick one.
    _samples = 1 if (config or {}).get("_tier") == "smoke" else 3
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
        result.stamp_provider(settings.main_model)

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

            scores = [scoring.score_format_compliance(answer, q["expected_format"])]

            # REPEAT to beat sampling noise (2026-09-15). One draw at production temperature is
            # a coin flip: the SAME model scored this category 83.8, 68.8 and 56.2 across three
            # consecutive runs — 27 points — so a single sample cannot distinguish two models,
            # and three different models landing on exactly 74 was coincidence, not agreement.
            # The median is reported and the spread recorded, so instability stays visible.
            #
            # Smoke keeps one sample: repeats are what make the number trustworthy and also
            # what would destroy the tier that exists to be quick. It says which it did.
            for _ in range(max(0, _samples - 1)):
                _resp = await rag_engine.query(notebook_id=notebook_id,
                                               question=q["question"], top_k=4)
                _ans = _resp.answer if hasattr(_resp, "answer") else _resp.get("answer", "")
                scores.append(scoring.score_format_compliance(_ans, q["expected_format"]))

            scores.sort()
            format_score = scores[len(scores) // 2]
            spread = scores[-1] - scores[0]
            result.format_score = format_score
            result.overall_score = format_score
            result.passed = format_score >= 50

            # Surface the IFEval category for dashboard grouping
            result.sub_scores = {
                "category": q["expected_format"].get("category", "unknown"),
                "compliance": format_score,
                "samples": scores,
                "spread": spread,
                "single_sample": _samples == 1,
            }

            if spread >= 30:
                result.mark_degraded(
                    f"UNSTABLE: {spread} points across {_samples} identical runs "
                    f"({scores}) — treat as indicative")
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
