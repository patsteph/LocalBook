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
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result.stamp_provider(settings.main_model)

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

        questions = quiz_output.questions if hasattr(quiz_output, 'questions') else []
        result.output_chars = sum(len(q.question) + len(q.answer) for q in questions)
        result.actual_output_preview = f"{len(questions)} questions generated"

        # ── Nothing produced is a zero, not a consolation prize ─────────────
        # This used to award `speed_score * 0.15` regardless, so a model that
        # failed INSTANTLY scored 15 while one that answered slowly scored less
        # on that component. It hid a real breakage (2026-09-22: quantized KV
        # made gemma return nothing, and the category still reported 15 rather
        # than 0) and it rewards exactly the wrong behaviour.
        if not questions:
            result.passed = False
            result.overall_score = 0
            result.failure_reason = (
                f"produced no questions in {elapsed:.0f}ms — structured generation "
                f"returned nothing parseable")
            result.sub_scores = {"questions": 0, "elapsed_ms": round(elapsed)}
            print(f"[EVAL-JSON] Score=0, 0 questions, {elapsed:.0f}ms — produced nothing")
            return [result]

        # ── 1. Count: did it return what was asked for? ─────────────────────
        count_score = min(100, int((len(questions) / max(1, num_questions)) * 100))

        # ── 2. Validity: required fields, PER QUESTION TYPE ─────────────────
        # `bool(options)` counted a one-option multiple choice as complete. The
        # schema differs by type and the check has to as well.
        def _valid(q) -> float:
            qt = (getattr(q, "question_type", "") or "multiple_choice").lower()
            opts = list(getattr(q, "options", None) or [])
            checks = [
                bool((getattr(q, "question", "") or "").strip()),
                bool((getattr(q, "answer", "") or "").strip()),
                bool((getattr(q, "explanation", "") or "").strip()),
            ]
            if qt == "multiple_choice":
                checks.append(len(opts) >= 2)
            elif qt == "true_false":
                checks.append({o.strip().lower() for o in opts} == {"true", "false"}
                              or not opts)
            else:
                checks.append(True)          # open types need no options
            return sum(checks) / len(checks) * 100

        validity_score = int(sum(_valid(q) for q in questions) / len(questions))

        # ── 3. Consistency: does the answer belong to its own question? ─────
        # Objective and judge-free: a multiple-choice answer that is not among
        # its options is broken output however fluent it reads, and nothing
        # previously checked it.
        def _consistent(q) -> bool:
            qt = (getattr(q, "question_type", "") or "multiple_choice").lower()
            ans = (getattr(q, "answer", "") or "").strip().lower()
            opts = [str(o).strip().lower() for o in (getattr(q, "options", None) or [])]
            if qt == "true_false":
                return ans in ("true", "false")
            if qt == "multiple_choice" and opts:
                return any(ans == o or ans in o or o in ans for o in opts if o)
            return bool(ans)
        consistency_score = int(sum(_consistent(q) for q in questions) / len(questions) * 100)

        # ── 4. Grounding: is it about the SOURCE, or plausible-sounding? ────
        # Was keyword bingo against a hardcoded list ("rag", "model", "embed"…)
        # scaled by 120, so six of seven terms scored full marks and a fluent
        # non-answer containing them could too. Measure against the actual
        # content instead: the verbatim evidence span when the model emits one,
        # else vocabulary overlap with the source.
        content_words = {w for w in
                         "".join(c.lower() if c.isalnum() else " " for c in content).split()
                         if len(w) > 4}
        grounded = 0
        for q in questions:
            quote = (getattr(q, "evidence_quote", "") or "").strip()
            if quote and quote.lower()[:60] in content.lower():
                grounded += 1
                continue
            terms = {w for w in
                     "".join(c.lower() if c.isalnum() else " " for c in
                             f"{q.question} {q.answer}").split() if len(w) > 4}
            if terms and len(terms & content_words) / len(terms) >= 0.25:
                grounded += 1
        grounding_score = int(grounded / len(questions) * 100)

        # Speed is REPORTED, never scored. Mixing it into a capability score is
        # what let a total failure outscore a slow success; throughput has its
        # own measurement in the run's performance profile.
        speed_score = 100 if elapsed < 30000 else max(0, int(100 - (elapsed - 30000) / 500))

        result.accuracy_score = consistency_score
        result.completeness_score = validity_score
        result.format_score = count_score
        result.overall_score = int(
            count_score * 0.25 + validity_score * 0.25
            + consistency_score * 0.25 + grounding_score * 0.25
        )
        result.passed = result.overall_score >= 60
        result.sub_scores = {
            "questions": len(questions),
            "requested": num_questions,
            "count": count_score,
            "validity": validity_score,
            "consistency": consistency_score,
            "grounding": grounding_score,
            "elapsed_ms": round(elapsed),
            "speed_score_unweighted": speed_score,
        }
        if not result.passed:
            weakest = min(("count", count_score), ("validity", validity_score),
                          ("consistency", consistency_score), ("grounding", grounding_score),
                          key=lambda kv: kv[1])
            result.failure_reason = f"weakest dimension: {weakest[0]} at {weakest[1]}"

        print(f"[EVAL-JSON] Score={result.overall_score} "
              f"(count={count_score} valid={validity_score} "
              f"consistent={consistency_score} grounded={grounding_score}), "
              f"{len(questions)} questions, {elapsed:.0f}ms")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-JSON] FAILED: {e}")

    return [result]
