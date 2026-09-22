"""Scoring structured output on whether it is USABLE, not on how fast it failed.

2026-09-22: a KV-quantization bug made the main model return nothing, and this
category reported **15**, not 0 — because `speed_score * 0.15` was awarded
unconditionally and a total failure is very fast. A model that produced perfect
JSON slowly scored LOWER on that component than one that instantly produced
none. The A/B that found the bug nearly lost it in the noise.

Three other things it was measuring badly:

  * `bool(options)` counted a one-option multiple choice as complete.
  * Nothing checked that a multiple-choice answer was among its own options —
    the most basic correctness property a quiz has.
  * Relevance was keyword bingo against a hardcoded list ("rag", "model",
    "embed"…) scaled by 120, so six of seven terms scored full marks and a
    fluent non-answer containing them scored the same as a grounded one.
"""
import asyncio
import types

import pytest


def _q(question="What is retrieval augmented generation?",
       answer="Combining retrieval with generation",
       explanation="Because documents are retrieved first.",
       qtype="multiple_choice", options=None, evidence=None):
    return types.SimpleNamespace(
        question=question, answer=answer, explanation=explanation,
        question_type=qtype,
        options=options if options is not None else
                ["Combining retrieval with generation", "A database", "A GPU"],
        evidence_quote=evidence)


async def _score(questions, *, requested=3, content=None, attempts=1,
                 wanted_types=None):
    """Drive the real runner with a stubbed quiz generator."""
    from evaluator.test_runners import structured_json as sj
    import services.structured_llm as slm
    import storage.source_store as ss

    content = content or (
        "Retrieval augmented generation combines retrieval with generation. "
        "Documents are embedded into vectors and retrieved by similarity before "
        "the language model produces its answer.")

    class _Store:
        async def list(self, nb):
            return [{"filename": "doc.md", "content": content}]

    class _LLM:
        async def generate_quiz(self, **kw):
            return types.SimpleNamespace(questions=list(questions), attempts=attempts)

    orig_store, orig_llm = ss.source_store, slm.structured_llm
    ss.source_store, slm.structured_llm = _Store(), _LLM()
    try:
        cfg = {"num_questions": requested}
        if wanted_types is not None:
            cfg["question_types"] = wanted_types
        res = await sj.run("nb", {"quiz_generation": cfg}, "combo", "hw")
        return res[0]
    finally:
        ss.source_store, slm.structured_llm = orig_store, orig_llm


# ── the bug ─────────────────────────────────────────────────────────────────

def test_producing_nothing_scores_zero_not_fifteen():
    """THE regression. Failing instantly must not earn speed points — that is
    how a total breakage reported 15 and looked like a quality wobble."""
    r = asyncio.run(_score([]))
    assert r.overall_score == 0
    assert r.passed is False
    assert "no questions" in r.failure_reason


def test_speed_never_contributes_to_the_score():
    """Reported for visibility, excluded from the total. A capability score
    that mixes in latency cannot be read as either."""
    r = asyncio.run(_score([_q(), _q(), _q()]))
    assert "speed_score_unweighted" in r.sub_scores
    w = {"count": 0.15, "validity": 0.20, "consistency": 0.20, "grounding": 0.15,
         "distractors": 0.10, "type_mix": 0.10, "reliability": 0.10}
    parts = [(r.sub_scores[k], wt) for k, wt in w.items()
             if r.sub_scores.get(k) is not None]
    expected = sum(v * wt for v, wt in parts) / sum(wt for _, wt in parts)
    assert abs(r.overall_score - expected) <= 1


# ── what it measures instead ────────────────────────────────────────────────

def test_a_good_quiz_scores_well():
    r = asyncio.run(_score([_q(), _q(), _q()]))
    assert r.overall_score >= 75, r.sub_scores
    assert r.passed is True


def test_an_answer_that_is_not_among_its_options_is_caught():
    """The most basic correctness property a multiple-choice question has, and
    nothing checked it."""
    bad = _q(answer="Something else entirely", options=["A database", "A GPU"])
    r = asyncio.run(_score([bad, bad, bad]))
    assert r.sub_scores["consistency"] == 0
    assert r.overall_score < 80


def test_a_one_option_multiple_choice_is_not_complete():
    """`bool(options)` passed this."""
    thin = _q(options=["Only one"])
    r = asyncio.run(_score([thin, thin, thin]))
    assert r.sub_scores["validity"] < 100


def test_true_false_is_validated_on_its_own_terms():
    good = _q(answer="True", qtype="true_false", options=["True", "False"])
    bad = _q(answer="Perhaps", qtype="true_false", options=["True", "False"])
    assert asyncio.run(_score([good] * 3)).sub_scores["consistency"] == 100
    assert asyncio.run(_score([bad] * 3)).sub_scores["consistency"] == 0


def test_returning_fewer_questions_than_asked_costs_marks():
    r = asyncio.run(_score([_q()], requested=3))
    assert r.sub_scores["count"] == 33


def test_grounding_measures_the_actual_source_not_a_keyword_list():
    """A fluent answer about something else must not score as grounded merely
    for containing words from a hardcoded list."""
    off_topic = _q(question="What is the capital of Portugal?",
                   answer="Lisbon", options=["Lisbon", "Porto", "Faro"],
                   explanation="It is the capital.")
    r = asyncio.run(_score([off_topic] * 3))
    assert r.sub_scores["grounding"] == 0, r.sub_scores


def test_a_verbatim_evidence_quote_counts_as_grounded():
    """The schema has `evidence_quote` for exactly this; using it beats guessing
    from vocabulary overlap."""
    quoted = _q(evidence="Documents are embedded into vectors")
    r = asyncio.run(_score([quoted] * 3))
    assert r.sub_scores["grounding"] == 100


def test_a_failing_run_names_its_weakest_dimension():
    """"It scored 40" is not actionable; "consistency at 0" is."""
    bad = _q(answer="Not an option", options=["A", "A"],
             question="Capital of Portugal?", explanation="")
    r = asyncio.run(_score([bad], requested=6, attempts=3))
    assert r.passed is False, r.sub_scores
    assert r.failure_reason and "weakest dimension" in r.failure_reason


def test_a_dimension_with_nothing_to_judge_is_excluded_not_awarded():
    """Free marks for a measurement that did not happen is what let a total
    failure score 15 for being fast. A deck with no multiple-choice questions
    must not collect full distractor marks it never earned."""
    fib = _q(qtype="fill_in_the_blank", options=None,
             evidence="Documents are embedded into vectors")
    r = asyncio.run(_score([fib] * 3))
    assert r.sub_scores["distractors"] is None
    assert r.sub_scores["type_mix"] is None


# ── sharpening: dimensions a HEALTHY model can still lose points on ─────────
#
# After the rework the category scored a flat 100 for a working model, which
# detects breakage and cannot rank two models that both work — the same ceiling
# documented for nine other categories. These three measure things a competent
# model does better than a merely adequate one.

def test_needing_retries_costs_marks():
    """structured_llm retries up to three times. A model that needs three tries
    to emit valid structure is worse at structured output than one that gets it
    first time, and that difference was only ever a log line."""
    first = asyncio.run(_score([_q()] * 3, attempts=1))
    second = asyncio.run(_score([_q()] * 3, attempts=2))
    third = asyncio.run(_score([_q()] * 3, attempts=3))
    assert first.sub_scores["reliability"] == 100
    assert second.sub_scores["reliability"] == 60
    assert third.sub_scores["reliability"] == 25
    assert first.overall_score > second.overall_score > third.overall_score


def test_collapsing_a_deck_into_one_type_costs_marks():
    """The generator's own comments note models collapse to a single easy type.
    Nothing measured it, and it is exactly what separates two valid outputs."""
    wanted = ["multiple_choice", "true_false", "fill_in_the_blank"]
    varied = [_q(qtype="multiple_choice"),
              _q(qtype="true_false", answer="True", options=["True", "False"]),
              _q(qtype="fill_in_the_blank", options=None)]
    collapsed = [_q(qtype="multiple_choice")] * 3
    assert asyncio.run(_score(varied, wanted_types=wanted)).sub_scores["type_mix"] == 100
    assert asyncio.run(_score(collapsed, wanted_types=wanted)).sub_scores["type_mix"] == 33


def test_duplicate_options_are_caught():
    dupes = _q(options=["A database", "A database", "A GPU"])
    assert asyncio.run(_score([dupes] * 3)).sub_scores["distractors"] < 100


def test_a_systematically_longest_correct_answer_is_penalised():
    """The oldest giveaway in multiple-choice writing. One long answer is fair;
    every single one is a model padding the right option."""
    tell = _q(answer="Combining retrieval with generation over a vector index",
              options=["Combining retrieval with generation over a vector index", "A DB", "A GPU"])
    fair = _q(answer="A GPU", options=["Combining retrieval with generation", "A DB", "A GPU"])
    assert asyncio.run(_score([tell] * 4)).sub_scores["distractors"] < \
        asyncio.run(_score([fair] * 4)).sub_scores["distractors"]


def test_one_long_answer_among_several_is_not_punished():
    """The penalty must be statistical, not per-question — a correct answer is
    sometimes legitimately the longest."""
    tell = _q(answer="Combining retrieval with generation over a vector index",
              options=["Combining retrieval with generation over a vector index", "A DB", "A GPU"])
    fair = _q(answer="A GPU", options=["Combining retrieval with generation", "A DB", "A GPU"])
    assert asyncio.run(_score([tell] + [fair] * 3)).sub_scores["distractors"] == 100


def test_the_category_can_now_separate_two_working_models():
    """The point of the exercise. Both produce valid, consistent, grounded
    output; one is better at it."""
    wanted = ["multiple_choice", "true_false"]
    strong = asyncio.run(_score(
        [_q(qtype="multiple_choice", evidence="Documents are embedded into vectors"),
         _q(qtype="true_false", answer="True", options=["True", "False"],
            evidence="retrieved by similarity"),
         _q(qtype="multiple_choice", evidence="Documents are embedded into vectors")],
        wanted_types=wanted, attempts=1))
    adequate = asyncio.run(_score(
        [_q(qtype="multiple_choice"), _q(qtype="multiple_choice"),
         _q(qtype="multiple_choice")],
        wanted_types=wanted, attempts=2))
    assert strong.overall_score > adequate.overall_score
    assert strong.passed and adequate.passed, "both should still be healthy, just ranked"
