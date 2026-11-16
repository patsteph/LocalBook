"""The one-way import into the Swift Mac app: loopback only, every notebook (device-only included), full source
text and Canvas notes. Read-only."""
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import migrate
from services.sync import phone_library
from tests.test_phone_library import NB_OPEN, NB_PRIVATE, lib  # noqa: F401  (the fixture: two notebooks, Lance tables)


@pytest.fixture()
def app(lib):
    a = FastAPI()
    a.include_router(migrate.router, prefix="/migrate")
    # The fixture's main database has only notebooks; add what the import reads beyond the phone's needs.
    from config import settings
    c = sqlite3.connect(settings.data_dir / "localbook.db")
    c.execute("""CREATE TABLE sources (id TEXT PRIMARY KEY, notebook_id TEXT, filename TEXT, content TEXT, url TEXT,
                 author TEXT, date TEXT, format TEXT, type TEXT, notes TEXT, tags TEXT DEFAULT '[]', created_at TEXT)""")
    c.execute("INSERT INTO sources (id, notebook_id, filename, content, url, format, type, tags, created_at) VALUES "
              "('s1', ?, 's1.md', 'alpha beta, the whole text', 'https://x.test/a', 'md', 'markdown', '[\"t\"]', '2026-01-02')", (NB_OPEN,))
    c.execute("""CREATE TABLE canvas_notes (id TEXT PRIMARY KEY, notebook_id TEXT, title TEXT, content_markdown TEXT,
                 source_type TEXT, note_type TEXT, tags TEXT, created_at TEXT, updated_at TEXT)""")
    c.execute("INSERT INTO canvas_notes VALUES ('n1', ?, 'Idea', '# Idea\n\nkeep this', 'typed', 'note', '[]', '2026-01-03', '2026-01-04')", (NB_OPEN,))
    c.commit(); c.close()
    return a


def local(app):
    return TestClient(app, client=("127.0.0.1", 50000))


def test_only_a_process_on_this_mac_may_call(app):
    assert TestClient(app, client=("10.0.0.7", 50000)).post("/migrate/manifest").status_code == 403
    assert local(app).post("/migrate/manifest").status_code == 200


def test_every_notebook_is_listed_even_the_device_only_ones(app):
    phone_library.set_excluded([NB_PRIVATE])
    c = local(app)
    ids = {n["id"] for n in c.post("/migrate/manifest").json()["notebooks"]}
    assert ids == {NB_OPEN, NB_PRIVATE}
    assert c.post("/migrate/sources", json={"notebook_id": NB_PRIVATE}).status_code == 200
    page = c.post("/migrate/chunks", json={"notebook_id": NB_PRIVATE}).json()
    assert [r["text"] for r in page["chunks"]] == ["secret"]
    hello = c.post("/migrate/hello").json()
    assert hello["notebooks"] == 2 and hello["device_only"] == 1
    # The phone's own view is unchanged: the context var never leaks out of a migrate call.
    assert phone_library.excluded() == [NB_PRIVATE]


def test_chunks_page_like_the_phone(app):
    c = local(app)
    page = c.post("/migrate/chunks", json={"notebook_id": NB_OPEN, "limit": 2}).json()
    assert len(page["chunks"]) == 2 and page["more"] and page["after"] == {"source_id": "s1", "chunk_index": 1}


def test_source_text_and_notes_come_from_the_main_database(app):
    c = local(app)
    t = c.post("/migrate/source_text", json={"notebook_id": NB_OPEN, "source_id": "s1"}).json()
    assert t["text"] == "alpha beta, the whole text" and t["url"] == "https://x.test/a" and t["tags"] == ["t"]
    assert "content" not in t
    assert c.post("/migrate/source_text", json={"notebook_id": NB_OPEN, "source_id": "nope"}).status_code == 404
    n = c.post("/migrate/notes", json={"notebook_id": NB_OPEN}).json()
    assert n["notes"][0]["title"] == "Idea" and n["notes"][0]["text"].startswith("# Idea")
    assert c.post("/migrate/notes", json={"notebook_id": "empty"}).json()["notes"] == []
