"""CI-pure tests for Journey Canvas recall (Run R1). Pure helpers over an in-memory DB."""
import sqlite3

from storage import canvas_recall_store as recall


def _mem():
    conn = sqlite3.connect(":memory:")
    recall._ensure_schema(conn)
    return conn


def test_next_interval_schedule():
    # New card, "good" → 1 day; second "good" → 3 days; then grows by ease.
    assert recall._next_interval("good", 0.0, 2.3, 0) == (1.0, 2.3)
    assert recall._next_interval("good", 1.0, 2.3, 1) == (3.0, 2.3)
    i, e = recall._next_interval("good", 3.0, 2.3, 2)
    assert i == 3.0 * 2.3 and e == 2.3
    # "again" resets to 1 day and drops ease (floored at 1.3).
    assert recall._next_interval("again", 30.0, 1.4, 5) == (1.0, 1.3)
    # "easy" jumps and bumps ease.
    i, e = recall._next_interval("easy", 4.0, 2.3, 3)
    assert i == 4.0 * 2.3 * 1.3 and e == 2.3 + 0.15


def test_review_persists_and_advances():
    conn = _mem()
    r1 = recall._review(conn, "nb", "n1", "good", now=1000.0)
    assert r1["reps"] == 1 and r1["interval_days"] == 1.0
    assert r1["next_due"] == 1000.0 + 1.0 * recall._DAY
    # Second review advances reps + interval.
    r2 = recall._review(conn, "nb", "n1", "good", now=2000.0)
    assert r2["reps"] == 2 and r2["interval_days"] == 3.0
    # State round-trips.
    st = recall._states(conn, "nb")
    assert st["n1"]["reps"] == 2 and st["n1"]["next_due"] == 2000.0 + 3.0 * recall._DAY


def test_again_resets_after_growth():
    conn = _mem()
    recall._review(conn, "nb", "n1", "good", now=0.0)
    recall._review(conn, "nb", "n1", "easy", now=1.0)   # grows
    grown = recall._states(conn, "nb")["n1"]["interval_days"]
    assert grown > 3.0
    recall._review(conn, "nb", "n1", "again", now=2.0)  # lapse → back to 1 day
    assert recall._states(conn, "nb")["n1"]["interval_days"] == 1.0


def test_public_api_never_raises(monkeypatch):
    # Force the connection to fail → public wrappers degrade, don't raise.
    monkeypatch.setattr(recall, "_get_conn", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert recall.get_states("nb") == {}
    out = recall.review("nb", "n1", "good")
    assert out["node_id"] == "n1" and out["interval_days"] == 1.0
