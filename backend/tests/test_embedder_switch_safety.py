"""Switching the embedder must never strand data on a fixed dimension (EmbeddingGemma 2 plan, phase 1).

Each test fakes the LIVE dimension through settings.embedding_dim and builds real LanceDB
tables in a tmp dir — never the user's data.
"""

import pyarrow as pa
import pytest

lancedb = pytest.importorskip("lancedb")


def _table(db, name, dim, rows=1):
    data = [{"id": f"r{i}", "vector": [0.1] * dim} for i in range(rows)]
    return db.create_table(name, data=data, schema=pa.schema([pa.field("id", pa.string()),
                                                              pa.field("vector", pa.list_(pa.float32(), dim))]))


def test_archival_index_follows_the_live_dimension_and_flags_a_reembed(tmp_path, monkeypatch):
    from config import settings
    from storage.memory_store import MemoryStore

    store = object.__new__(MemoryStore)              # just the archival part, no full init
    store.archival_db_path = tmp_path / "archival_memory"
    monkeypatch.setattr(settings, "embedding_dim", 1024)
    store._init_archival_db()
    assert store.archival_needs_reembed is False

    monkeypatch.setattr(settings, "embedding_dim", 768)   # the embedder was switched
    store._init_archival_db()
    vec = store.archival_db.open_table("archival_memories").schema.field("vector").type
    assert vec.list_size == 768 and store.archival_needs_reembed is True


def test_startup_reads_the_stores_flag_not_a_path_it_guessed(monkeypatch):
    from services import startup_checks
    from storage import memory_store as ms

    class _S:
        archival_needs_reembed = True
    monkeypatch.setattr(ms, "memory_store", _S())
    assert startup_checks.check_memory_store_dimensions() is True


def test_reindex_drops_only_tables_it_cannot_reuse(tmp_path, monkeypatch):
    from api import reindex
    from config import settings

    db = lancedb.connect(str(tmp_path))
    _table(db, "notebook_old", 1024)
    _table(db, "notebook_new", 768)
    monkeypatch.setattr(settings, "db_path", tmp_path)
    monkeypatch.setattr(settings, "embedding_dim", 768)

    assert reindex._drop_unusable_tables(drop_all=False) == ["notebook_old"]
    assert reindex._drop_unusable_tables(drop_all=True) == ["notebook_new"]


def test_memory_search_embeds_its_question_as_a_query(monkeypatch):
    import numpy as np
    from services import rag_embeddings
    from storage.memory_store import MemoryStore

    seen = []
    monkeypatch.setattr(rag_embeddings, "encode",
                        lambda text, is_query=False: seen.append(is_query) or np.ones((1, 4)))
    store = object.__new__(MemoryStore)
    store.get_embedding("what did I say about maps?", is_query=True)
    store.get_embedding("a stored memory")
    assert seen == [True, False]


def test_the_embedding_cache_never_serves_another_models_vector(monkeypatch):
    from config import settings
    from services.rag_cache import EmbeddingCache

    cache = EmbeddingCache(max_size=10, persist=False)
    monkeypatch.setattr(settings, "embedding_model", "arctic")
    cache.put("what is RAG?", [0.1] * 1024)
    assert cache.get("what is RAG?") is not None
    monkeypatch.setattr(settings, "embedding_model", "embeddinggemma-2")
    assert cache.get("what is RAG?") is None             # a miss, not a 1024-d vector


def test_the_answer_cache_only_matches_within_one_embedder(monkeypatch):
    import asyncio
    import numpy as np
    from config import settings
    from services.rag_cache import AnswerCache

    cache = AnswerCache(max_size=10, similarity_threshold=0.9, persist=False)
    monkeypatch.setattr(settings, "embedding_model", "arctic")
    q = np.ones(1024) / np.sqrt(1024)
    asyncio.run(cache.put("q?", "nb", q, "an answer", []))
    assert asyncio.run(cache.get("other wording?", "nb", q)) is not None
    monkeypatch.setattr(settings, "embedding_model", "embeddinggemma-2")
    q768 = np.ones(768) / np.sqrt(768)
    assert asyncio.run(cache.get("other wording?", "nb", q768)) is None
