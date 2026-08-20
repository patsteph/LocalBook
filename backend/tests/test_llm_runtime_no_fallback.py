"""What `llm_runtime` does when no engine can serve a call.

Stage 4 Phase 2.2 removed the HTTP transport, so every path that used to degrade to Ollama
now has nothing to degrade to. The failure behaviour is deliberately NOT uniform, and these
tests pin the asymmetry:

  · text and vision return an empty result and log an error — visible to the user, retryable,
    and the `never raises` contract that ~100 call sites depend on is preserved;
  · embeddings RAISE — a wrong or zero vector is written into LanceDB and is only fixable by
    re-ingesting every notebook. This install already carries 104 zero vectors (1.42%) from
    the era when embeddings failed quietly. Failing one ingest loudly is the cheaper error.

If someone later "fixes" the embedding raise into a graceful empty return, these fail — which
is the point.
"""
import asyncio

import pytest

from services.llm_runtime import llm_runtime


@pytest.fixture
def no_engine(monkeypatch):
    """Force every dispatch to find no usable engine."""
    monkeypatch.setattr("services.mlx_engine.mlx_engine.available", lambda: False)


# ── The deleted Ollama surface ──────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["chat", "stream_generate", "check_model", "list_models",
                                  "_get_client", "close"])
def test_the_ollama_surface_is_gone(name):
    """Each had zero callers when it was deleted. A reappearance means someone reintroduced
    an HTTP path instead of routing through the engine."""
    assert not hasattr(llm_runtime, name)


def test_no_http_client_is_constructed_anywhere_in_the_module():
    """The module used to hold a pooled httpx.AsyncClient. Nothing should build one now —
    an httpx import here is the signature of a hand-rolled call creeping back in."""
    import services.llm_runtime as m

    src = open(m.__file__.replace(".pyc", ".py")).read()
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#") and not l.strip().startswith('"'))
    assert "httpx.AsyncClient" not in code
    assert "/api/generate" not in code and "/api/embed" not in code


# ── Text + vision: empty result, never raise ────────────────────────────────────

def test_generate_returns_empty_rather_than_raising(no_engine):
    """~100 call sites do `result.get("response")` with no try/except. Raising here would
    turn one unavailable model into a cascade of unhandled exceptions."""
    out = asyncio.run(llm_runtime.generate("hello"))
    assert out == {"response": ""}


def test_vision_describe_returns_an_error_string(no_engine):
    """Contract is `-> str`, and callers concatenate it. Must not be None or an exception."""
    out = asyncio.run(llm_runtime.vision_describe("ZmFrZQ==", "describe"))
    assert isinstance(out, str) and out.startswith("Error:")


# ── Embeddings: raise, always ───────────────────────────────────────────────────

def test_embed_batch_raises_instead_of_zero_filling(no_engine):
    """THE test. Zero vectors are unretrievable forever and indistinguishable from real ones
    once written."""
    with pytest.raises(RuntimeError, match="unserviceable"):
        asyncio.run(llm_runtime.embed_batch(["a", "b", "c"]))


def test_single_embed_raises_too(no_engine):
    with pytest.raises(RuntimeError, match="unserviceable"):
        asyncio.run(llm_runtime.embed("a query"))


def test_an_empty_batch_is_still_a_no_op(no_engine):
    """Nothing to corrupt, so nothing to raise about — callers pass empty lists routinely."""
    assert asyncio.run(llm_runtime.embed_batch([])) == []


def test_a_wrong_length_vector_is_zero_filled_not_dropped(monkeypatch):
    """Dropping a bad vector would misalign EVERY subsequent vector with its chunk — a worse
    corruption than one unretrievable chunk. So this one case still zero-fills, and reports."""
    from config import settings

    async def _short(texts):
        return [[0.1] * settings.embedding_dim, [0.1] * 3, [0.1] * settings.embedding_dim]

    monkeypatch.setattr(llm_runtime, "_mlx_embed_or_none", _short)
    out = asyncio.run(llm_runtime.embed_batch(["a", "b", "c"]))
    assert len(out) == 3, "count must match the input or chunks misalign"
    assert not any(out[1]), "the wrong-length vector should be zeroed"
    assert any(out[0]) and any(out[2]), "good vectors must survive intact"


# ── The surviving engine-neutral surface ────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "effective_num_ctx_cap", "compute_num_ctx", "clamp_num_predict",
    "seconds_since_ollama_activity", "model_lane", "PRIORITY_BACKGROUND",
])
def test_engine_neutral_helpers_survived_the_excise(name):
    """These are imported across ~10 modules and have nothing to do with any engine. The
    excise deleted 45% of this file; losing one of these breaks callers silently."""
    import services.llm_runtime as m

    assert hasattr(m, name)
