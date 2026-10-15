"""Phase 2 of the EmbeddingGemma 2 switch: shadow build → switch at launch → roll back.

Real LanceDB tables in a tmp data dir; the embedder is faked (dimension 3 for the "new"
model, against 4 in the live index) so the tests check what is copied, not model quality.
"""

import asyncio
import json

import pytest

lancedb = pytest.importorskip("lancedb")
pa = pytest.importorskip("pyarrow")

from services import embedding_migration as em

OLD, NEW = "org/old-embedder", "org/new-embedder"


@pytest.fixture
def lib(tmp_path, monkeypatch):
    from config import settings
    from services.mlx_engine import mlx_engine

    data = tmp_path / "LocalBook"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "db_path", data / "lancedb")
    monkeypatch.setattr(settings, "embedding_model", OLD)
    monkeypatch.setattr("services.model_presence.is_present", lambda m: True)

    async def admit(*a, **k):
        return True

    async def unload(*a, **k):
        return True
    monkeypatch.setattr(mlx_engine, "admit", admit)
    monkeypatch.setattr(mlx_engine, "unload", unload)

    async def no_pace():
        return None
    monkeypatch.setattr(em, "_pace", no_pace)
    seen = []

    async def fake_embed(target, texts):
        seen.extend(texts)
        return [[float(len(t)), 1.0, 0.5] for t in texts]
    monkeypatch.setattr(em, "_embed", fake_embed)

    db = lancedb.connect(str(data / "lancedb"))

    def table(nb, rows):
        db.create_table(f"notebook_{nb}", data=[{
            "vector": [0.1, 0.2, 0.3, 0.4], "parent_text": "", "filename": "f.pdf", **r} for r in rows],
            mode="overwrite")
    table("a", [{"text": "chunk one", "synthetic_questions": "Q1?", "source_id": "s1", "chunk_index": 0,
                 "source_type": "pdf"},
                {"text": "[SUMMARY] the summary", "synthetic_questions": "", "source_id": "s1",
                 "chunk_index": -1, "source_type": "summary"},
                {"text": "an image description", "synthetic_questions": "", "source_id": "s1",
                 "chunk_index": 5, "source_type": "supplementary"}])
    table("b", [{"text": "other", "synthetic_questions": "", "source_id": "s2", "chunk_index": 0,
                 "source_type": "web"}])
    return {"data": data, "db": db, "seen": seen, "table": table}


def _build():
    async def go():
        out = await em.start(NEW)
        assert out["started"], out
        await em._task
    asyncio.run(go())


def test_the_embedded_text_is_exactly_what_ingest_embedded():
    assert em.embedded_text({"text": "c", "synthetic_questions": "Q?"}) == "c\n\nQuestions this answers:\nQ?"
    assert em.embedded_text({"text": "[SUMMARY] s", "chunk_index": -1}) == "s"
    assert em.embedded_text({"text": "plain", "synthetic_questions": ""}) == "plain"


def test_the_shadow_build_copies_every_row_and_column_at_the_new_dimension(lib):
    _build()
    st = em.status()
    assert st["ready"] and st["tables_done"] == 2 and st["rows_done"] == 4
    shadow = lancedb.connect(str(em.shadow_dir(NEW)))
    a = shadow.open_table("notebook_a").to_arrow()
    assert a.num_rows == 3 and a.schema.field("vector").type.list_size == 3
    assert set(a.column("source_type").to_pylist()) == {"pdf", "summary", "supplementary"}  # image text kept
    assert "chunk one\n\nQuestions this answers:\nQ1?" in lib["seen"] and "the summary" in lib["seen"]
    live = lib["db"].open_table("notebook_a").to_arrow()
    assert live.schema.field("vector").type.list_size == 4          # the live index is untouched


def test_a_build_resumes_where_it_stopped(lib, monkeypatch):
    em._write(em.LEDGER, {"target": NEW, "tables": {"notebook_a": {"done": True,
               "version": em._table_version(lib["db"], "notebook_a"), "rows": 3}}, "rows_done": 3})
    copied = []
    real = em._copy_table

    async def spy(target, live_db, shadow_db, name, led):
        copied.append(name)
        return await real(target, live_db, shadow_db, name, led)
    monkeypatch.setattr(em, "_copy_table", spy)
    _build()
    assert copied == ["notebook_b"]


def test_a_change_during_the_build_is_caught_up_before_the_switch(lib):
    _build()
    lib["db"].open_table("notebook_b").add([{"vector": [0.1] * 4, "parent_text": "", "filename": "g",
                                            "text": "arrived later", "synthetic_questions": "",
                                            "source_id": "s3", "chunk_index": 0, "source_type": "web"}])

    async def go():
        first = await em.request_switch()
        await em._task
        second = await em.request_switch()
        return first, second
    first, second = asyncio.run(go())
    assert first["ok"] is False and first["catching_up"] == ["notebook_b"]
    assert second == {"ok": True, "restart_required": True}
    shadow = lancedb.connect(str(em.shadow_dir(NEW)))
    assert shadow.open_table("notebook_b").count_rows() == 2


def test_switch_at_launch_then_roll_back(lib):
    _build()
    assert asyncio.run(em.request_switch())["ok"]
    out = em.apply_pending_at_startup()
    assert out == {"switched": True, "from": OLD, "to": NEW}
    data = lib["data"]
    assert lancedb.connect(str(data / "lancedb")).open_table("notebook_a").schema.field("vector").type.list_size == 3
    assert (data / f"lancedb@{em.slug(OLD)}").is_dir()               # kept for roll back
    prefs = json.loads((data / "user_preferences.json").read_text())["default_combo"]
    assert prefs["embedding_model"] == NEW and prefs["embeddings"] == NEW
    assert em.apply_pending_at_startup() is None                     # applied once

    assert em.request_rollback()["ok"]
    back = em.apply_pending_at_startup()
    assert back["rollback"] and back["to"] == OLD
    assert lancedb.connect(str(data / "lancedb")).open_table("notebook_a").schema.field("vector").type.list_size == 4
    assert json.loads((data / "user_preferences.json").read_text())["default_combo"]["embedding_model"] == OLD


def test_a_missing_shadow_changes_nothing(lib):
    em._write(em.FLIP, {"target": NEW, "previous": OLD})
    out = em.apply_pending_at_startup()
    assert out["switched"] is False and "missing" in out["error"]
    assert lancedb.connect(str(lib["data"] / "lancedb")).open_table("notebook_a").count_rows() == 3
    assert not (lib["data"] / em.FLIP).exists()


def test_preflight_refuses_the_same_model_or_one_not_downloaded(lib, monkeypatch):
    assert em.preflight(OLD)["checks"]["different_model"]["ok"] is False
    monkeypatch.setattr("services.model_presence.is_present", lambda m: False)
    pre = em.preflight(NEW)
    assert pre["ready"] is False and pre["checks"]["downloaded"]["ok"] is False


def test_the_old_direct_swap_is_refused_for_embedders():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import evaluator

    app = FastAPI()
    app.include_router(evaluator.router)
    r = TestClient(app).post("/evaluator/swap", json={"target_model": NEW, "role": "embeddings"})
    assert r.status_code == 409
