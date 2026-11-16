"""A peer reads one notebook's topics: the topic model's output, counted for that notebook only."""
import sys
import types

import pytest
from services.sync import phone_library
from tests.test_phone_library import NB_OPEN, NB_PRIVATE, _call, lib  # noqa: F401


class Topic:
    def __init__(self, topic_id, name, keywords):
        self.topic_id, self.name, self.enhanced_name, self.keywords = topic_id, name, None, keywords

    @property
    def display_name(self):
        return self.enhanced_name or self.name


class Doc:
    def __init__(self, notebook_id, source_id, topic_id):
        self.notebook_id, self.source_id, self.topic_id = notebook_id, source_id, topic_id


@pytest.fixture()
def model(monkeypatch):
    topics = [Topic(0, "RAG pipelines", [("rag", .9), ("retrieval", .8)]), Topic(1, "Reranking", [("rerank", .7)]),
              Topic(2, "Elsewhere", [("other", .5)])]
    docs = [Doc(NB_OPEN, "s1", 0), Doc(NB_OPEN, "s2", 0), Doc(NB_OPEN, "s2", 1), Doc(NB_OPEN, "s3", -1),
            Doc(NB_PRIVATE, "p1", 2), Doc(NB_PRIVATE, "p1", 0)]

    async def get_topics(notebook_id=None):
        return topics

    svc = types.SimpleNamespace(get_topics=get_topics, _documents=docs)
    monkeypatch.setitem(sys.modules, "services.topic_modeling", types.SimpleNamespace(topic_modeling_service=svc))
    return svc


def test_topics_are_counted_for_the_notebook_asked_about(lib, model):
    r = _call(lib, "/phone/library/topics", {"notebook_id": NB_OPEN})
    assert r.status_code == 200
    got = r.json()["topics"]
    assert [(t["name"], t["chunks"], t["sources"]) for t in got] == [("RAG pipelines", 2, 2), ("Reranking", 1, 1)]
    assert got[0]["keywords"] == ["rag", "retrieval"] and got[0]["id"] == "0"


def test_a_hidden_notebook_has_no_topics_for_a_peer(lib, model):
    phone_library.set_excluded([NB_PRIVATE])
    assert _call(lib, "/phone/library/topics", {"notebook_id": NB_PRIVATE}).json()["topics"] == []


def test_a_broken_topic_model_means_no_topics_not_an_error(lib, monkeypatch):
    monkeypatch.setitem(sys.modules, "services.topic_modeling", types.SimpleNamespace())
    assert _call(lib, "/phone/library/topics", {"notebook_id": NB_OPEN}).json()["topics"] == []
