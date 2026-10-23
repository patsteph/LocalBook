"""The table helpers must see every notebook table, not only the first ten LanceDB lists by default."""
import pytest

lancedb = pytest.importorskip("lancedb")

from utils.lancedb_tables import has_table, list_tables


def _db(tmp_path, n):
    db = lancedb.connect(str(tmp_path))
    for i in range(n):
        db.create_table(f"notebook_{i:03d}", data=[{"vector": [0.0, 1.0], "source_id": "s", "chunk_index": 0}])
    return db


def test_every_table_is_listed_past_the_default_page(tmp_path):
    db = _db(tmp_path, 25)
    names = list_tables(db)
    assert len(names) == 25 and names == sorted(names)


def test_listing_spans_more_than_one_internal_page(tmp_path, monkeypatch):
    import utils.lancedb_tables as t
    monkeypatch.setattr(t, "_PAGE", 4)
    assert len(t.list_tables(_db(tmp_path, 11))) == 11


def test_has_table_finds_the_last_one_and_not_a_missing_one(tmp_path):
    db = _db(tmp_path, 25)
    assert has_table(db, "notebook_024") and not has_table(db, "notebook_999")
