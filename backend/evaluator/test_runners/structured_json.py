"""Structured JSON test runner — tests quiz generation for valid JSON output."""

import time
from datetime import datetime
from evaluator.models import EvalResult
from evaluator import scoring


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Generate a quiz and validate JSON structure."""
    from services.structured_llm import structured_llm
    from storage.source_store import source_store
    from config import settings

    quiz_config = config["quiz_generation"]
    result = EvalResult(
        test_id="structured_json_quiz",
        category="structured_json",
        test_name="Quiz Generation (JSON Validation)",
        model_combo=combo_name,
        model_used=settings.ollama_model,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )

    try:
        start = time.time()

        # Get source content for quiz generation
        sources = await source_store.list(notebook_id)
        content = "\n\n".join([
            f"[Source: {s.get('filename', 'Unknown')}]\n{s.get('content', '')[:2000]}"
            for s in sources[:5]
            if s.get("content")
        ])

        if not content.strip():
            raise ValueError("No source content available for quiz generation")

        num_questions = quiz_config.get("num_questions", 3)
        difficulty = quiz_config.get("difficulty", "medium")

        quiz_output = await structured_llm.generate_quiz(
            content=content,
            num_questions=num_questions,
            difficulty=difficulty,
        )

        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed

        # Evaluate quiz quality
        questions = quiz_output.questions if hasattr(quiz_output, 'questions') else []
        result.output_chars = sum(len(q.question) + len(q.answer) for q in questions)
        result.actual_output_preview = f"{len(questions)} questions generated"

        # Score: all expected questions returned
        count_score = 100 if len(questions) >= num_questions else int((len(questions) / num_questions) * 100)

        # Score: each question has required fields
        field_scores = []
        for q in questions:
            has_q = bool(getattr(q, 'question', ''))
            has_a = bool(getattr(q, 'answer', ''))
            has_opts = bool(getattr(q, 'options', None))
            has_exp = bool(getattr(q, 'explanation', ''))
            completeness = sum([has_q, has_a, has_opts, has_exp]) / 4 * 100
            field_scores.append(completeness)
        fields_score = int(sum(field_scores) / max(1, len(field_scores))) if field_scores else 0

        # Score: questions are relevant
        relevance_terms = ["rag", "model", "embed", "retriev", "vector", "neural", "network"]
        all_q_text = " ".join(q.question.lower() + " " + q.answer.lower() for q in questions)
        relevance_hits = sum(1 for t in relevance_terms if t in all_q_text)
        relevance_score = min(100, int((relevance_hits / max(1, len(relevance_terms))) * 120))

        speed_score = 100 if elapsed < 30000 else max(0, int(100 - (elapsed - 30000) / 500))

        result.accuracy_score = relevance_score
        result.completeness_score = fields_score
        result.format_score = count_score
        result.overall_score = int(
            count_score * 0.30 + fields_score * 0.30 + relevance_score * 0.25 + speed_score * 0.15
        )
        result.passed = result.overall_score >= 40

        print(f"[EVAL-JSON] Score={result.overall_score}, {len(questions)} questions, {elapsed:.0f}ms")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-JSON] FAILED: {e}")

    return [result]
