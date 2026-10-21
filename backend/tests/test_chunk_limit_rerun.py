"""The chunk-limit pass must look again at tables that changed after it ran."""
import json

from services import chunk_limit_migration as m


def test_seen_versions_are_only_trusted_for_the_same_embedder_and_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_marker_path", lambda: tmp_path / "marker.json")
    (tmp_path / "marker.json").write_text(json.dumps({"model": "a", "max_tokens": 512, "versions": {"notebook_x": 7}}))
    assert m._seen_versions("a", 512) == {"notebook_x": 7}
    assert m._seen_versions("b", 512) == {}            # another embedder: everything is checked again
    assert m._seen_versions("a", 256) == {}            # another limit
    (tmp_path / "marker.json").write_text(json.dumps({"model": "a", "max_tokens": 512}))
    assert m._seen_versions("a", 512) == {}            # a marker from before version tracking: one full look


def test_questions_are_trimmed_until_the_first_piece_fits_with_them():
    class Prof:
        max_tokens = 20
        @staticmethod
        def format_document(t): return t

    count = lambda t: len(t.split())
    row = {"text": " ".join(["w"] * 10), "synthetic_questions": " ".join(["q"] * 40)}
    m._fit_questions(row, Prof, count)
    from services.embedding_migration import embedded_text
    assert count(Prof.format_document(embedded_text(row))) + 2 <= 20
    assert row["text"] == " ".join(["w"] * 10)                      # the text is never touched
    row = {"text": " ".join(["w"] * 30), "synthetic_questions": "q q q"}
    m._fit_questions(row, Prof, count)
    assert row["synthetic_questions"] == ""                          # no room at all: questions go, text stays
