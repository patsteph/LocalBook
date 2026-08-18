"""An Ollama context cap must not truncate MLX output.

`llm_service` sizes `num_ctx` from the OLLAMA model's cap (`effective_num_ctx_cap` — 16384 for
gemma, 8192 for phi) and then clamps `num_predict` to fit that window. `mlx_engine` accepts
`num_ctx` and DISCARDS it, using the MLX model's own window instead — so the clamp was shortening
MLX long-form generation against a limit that does not apply to it. Live quality bug, found in the
MLX Locker/Evaluator tie-off research (2026-08-18).
"""
import asyncio

import pytest


class _Recorder:
    """Stands in for mlx_engine; records what the seam actually passes it."""

    def __init__(self):
        self.kwargs = None

    def available(self):
        return True

    async def generate(self, prompt, **kwargs):
        self.kwargs = kwargs
        return {"response": "ok", "eval_count": 5, "eval_duration": 1_000_000}


@pytest.fixture
def seam(monkeypatch):
    from services import llm_service
    import services.mlx_engine as me

    rec = _Recorder()
    monkeypatch.setattr(me, "mlx_engine", rec)
    monkeypatch.setattr(me, "mlx_model_for_role", lambda name: "mlx-community/fake-4bit")
    # Keep the test off the network + off the registry's opinions.
    monkeypatch.setattr(llm_service, "_get_model_options", lambda m: {})
    monkeypatch.setattr(llm_service, "_get_rag_profile", lambda m: {})
    return llm_service, rec


def test_mlx_gets_the_requested_num_predict_not_the_ollama_clamped_one(seam):
    """A long prompt + a big output request is exactly the case that used to be silently cut."""
    llm_service, rec = seam
    long_prompt = "x" * 60_000          # ~20k tokens at the 3-chars/token estimate
    asyncio.run(llm_service.generate_text(
        "", long_prompt, model="phi4-mini:latest",
        num_predict=4000, voice_modifier=False,
    ))
    assert rec.kwargs is not None, "MLX branch did not run"
    assert rec.kwargs["num_predict"] == 4000, (
        "MLX was handed the Ollama-window-clamped num_predict; long-form output is being "
        "truncated against a context cap MLX does not use"
    )


def test_short_prompt_is_unaffected(seam):
    """The clamp never bound here anyway — guards against over-correcting."""
    llm_service, rec = seam
    asyncio.run(llm_service.generate_text(
        "", "hello", model="phi4-mini:latest",
        num_predict=500, voice_modifier=False,
    ))
    assert rec.kwargs["num_predict"] == 500
