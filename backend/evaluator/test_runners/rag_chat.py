"""RAG Chat Q&A test runner — tests end-to-end retrieval + answer generation."""

import time
from datetime import datetime
from evaluator.models import EvalResult
from evaluator import scoring


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Run RAG chat tests: simple factual + complex multi-source queries."""
    from services.rag_engine import rag_engine
    from config import settings

    results = []
    queries = config["queries"]

    # ── Test 1: Simple factual query ─────────────────────────────────────
    q = queries["simple"]
    result = EvalResult(
        test_id="rag_chat_simple",
        category="rag_chat",
        test_name="Simple Factual Query",
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
        citations = response.citations if hasattr(response, 'citations') else response.get("citations", [])

        result.output_chars = len(answer)
        result.input_chars = len(q["question"])
        result.actual_output_preview = answer[:500]

        # Score
        fact_score = scoring.score_must_contain(answer, q.get("expected_facts", []))
        citation_score = scoring.score_has_citations(answer, min_citations=1)

        # Retrieval score (did we fetch the right chunks?)
        retrieved_text = " ".join([c.get("text", "") for c in citations]) if citations else ""
        retrieval_score = scoring.score_must_contain(retrieved_text, q.get("expected_facts", []))

        # LLM judge (use fast model to judge main model)
        judge_model = getattr(settings, 'fast_model', settings.ollama_model)
        if judge_model != settings.ollama_model:
            judge_score = await scoring.llm_judge_score(q["question"], answer, judge_model)
        else:
            judge_score = 60  # Default if can't use separate judge

        speed_score = 100 if elapsed < 15000 else max(0, int(100 - (elapsed - 15000) / 500))

        result.accuracy_score = fact_score
        result.completeness_score = judge_score
        result.format_score = citation_score
        result.overall_score = int(
            fact_score * 0.40 + citation_score * 0.20 + judge_score * 0.25 + speed_score * 0.15
        )
        
        result.sub_scores = {
            "retrieval": retrieval_score,
            "facts": fact_score,
            "citations": citation_score,
            "judge": judge_score,
            "speed": speed_score
        }
        result.passed = result.overall_score >= 40
        if not result.passed:
            result.failure_reason = f"Score {result.overall_score} < 40"

        print(f"[EVAL-RAG] Simple query: score={result.overall_score} "
              f"(facts={fact_score}, citations={citation_score}, judge={judge_score}, speed={speed_score}) "
              f"{elapsed:.0f}ms")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-RAG] Simple query FAILED: {e}")

    results.append(result)

    # ── Test 2: Complex multi-source query ───────────────────────────────
    q = queries["complex"]
    result = EvalResult(
        test_id="rag_chat_complex",
        category="rag_chat",
        test_name="Complex Multi-Source Query",
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
            top_k=6,
        )
        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed

        answer = response.answer if hasattr(response, 'answer') else response.get("answer", "")
        result.output_chars = len(answer)
        result.input_chars = len(q["question"])
        result.actual_output_preview = answer[:500]

        fact_score = scoring.score_must_contain(answer, q.get("expected_facts", []))
        citation_score = scoring.score_has_citations(answer, min_citations=2)

        citations = response.citations if hasattr(response, 'citations') else response.get("citations", [])
        retrieved_text = " ".join([c.get("text", "") for c in citations]) if citations else ""
        retrieval_score = scoring.score_must_contain(retrieved_text, q.get("expected_facts", []))

        judge_model = getattr(settings, 'fast_model', settings.ollama_model)
        if judge_model != settings.ollama_model:
            judge_score = await scoring.llm_judge_score(q["question"], answer, judge_model)
        else:
            judge_score = 60

        speed_score = 100 if elapsed < 25000 else max(0, int(100 - (elapsed - 25000) / 500))

        result.accuracy_score = fact_score
        result.completeness_score = judge_score
        result.format_score = citation_score
        result.overall_score = int(
            fact_score * 0.35 + citation_score * 0.20 + judge_score * 0.30 + speed_score * 0.15
        )
        
        result.sub_scores = {
            "retrieval": retrieval_score,
            "facts": fact_score,
            "citations": citation_score,
            "judge": judge_score,
            "speed": speed_score
        }
        result.passed = result.overall_score >= 40

        print(f"[EVAL-RAG] Complex query: score={result.overall_score} ({elapsed:.0f}ms)")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-RAG] Complex query FAILED: {e}")

    results.append(result)
    return results
