"""Smoke tests for the evaluator upgrade (v1.8.2).

Runs without a notebook, without live backends — pure unit-level invariants
for the capability matrix, skip-aware scoring, and provider stamping.

Invoke: `python -m evaluator._eval_smoke`
"""

from __future__ import annotations


def _test_capabilities():
    from evaluator.capabilities import capabilities_for, FEATURES

    bonsai = capabilities_for("bonsai-8b")
    assert bonsai.provider == "llama_server", bonsai.provider
    assert not bonsai.supports(FEATURES.VISION)
    assert not bonsai.supports(FEATURES.EMBEDDINGS)
    assert bonsai.supports(FEATURES.LARGE_CONTEXT), bonsai.context_window
    assert not bonsai.supports(FEATURES.KEEP_ALIVE)

    olmo = capabilities_for("olmo-3:7b-instruct")
    assert olmo.provider == "ollama"
    assert olmo.supports(FEATURES.KEEP_ALIVE)

    # Permissive fallback for community models
    unknown = capabilities_for("some-community-model:1b")
    assert unknown.supports(FEATURES.TEXT_GENERATE)
    print("[smoke] capabilities OK")


def _test_stamp_provider():
    from evaluator.models import EvalResult

    r = EvalResult(test_id="t", category="c", test_name="n")
    r.stamp_provider("bonsai-8b")
    assert r.provider == "llama_server"
    assert r.backend_url.startswith("http")
    assert r.model_context_window >= 32768   # Bonsai native 64k
    assert r.model_used == "bonsai-8b"

    r2 = EvalResult(test_id="t2", category="c", test_name="n")
    r2.stamp_provider("olmo-3:7b-instruct")
    assert r2.provider == "ollama"
    print("[smoke] stamp_provider OK")


def _test_mark_skipped():
    from evaluator.models import EvalResult

    r = EvalResult(test_id="t", category="c", test_name="n")
    r.mark_skipped("no vision support")
    assert r.skipped is True
    assert r.skip_reason == "no vision support"
    assert r.passed is True            # skip is not a fail
    assert r.overall_score == 0
    print("[smoke] mark_skipped OK")


def _test_scoring_skip_awareness():
    from evaluator.models import EvalResult
    from evaluator import scoring

    # Mixed results: one passes, one is skipped
    passed = EvalResult(test_id="a", category="x", test_name="n", overall_score=80, passed=True)
    skipped = EvalResult(test_id="b", category="x", test_name="n")
    skipped.mark_skipped("not applicable")

    # Category score should be the non-skipped mean (80), not (80+0)/2
    score, _grade = scoring.compute_category_score([passed, skipped])
    assert score == 80.0, score

    # All skipped → score 0 (marker), to be filtered out at summary level
    only_skip = EvalResult(test_id="c", category="x", test_name="n")
    only_skip.mark_skipped("not applicable")
    score2, _g2 = scoring.compute_category_score([only_skip])
    assert score2 == 0.0, score2
    print("[smoke] scoring skip-awareness OK")


def _test_overall_excludes_skipped():
    from evaluator import scoring

    # Three categories; vision is "skipped" (it would be filtered out by the
    # evaluator service before reaching compute_overall_score).
    scores_with = {"rag_chat": 80, "streaming": 70, "vision": 0}
    scores_without = {"rag_chat": 80, "streaming": 70}
    weights = {"rag_chat": 10, "streaming": 10, "vision": 10}

    with_skip, _g1 = scoring.compute_overall_score(scores_with, weights)
    without_skip, _g2 = scoring.compute_overall_score(scores_without, weights)
    # With vision included as 0, overall drops; without it, overall is honest.
    assert with_skip < without_skip, (with_skip, without_skip)
    print("[smoke] overall-excludes-skipped OK")


def _test_provider_translator_passthrough():
    """Make sure the llama-server translator keeps top_k and repeat_penalty."""
    from services.llm_provider import ollama_to_openai_payload

    payload = {
        "model": "bonsai-8b",
        "prompt": "hi",
        "options": {
            "temperature": 0.5,
            "top_k": 20,
            "repeat_penalty": 1.1,
            "num_predict": 50,
        },
    }
    out = ollama_to_openai_payload(payload, is_chat=False)
    assert out["top_k"] == 20
    assert out["repeat_penalty"] == 1.1
    assert out["max_tokens"] == 50
    assert out["temperature"] == 0.5
    print("[smoke] translator passthrough OK")


def main():
    _test_capabilities()
    _test_stamp_provider()
    _test_mark_skipped()
    _test_scoring_skip_awareness()
    _test_overall_excludes_skipped()
    _test_provider_translator_passthrough()
    print("\n[evaluator._eval_smoke] All v1.8.2 smoke tests passed.")


if __name__ == "__main__":
    main()
