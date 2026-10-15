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


# ── vectors are per Mac, tagged with their model (migration 3) ────────────────


def test_migration_3_tags_vectors_and_bumps_the_head(tmp_path):
    import sqlite3
    from services import migration_ledger as ledger
    from services.migration_ledger import MigrationContext

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE articles (id TEXT PRIMARY KEY, embedding BLOB)")
    conn.execute("CREATE TABLE canvas_topics (id TEXT PRIMARY KEY, centroid_json TEXT)")
    ledger._m0003_local_vectors(MigrationContext(conn=conn, data_dir=tmp_path))
    assert "embed_model" in {r[1] for r in conn.execute("PRAGMA table_info(articles)")}
    assert "centroid_model" in {r[1] for r in conn.execute("PRAGMA table_info(canvas_topics)")}
    assert max(m.number for m in ledger.migrations()) >= 3


def test_migration_3_tolerates_a_fresh_install_without_those_tables(tmp_path):
    import sqlite3
    from services import migration_ledger as ledger
    from services.migration_ledger import MigrationContext

    ledger._m0003_local_vectors(MigrationContext(conn=sqlite3.connect(":memory:"), data_dir=tmp_path))


def test_vectors_never_sync():
    from services.sync import registry
    t = {x.name: x for x in registry.tables("main")}
    assert {"embedding", "embed_model"} <= set(t["articles"].exclude)
    assert {"centroid_json", "centroid_model"} <= set(t["canvas_topics"].exclude)


def test_an_untagged_vector_counts_as_the_legacy_model(monkeypatch):
    from config import settings
    from services.embedding_profile import LEGACY_EMBED_MODEL, is_current
    monkeypatch.setattr(settings, "embedding_model", LEGACY_EMBED_MODEL)
    assert is_current(None) and is_current(LEGACY_EMBED_MODEL)
    monkeypatch.setattr(settings, "embedding_model", "mlx-community/embeddinggemma-2-bf16")
    assert not is_current(None) and is_current("mlx-community/embeddinggemma-2-bf16")


def test_a_foreign_canvas_centroid_gets_a_local_stand_in(monkeypatch):
    import asyncio
    from config import settings
    from services import canvas_subtopics
    from services.llm_runtime import llm_runtime

    monkeypatch.setattr(settings, "embedding_model", "mlx-community/embeddinggemma-2-bf16")

    async def embed_batch(texts, **kw):
        return [[0.5] * 768 for _ in texts]
    monkeypatch.setattr(llm_runtime, "embed_batch", embed_batch)
    topics = [{"id": "mine", "title": "a", "synthesis": "", "centroid": [0.1] * 768,
               "centroid_model": "mlx-community/embeddinggemma-2-bf16"},
              {"id": "theirs", "title": "Maps", "synthesis": "old maps",
               "centroid": [0.1] * 1024, "centroid_model": None},          # legacy arctic
              {"id": "synced", "title": "Hiring", "synthesis": "", "centroid": []}]
    stand_ins = asyncio.run(canvas_subtopics._localise_centroids(topics))
    assert set(stand_ins) == {"theirs", "synced"}
    assert all(len(t["centroid"]) == 768 for t in topics)


def test_articles_compare_only_vectors_from_the_live_model(monkeypatch):
    import asyncio
    from datetime import datetime
    from config import settings
    from services.embedding_profile import LEGACY_EMBED_MODEL
    from storage.article_store import article_store

    conn = article_store._get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("DELETE FROM articles WHERE id LIKE 'emb-test-%'")
    for aid, emb, model in (("emb-test-legacy", b"x", None), ("emb-test-gemma", b"y", "gemma"),
                            ("emb-test-none", None, None)):
        conn.execute("INSERT INTO articles (id, source_id, notebook_id, position, title, body_text, "
                     "created_at, embedding, embed_model) VALUES (?, 's', 'nb', 0, ?, 'body', ?, ?, ?)",
                     (aid, aid, now, emb, model))
    conn.commit()
    since = "2000-01-01"

    def ids(rows):
        return {r["id"] for r in rows if r["id"].startswith("emb-test-")}

    monkeypatch.setattr(settings, "embedding_model", LEGACY_EMBED_MODEL)
    assert ids(asyncio.run(article_store.list_with_embeddings(since))) == {"emb-test-legacy"}
    assert ids(asyncio.run(article_store.list_needing_embedding(since))) == {"emb-test-gemma", "emb-test-none"}
    monkeypatch.setattr(settings, "embedding_model", "gemma")
    assert ids(asyncio.run(article_store.list_with_embeddings(since))) == {"emb-test-gemma"}
    assert ids(asyncio.run(article_store.list_needing_embedding(since))) == {"emb-test-legacy", "emb-test-none"}
