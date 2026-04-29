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

        # ── Industry-standard RAG metrics (RAGAS-style) ────────────────
        # 1. Context Recall: did retrieval find the gold chunk?
        context_recall = scoring.score_context_recall(citations, q.get("gold_chunk_marker", ""))
        
        # 2. Answer Correctness: semantic similarity to reference answer
        ref_answer = q.get("reference_answer", "")
        if ref_answer:
            answer_correctness = await scoring.score_semantic_similarity(answer, ref_answer)
        else:
            answer_correctness = scoring.score_must_contain(answer, q.get("expected_facts", []))
        
        # 3. Faithfulness: does answer use only retrieved context (no hallucination)?
        judge_model = getattr(settings, 'ollama_fast_model', settings.ollama_model)
        if judge_model != settings.ollama_model and citations:
            faithfulness = await scoring.score_faithfulness(answer, citations, judge_model)
        else:
            faithfulness = 60
        
        # 4. Citation presence (lightweight format check, deweighted)
        citation_score = scoring.score_has_citations(answer, min_citations=1)
        
        # 5. Speed (separate quality axis)
        speed_score = 100 if elapsed < 15000 else max(0, int(100 - (elapsed - 15000) / 500))

        result.accuracy_score = answer_correctness
        result.completeness_score = faithfulness
        result.format_score = citation_score
        # Weighted: correctness (35%) + recall (25%) + faithfulness (20%) + citations (10%) + speed (10%)
        result.overall_score = int(
            answer_correctness * 0.35 + context_recall * 0.25 + faithfulness * 0.20 +
            citation_score * 0.10 + speed_score * 0.10
        )
        
        result.sub_scores = {
            "context_recall": context_recall,
            "answer_correctness": answer_correctness,
            "faithfulness": faithfulness,
            "citations": citation_score,
            "speed": speed_score,
        }
        result.passed = result.overall_score >= 40
        if not result.passed:
            result.failure_reason = f"Score {result.overall_score} < 40"

        print(f"[EVAL-RAG] Simple query: score={result.overall_score} "
              f"(recall={context_recall}, correctness={answer_correctness}, "
              f"faithful={faithfulness}, citations={citation_score}, speed={speed_score}) "
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

        citations = response.citations if hasattr(response, 'citations') else response.get("citations", [])
        
        # ── Industry-standard RAG metrics (RAGAS-style) ────────────────
        context_recall = scoring.score_context_recall(citations, q.get("gold_chunk_marker", ""))
        
        ref_answer = q.get("reference_answer", "")
        if ref_answer:
            answer_correctness = await scoring.score_semantic_similarity(answer, ref_answer)
        else:
            answer_correctness = scoring.score_must_contain(answer, q.get("expected_facts", []))
        
        judge_model = getattr(settings, 'ollama_fast_model', settings.ollama_model)
        if judge_model != settings.ollama_model and citations:
            faithfulness = await scoring.score_faithfulness(answer, citations, judge_model)
        else:
            faithfulness = 60
        
        citation_score = scoring.score_has_citations(answer, min_citations=2)
        speed_score = 100 if elapsed < 25000 else max(0, int(100 - (elapsed - 25000) / 500))

        result.accuracy_score = answer_correctness
        result.completeness_score = faithfulness
        result.format_score = citation_score
        result.overall_score = int(
            answer_correctness * 0.35 + context_recall * 0.25 + faithfulness * 0.20 +
            citation_score * 0.10 + speed_score * 0.10
        )
        
        result.sub_scores = {
            "context_recall": context_recall,
            "answer_correctness": answer_correctness,
            "faithfulness": faithfulness,
            "citations": citation_score,
            "speed": speed_score,
        }
        result.passed = result.overall_score >= 40

        print(f"[EVAL-RAG] Complex query: score={result.overall_score} "
              f"(recall={context_recall}, correctness={answer_correctness}, "
              f"faithful={faithfulness}) {elapsed:.0f}ms")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-RAG] Complex query FAILED: {e}")

    results.append(result)
    return results
