"""Contradiction persistence — the scan (and the user's dismissal) must survive a restart.

Before this store, `_contradiction_cache` was a module-level dict: an expensive LLM scan died
with the process, and `dismiss_contradiction` — the user saying "this conflict isn't real" —
was forgotten on the next launch, so the same rejected conflict came back.
"""
import importlib

import pytest


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A fresh store bound to a temp DB. `settings.data_dir` in the dev venv is the REAL
    production data dir, so this must be pointed away before anything writes."""
    from config import settings
    # MUST stay a Path: stores do `data_dir / "x.db"`, so a str here fails with
    # "unsupported operand type(s) for /". Documented trap; walked into it anyway.
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    import storage.database as db
    importlib.reload(db)
    import storage.contradiction_store as cs
    importlib.reload(cs)
    cs._SCHEMA_READY = False
    return cs


def _report(nb="nb1", cid="c1", a="sA", b="sB", severity="high", dismissed=False):
    return {
        "notebook_id": nb,
        "generated_at": "2026-08-18T00:00:00",
        "claims_analyzed": 12,
        "sources_analyzed": 3,
        "contradictions": [{
            "id": cid,
            "claim_a": {"id": "k1", "text": "up", "source_id": a, "source_name": "A",
                        "chunk_text": "", "claim_type": "factual"},
            "claim_b": {"id": "k2", "text": "down", "source_id": b, "source_name": "B",
                        "chunk_text": "", "claim_type": "factual"},
            "contradiction_type": "factual",
            "severity": severity,
            "explanation": "One says up, the other down.",
            "detected_at": "2026-08-18T00:00:00",
            "dismissed": dismissed,
            "resolved": False,
        }],
    }


def test_roundtrip_survives_a_new_process(store):
    assert store.save_report("nb1", _report()) is True
    # Simulate a restart: nothing in memory, read straight from disk.
    got = store.load_report("nb1")
    assert got is not None
    assert got["claims_analyzed"] == 12
    assert len(got["contradictions"]) == 1
    assert got["contradictions"][0]["explanation"] == "One says up, the other down."


def test_unscanned_notebook_returns_none_not_an_empty_report(store):
    """Callers distinguish "never scanned" from "scanned, found nothing"."""
    assert store.load_report("never-scanned") is None


def test_dismissal_persists(store):
    store.save_report("nb1", _report())
    assert store.set_flag("nb1", "c1", "dismissed", True) is True
    assert store.load_report("nb1")["contradictions"][0]["dismissed"] is True


def test_a_rescan_does_not_resurrect_a_dismissed_conflict(store):
    """THE case that made dismissal feel broken: re-scan finds the same conflict again, and the
    user's judgment must not be silently overwritten."""
    store.save_report("nb1", _report())
    store.set_flag("nb1", "c1", "dismissed", True)
    store.save_report("nb1", _report())          # same id comes back from a fresh scan
    assert store.load_report("nb1")["contradictions"][0]["dismissed"] is True


def test_rescan_replaces_the_previous_set(store):
    store.save_report("nb1", _report(cid="old"))
    store.save_report("nb1", _report(cid="new"))
    ids = [c["id"] for c in store.load_report("nb1")["contradictions"]]
    assert ids == ["new"]


def test_notebooks_are_isolated(store):
    store.save_report("nb1", _report(nb="nb1", cid="c1"))
    store.save_report("nb2", _report(nb="nb2", cid="c2"))
    assert [c["id"] for c in store.load_report("nb1")["contradictions"]] == ["c1"]
    assert [c["id"] for c in store.load_report("nb2")["contradictions"]] == ["c2"]
    store.clear("nb1")
    assert store.load_report("nb1") is None
    assert store.load_report("nb2") is not None


# ── source_pairs: what the canvas actually draws ────────────────────────────────

def test_source_pairs_collapses_many_conflicts_between_the_same_two_sources(store):
    """Two sources clashing on six points is ONE disagreement on the map, not six edges."""
    rep = _report()
    rep["contradictions"] = [
        dict(rep["contradictions"][0], id=f"c{i}", severity=s)
        for i, s in enumerate(["low", "high", "medium"])
    ]
    store.save_report("nb1", rep)
    pairs = store.source_pairs("nb1")
    assert len(pairs) == 1
    assert pairs[0]["severity"] == "high"   # worst severity wins


def test_source_pairs_is_order_independent(store):
    """(A,B) and (B,A) are the same disagreement."""
    rep = _report()
    c = rep["contradictions"][0]
    flipped = dict(c, id="c2", claim_a=c["claim_b"], claim_b=c["claim_a"])
    rep["contradictions"] = [c, flipped]
    store.save_report("nb1", rep)
    assert len(store.source_pairs("nb1")) == 1


def test_source_pairs_excludes_dismissed_by_default(store):
    store.save_report("nb1", _report())
    assert len(store.source_pairs("nb1")) == 1
    store.set_flag("nb1", "c1", "dismissed", True)
    assert store.source_pairs("nb1") == []
    assert len(store.source_pairs("nb1", include_dismissed=True)) == 1


def test_source_pairs_drops_self_and_blank_pairs(store):
    rep = _report(a="same", b="same")
    store.save_report("nb1", rep)
    assert store.source_pairs("nb1") == []
