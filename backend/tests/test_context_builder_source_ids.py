"""BuiltContext must carry REAL source ids, not just filenames (Wave A, chokepoint fix).

The citation contract (`[Sn] filename`) is filename-keyed, which is right for prompting and
wrong as an identity. Callers that needed identity — provenance — matched those filenames back
against source_store, which silently recorded NOTHING when two sources share a filename, when a
source was renamed after generation, or when the filename was the "Unknown" default.

These pin the ids surviving the trip, and specifically the three cases the old lookup lost.
"""
import asyncio

import pytest

from services.context_builder import BuiltContext, ContextProfile, ContextBuilder


class _FakeSourceStore:
    def __init__(self, contents):
        self._contents = contents

    async def get_content(self, notebook_id, source_id):
        body = self._contents.get(source_id)
        return {"content": body} if body else None


def _profile(**kw):
    base = dict(max_sources=10, chars_per_source=1000, total_context_chars=10000,
                strategy="depth", use_chunks=False, chunk_top_k=0, use_map_reduce=False)
    base.update(kw)
    return ContextProfile(**base)


def _direct(monkeypatch, sources, contents):
    """Run _build_direct_context against a stubbed source_store."""
    import storage.source_store as ss
    monkeypatch.setattr(ss, "source_store", _FakeSourceStore(contents))
    cb = ContextBuilder()
    return asyncio.run(cb._build_direct_context("nb1", sources, _profile()))


def test_direct_context_returns_ids_parallel_to_names(monkeypatch):
    sources = [{"id": "s1", "filename": "a.pdf"}, {"id": "s2", "filename": "b.pdf"}]
    parts, names, ids = _direct(monkeypatch, sources, {"s1": "alpha", "s2": "beta"})
    assert names == ["a.pdf", "b.pdf"]
    assert ids == ["s1", "s2"]
    assert len(parts) == 2


def test_a_source_with_no_content_drops_from_both_lists(monkeypatch):
    """Skipping a source must not shift ids out of step with names."""
    sources = [{"id": "s1", "filename": "a.pdf"},
               {"id": "s2", "filename": "b.pdf"},
               {"id": "s3", "filename": "c.pdf"}]
    _, names, ids = _direct(monkeypatch, sources, {"s1": "alpha", "s3": "gamma"})
    assert names == ["a.pdf", "c.pdf"]
    assert ids == ["s1", "s3"]


def test_duplicate_filenames_keep_distinct_ids(monkeypatch):
    """THE case the old filename lookup lost: same filename, two real sources. The citation
    contract collapses them to one [Sn]; identity must not collapse."""
    sources = [{"id": "s1", "filename": "report.pdf"}, {"id": "s2", "filename": "report.pdf"}]
    _, names, ids = _direct(monkeypatch, sources, {"s1": "one", "s2": "two"})
    assert names == ["report.pdf", "report.pdf"]
    assert ids == ["s1", "s2"]


def test_unknown_filename_still_yields_an_id(monkeypatch):
    """A source with no filename defaults to "Unknown"; the old lookup mapped that to None and
    the provenance row was silently dropped."""
    _, names, ids = _direct(monkeypatch, [{"id": "s9"}], {"s9": "body"})
    assert names == ["Unknown"]
    assert ids == ["s9"]


def test_builtcontext_defaults_are_empty_not_none():
    """Every early-return path (cursor data notebook, empty notebook) leaves these empty, and
    callers gate on truthiness — a None would raise instead of skipping."""
    bc = BuiltContext(context="", sources_used=0, source_names=[], total_chars=0,
                      strategy_used="none", profile_used="x")
    assert bc.source_ids == []
    assert bc.sources_id_map == {}


def test_storyboard_source_ids_default_for_rehydrated_json():
    """A Storyboard rebuilt from an older persisted storyboard_json has no source_ids field."""
    from services.video_storyboard import Storyboard
    sb = Storyboard(title="t", topic="x", scenes=[], source_names=["a.pdf"],
                    estimated_duration_seconds=60)
    assert sb.source_ids == []
    assert "source_ids" in sb.to_dict()
