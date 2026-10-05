"""LB-12 phases D2 + E: archival memory as records, and audio/video files.

Files: the confinement rules (a peer may only reach audio/ and video/ inside the
data dir), resumable chunked writes, and whole-file hash verification.
Archival: records are the truth; `reconcile` makes the LanceDB index match them.
"""

import base64
import hashlib
import importlib

import pytest

from services.sync import blobs


@pytest.fixture
def ddir(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


# ── files ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "audio/../../x", "notebooks/x.json",
                                 "localbook.db", "", "@data/../x"])
def test_a_peer_can_only_reach_audio_and_video(ddir, bad):
    with pytest.raises(blobs.BlobPathError):
        blobs.safe_path(bad)


def test_allowed_paths_resolve_inside_the_data_dir(ddir):
    assert blobs.safe_path("audio/a.wav") == (ddir / "audio" / "a.wav").resolve()
    assert blobs.safe_path("@data/video/v.mp4") == (ddir / "video" / "v.mp4").resolve()


def _send(rel, payload, chunk):
    digest = hashlib.sha256(payload).hexdigest()
    offset = 0
    while True:
        part = payload[offset:offset + chunk]
        res = blobs.write_chunk(rel, offset, base64.b64encode(part).decode(), len(payload), digest)
        offset = res["next"]
        if res["done"]:
            return


def test_a_chunked_transfer_lands_whole_and_verified(ddir, monkeypatch):
    payload = bytes(range(256)) * 1000
    _send("audio/x.wav", payload, 10_000)
    assert (ddir / "audio" / "x.wav").read_bytes() == payload
    assert not (ddir / "audio" / "x.wav.part").exists()


def test_an_interrupted_transfer_resumes_from_what_is_on_disk(ddir):
    payload = b"0123456789" * 3000
    digest = hashlib.sha256(payload).hexdigest()
    blobs.write_chunk("audio/r.wav", 0, base64.b64encode(payload[:10_000]).decode(), len(payload), digest)
    assert blobs.partial_offset("audio/r.wav") == 10_000
    # a retry that thinks it is at 0 is told where to continue, nothing is duplicated
    res = blobs.write_chunk("audio/r.wav", 0, base64.b64encode(payload[:10_000]).decode(), len(payload), digest)
    assert res == {"next": 10_000, "done": False}
    blobs.write_chunk("audio/r.wav", 10_000, base64.b64encode(payload[10_000:]).decode(), len(payload), digest)
    assert (ddir / "audio" / "r.wav").read_bytes() == payload


def test_a_corrupted_transfer_is_discarded_not_kept(ddir):
    payload = b"real audio" * 100
    with pytest.raises(ValueError, match="hash mismatch"):
        blobs.write_chunk("audio/c.wav", 0, base64.b64encode(payload).decode(), len(payload), "0" * 64)
    assert not (ddir / "audio" / "c.wav").exists() and not (ddir / "audio" / "c.wav.part").exists()


def test_referenced_and_missing_follow_the_rows(ddir):
    import sqlite3
    c = sqlite3.connect(ddir / "x.db")
    c.execute("CREATE TABLE audio_generations (audio_file_path TEXT)")
    c.execute("CREATE TABLE video_generations (video_file_path TEXT)")
    c.executemany("INSERT INTO audio_generations VALUES (?)",
                  [(str(ddir / "audio" / "here.wav"),), ("@data/audio/gone.wav",), ("/Users/x/elsewhere.wav",)])
    (ddir / "audio").mkdir()
    (ddir / "audio" / "here.wav").write_bytes(b"x")
    refs = blobs.referenced(c)
    assert refs == ["audio/gone.wav", "audio/here.wav"]          # outside the data dir: not ours
    assert blobs.missing(refs) == ["audio/gone.wav"]


# ── archival memory: records are the truth, LanceDB the index ───────────────


@pytest.fixture
def mem(tmp_path, monkeypatch):
    from config import settings
    from storage import memory_store as ms
    from tests.test_memory_bridge import _embed

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    import storage.database as _db
    importlib.reload(_db)
    monkeypatch.setattr(ms.MemoryStore, "_instance", None)
    store = ms.MemoryStore()
    monkeypatch.setattr(store, "get_embedding", lambda text: _embed(text, settings.embedding_dim))
    monkeypatch.setattr(ms, "memory_store", store)
    return store


def _lance_ids(store):
    t = store.archival_db.open_table("archival_memories")
    return set(t.to_pandas()["id"].tolist()) if t.count_rows() else set()


def test_adding_a_memory_writes_its_record_and_its_index(mem):
    from models.memory import ArchivalMemoryEntry

    e = ArchivalMemoryEntry(content="The user moved to Lisbon", content_type="fact", source_type="user_stated")
    mem.add_archival_memory(e)
    conn = mem._get_recall_connection()
    assert conn.execute("SELECT content FROM archival_records WHERE id=?", (e.id,)).fetchone()[0] \
        == "The user moved to Lisbon"
    assert e.id in _lance_ids(mem)


def test_reconcile_indexes_records_from_another_mac_and_drops_deleted_ones(mem):
    from models.memory import ArchivalMemoryEntry
    from storage import archival_records

    local = ArchivalMemoryEntry(content="local memory", content_type="fact", source_type="user_stated")
    mem.add_archival_memory(local)
    conn = mem._get_recall_connection()
    # a record that arrived by sync (no vector yet) …
    archival_records.write(conn, {"id": "remote-1", "namespace": "system", "content": "synced memory",
                                  "content_type": "fact", "source_type": "user_stated",
                                  "created_at": "2026-10-01T00:00:00", "importance": "medium",
                                  "topics": "[]", "entities": "[]"})
    # … and a record another Mac deleted
    archival_records.delete(conn, [local.id])
    conn.commit()
    out = archival_records.reconcile(mem)
    assert out == {"indexed": 1, "dropped": 1}
    assert _lance_ids(mem) == {"remote-1"}
    found = mem.search_archival_memory("synced memory")
    assert [r.entry.id for r in found] == ["remote-1"]


def test_existing_lancedb_memories_are_backfilled_into_records(mem):
    from storage import archival_records

    mem._index_archival(
        __import__("models.memory", fromlist=["ArchivalMemoryEntry"]).ArchivalMemoryEntry(
            id="old-1", content="from before records existed", content_type="fact", source_type="user_stated"),
        "system", "")
    assert archival_records.backfill(mem) == 1
    conn = mem._get_recall_connection()
    assert conn.execute("SELECT content FROM archival_records WHERE id='old-1'").fetchone()[0] \
        == "from before records existed"


def test_tray_status_reports_sync_state(ddir):
    import asyncio

    from api import system
    out = asyncio.run(system.get_tray_status())
    assert out["sync"] == {"enabled": False, "paired": 0, "running": False, "label": ""}
