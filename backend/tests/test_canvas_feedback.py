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


def test_process_user_edge_orchestrates_all_three_effects(monkeypatch):
    """CI-pure: monkeypatch the three effect seams (event bus / signal / graph link)
    so NO real writes happen, and assert process_user_edge fans out to all three with
    the right node refs and threads their results back into the summary dict."""
    calls = {"emit": None, "signal": None, "link": None}

    def _emit(nb, edge):
        calls["emit"] = (nb, edge.get("id"))
        return True

    def _signal(nb, edge):
        calls["signal"] = (nb, edge.get("label"))
        return True

    async def _link(nb, src, dst, edge):
        calls["link"] = (nb, src, dst)
        return True

    monkeypatch.setattr(cf, "_emit_connection_event", _emit)
    monkeypatch.setattr(cf, "_record_connection_signal", _signal)
    monkeypatch.setattr(cf, "_write_user_link", _link)

    edge = {"id": "e1", "state": "user", "source": "n1", "target": "n2", "label": "relates"}
    r = asyncio.run(cf.process_user_edge("nb1", edge))

    assert set(r.keys()) == {"emitted", "linked", "signal", "skipped"}
    assert r == {"emitted": True, "linked": True, "signal": True, "skipped": None}
    assert calls["emit"] == ("nb1", "e1")
    assert calls["signal"] == ("nb1", "relates")
    assert calls["link"] == ("nb1", "n1", "n2")  # endpoints passed source→target


def test_process_user_edge_is_failopen_when_every_effect_raises(monkeypatch):
    """CI-pure: all three seams explode; process_user_edge must swallow each failure,
    never crash the task, and still return a well-formed result with every effect False."""
    def boom(*_a, **_k):
        raise RuntimeError("dep exploded")

    async def aboom(*_a, **_k):
        raise RuntimeError("dep exploded")

    monkeypatch.setattr(cf, "_emit_connection_event", boom)
    monkeypatch.setattr(cf, "_record_connection_signal", boom)
    monkeypatch.setattr(cf, "_write_user_link", aboom)

    r = asyncio.run(cf.process_user_edge("nb1", {"id": "e1", "state": "user", "source": "n1", "target": "n2"}))
    assert isinstance(r, dict)
    assert r == {"emitted": False, "linked": False, "signal": False, "skipped": None}
