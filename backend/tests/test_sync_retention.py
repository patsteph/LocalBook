"""Sync's bookkeeping stops growing forever — without leaving any Mac behind.

Tombstones go only once every paired Mac holds the delete (and it is old); log rows
past their age are purged locally on every Mac by the same rule, never shipped.
"""
from datetime import datetime

import pytest

from services.sync import engine, retention
from tests.sync_harness import Mac, pull, sync, template

FUTURE = 4_000_000_000_000          # far past any tombstone's age


@pytest.fixture
def macs(tmp_path_factory):
    root = tmp_path_factory.mktemp("ret")
    tmpl = template(tmp_path_factory.getbasetemp())
    return lambda *n: [Mac(root, x, tmpl) for x in n]


def _tombstones(m):
    return m.engine_conn.execute(
        "SELECT COUNT(*) FROM _sync_rows WHERE json_extract(meta, '$.__del__.v') = 1").fetchone()[0]


def _deleted_note_everywhere(macs):
    a = macs[0]
    a.notebook("nb1")
    a.note("n1", "nb1")
    for m in macs[1:]:
        sync(m, a)
    a.sql("DELETE FROM canvas_notes WHERE id='n1'")
    engine.ship(a.rep)


def test_a_tombstone_goes_once_every_mac_holds_the_delete(macs):
    a, b = macs("A", "B")
    _deleted_note_everywhere([a, b])
    assert retention.gc_tombstones(a.rep, [engine.vv(b.conn)], FUTURE) == 0      # B hasn't got it
    sync(b, a)
    assert b.sql("SELECT COUNT(*) FROM canvas_notes").fetchone()[0] == 0
    assert retention.gc_tombstones(a.rep, [engine.vv(b.conn)], FUTURE) == 1
    assert _tombstones(a) == 0


def test_a_mac_that_is_behind_blocks_gc_and_nothing_comes_back(macs):
    a, b, c = macs("A", "B", "C")
    _deleted_note_everywhere([a, b, c])
    sync(b, a)                                   # B has the delete; C was away
    assert retention.gc_tombstones(a.rep, [engine.vv(b.conn), engine.vv(c.conn)], FUTURE) == 0
    c.sql("UPDATE canvas_notes SET content_markdown='edited while away' WHERE id='n1'")
    sync(a, c)
    # the tombstone was kept, so the late edit is an edited-while-deleted conflict, not a resurrection
    assert a.sql("SELECT COUNT(*) FROM canvas_notes WHERE id='n1'").fetchone()[0] == 0
    assert any(row[3] == "__del__" or "edit" in str(row) for row in a.conflicts())


def test_a_young_tombstone_is_kept(macs):
    a, b = macs("A", "B")
    _deleted_note_everywhere([a, b])
    sync(b, a)
    assert retention.gc_tombstones(a.rep, [engine.vv(b.conn)], now_ms=0) == 0


def test_old_log_rows_are_purged_locally_and_not_shipped(macs):
    a, b = macs("A", "B")
    a.sql("INSERT INTO correspondent_events (ts, event_type, sender) VALUES ('2025-01-01T00:00:00', 'e', 'old')")
    a.sql("INSERT INTO correspondent_events (ts, event_type, sender) VALUES ('2026-10-01T00:00:00', 'e', 'new')")
    sync(b, a)
    out = retention.purge_old_logs(a.rep, {"correspondent_events": ("ts", 180)}, datetime(2026, 10, 3))
    assert out == {"correspondent_events": 1}
    assert [r[0] for r in a.sql("SELECT sender FROM correspondent_events")] == ["new"]
    pull(b, a)                                   # nothing ships: B still has its own copy
    assert sorted(r[0] for r in b.sql("SELECT sender FROM correspondent_events")) == ["new", "old"]
    assert engine.export(a.rep, engine.vv(b.conn))["versions"] == []
    # B applies the same rule on its own run and they agree again
    retention.purge_old_logs(b.rep, {"correspondent_events": ("ts", 180)}, datetime(2026, 10, 3))
    assert a.snapshot() == b.snapshot()


def test_acked_vectors_only_move_forward(tmp_path, monkeypatch):
    from config import settings
    from services.sync import store
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    retention.record_acked("mbp", {"main": {"A": 5, "B": 2}})
    retention.record_acked("mbp", {"main": {"A": 3, "B": 7}})
    assert store.get("acked_vv:mbp:main") == {"A": 5, "B": 7}
