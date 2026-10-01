"""LB-12m — three replicas, random histories, random sync orders: they converge.

Hypothesis generates interleavings of writes on three Macs (create, edit a
content field, edit a last-writer-wins field, delete, cascade-delete a notebook)
and pairwise syncs in random directions, including pulls cut short after one
page (a dropped connection). After a final full mesh, every replica must hold
identical data and identical conflict items, and syncing again must change
nothing.

CI size by default; `LB_SYNC_SCENARIOS=10000` for the release gate (12m).
"""

import os

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from services.sync import engine
from tests.sync_harness import Mac, assert_converged, mesh, pull, template

N = int(os.environ.get("LB_SYNC_SCENARIOS", "150"))
MACS = ("A", "B", "C")
NOTES = ("n1", "n2", "n3")
NOTEBOOKS = ("nb1", "nb2")

mac_i = st.integers(0, 2)
op = st.one_of(
    st.tuples(st.just("note"), mac_i, st.sampled_from(NOTES), st.sampled_from(NOTEBOOKS)),
    st.tuples(st.just("body"), mac_i, st.sampled_from(NOTES), st.sampled_from(["x", "y", "z", "w"])),
    st.tuples(st.just("title"), mac_i, st.sampled_from(NOTES), st.sampled_from(["T1", "T2", "T3"])),
    st.tuples(st.just("tag"), mac_i, st.sampled_from(NOTES), st.sampled_from(["red", "blue"])),
    st.tuples(st.just("del"), mac_i, st.sampled_from(NOTES), st.just(None)),
    st.tuples(st.just("rmnb"), mac_i, st.sampled_from(NOTEBOOKS), st.just(None)),
    st.tuples(st.just("sync"), mac_i, mac_i, st.booleans()),
)


def _do(macs, step):
    kind, i, x, y = step
    m = macs[i]
    if kind == "note":
        m.sql("INSERT OR IGNORE INTO notebooks (id, title, created_at, updated_at) "
              "VALUES (?, 'NB', 'now', 'now')", y)
        m.sql("INSERT OR IGNORE INTO canvas_notes (id, notebook_id, title, content_markdown, "
              "created_at, updated_at) VALUES (?, ?, 't', 'b', 'now', 'now')", x, y)
    elif kind == "body":
        m.sql("UPDATE canvas_notes SET content_markdown=? WHERE id=?", y, x)
    elif kind == "title":
        m.sql("UPDATE canvas_notes SET title=? WHERE id=?", y, x)
    elif kind == "tag":
        m.sql("UPDATE canvas_notes SET tags=? WHERE id=?", y, x)
    elif kind == "del":
        m.sql("DELETE FROM canvas_notes WHERE id=?", x)
    elif kind == "rmnb":
        m.sql("DELETE FROM notebooks WHERE id=?", x)
    elif kind == "sync":
        j, partial = x, y
        if i == j:
            return
        if partial:                             # one page, then the connection drops
            page = engine.export(macs[j].rep, engine.vv(m.conn), limit=1)
            engine.apply(m.rep, page)
        else:
            pull(m, macs[j], page_size=2)


@pytest.fixture(scope="module")
def tmpl(tmp_path_factory):
    return template(tmp_path_factory.getbasetemp())


@settings(max_examples=N, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(steps=st.lists(op, min_size=1, max_size=30))
def test_three_replicas_converge_whatever_the_history(steps, tmpl, tmp_path_factory):
    root = tmp_path_factory.mktemp("h")
    macs = [Mac(root, n, tmpl) for n in MACS]
    for step in steps:
        _do(macs, step)
    mesh(macs, rounds=3)
    assert_converged(macs)
    before = [m.snapshot() for m in macs]
    mesh(macs, rounds=1)
    assert [m.snapshot() for m in macs] == before          # quiescent: nothing left to move
    for m in macs:
        m.conn.close()
        m.engine_conn.close()
