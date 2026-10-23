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


def test_mac_items_are_listed_for_visible_notebooks_only_and_fetched_on_request(db, tmp_path, monkeypatch):
    db.executescript("""
    CREATE TABLE visual_generations (visual_id TEXT PRIMARY KEY, notebook_id TEXT, topic TEXT, title TEXT, svg_markup TEXT,
        mermaid_code TEXT, template_id TEXT, v2_path TEXT, critic_overall REAL, prompt TEXT, created_at TEXT, updated_at TEXT);
    CREATE TABLE video_generations (video_id TEXT PRIMARY KEY, notebook_id TEXT, topic TEXT, duration_minutes INTEGER,
        visual_style TEXT, voice TEXT, format_type TEXT, video_file_path TEXT, duration_seconds REAL, storyboard TEXT,
        narration_script TEXT, slide_count INTEGER, status TEXT, error_message TEXT, created_at TEXT, updated_at TEXT);
    """)
    monkeypatch.setattr(phone_library, "notebook_titles", lambda: {"nb": "NB", "hid": "Hidden"})
    monkeypatch.setattr(phone_library, "excluded", lambda: ["hid"])
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "a1.wav").write_bytes(b"RIFF" * 10)
    db.execute("INSERT INTO content_generations VALUES ('d1','nb','study','Study guide','text','Topic',0,'t0','t1')")
    db.execute("INSERT INTO content_generations VALUES ('d2','hid','study','Study guide','secret','X',0,'t0','t1')")
    qs = [{"question": "Q", "answer": "b", "options": ["a", "b"], "question_type": "multiple_choice", "explanation": "e"},
          {"question": "TF", "answer": "True", "options": None, "question_type": "true_false"},
          {"question": "typed", "answer": "x", "options": None, "question_type": "short_answer"}]
    db.execute("INSERT INTO quiz_generations VALUES ('q1','nb','T','medium',3,?,NULL,0,'t0','t1')", (json.dumps(qs),))
    db.execute("INSERT INTO audio_generations VALUES ('a1','nb','Alex: hi','T',1,'m','f','us',NULL,?,12.5,'completed',NULL,'t0','t1')",
               (str(tmp_path / "audio" / "a1.wav"),))
    db.execute("INSERT INTO audio_generations VALUES ('a2','nb','','T',1,'m','f','us',NULL,NULL,0,'pending',NULL,'t0','t1')")
    db.execute("INSERT INTO visual_generations VALUES ('v1','nb','T','A map','<svg/>',NULL,NULL,NULL,NULL,NULL,'t0','t1')")
    db.commit()
    items = {i["id"]: i for i in phone_studio.listing()}
    assert set(items) == {"d1", "q1", "a1", "v1"}                        # hidden notebook and unfinished audio are absent
    assert items["a1"]["bytes"] == 40 and items["d1"]["title"] == "Study guide: Topic"
    got = {g["id"]: g for g in phone_studio.get_items([{"kind": "doc", "id": "d1"}, {"kind": "doc", "id": "d2"},
                                                        {"kind": "quiz", "id": "q1"}, {"kind": "visual", "id": "v1"}])}
    assert set(got) == {"d1", "q1", "v1"} and got["d1"]["content"] == "text" and got["v1"]["svg"] == "<svg/>"
    assert [q["answer"] for q in got["q1"]["questions"]] == [1, 0] and got["q1"]["questions"][1]["choices"] == ["True", "False"]
    chunk = phone_studio.file_chunk("audio", "a1", 0)
    assert chunk["total"] == 40 and base64.b64decode(chunk["data"]) == b"RIFF" * 10
    with pytest.raises(HTTPException):
        phone_studio.file_chunk("audio", "a2", 0)
