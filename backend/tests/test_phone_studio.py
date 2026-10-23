"""Studio items from a paired phone land in the Studio tables once, whole, and only for visible notebooks."""
import asyncio
import base64
import hashlib
import json
import sqlite3

import pytest
from fastapi import HTTPException

from services.sync import blobs, phone_library, phone_studio


@pytest.fixture
def db(tmp_path, monkeypatch):
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE content_generations (content_id TEXT PRIMARY KEY, notebook_id TEXT, skill_id TEXT, skill_name TEXT,
        content TEXT, topic TEXT, sources_used INTEGER, created_at TEXT, updated_at TEXT);
    CREATE TABLE quiz_generations (quiz_id TEXT PRIMARY KEY, notebook_id TEXT, topic TEXT, difficulty TEXT,
        num_questions INTEGER, questions_json TEXT, source_summary TEXT, sources_used INTEGER, created_at TEXT, updated_at TEXT);
    CREATE TABLE audio_generations (audio_id TEXT PRIMARY KEY, notebook_id TEXT, script TEXT, topic TEXT,
        duration_minutes INTEGER, host1_gender TEXT, host2_gender TEXT, accent TEXT, skill_id TEXT, audio_file_path TEXT,
        duration_seconds REAL, status TEXT, error_message TEXT, created_at TEXT, updated_at TEXT);
    """)
    monkeypatch.setattr(phone_studio, "_conn", lambda: c)
    monkeypatch.setattr(phone_library, "is_visible", lambda n: n == "nb")
    monkeypatch.setattr(blobs, "data_dir", lambda: tmp_path)
    return c


def test_doc_and_cards_are_stored_once(db):
    for _ in range(2):
        asyncio.run(phone_studio.put("doc", "doc-00000001", "nb", "T", "topic", "", "# Hi\n\ntext"))
    asyncio.run(phone_studio.put("cards", "cards-0000001", "nb", "T", "", "", [{"front": "Q?", "back": "A"}]))
    rows = db.execute("SELECT * FROM content_generations ORDER BY content_id").fetchall()
    assert [r["skill_name"] for r in rows] == ["Flashcards", "Briefing"]
    assert "**Q?**" in rows[0]["content"]


def test_quiz_is_mapped_to_the_mac_shape(db):
    asyncio.run(phone_studio.put("quiz", "quiz-00000001", "nb", "T", "x", "", [
        {"question": "Q", "choices": ["a", "b"], "answer": 1, "why": "because"},
        {"question": "bad", "choices": ["a"], "answer": 5}]))
    row = db.execute("SELECT * FROM quiz_generations").fetchone()
    qs = json.loads(row["questions_json"])
    assert row["num_questions"] == 1 and qs[0]["answer"] == "b" and qs[0]["options"] == ["a", "b"]
    assert qs[0]["question_type"] == "multiple_choice" and qs[0]["explanation"] == "because"


def test_hidden_notebook_and_bad_ids_are_refused(db):
    for args, code in [(("doc", "doc-00000001", "other", "T", "", "", "x"), 404), (("doc", "../x", "nb", "T", "", "", "x"), 400),
                       (("doc", "doc-00000001", "nb", "T", "", "", "  "), 400), (("video", "vid-00000001", "nb", "T", "", "", "x"), 400)]:
        with pytest.raises(HTTPException) as e:
            asyncio.run(phone_studio.put(*args))
        assert e.value.status_code == code


def test_audio_arrives_in_chunks_and_is_recorded_when_whole(db, tmp_path):
    data = bytes(range(256)) * 40
    digest = hashlib.sha256(data).hexdigest()
    base = {"id": "aud-00000001", "notebook_id": "nb", "total": len(data), "sha256": digest, "script": "Alex: hi", "duration": 130.0}
    first = asyncio.run(phone_studio.put_audio({**base, "offset": 0, "data": base64.b64encode(data[:6000]).decode()}))
    assert first == {"next": 6000, "done": False}
    assert db.execute("SELECT COUNT(*) FROM audio_generations").fetchone()[0] == 0
    last = asyncio.run(phone_studio.put_audio({**base, "offset": 6000, "data": base64.b64encode(data[6000:]).decode()}))
    assert last["done"] is True
    row = db.execute("SELECT * FROM audio_generations").fetchone()
    assert row["status"] == "completed" and row["duration_minutes"] == 2
    assert (tmp_path / "audio" / "aud-00000001.m4a").read_bytes() == data


def test_delete_removes_the_item_and_its_audio_and_is_repeatable(db, tmp_path):
    asyncio.run(phone_studio.put("doc", "doc-00000001", "nb", "T", "", "", "text"))
    data = b"abc" * 100
    asyncio.run(phone_studio.put_audio({"id": "aud-00000001", "notebook_id": "nb", "total": len(data), "offset": 0,
                                        "sha256": hashlib.sha256(data).hexdigest(), "data": base64.b64encode(data).decode()}))
    assert (tmp_path / "audio" / "aud-00000001.m4a").exists()
    assert asyncio.run(phone_studio.delete("doc-00000001"))["removed"] == 1
    assert asyncio.run(phone_studio.delete("aud-00000001"))["removed"] == 1
    assert not (tmp_path / "audio" / "aud-00000001.m4a").exists()
    assert asyncio.run(phone_studio.delete("aud-00000001"))["removed"] == 0
    assert db.execute("SELECT COUNT(*) FROM content_generations").fetchone()[0] == 0
    with pytest.raises(HTTPException):
        asyncio.run(phone_studio.delete("../x"))
