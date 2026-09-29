"""LB-2 `events_since` — the cursor-paged feed that replaces LB-5's webhooks.

The property everything here defends: **a poll must never silently skip an
event.** Jocasta polls this from Hermes's heartbeat and has no other way to
learn what LocalBook did, so a dropped event is invisible rather than noisy.

Both sources are faked with real in-memory SQLite, because the cursor logic is
entirely about how two independent AUTOINCREMENT sequences interleave — a mock
returning canned lists would test the arithmetic and miss the thing that breaks.
"""

import sqlite3

import pytest

from services import event_feed


class _FakeBrain:
    def __init__(self, conn):
        self._conn = conn


@pytest.fixture
def feed(monkeypatch):
    """Two separate in-memory databases, as in production."""
    brain = sqlite3.connect(":memory:")
    brain.row_factory = sqlite3.Row
    brain.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
        "notebook_id TEXT, actor TEXT, action TEXT, intent TEXT, payload TEXT, outcome TEXT)"
    )

    activity = sqlite3.connect(":memory:")
    activity.row_factory = sqlite3.Row
    activity.execute(
        "CREATE TABLE activity_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "notebook_id TEXT, ts TEXT, kind TEXT, actor TEXT, payload_json TEXT)"
    )

    import services.curator_brain as cb
    import services.activity_ledger as al

    monkeypatch.setattr(cb, "curator_brain", _FakeBrain(brain), raising=False)
    monkeypatch.setattr(al, "_get_conn", lambda: activity)

    class Handles:
        pass

    h = Handles()
    h.brain, h.activity = brain, activity
    return h


def add_curator(feed, ts, action, notebook="nb1", actor="@curator", payload="{}",
                intent=None, outcome=None):
    feed.brain.execute(
        "INSERT INTO events (ts, notebook_id, actor, action, intent, payload, outcome) "
        "VALUES (?,?,?,?,?,?,?)",
        (ts, notebook, actor, action, intent, payload, outcome),
    )
    feed.brain.commit()


def add_activity(feed, ts, kind, notebook="nb1", actor="user", payload="{}"):
    feed.activity.execute(
        "INSERT INTO activity_events (notebook_id, ts, kind, actor, payload_json) "
        "VALUES (?,?,?,?,?)",
        (notebook, ts, kind, actor, payload),
    )
    feed.activity.commit()


# ── the cursor ──────────────────────────────────────────────────────────────


def test_cursor_round_trips():
    assert event_feed.parse_cursor(event_feed.encode_cursor(12, 34)) == (12, 34)


def test_no_cursor_starts_from_the_beginning():
    assert event_feed.parse_cursor(None) == (0, 0)
    assert event_feed.parse_cursor("") == (0, 0)


def test_a_corrupt_cursor_replays_rather_than_skips():
    """Replaying is de-duplicable by the client. Guessing forward loses events
    and looks like nothing happened."""
    assert event_feed.parse_cursor("garbage") == (0, 0)
    assert event_feed.parse_cursor("b12") == (0, 0)
    assert event_feed.parse_cursor("bX.aY") == (0, 0)


# ── reading both sources ────────────────────────────────────────────────────


def test_both_sources_appear_in_one_feed(feed):
    add_curator(feed, "2026-09-29T10:00:00", "morning_brief")
    add_activity(feed, "2026-09-29T10:01:00", "source_added")

    result = event_feed.events_since()

    assert [e["kind"] for e in result["events"]] == ["morning_brief", "source_added"]
    assert {e["source"] for e in result["events"]} == {"curator", "activity"}


def test_events_are_merged_in_time_order(feed):
    add_activity(feed, "2026-09-29T10:00:00", "source_added")
    add_curator(feed, "2026-09-29T09:00:00", "morning_brief")
    add_activity(feed, "2026-09-29T11:00:00", "chat_qa")

    kinds = [e["kind"] for e in event_feed.events_since()["events"]]
    assert kinds == ["morning_brief", "source_added", "chat_qa"]


def test_curator_intent_and_outcome_land_in_the_payload(feed):
    add_curator(feed, "2026-09-29T10:00:00", "judge_item",
                payload='{"title": "x"}', intent="triage", outcome="approved")

    payload = event_feed.events_since()["events"][0]["payload"]
    assert payload == {"title": "x", "intent": "triage", "outcome": "approved"}


def test_unparseable_payload_does_not_break_the_feed(feed):
    add_curator(feed, "2026-09-29T10:00:00", "weird", payload="not json at all")
    event = event_feed.events_since()["events"][0]
    assert event["payload"]["raw"].startswith("not json")


# ── paging: the part that must not skip ─────────────────────────────────────


def test_a_second_poll_returns_only_what_is_new(feed):
    add_curator(feed, "2026-09-29T10:00:00", "a")
    add_activity(feed, "2026-09-29T10:01:00", "b")

    first = event_feed.events_since()
    assert len(first["events"]) == 2

    second = event_feed.events_since(first["cursor"])
    assert second["events"] == []

    add_activity(feed, "2026-09-29T10:02:00", "c")
    third = event_feed.events_since(second["cursor"])
    assert [e["kind"] for e in third["events"]] == ["c"]


def test_nothing_is_skipped_when_a_page_is_truncated(feed):
    """The failure this design exists to prevent: advancing the cursor past
    events the page did not actually return."""
    for i in range(10):
        add_curator(feed, f"2026-09-29T10:{i:02d}:00", f"c{i}")
        add_activity(feed, f"2026-09-29T10:{i:02d}:30", f"a{i}")

    seen = []
    cursor = None
    for _ in range(20):
        page = event_feed.events_since(cursor, limit=3)
        if not page["events"]:
            break
        seen.extend(e["kind"] for e in page["events"])
        cursor = page["cursor"]

    assert len(seen) == 20
    assert len(set(seen)) == 20      # nothing duplicated
    assert set(seen) == {f"c{i}" for i in range(10)} | {f"a{i}" for i in range(10)}


def test_a_busy_source_delays_but_never_loses_the_quiet_one(feed):
    """A page CAN be entirely one source — 30 curator events sorted ahead of a
    later notebook event will fill it. What must hold is that the quiet source's
    cursor did not advance past the event it never returned, so the next poll
    still gets it."""
    for i in range(30):
        add_curator(feed, f"2026-09-29T09:{i:02d}:00", f"noise{i}")
    add_activity(feed, "2026-09-29T23:00:00", "the_one_that_matters")

    first = event_feed.events_since(limit=5)
    assert first["has_more"] is True

    seen, cursor = [], None
    for _ in range(20):
        page = event_feed.events_since(cursor, limit=5)
        if not page["events"]:
            break
        seen.extend(e["kind"] for e in page["events"])
        cursor = page["cursor"]

    assert "the_one_that_matters" in seen
    assert len(seen) == 31
    assert len(set(seen)) == 31


def test_has_more_is_set_when_there_is_more(feed):
    for i in range(10):
        add_curator(feed, f"2026-09-29T10:{i:02d}:00", f"c{i}")
    page = event_feed.events_since(limit=3)
    assert page["has_more"] is True

    rest = event_feed.events_since(page["cursor"], limit=100)
    assert rest["has_more"] is False


def test_an_empty_feed_is_not_an_error(feed):
    result = event_feed.events_since()
    assert result["events"] == []
    assert result["cursor"] == "b0.a0"


# ── filtering + bounds ──────────────────────────────────────────────────────


def test_kinds_filters_both_sources(feed):
    add_curator(feed, "2026-09-29T10:00:00", "morning_brief")
    add_curator(feed, "2026-09-29T10:01:00", "judge_item")
    add_activity(feed, "2026-09-29T10:02:00", "morning_brief")
    add_activity(feed, "2026-09-29T10:03:00", "chat_qa")

    kinds = [e["kind"] for e in event_feed.events_since(kinds=["morning_brief"])["events"]]
    assert kinds == ["morning_brief", "morning_brief"]


def test_limit_is_clamped(feed):
    for i in range(10):
        add_curator(feed, f"2026-09-29T10:{i:02d}:00", f"c{i}")
    assert len(event_feed.events_since(limit=100_000)["events"]) <= event_feed.MAX_LIMIT
    assert len(event_feed.events_since(limit=0)["events"]) == 1
    assert len(event_feed.events_since(limit=None)["events"]) == 10


def test_known_kinds_lists_both_sources(feed):
    add_curator(feed, "2026-09-29T10:00:00", "morning_brief")
    add_activity(feed, "2026-09-29T10:01:00", "source_added")
    assert event_feed.known_kinds() == ["morning_brief", "source_added"]


# ── one source failing ──────────────────────────────────────────────────────


def test_the_feed_survives_one_source_being_unavailable(feed, monkeypatch):
    """brain.db being locked must not blank out notebook activity as well.

    The brain is replaced with something that raises on access; the activity
    connection from the fixture is left alone. (An earlier version of this test
    called monkeypatch.undo(), which unwound BOTH patches and removed the very
    source it was checking still worked.)
    """
    add_activity(feed, "2026-09-29T10:00:00", "source_added")

    import services.curator_brain as cb

    monkeypatch.setattr(cb, "curator_brain", object(), raising=False)

    result = event_feed.events_since()

    assert [e["kind"] for e in result["events"]] == ["source_added"]
    assert result["cursor"] == "b0.a1"
