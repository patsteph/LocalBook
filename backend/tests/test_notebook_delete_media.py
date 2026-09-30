"""Deleting a notebook removes its generated media and vector table.

`audio_generations` / `video_generations` cascade on the notebook FK, so the rows
vanished with the notebook while the files stayed — 49 orphaned podcasts
(~300 MB) in one real data dir, plus 74 orphaned LanceDB tables.
"""

import asyncio

import pytest

from api import notebooks


@pytest.fixture
def data(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "audio").mkdir()
    (tmp_path / "video").mkdir()
    return tmp_path


def test_media_files_and_their_siblings_are_removed(data):
    a = "11111111-aaaa-4aaa-8aaa-111111111111"
    keep = "22222222-bbbb-4bbb-8bbb-222222222222"
    (data / "audio" / f"{a}.wav").write_bytes(b"x")
    (data / "audio" / f"{a}_speech.wav").write_bytes(b"x")
    (data / "audio" / f"{a}_parts").mkdir()
    (data / "audio" / f"{a}_parts" / "part_0000.wav").write_bytes(b"x")
    (data / "audio" / f"{keep}.wav").write_bytes(b"x")
    (data / "video" / f"{a}.mp4").write_bytes(b"x")

    removed = notebooks.remove_media_files({"audio": [a], "video": [a]})

    assert removed == 4
    assert sorted(p.name for p in (data / "audio").iterdir()) == [f"{keep}.wav"]
    assert not any((data / "video").iterdir())


def test_an_empty_id_cannot_glob_everything(data):
    (data / "audio" / "anything.wav").write_bytes(b"x")
    assert notebooks.remove_media_files({"audio": ["", None]}) == 0
    assert (data / "audio" / "anything.wav").exists()


def test_media_ids_are_collected_before_the_cascade_deletes_them(data, monkeypatch):
    order = []

    async def fake_ids(nb):
        order.append("ids")
        return {"audio": [], "video": []}

    async def fake_delete(nb):
        order.append("delete")
        return True

    monkeypatch.setattr(notebooks, "_media_ids_for", fake_ids)
    monkeypatch.setattr(notebooks.notebook_store, "delete", fake_delete)
    from services import rag_storage

    monkeypatch.setattr(rag_storage, "drop_notebook_table", lambda nb: False)

    asyncio.run(notebooks.delete_notebook("nb-x"))
    assert order[:2] == ["ids", "delete"]


def test_the_vector_table_is_dropped(tmp_path, monkeypatch):
    import lancedb

    from services import rag_storage

    db = lancedb.connect(str(tmp_path))
    db.create_table("notebook_gone", data=[{"vector": [0.0, 1.0], "text": "t"}])
    db.create_table("notebook_kept", data=[{"vector": [0.0, 1.0], "text": "t"}])
    monkeypatch.setattr(rag_storage, "_db", db)

    assert rag_storage.drop_notebook_table("gone") is True
    assert rag_storage.drop_notebook_table("gone") is False
    assert set(db.table_names()) == {"notebook_kept"}
