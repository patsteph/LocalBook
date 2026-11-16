"""A peer reads one notebook's topics: the source tags, counted across that notebook's sources."""
import json
import sqlite3

import pytest
from services.sync import phone_library
from tests.test_phone_library import NB_OPEN, NB_PRIVATE, _call, lib  # noqa: F401


@pytest.fixture()
def tagged(lib, tmp_path):  # noqa: F811
    c = sqlite3.connect(tmp_path / "localbook.db")
    c.execute("CREATE TABLE sources (id TEXT PRIMARY KEY, notebook_id TEXT, tags TEXT DEFAULT '[]')")
    rows = [("s1", NB_OPEN, ["research", "technology"]), ("s2", NB_OPEN, ["technology", " research ", "research"]),
            ("s3", NB_OPEN, []), ("s4", NB_OPEN, None), ("p1", NB_PRIVATE, ["secret"])]
    c.executemany("INSERT INTO sources VALUES (?,?,?)", [(i, nb, json.dumps(t) if t is not None else None) for i, nb, t in rows])
    c.execute("INSERT INTO sources VALUES ('s5', ?, 'not json')", (NB_OPEN,))
    c.commit(); c.close()


def test_tags_are_counted_once_per_source_biggest_first(lib, tagged):
    r = _call(lib, "/phone/library/topics", {"notebook_id": NB_OPEN})
    assert r.status_code == 200
    assert [(t["name"], t["sources"]) for t in r.json()["topics"]] == [("research", 2), ("technology", 2)]
    assert r.json()["sources"] == 5 and r.json()["untagged"] == 3      # s3 empty, s4 null, s5 unreadable


def test_a_hidden_notebook_has_no_topics_for_a_peer(lib, tagged):
    phone_library.set_excluded([NB_PRIVATE])
    assert _call(lib, "/phone/library/topics", {"notebook_id": NB_PRIVATE}).json()["topics"] == []


def test_no_sources_table_means_no_topics_not_an_error(lib):
    assert _call(lib, "/phone/library/topics", {"notebook_id": NB_OPEN}).json()["topics"] == []
