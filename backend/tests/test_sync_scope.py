"""Notebooks kept on one Mac never sync with another device, in either direction."""

import pytest

from services.sync import engine, scope
from tests.sync_harness import Mac, assert_converged, pull, sync, template


@pytest.fixture
def macs(tmp_path_factory):
    root = tmp_path_factory.mktemp("scope")
    tmpl = template(tmp_path_factory.getbasetemp())
    blocked = {}
    scope.install(lambda r: blocked.get(r.device, frozenset()))
    made = lambda *names: [Mac(root, n, tmpl) for n in names]
    made.blocked = blocked
    yield made
    scope.install(lambda r: frozenset())


def _has(mac, table, key, value):
    return mac.sql(f"SELECT COUNT(*) FROM {table} WHERE {key}=?", value).fetchone()[0] > 0


def test_a_local_notebook_does_not_leave_the_mac(macs):
    a, b = macs("A", "B")
    macs.blocked[a.rep.device] = frozenset({"private"})
    a.notebook("private", "Mine"); a.source("s1", "private", "secret text"); a.note("n1", "private")
    a.notebook("shared", "Shared"); a.source("s2", "shared", "fine")
    sync(b, a)
    assert not _has(b, "notebooks", "id", "private") and not _has(b, "sources", "id", "s1") and not _has(b, "canvas_notes", "id", "n1")
    assert _has(b, "notebooks", "id", "shared") and _has(b, "sources", "id", "s2")


def test_a_peers_copy_of_a_local_notebook_is_not_taken_in(macs):
    a, b = macs("A", "B")
    b.notebook("private", "Theirs"); b.source("s1", "private", "their text")
    macs.blocked[a.rep.device] = frozenset({"private"})
    sync(a, b)
    assert not _has(a, "notebooks", "id", "private") and not _has(a, "sources", "id", "s1")


def test_pages_of_only_skipped_rows_still_make_progress(macs):
    a, b = macs("A", "B")
    macs.blocked[a.rep.device] = frozenset({"private"})
    a.notebook("private", "Mine")
    for i in range(12):
        a.source(f"s{i}", "private", f"text {i}")
    a.notebook("shared", "Shared"); a.source("ok", "shared", "visible")
    pull(b, a, page_size=3)                      # would loop forever if the skipped rows were re-offered
    assert _has(b, "sources", "id", "ok") and not _has(b, "sources", "id", "s0")
    again = engine.export(a.rep, engine.vv(b.conn), limit=3)
    assert again["versions"] == [] and not again["more"]


def test_releasing_a_notebook_sends_it_and_fetches_the_peers_copy(macs):
    a, b = macs("A", "B")
    macs.blocked[a.rep.device] = frozenset({"private"})
    a.notebook("private", "Mine"); a.source("s1", "private", "mine")
    b.notebook("private2", "Theirs")             # a different notebook, held only by B
    sync(b, a)
    assert not _has(b, "sources", "id", "s1")
    macs.blocked[a.rep.device] = frozenset()
    assert engine.release(a.rep, ["private"]) >= 2
    sync(b, a)
    assert _has(b, "sources", "id", "s1") and _has(b, "notebooks", "id", "private")


def test_tables_that_name_the_notebook_differently_are_covered():
    pk = ["x"]
    assert scope.notebooks_of("recall", "archival_records", pk, ("id",), lambda c: {"source_notebook_id": "n1"}.get(c)) == {"n1"}
    assert scope.notebooks_of("main", "documents", ["collector_config", "n2"], ("kind", "key"), lambda c: None) == {"n2"}
    assert scope.notebooks_of("main", "documents", ["quiz_card", "n3/abc"], ("kind", "key"), lambda c: None) == {"n3"}
    assert scope.notebooks_of("main", "documents", ["core_memory", "x"], ("kind", "key"), lambda c: None) == set()
    assert scope.notebooks_of("brain", "insights", ["u"], ("uid",), lambda c: '["a","b"]' if c == "notebooks" else None) == {"a", "b"}
    assert scope.notebooks_of("main", "notebooks", ["n4"], ("id",), lambda c: None) == {"n4"}
