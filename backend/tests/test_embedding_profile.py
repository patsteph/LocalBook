"""Per-model embedding profiles: prompts, pooling, loader — resolved in one place."""

import pytest

from services import embedding_profile as ep

ARCTIC = "mlx-community/snowflake-arctic-embed-l-v2.0-bf16"
GEMMA2 = "mlx-community/embeddinggemma-2-bf16"


@pytest.fixture(autouse=True)
def _fresh():
    ep.for_model.cache_clear()
    yield
    ep.for_model.cache_clear()


def test_arctic_is_unchanged_query_prefix_only_cls_pooled():
    p = ep.for_model(ARCTIC)
    assert p.source == "registry" and p.pooling == "cls" and p.loader == "mlx_embeddings"
    assert p.format_query("what is RAG?") == "query: what is RAG?"
    assert ep.format_documents(["a passage"], p) == ["a passage"]      # no index goes stale


def test_embeddinggemma_uses_its_own_prompts_pooling_and_loader():
    p = ep.for_model(GEMMA2)
    assert (p.loader, p.pooling, p.min_dtype, p.dims) == ("mlx_vlm", "model", "bfloat16", 768)
    assert p.format_query("Red Planet?") == "task: search result | query: Red Planet?"
    assert p.format_document("Mars is red.") == "title: none | text: Mars is red."
    assert p.format_document("Mars is red.", title="Planets") == "title: Planets | text: Mars is red."
    assert 256 in p.mrl_dims


def test_formatting_is_never_doubled():
    p = ep.for_model(GEMMA2)
    once = p.format_query("q")
    assert p.format_query(once) == once
    doc = p.format_document("d")
    assert p.format_document(doc) == doc


def test_an_unregistered_checkpoint_supplies_its_own_prompts(monkeypatch):
    files = {"config_sentence_transformers.json": {"prompts": {"query": "task: search result | query: ",
                                                               "document": "title: none | text: "}},
             "config.json": {"model_type": "embedding_gemma2"}}
    monkeypatch.setattr(ep, "_from_registry", lambda m: None)
    monkeypatch.setattr(ep, "_cached_json", lambda m, f: files.get(f))
    p = ep.for_model("someone/embeddinggemma-2-6bit")
    assert p.source == "checkpoint" and p.loader == "mlx_vlm" and p.pooling == "model"
    assert p.format_document("x", title="T") == "title: T | text: x"


def test_an_unknown_embedder_gets_nothing(monkeypatch):
    monkeypatch.setattr(ep, "_cached_json", lambda m, f: None)
    p = ep.for_model("some-org/mystery-embedder-v9")
    assert (p.source, p.pooling, p.query_prefix) == ("default", "cls", "")
    assert p.format_document("x") == "x"


def test_startup_checks_expect_the_live_dimension_not_a_constant(monkeypatch):
    """A hardcoded 1024 drove a forced re-index + table drops on every launch after a switch
    to a 768-dim model. The checks must expect whatever the live embedder produces."""
    from config import settings
    from services import startup_checks
    monkeypatch.setattr(settings, "embedding_dim", 768)
    assert startup_checks.expected_embedding_dim() == 768
    monkeypatch.setattr(settings, "embedding_dim", 1024)
    assert startup_checks.expected_embedding_dim() == 1024


def test_every_registered_embedder_knows_its_dimension():
    """main.py derives settings.embedding_dim from the active model's profile at startup."""
    assert ep.for_model(ARCTIC).dims == 1024
    assert ep.for_model(GEMMA2).dims == 768


def test_the_locker_persists_the_dimension_under_a_key_config_reads():
    import inspect
    from services import llm_locker
    src = inspect.getsource(llm_locker.LLMLocker._patch_environment)
    assert '"embedding_dim": "embedding_dim"' in src and '"embedding_dim": "LOCALBOOK_EMBEDDING_DIM"' not in src


def test_symmetric_tasks_use_embeddinggemmas_prompts_and_leave_arctic_alone():
    g, a = ep.for_model(GEMMA2), ep.for_model(ARCTIC)
    assert g.format_task("x", "clustering") == "task: clustering | query: x"
    assert g.format_task("x", "similarity") == "task: sentence similarity | query: x"
    assert g.format_task("x", "query") == "task: search result | query: x"
    assert g.format_task("x", "document") == "title: none | text: x"
    for task in ("clustering", "similarity", "classification", "document"):
        assert a.format_task("x", task) == "x"               # arctic vectors unchanged
    assert ep.format_task_list(["x"], None, g) == ["x"]       # no task → as-is


def test_embed_batch_formats_for_the_task_before_the_engine(monkeypatch):
    import asyncio
    from config import settings
    from services.llm_runtime import llm_runtime

    seen = []

    async def fake(texts):
        seen.extend(texts)
        return [[1.0] * settings.embedding_dim for _ in texts]
    monkeypatch.setattr(llm_runtime, "_mlx_embed_or_none", fake)
    monkeypatch.setattr(settings, "embedding_model", GEMMA2)
    asyncio.run(llm_runtime.embed_batch(["a", "b"], task="clustering"))
    asyncio.run(llm_runtime.embed("c", task="similarity"))
    assert seen == ["task: clustering | query: a", "task: clustering | query: b",
                    "task: sentence similarity | query: c"]


def test_thresholds_are_per_model_and_default_to_todays_values(monkeypatch):
    from services.embedding_profile import EmbeddingProfile
    monkeypatch.setattr(ep, "active", lambda: ep.for_model(ARCTIC))
    assert ep.threshold("router", 0.75) == 0.75                # arctic: nothing changes
    tuned = EmbeddingProfile(model_id="g", thresholds=(("router", 0.81),))
    monkeypatch.setattr(ep, "active", lambda: tuned)
    assert ep.threshold("router", 0.75) == 0.81
    assert ep.threshold("consensus", 0.72) == 0.72             # not calibrated → default


def test_embeddinggemma_carries_the_shared_512_token_limit():
    assert ep.for_model(GEMMA2).max_tokens == 512
    assert ep.for_model(ARCTIC).max_tokens == 0          # arctic keeps its own context


def test_embed_max_length_obeys_the_shared_limit(monkeypatch):
    from services import mlx_engine
    monkeypatch.delenv("LOCALBOOK_MLX_EMBED_MAX_LENGTH", raising=False)
    mlx_engine._EMBED_MAXLEN_CACHE.clear()
    try:
        assert mlx_engine._embed_max_length(GEMMA2) == 512
        assert mlx_engine._embed_max_length(ARCTIC) > 512
    finally:
        mlx_engine._EMBED_MAXLEN_CACHE.clear()
