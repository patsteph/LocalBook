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


# ── The SYNC embedding path, which the audit's excise list missed ───────────────

def test_the_sync_embed_path_also_raises(monkeypatch):
    """`rag_embeddings` has its own sync helpers that bypass llm_runtime entirely. They
    fell through to Ollama over `requests` and zero-filled the batch on failure — so with
    Ollama gone, every sync embed would have written unretrievable vectors into LanceDB
    while looking like it worked. Same contract as the async path, for the same reason.
    """
    from services import rag_embeddings as r

    monkeypatch.setattr(r, "_mlx_embed_sync_or_none", lambda texts: None)
    with pytest.raises(RuntimeError, match="unserviceable"):
        r._get_embeddings_batch_sync(["a", "b"])
    with pytest.raises(RuntimeError, match="unserviceable"):
        r._get_embedding_sync("a")   # NB: `_get_embedding` is the ASYNC sibling


def test_the_sync_path_still_zero_fills_a_wrong_length_vector(monkeypatch):
    """Same alignment argument as the async side: a dropped vector shifts every later
    chunk onto the wrong text."""
    from config import settings
    from services import rag_embeddings as r

    monkeypatch.setattr(r, "_mlx_embed_sync_or_none",
                        lambda texts: [[0.1] * settings.embedding_dim, [0.1] * 3])
    out = r._get_embeddings_batch_sync(["a", "b"])
    assert len(out) == 2 and any(out[0]) and not any(out[1])


def test_no_sync_http_embed_path_remains():
    """`requests.post(.../api/embed)` here was invisible to every llm_runtime-level guard."""
    import services.rag_embeddings as m

    src = open(m.__file__).read()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "/api/embed" not in code
    assert "requests.post" not in code


# ── The presence signal the excise nearly killed ────────────────────────────────

def test_an_mlx_call_marks_the_system_busy(monkeypatch):
    """The activity marker hangs off the model lane, and the lane used to be acquired by the
    HTTP path. Deleting that transport left NOTHING marking activity, so
    `presence.system_busy()` would report idle forever and enrichment would fire straight
    into a live MLX ingest — the exact 2026-06-23 failure the signal exists to prevent.

    The MLX dispatch now joins the lane, which restores both this signal and FOREGROUND
    preemption (mlx_engine serializes per model but has no priority concept).
    """
    import services.llm_runtime as lr
    from services import presence

    async def _fake_generate(prompt, **kw):
        return {"response": "hi", "eval_count": 5, "eval_duration": 10 ** 9}

    monkeypatch.setattr("services.mlx_engine.mlx_engine.available", lambda: True)
    monkeypatch.setattr("services.mlx_engine.mlx_engine.generate", _fake_generate)
    monkeypatch.setattr("services.mlx_engine.mlx_model_for_role", lambda m: "mlx-community/fake")
    monkeypatch.setattr(lr, "_last_llm_activity_ts", 0.0)

    assert presence.system_busy() is False, "fixture should start from an idle clock"
    asyncio.run(lr.llm_runtime.generate("hello"))
    assert presence.system_busy() is True, "an MLX generation must register as system activity"


# ── The surviving engine-neutral surface ────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "effective_num_ctx_cap", "compute_num_ctx", "clamp_num_predict",
    "seconds_since_llm_activity", "model_lane", "PRIORITY_BACKGROUND",
])
def test_engine_neutral_helpers_survived_the_excise(name):
    """These are imported across ~10 modules and have nothing to do with any engine. The
    excise deleted 45% of this file; losing one of these breaks callers silently."""
    import services.llm_runtime as m

    assert hasattr(m, name)


# ── Batching (migrated from _wave1_embed_test.py, 2026-08-20) ───────────────────
# Its raise/zero-fill assertions duplicated the ones above verbatim — both were written the
# same day for the same contract. What was NOT covered anywhere in pytest is the batching
# shape, so only that came across.

def test_embed_batch_makes_one_in_process_call_for_the_whole_list(monkeypatch):
    """The old Ollama path sliced by `max_batch` to cap HTTP round-trips — the 2026-06-26
    loop-freeze came from one request per chunk. In-process there are no round-trips to cap,
    so slicing would only add overhead."""
    from config import settings

    calls = []

    async def _fake(texts):
        calls.append(len(texts))
        return [[0.1] * settings.embedding_dim for _ in texts]

    monkeypatch.setattr(llm_runtime, "_mlx_embed_or_none", _fake)
    out = asyncio.run(llm_runtime.embed_batch([f"t{i}" for i in range(100)], max_batch=64))
    assert len(out) == 100
    assert calls == [100], f"expected one call for the whole list, got {calls}"


def test_encode_async_sub_batches_rather_than_fanning_out_per_text(monkeypatch):
    """`rag_embeddings.encode_async` is the ingest entry point. Its 64-item sub-batching is
    what keeps a large ingest from issuing one embed per chunk — the original loop-freeze.
    It yields to foreground work between sub-batches, which is why the batching stays."""
    from config import settings
    from services import rag_embeddings

    seen = []

    async def fake_embed_batch(texts, **kw):
        seen.append(len(texts))
        return [[0.2] * settings.embedding_dim for _ in texts]

    monkeypatch.setattr(llm_runtime, "embed_batch", fake_embed_batch)
    monkeypatch.setattr(rag_embeddings, "_use_ollama", True)

    import services.memory_steward as ms

    async def _noop():
        return None

    monkeypatch.setattr(ms, "await_background_clearance", _noop)

    arr = asyncio.run(rag_embeddings.encode_async([f"x{i}" for i in range(150)]))
    assert arr.shape == (150, settings.embedding_dim)
    assert seen == [64, 64, 22], f"expected 64/64/22 sub-batches, got {seen}"
