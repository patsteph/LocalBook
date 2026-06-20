"""Fast follow-up test runner — tests both fast model (direct) and main model (RAG) response speed."""

import time
from datetime import datetime
from evaluator.models import EvalResult


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Run fast follow-up tests: fast model (direct generate) + main model (RAG query)."""
    from services.rag_engine import rag_engine
    from services.ollama_service import ollama_service
    from config import settings

    results = []
    q = config["queries"]["followup"]

    # ── Test 1: Fast Model (direct generate, no RAG overhead) ─────────────
    fast_model = getattr(settings, 'ollama_fast_model', settings.ollama_model)
    result_fast = EvalResult(
        test_id="fast_followup_fast_model",
        category="fast_followup",
        test_name="Fast Model Direct Response",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result_fast.stamp_provider(fast_model)

    try:
        start = time.time()
        response = await ollama_service.generate(
            prompt=f"Summarize the following in 3 bullet points:\n\n{q['question']}",
            model=fast_model,
            num_predict=150,
        )
        elapsed = (time.time() - start) * 1000
        result_fast.total_time_ms = elapsed

        answer = response.get("response", "")
        result_fast.output_chars = len(answer)
        result_fast.input_chars = len(q["question"])
        result_fast.actual_output_preview = answer[:500]

        # Fast model scoring: aggressive speed thresholds (should be < 3s)
        speed_score = 100 if elapsed < 3000 else max(0, int(100 - (elapsed - 3000) / 150))
        coherence_score = 100 if len(answer) > 20 else max(0, int(len(answer) * 4))
        has_bullets = any(line.strip().startswith(("-", "•", "*"))
                         for line in answer.split("\n") if line.strip())
        format_score = 100 if has_bullets else 60

        result_fast.overall_score = int(
            speed_score * 0.50 + coherence_score * 0.30 + format_score * 0.20
        )
        result_fast.passed = result_fast.overall_score >= 40

        print(f"[EVAL-FOLLOWUP-FAST] Score={result_fast.overall_score}, {elapsed:.0f}ms, {len(answer)} chars")

    except Exception as e:
        result_fast.passed = False
        result_fast.failure_reason = str(e)[:200]
        result_fast.overall_score = 0
        print(f"[EVAL-FOLLOWUP-FAST] FAILED: {e}")

    results.append(result_fast)

    # ── Test 2: Main Model (full RAG query) ────────────────────────────────
    main_model = settings.ollama_model
    result_main = EvalResult(
        test_id="fast_followup_main_model",
        category="fast_followup",
        test_name="Main Model RAG Follow-Up",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result_main.stamp_provider(main_model)

    try:
        start = time.time()
        response = await rag_engine.query(
            notebook_id=notebook_id,
            question=q["question"],
            top_k=3,
        )
        elapsed = (time.time() - start) * 1000
        result_main.total_time_ms = elapsed

        answer = response.answer if hasattr(response, 'answer') else response.get("answer", "")
        result_main.output_chars = len(answer)
        result_main.input_chars = len(q["question"])
        result_main.actual_output_preview = answer[:500]

        # Main model scoring: more lenient (RAG overhead included)
        speed_score = 100 if elapsed < 5000 else max(0, int(100 - (elapsed - 5000) / 200))
        coherence_score = 100 if len(answer) > 30 else max(0, int(len(answer) * 3))
        has_bullets = any(line.strip().startswith(("-", "•", "*", "1", "2", "3"))
                         for line in answer.split("\n") if line.strip())
        format_score = 100 if has_bullets else 60

        result_main.overall_score = int(
            speed_score * 0.40 + coherence_score * 0.40 + format_score * 0.20
        )
        result_main.passed = result_main.overall_score >= 40

        print(f"[EVAL-FOLLOWUP-MAIN] Score={result_main.overall_score}, {elapsed:.0f}ms, {len(answer)} chars")

    except Exception as e:
        result_main.passed = False
        result_main.failure_reason = str(e)[:200]
        result_main.overall_score = 0
        print(f"[EVAL-FOLLOWUP-MAIN] FAILED: {e}")

    results.append(result_main)

    # ── Combined rollup for backward compatibility ─────────────────────────
    avg_score = int((result_fast.overall_score + result_main.overall_score) / 2)
    print(f"[EVAL-FOLLOWUP] Combined avg score: {avg_score}")

    return results
