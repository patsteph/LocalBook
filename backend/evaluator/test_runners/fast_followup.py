"""Fast follow-up test runner — tests quick model response to follow-up questions."""

import time
from datetime import datetime
from evaluator.models import EvalResult
from evaluator import scoring


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Run fast follow-up test via real RAG query."""
    from services.rag_engine import rag_engine
    from config import settings

    q = config["queries"]["followup"]
    result = EvalResult(
        test_id="fast_followup",
        category="fast_followup",
        test_name="Fast Follow-Up Response",
        model_combo=combo_name,
        model_used=getattr(settings, 'fast_model', settings.ollama_model),
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )

    try:
        start = time.time()
        response = await rag_engine.query(
            notebook_id=notebook_id,
            question=q["question"],
            top_k=3,
        )
        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed

        answer = response.answer if hasattr(response, 'answer') else response.get("answer", "")
        result.output_chars = len(answer)
        result.input_chars = len(q["question"])
        result.actual_output_preview = answer[:500]

        # Score: speed is the primary metric for follow-ups
        speed_score = 100 if elapsed < 5000 else max(0, int(100 - (elapsed - 5000) / 200))
        coherence_score = 100 if len(answer) > 30 else max(0, int(len(answer) * 3))

        # Check for bullet points since we asked for them
        has_bullets = any(line.strip().startswith(("-", "•", "*", "1", "2", "3"))
                         for line in answer.split("\n") if line.strip())
        format_score = 100 if has_bullets else 60

        result.overall_score = int(
            speed_score * 0.40 + coherence_score * 0.40 + format_score * 0.20
        )
        result.passed = result.overall_score >= 40

        print(f"[EVAL-FOLLOWUP] Score={result.overall_score}, {elapsed:.0f}ms, {len(answer)} chars")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-FOLLOWUP] FAILED: {e}")

    return [result]
