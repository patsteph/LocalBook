"""CI-pure tests for the curator-brain provenance (made-from) edges (Walk phase).

Isolated: monkeypatch settings.data_dir to a tmp dir and build a FRESH CuratorBrain
so brain.db lands under the pytest tmp dir — NEVER the production data dir (the dev
venv points settings.data_dir at real production data).
"""


def _fresh_brain(monkeypatch, tmp_path):
    from config import settings
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    from services.curator_brain._brain import CuratorBrain
    return CuratorBrain()


def test_provenance_roundtrip_and_coercion(monkeypatch, tmp_path):
    brain = _fresh_brain(monkeypatch, tmp_path)
    # mixed input: dicts, a bare id, a None-source (dropped), a dup (deduped)
    n = brain.record_provenance(
        "infographic", "ig_1",
        [{"source_id": "s1"}, {"source_id": "s2"}, {"source_id": None, "n": 3}, "s3", {"source_id": "s1"}],
        notebook_id="nb1",
    )
    assert n == 3  # s1, s2, s3 (None dropped, s1 deduped)
    edges = brain.get_provenance("ig_1")
    assert {e["source_id"] for e in edges} == {"s1", "s2", "s3"}
    assert all(e["artifact_type"] == "infographic" and e["notebook_id"] == "nb1" for e in edges)


def test_provenance_reverse_edge(monkeypatch, tmp_path):
    brain = _fresh_brain(monkeypatch, tmp_path)
    brain.record_provenance("infographic", "ig_1", [{"source_id": "s1"}, {"source_id": "s2"}], notebook_id="nb1")
    brain.record_provenance("document", "doc_9", [{"source_id": "s1"}], notebook_id="nb1")
    rev = brain.get_derived_from_source("s1", notebook_id="nb1")
    got = {(r["artifact_type"], r["artifact_id"]) for r in rev}
    assert got == {("infographic", "ig_1"), ("document", "doc_9")}
    # a source that produced nothing → empty
    assert brain.get_derived_from_source("nope") == []


def test_provenance_regenerate_is_idempotent(monkeypatch, tmp_path):
    brain = _fresh_brain(monkeypatch, tmp_path)
    brain.record_provenance("infographic", "ig_1", ["s1", "s2", "s3"], notebook_id="nb1")
    # re-record (a regenerate) with fewer sources REPLACES, never accumulates dupes
    brain.record_provenance("infographic", "ig_1", ["s1"], notebook_id="nb1")
    edges = brain.get_provenance("ig_1")
    assert [e["source_id"] for e in edges] == ["s1"]


def test_provenance_never_raises_on_junk(monkeypatch, tmp_path):
    brain = _fresh_brain(monkeypatch, tmp_path)
    assert brain.record_provenance("", "", None) == 0
    assert brain.record_provenance("infographic", "ig_x", []) == 0
    assert brain.get_provenance("does-not-exist") == []
