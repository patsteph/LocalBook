"""HyDE questions made on one Mac travel with the source and are reused on the other only when they still fit."""
from services import rag_storage
from services.sync import indexer


def test_a_record_is_usable_only_for_the_same_text_and_chunk_count():
    text = "alpha beta gamma"
    rec = rag_storage.hyde_record(text, ["q1 q2 q3", "q4 q5 q6"])
    assert rag_storage.hyde_usable(rec, text, 2) == ["q1 q2 q3", "q4 q5 q6"]
    assert rag_storage.hyde_usable(rec, text + "!", 2) is None          # text changed
    assert rag_storage.hyde_usable(rec, text, 3) is None                # chunked differently
    assert rag_storage.hyde_usable(None, text, 2) is None
    assert rag_storage.hyde_usable({"q": "not a list"}, text, 2) is None


def test_indexer_passes_nothing_when_the_source_carries_no_questions():
    assert indexer._carried_questions({"hyde": None, "type": "web", "filename": "x"}, "text") is None
    assert indexer._carried_questions({"type": "web", "filename": "x"}, "text") is None


def test_indexer_reuses_questions_that_match(monkeypatch):
    from services import rag_chunking
    text = "one two three four"
    monkeypatch.setattr(rag_chunking, "chunk_text_smart", lambda t, ty, fn: ["a", "b"])
    rec = rag_storage.hyde_record(text, ["qa", "qb"])
    assert indexer._carried_questions({"hyde": rec, "type": "web", "filename": "x"}, text) == ["qa", "qb"]
    monkeypatch.setattr(rag_chunking, "chunk_text_smart", lambda t, ty, fn: ["a", "b", "c"])
    assert indexer._carried_questions({"hyde": rec, "type": "web", "filename": "x"}, text) is None
