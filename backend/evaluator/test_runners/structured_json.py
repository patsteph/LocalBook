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
        wanted_types = [t.lower() for t in (quiz_config.get("question_types") or [])]

        quiz_output = await structured_llm.generate_quiz(
            content=content,
            num_questions=num_questions,
            difficulty=difficulty,
            question_types=list(wanted_types) or None,
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

        # ── 5. Distractors: are the wrong options actually wrong-but-plausible? ──
        # Two objective tells that separate a competent quiz from a passable one:
        # duplicated or empty options, and a correct answer that is SYSTEMATICALLY
        # the longest — the oldest giveaway in multiple-choice writing. One long
        # answer is fair; five out of six is a model padding the right one.
        mc = [q for q in questions
              if (getattr(q, "question_type", "") or "multiple_choice").lower() == "multiple_choice"
              and len(list(getattr(q, "options", None) or [])) >= 2]
        if mc:
            clean = 0
            longest_is_answer = 0
            for q in mc:
                opts = [str(o).strip() for o in (getattr(q, "options", None) or [])]
                distinct = len({o.lower() for o in opts if o}) == len([o for o in opts if o])
                if distinct and all(opts):
                    clean += 1
                ans = (getattr(q, "answer", "") or "").strip()
                if opts and ans and len(ans) >= max(len(o) for o in opts):
                    longest_is_answer += 1
            clean_pct = clean / len(mc) * 100
            giveaway = longest_is_answer / len(mc)
            # Tolerate up to half; penalise the systematic case.
            giveaway_penalty = max(0.0, (giveaway - 0.5) * 2) * 40
            distractor_score = int(max(0.0, clean_pct - giveaway_penalty))
        else:
            distractor_score = None     # no multiple-choice questions to judge

        # ── 6. Type mix: did it honour the variety asked for? ───────────────
        # The generator's own comments note that models collapse a deck into a
        # single easy type. Nothing measured it, and it is exactly the kind of
        # instruction-following that separates two otherwise-valid outputs.
        # Two distinct ways to ignore the instruction, and they need measuring
        # separately because a model can do one without the other:
        #
        #   coverage   — did it USE the variety asked for? (missing true_false)
        #   compliance — did it STAY WITHIN it? (substituting short_answer)
        #
        # Compliance is counted per QUESTION, not per type: five off-list
        # questions is a worse violation than one, and a type-level set
        # comparison cannot tell those apart.
        if wanted_types:
            wanted_set = set(wanted_types)
            types_per_q = [(getattr(q, "question_type", "") or "").lower() for q in questions]
            got = set(types_per_q)
            coverage = len(got & wanted_set) / len(wanted_set) * 100
            in_set = sum(1 for t in types_per_q if t in wanted_set)
            compliance = in_set / len(types_per_q) * 100
            mix_score = int((coverage + compliance) / 2)
            unrequested = sorted(got - wanted_set)
        else:
            mix_score = None            # no mix was requested
            coverage = compliance = None
            unrequested = []

        # ── 7. First attempt: reliability, not just capability ─────────────
        # structured_llm retries up to three times. A model that needs three
        # tries to emit valid structure is measurably worse at structured output
        # than one that gets it first time, and until now that difference was
        # only a log line. Full marks first time, then a steep drop.
        attempts = int(getattr(quiz_output, "attempts", 1) or 1)
        reliability_score = {1: 100, 2: 60}.get(attempts, 25)

        # Speed is REPORTED, never scored. Mixing it into a capability score is
        # what let a total failure outscore a slow success; throughput has its
        # own measurement in the run's performance profile.
        speed_score = 100 if elapsed < 30000 else max(0, int(100 - (elapsed - 30000) / 500))

        result.accuracy_score = consistency_score
        result.completeness_score = validity_score
        result.format_score = count_score
        # Weighted so the four correctness dimensions still dominate, while the
        # three sharpeners — which a healthy model CAN lose points on — decide
        # between models that would otherwise all sit at 100.
        # A dimension with nothing to judge is EXCLUDED and its weight
        # redistributed — never awarded 100. Handing out free marks for a
        # measurement that did not happen is exactly what let a total failure
        # score 15 for being fast, and one garbage question would otherwise
        # collect full marks for distractors and type mix it never had.
        weighted = [
            (count_score, 0.15), (validity_score, 0.20),
            (consistency_score, 0.20), (grounding_score, 0.15),
            (distractor_score, 0.10), (mix_score, 0.10),
            (reliability_score, 0.10),
        ]
        measured = [(v, w) for v, w in weighted if v is not None]
        total_weight = sum(w for _, w in measured) or 1.0
        result.overall_score = int(sum(v * w for v, w in measured) / total_weight)
        result.passed = result.overall_score >= 60
        result.sub_scores = {
            "questions": len(questions),
            "requested": num_questions,
            "count": count_score,
            "validity": validity_score,
            "consistency": consistency_score,
            "grounding": grounding_score,
            "distractors": distractor_score,
            "type_mix": mix_score,
            "type_coverage": None if coverage is None else int(coverage),
            "type_compliance": None if compliance is None else int(compliance),
            "unrequested_types": unrequested,
            "reliability": reliability_score,
            "attempts": attempts,
            "types_seen": sorted({(getattr(q, "question_type", "") or "?").lower()
                                  for q in questions}),
            "elapsed_ms": round(elapsed),
            "speed_score_unweighted": speed_score,
        }
        if not result.passed:
            named = [("count", count_score), ("validity", validity_score),
                     ("consistency", consistency_score), ("grounding", grounding_score),
                     ("distractors", distractor_score), ("type_mix", mix_score),
                     ("reliability", reliability_score)]
            weakest = min((kv for kv in named if kv[1] is not None),
                          key=lambda kv: kv[1], default=("nothing measurable", 0))
            result.failure_reason = f"weakest dimension: {weakest[0]} at {weakest[1]}"

        print(f"[EVAL-JSON] Score={result.overall_score} "
              f"(count={count_score} valid={validity_score} consistent={consistency_score} "
              f"grounded={grounding_score} distract={distractor_score if distractor_score is not None else '-'} "
              f"mix={mix_score if mix_score is not None else '-'}"
              f"{'(+' + ','.join(unrequested) + ')' if unrequested else ''} "
              f"attempt{attempts}), {len(questions)}/{num_questions} questions, {elapsed:.0f}ms")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-JSON] FAILED: {e}")

    return [result]
