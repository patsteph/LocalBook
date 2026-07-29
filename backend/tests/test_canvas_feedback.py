"""CI-pure tests for the Journey Canvas Walk feedback loop (2.2.0).

Locks the fail-open contract: a user-drawn edge fans out to 3 effects (event bus,
quality signal, knowledge-graph user link) but NONE of them may ever raise or crash
the edge-creation path, and non-`user` edges are ignored. Effects' real deps may be
absent in the slim test env — that's fine, each fails open to False.
"""
import asyncio

from services import canvas_feedback as cf


def test_schedule_never_raises_and_ignores_non_user():
    # No running loop + various shapes → must never raise.
    cf.schedule_user_edge_feedback("nb1", {"state": "candidate", "source": "a", "target": "b"})
    cf.schedule_user_edge_feedback("nb1", {})
    cf.schedule_user_edge_feedback("nb1", None)  # type: ignore[arg-type]


def test_process_skips_non_user_edge():
    r = asyncio.run(cf.process_user_edge("nb1", {"state": "provenance", "source": "a", "target": "b"}))
    assert r["skipped"] == "non-user-state"
    assert r["emitted"] is False and r["linked"] is False and r["signal"] is False


def test_process_user_edge_returns_wellformed_result_without_crashing():
    r = asyncio.run(cf.process_user_edge(
        "nb1", {"id": "e1", "state": "user", "source": "n1", "target": "n2", "label": "relates"}
    ))
    assert set(r.keys()) == {"emitted", "linked", "signal", "skipped"}
    assert r["skipped"] is None  # a user edge is processed, not skipped


def test_process_user_edge_is_failopen_when_an_effect_raises(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("dep exploded")
    monkeypatch.setattr(cf, "_emit_connection_event", boom)
    monkeypatch.setattr(cf, "_record_connection_signal", boom)
    # Must swallow the failure and still return a result dict (never crash the task).
    r = asyncio.run(cf.process_user_edge("nb1", {"id": "e1", "state": "user", "source": "n1", "target": "n2"}))
    assert isinstance(r, dict) and r["skipped"] is None
