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
