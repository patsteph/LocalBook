"""delete_source must work for every notebook, not only the first ten tables LanceDB lists.

db.table_names() lists 10 tables by default. With more notebooks than that, delete_source answered "nothing to
delete" for the rest, and a re-index then added each source's chunks a second time."""
import asyncio

import pytest

lancedb = pytest.importorskip("lancedb")


def test_a_source_in_the_twelfth_table_is_really_deleted(tmp_path, monkeypatch):
    from services import rag_storage

    db = lancedb.connect(str(tmp_path))
    row = lambda sid, i: {"vector": [0.0, 1.0], "text": "t", "source_id": sid, "chunk_index": i}
    for n in range(12):
        db.create_table(f"notebook_nb{n:02d}", data=[row("keep", 0), row("drop", 0), row("drop", 1)])
    monkeypatch.setattr(rag_storage, "_get_db", lambda: db)

    assert asyncio.run(rag_storage.delete_source("nb11", "drop"))
    left = db.open_table("notebook_nb11").to_arrow().to_pylist()
    assert [r["source_id"] for r in left] == ["keep"]
