"""LB-12 sync engine — the behaviours the spec's "done when" names, as examples.

The property harness (test_sync_harness.py) checks convergence over random
histories; these pin the specific outcomes a user would notice.
"""

import json

import pytest

from services.sync import engine, journal, merge, registry
from tests.sync_harness import Mac, assert_converged, mesh, pull, sync, template


@pytest.fixture
def macs(tmp_path_factory):
    root = tmp_path_factory.mktemp("sync")
    tmpl = template(tmp_path_factory.getbasetemp())
    return lambda *names: [Mac(root, n, tmpl) for n in names]


def test_every_synced_table_has_its_three_triggers(macs):
    (a,) = macs("A")
    assert journal.missing_triggers(a.conn, "main") == []


def test_every_table_in_localbook_db_is_classified(macs):
    (a,) = macs("A")
    names = [r[0] for r in a.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    unclassified = [n for n in names if registry.classify_table("main", n) == "UNCLASSIFIED"]
    assert unclassified == []


def test_a_source_added_on_one_mac_appears_on_the_other(macs):
    a, b = macs("A", "B")
    a.notebook("nb1", "Leadership")
    a.source("s1", "nb1", "The whole document")
    sync(b, a)
    assert b.sql("SELECT content FROM sources WHERE id='s1'").fetchone()[0] == "The whole document"
    assert_converged([a, b])


def test_rows_that_predate_sync_are_enrolled(macs):
    """Existing data on a Mac that turns sync on must ship too."""
    a, b = macs("A", "B")
    a.sql("DROP TRIGGER _sync_notebooks_ai")           # a write the triggers never saw
    a.notebook("old", "Before sync")
    journal.install(a.conn, "main")
    a.sql("DELETE FROM _sync_state WHERE key LIKE 'enrolled:%'")
    sync(b, a)
    assert b.sql("SELECT title FROM notebooks WHERE id='old'").fetchone()[0] == "Before sync"


def test_edits_to_different_fields_merge_without_a_conflict(macs):
    a, b = macs("A", "B")
    a.notebook("nb1")
    a.note("n1", "nb1", "Title", "Body")
    sync(b, a)
    a.sql("UPDATE canvas_notes SET title='New title' WHERE id='n1'")
    b.sql("UPDATE canvas_notes SET content_markdown='New body' WHERE id='n1'")
    sync(a, b)
    for m in (a, b):
        assert m.sql("SELECT title, content_markdown FROM canvas_notes").fetchone() == ("New title", "New body")
        assert m.conflicts() == []


def test_a_note_edited_on_two_macs_apart_is_exactly_one_conflict_and_loses_nothing(macs):
    """The spec's done-when, verbatim."""
    a, b = macs("A", "B")
    a.notebook("nb1")
    a.note("n1", "nb1", "T", "original")
    sync(b, a)
    a.sql("UPDATE canvas_notes SET content_markdown='written on A' WHERE id='n1'")
    b.sql("UPDATE canvas_notes SET content_markdown='written on B' WHERE id='n1'")
    sync(a, b)
    sync(b, a)
    assert_converged([a, b])
    conflicts = a.conflicts()
    assert len(conflicts) == 1
    kept, other = json.loads(conflicts[0][4]), json.loads(conflicts[0][5])
    assert {kept, other} == {"written on A", "written on B"}


def test_a_later_edit_on_top_of_a_synced_one_is_not_a_conflict(macs):
    a, b = macs("A", "B")
    a.notebook("nb1")
    a.note("n1", "nb1")
    sync(b, a)
    a.sql("UPDATE canvas_notes SET content_markdown='v2' WHERE id='n1'")
    sync(b, a)
    b.sql("UPDATE canvas_notes SET content_markdown='v3' WHERE id='n1'")
    sync(a, b)
    assert a.sql("SELECT content_markdown FROM canvas_notes").fetchone()[0] == "v3"
    assert a.conflicts() == []


def test_changes_relay_through_a_mac_in_the_middle(macs):
    """A and C never meet (the MDM Mac is outbound-only; A may be off)."""
    a, b, c = macs("A", "B", "C")
    a.notebook("nb1", "From A")
    sync(b, a)
    sync(c, b)
    assert c.sql("SELECT title FROM notebooks").fetchone()[0] == "From A"
    c.sql("UPDATE notebooks SET title='Edited on C'")
    sync(c, b)
    sync(a, b)
    assert a.sql("SELECT title FROM notebooks").fetchone()[0] == "Edited on C"


def test_delete_racing_an_edit_deletes_but_keeps_the_edit_in_a_conflict(macs):
    a, b = macs("A", "B")
    a.notebook("nb1")
    a.note("n1", "nb1", "T", "before")
    sync(b, a)
    a.sql("DELETE FROM canvas_notes WHERE id='n1'")
    b.sql("UPDATE canvas_notes SET content_markdown='edited while A deleted it' WHERE id='n1'")
    sync(a, b)
    sync(b, a)
    assert_converged([a, b])
    for m in (a, b):
        assert m.sql("SELECT COUNT(*) FROM canvas_notes").fetchone()[0] == 0
    kept = [json.loads(c[5]) for c in a.conflicts()]
    assert "edited while A deleted it" in kept


def test_deleting_a_notebook_cascades_everywhere(macs):
    a, b = macs("A", "B")
    a.notebook("nb1")
    a.source("s1", "nb1")
    sync(b, a)
    a.sql("DELETE FROM notebooks WHERE id='nb1'")
    sync(b, a)
    assert b.sql("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    assert_converged([a, b])


def test_resyncing_changes_nothing(macs):
    a, b = macs("A", "B")
    a.notebook("nb1")
    a.note("n1", "nb1")
    sync(b, a)
    rep = pull(b, a)
    assert rep["inserted"] == rep["updated"] == rep["deleted"] == 0


def test_dry_run_reports_and_writes_nothing(macs):
    a, b = macs("A", "B")
    a.notebook("nb1", "Preview me")
    rep = pull(b, a, dry_run=True)
    assert rep["inserted"] >= 1
    assert b.sql("SELECT COUNT(*) FROM notebooks").fetchone()[0] == 0
    assert engine.vv(b.conn) == {}


def test_a_page_interrupted_mid_pull_is_safe_to_repeat(macs):
    a, b = macs("A", "B")
    a.notebook("nb1")
    for i in range(20):
        a.note(f"n{i}", "nb1", f"t{i}")
    page = engine.export(a.rep, engine.vv(b.conn), limit=5)
    engine.apply(b.rep, page)
    engine.apply(b.rep, page)             # the same page again (a retry after a drop)
    pull(b, a)
    assert_converged([a, b])


def test_absolute_paths_under_the_data_dir_travel_relative(macs):
    a, b = macs("A", "B")
    a.notebook("nb1")
    a.sql("INSERT INTO audio_generations (audio_id, notebook_id, audio_file_path, created_at, updated_at) "
          "VALUES ('au1', 'nb1', ?, 'now', 'now')", str(a.dir / "audio" / "au1.wav"))
    sync(b, a)
    assert b.sql("SELECT audio_file_path FROM audio_generations").fetchone()[0] == str(b.dir / "audio" / "au1.wav")


def test_counters_never_ship(macs):
    a, b = macs("A", "B")
    a.notebook("nb1")
    sync(b, a)
    a.sql("UPDATE notebooks SET source_count = 42")
    assert engine.ship(a.rep) == 0                       # not a change worth shipping
    sync(b, a)
    assert b.sql("SELECT source_count FROM notebooks").fetchone()[0] != 42


def test_merge_register_is_commutative():
    l = {"v": "A", "c": "0000000000002.000000.A", "h": ["0000000000002.000000.A", "0000000000001.000000.X"]}
    r = {"v": "B", "c": "0000000000003.000000.B", "h": ["0000000000003.000000.B", "0000000000001.000000.X"]}
    assert merge.merge_register(l, r) == merge.merge_register(r, l)


def test_a_note_added_to_a_notebook_deleted_elsewhere_is_unfiled_not_lost(macs):
    """Found by the 10k harness: A deletes nb1 while B adds a note to it.
    canvas_notes.notebook_id is ON DELETE SET NULL — the note survives, unfiled,
    on both Macs."""
    a, b = macs("A", "B")
    a.notebook("nb1")
    sync(b, a)
    a.sql("DELETE FROM notebooks WHERE id='nb1'")
    b.note("n1", "nb1", "T", "written while A deleted the notebook")
    sync(a, b)
    sync(b, a)
    assert_converged([a, b])
    for m in (a, b):
        assert m.sql("SELECT notebook_id, content_markdown FROM canvas_notes").fetchone() == \
            (None, "written while A deleted the notebook")


def test_a_source_added_to_a_notebook_deleted_elsewhere_is_kept_in_a_conflict(macs):
    """sources.notebook_id is ON DELETE CASCADE — the source goes, but its text is
    kept in one conflict item, the same on both Macs."""
    a, b = macs("A", "B")
    a.notebook("nb1")
    sync(b, a)
    a.sql("DELETE FROM notebooks WHERE id='nb1'")
    b.source("s1", "nb1", "a document added on B")
    sync(a, b)
    sync(b, a)
    assert_converged([a, b])
    for m in (a, b):
        assert m.sql("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    items = a.conflicts()
    assert len(items) == 1 and "a document added on B" in items[0][5]


# ── phase D/F: documents and logs ride the same engine ──────────────────────


def _doc(m, kind, key, body):
    m.sql("INSERT INTO documents (kind, key, uuid, body_json, updated_at) VALUES (?, ?, ?, ?, 'now') "
          "ON CONFLICT(kind, key) DO UPDATE SET body_json=excluded.body_json",
          kind, key, f"{m.name}-{key}", json.dumps(body))


def test_core_memories_added_on_two_macs_both_survive(macs):
    """One document per entry: two Macs adding memories while apart is not a conflict."""
    a, b = macs("A", "B")
    _doc(a, "core_memory", "e1", {"key": "name", "value": "Pat"})
    _doc(b, "core_memory", "e2", {"key": "city", "value": "Lisbon"})
    sync(a, b)
    sync(b, a)
    assert_converged([a, b])
    for m in (a, b):
        assert sorted(k for (k,) in m.sql("SELECT key FROM documents WHERE kind='core_memory'")) == ["e1", "e2"]
        assert m.conflicts() == []


def test_log_rows_sync_by_uid_without_id_collisions(macs):
    """Both Macs' first log row has local id 1; they must not overwrite each other."""
    a, b = macs("A", "B")
    a.sql("INSERT INTO correspondent_events (ts, event_type, sender) VALUES ('t1', 'mail', 'a@x')")
    b.sql("INSERT INTO correspondent_events (ts, event_type, sender) VALUES ('t2', 'mail', 'b@x')")
    assert a.sql("SELECT id FROM correspondent_events").fetchone()[0] == 1
    assert b.sql("SELECT id FROM correspondent_events").fetchone()[0] == 1
    sync(a, b)
    sync(b, a)
    for m in (a, b):
        rows = sorted(m.sql("SELECT sender FROM correspondent_events").fetchall())
        assert rows == [("a@x",), ("b@x",)]
        assert m.sql("SELECT COUNT(DISTINCT uid) FROM correspondent_events").fetchone()[0] == 2
    assert_converged([a, b])
