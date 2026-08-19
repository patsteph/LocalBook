"""A phase that times out is NO DATA, not a zero score.

Found by running the first real A/B (2026-08-19): the Ollama run scored 67.6 D, dragged down by
Streaming and Fast Follow-Up both at F/0. Neither had failed — both had exceeded the 180 s phase
timeout while the machine sat at 0.43 GB available, and `_run_phase_with_timeout` returns `[]`,
which `compute_category_score` scores as 0.

Why this matters beyond tidiness: the engine A/B runs on a memory-constrained machine BY DESIGN
(16 GB is the shipping floor). Whichever run happened to trip the timeout would score arbitrarily
worse, and the delta would be attributed to the engine rather than to the timeout. That is a
false conclusion of exactly the kind the whole measurement stage exists to prevent.
"""
from evaluator.evaluator_service import _build_category
from evaluator.models import EvalResult


def _passing(test_id="t1"):
    # `compute_category_score` averages `overall_score`, not `score`.
    r = EvalResult(test_id=test_id, category="c", passed=True)
    r.overall_score = 100.0
    return r


def test_a_phase_with_no_results_is_skipped_not_failed():
    cat = _build_category("streaming", "Streaming Generation", [])
    assert cat.skipped is True
    assert cat.verdict == "not_applicable"
    assert "timed out" in cat.skip_reason
    # `passed` must not read as a failure — it is excluded, not flunked.
    assert cat.passed is True


def test_a_real_failure_is_still_a_failure():
    """The fix must not turn genuine failures into skips."""
    bad = EvalResult(test_id="t", category="c", passed=False)
    bad.overall_score = 0.0
    cat = _build_category("streaming", "Streaming Generation", [bad])
    assert cat.skipped is False
    assert cat.verdict == "fail"


def test_a_passing_phase_is_unaffected():
    cat = _build_category("rag_chat", "RAG Chat", [_passing(), _passing("t2")])
    assert cat.skipped is False
    assert cat.verdict == "pass"
    assert cat.score > 0


def test_timeouts_are_recorded_so_they_cannot_pass_silently():
    """A run with half its phases missing must not report a healthy number quietly."""
    import asyncio

    from evaluator import evaluator_service as svc

    svc._TIMED_OUT.clear()

    async def _slow():
        await asyncio.sleep(5)
        return ["never"]

    out = asyncio.run(svc._run_phase_with_timeout(_slow(), "Streaming", timeout=0.05))
    assert out == []
    assert "Streaming" in svc._TIMED_OUT
    svc._TIMED_OUT.clear()
