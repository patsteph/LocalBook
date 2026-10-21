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
