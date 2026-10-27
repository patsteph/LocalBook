"""Orphan vector tables: empty ones are dropped at start-up, ones holding data are only reported."""
import lancedb

from config import settings
from services import rag_storage


def _make(db, name, rows):
    data = [{"vector": [0.0, 0.0], "text": t, "source_id": s, "chunk_index": i} for i, (s, t) in enumerate(rows)]
    return db.create_table(name, data=data)


def test_empty_orphans_go_data_orphans_stay(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", tmp_path, raising=False)
    monkeypatch.setattr(rag_storage, "_get_db", lambda: lancedb.connect(str(tmp_path)))
    db = lancedb.connect(str(tmp_path))
    _make(db, "notebook_live", [("s1", "kept")])
    _make(db, "notebook_empty", [("placeholder", "placeholder")])
    _make(db, "notebook_data", [("s9", "someone's chunks")])
    out = rag_storage.reconcile_vector_tables({"live"})
    assert out == {"dropped_empty": 1, "orphans_with_data": 1}
    names = {p.name for p in tmp_path.glob("*.lance")}
    assert names == {"notebook_live.lance", "notebook_data.lance"}


def test_never_reconciles_against_an_empty_live_list(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", tmp_path, raising=False)
    monkeypatch.setattr(rag_storage, "_get_db", lambda: lancedb.connect(str(tmp_path)))
    _make(lancedb.connect(str(tmp_path)), "notebook_x", [("placeholder", "placeholder")])
    assert rag_storage.reconcile_vector_tables(set()) == {"dropped_empty": 0, "orphans_with_data": 0}
    assert (tmp_path / "notebook_x.lance").exists()
