"""CI-pure unit tests for the Journey Canvas layout store (2.2.0 Crawl P1).

Exercises the connection-injectable core against an in-memory sqlite — no production
localbook.db, no heavy deps (sqlite3 is stdlib).
"""
import sqlite3

import pytest

from storage import canvas_layout_store as cl


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    cl._ensure_schema(c)
    return c


def test_empty_layout(conn):
    layout = cl._get_layout(conn, "nb1")
    assert layout == {"nodes": [], "edges": [], "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0}}


def test_save_and_get_roundtrip(conn):
    nodes = [
        {"id": "n1", "x": 10.0, "y": 20.0, "kind": "chat_turn", "ref_type": "exploration_query",
         "ref_id": "q1", "snapshot": {"type": "markdown", "payload": "hi"}, "title": "T", "z": 1},
        {"id": "n2", "x": 30.0, "y": 40.0, "kind": "source", "ref_type": "source", "ref_id": "s1"},
    ]
    edges = [{"id": "e1", "source": "n1", "target": "n2", "state": "user", "label": "relates"}]
    cl._save_layout(conn, "nb1", nodes, edges, {"x": 5.0, "y": 6.0, "zoom": 1.5})

    layout = cl._get_layout(conn, "nb1")
    assert len(layout["nodes"]) == 2 and len(layout["edges"]) == 1
    n1 = next(n for n in layout["nodes"] if n["id"] == "n1")
    assert n1["x"] == 10.0 and n1["snapshot"] == {"type": "markdown", "payload": "hi"}
    assert layout["edges"][0]["state"] == "user"
    assert layout["viewport"] == {"x": 5.0, "y": 6.0, "zoom": 1.5}


def test_save_layout_is_full_replace(conn):
    cl._save_layout(conn, "nb1", [{"id": "n1", "x": 0, "y": 0, "kind": "a"}], [])
    cl._save_layout(conn, "nb1", [{"id": "n2", "x": 1, "y": 1, "kind": "b"}], [])
    ids = [n["id"] for n in cl._get_layout(conn, "nb1")["nodes"]]
    assert ids == ["n2"]  # n1 replaced


def test_layout_is_notebook_scoped(conn):
    cl._save_layout(conn, "nb1", [{"id": "a", "x": 0, "y": 0, "kind": "k"}], [])
    cl._save_layout(conn, "nb2", [{"id": "b", "x": 0, "y": 0, "kind": "k"}], [])
    assert [n["id"] for n in cl._get_layout(conn, "nb1")["nodes"]] == ["a"]
    assert [n["id"] for n in cl._get_layout(conn, "nb2")["nodes"]] == ["b"]


def test_patch_node(conn):
    cl._save_layout(conn, "nb1", [{"id": "n1", "x": 0.0, "y": 0.0, "kind": "k"}], [])
    assert cl._patch_node(conn, "nb1", "n1", 99.0, 88.0) is True
    n1 = cl._get_layout(conn, "nb1")["nodes"][0]
    assert n1["x"] == 99.0 and n1["y"] == 88.0
    # wrong notebook / missing node → no update
    assert cl._patch_node(conn, "nbX", "n1", 1, 1) is False
    assert cl._patch_node(conn, "nb1", "missing", 1, 1) is False


def test_upsert_edge_insert_then_update(conn):
    e = cl._upsert_edge(conn, "nb1", {"id": "e1", "source": "a", "target": "b", "state": "user"})
    assert e["state"] == "user"
    updated = cl._upsert_edge(conn, "nb1", {"id": "e1", "source": "a", "target": "b",
                                            "state": "researched", "label": "found"})
    assert updated["state"] == "researched" and updated["label"] == "found"
    assert len(cl._get_layout(conn, "nb1")["edges"]) == 1  # updated, not duplicated


def test_delete_edge(conn):
    cl._upsert_edge(conn, "nb1", {"id": "e1", "source": "a", "target": "b", "state": "user"})
    assert cl._delete_edge(conn, "nb1", "e1") is True
    assert cl._get_layout(conn, "nb1")["edges"] == []
    assert cl._delete_edge(conn, "nb1", "e1") is False  # already gone


def test_save_viewport_upsert(conn):
    cl._save_viewport(conn, "nb1", 1.0, 2.0, 3.0)
    cl._save_viewport(conn, "nb1", 4.0, 5.0, 6.0)
    assert cl._get_layout(conn, "nb1")["viewport"] == {"x": 4.0, "y": 5.0, "zoom": 6.0}


def test_edge_states_constant():
    assert cl.EDGE_STATES == ("candidate", "provenance", "user", "curator", "researched")


# ── width/height resize-persistence (2.2.0) ──────────────────────────────────────────
def test_save_layout_persists_width_height(conn):
    nodes = [
        {"id": "n1", "x": 0.0, "y": 0.0, "kind": "k", "width": 320.0, "height": 210.0},
        {"id": "n2", "x": 1.0, "y": 1.0, "kind": "k"},  # never resized → null dims
    ]
    cl._save_layout(conn, "nb1", nodes, [])
    layout = cl._get_layout(conn, "nb1")
    n1 = next(n for n in layout["nodes"] if n["id"] == "n1")
    n2 = next(n for n in layout["nodes"] if n["id"] == "n2")
    assert n1["width"] == 320.0 and n1["height"] == 210.0
    assert n2["width"] is None and n2["height"] is None


def test_patch_node_updates_dimensions(conn):
    cl._save_layout(conn, "nb1", [{"id": "n1", "x": 0.0, "y": 0.0, "kind": "k"}], [])
    assert cl._patch_node(conn, "nb1", "n1", 5.0, 6.0, width=400.0, height=250.0) is True
    n1 = cl._get_layout(conn, "nb1")["nodes"][0]
    assert (n1["x"], n1["y"], n1["width"], n1["height"]) == (5.0, 6.0, 400.0, 250.0)


def test_patch_node_without_dims_preserves_existing_size(conn):
    cl._save_layout(conn, "nb1",
                    [{"id": "n1", "x": 0.0, "y": 0.0, "kind": "k", "width": 300.0, "height": 200.0}], [])
    # A pure-drag patch (no width/height) must not clobber the persisted size.
    assert cl._patch_node(conn, "nb1", "n1", 9.0, 9.0) is True
    n1 = cl._get_layout(conn, "nb1")["nodes"][0]
    assert n1["width"] == 300.0 and n1["height"] == 200.0
    assert n1["x"] == 9.0 and n1["y"] == 9.0


def test_ensure_schema_migrates_legacy_table():
    """A prod DB whose canvas_nodes predates width/height gets the columns added in place."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    # Legacy schema — no width/height.
    c.execute(
        "CREATE TABLE canvas_nodes (id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL, "
        "x REAL NOT NULL, y REAL NOT NULL, kind TEXT NOT NULL, ref_type TEXT, ref_id TEXT, "
        "snapshot_json TEXT DEFAULT '{}', title TEXT DEFAULT '', z INTEGER DEFAULT 0, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    c.execute(
        "INSERT INTO canvas_nodes (id, notebook_id, x, y, kind, created_at, updated_at) "
        "VALUES ('n1','nb1',0,0,'k','t','t')"
    )
    c.commit()
    cl._ensure_schema(c)  # idempotent migration
    cols = {r[1] for r in c.execute("PRAGMA table_info(canvas_nodes)").fetchall()}
    assert "width" in cols and "height" in cols
    # Existing row survives, new dims default to NULL, and it round-trips.
    n1 = cl._get_layout(c, "nb1")["nodes"][0]
    assert n1["id"] == "n1" and n1["width"] is None and n1["height"] is None


def test_parents_are_returned_before_their_children(conn):
    """React Flow v12 resolves `parentId` against nodes it has ALREADY seen, so a child listed
    first renders detached — its parent-relative position is treated as absolute and the thread
    lands elsewhere on the canvas while its topic card shows up empty.

    The SQL order alone can't guarantee this: threads keep their ORIGINAL created_at (months old)
    while a topic card is minted at populate time, so `ORDER BY z, created_at` puts every child
    ahead of its parent. Observed on live data 2026-08-12: 43 of 55 children preceded their card.
    """
    card = {"id": "topic_1", "x": 100.0, "y": 100.0, "kind": "topic", "ref_type": "topic",
            "ref_id": "t1", "title": "Card", "width": 656.0, "height": 480.0,
            "created_at": "2026-08-12T10:00:00"}          # minted NOW
    kids = [
        {"id": f"k{i}", "x": 16.0, "y": 64.0, "kind": "chat_turn", "ref_type": "exploration_query",
         "ref_id": f"q{i}", "parent_id": "topic_1", "created_at": f"2026-05-{10 + i:02d}T09:00:00"}
        for i in range(3)                                  # ...but threads are MONTHS older
    ]
    # Saved parent-first, exactly as the layout emits it.
    cl._save_layout(conn, "nb1", [card, *kids], [], {"x": 0, "y": 0, "zoom": 1})

    nodes = cl._get_layout(conn, "nb1")["nodes"]
    pos = {n["id"]: i for i, n in enumerate(nodes)}
    for n in nodes:
        if n.get("parent_id"):
            assert pos[n["parent_id"]] < pos[n["id"]], (
                f"child {n['id']} precedes parent {n['parent_id']} — it will render detached")


def test_child_ordering_preserves_relative_order_within_a_group(conn):
    """Parents-first must be a STABLE partition — z/recency order inside each group survives."""
    nodes = [
        {"id": "p1", "x": 0.0, "y": 0.0, "kind": "topic", "ref_type": "topic", "ref_id": "t1",
         "created_at": "2026-08-01T00:00:00"},
        {"id": "p2", "x": 0.0, "y": 0.0, "kind": "topic", "ref_type": "topic", "ref_id": "t2",
         "created_at": "2026-08-02T00:00:00"},
        {"id": "a", "x": 1.0, "y": 1.0, "kind": "chat_turn", "ref_type": "exploration_query",
         "ref_id": "q1", "parent_id": "p1", "created_at": "2026-05-01T00:00:00"},
        {"id": "b", "x": 1.0, "y": 1.0, "kind": "chat_turn", "ref_type": "exploration_query",
         "ref_id": "q2", "parent_id": "p1", "created_at": "2026-05-02T00:00:00"},
    ]
    cl._save_layout(conn, "nb1", nodes, [], {"x": 0, "y": 0, "zoom": 1})
    got = [n["id"] for n in cl._get_layout(conn, "nb1")["nodes"]]
    assert got == ["p1", "p2", "a", "b"]
