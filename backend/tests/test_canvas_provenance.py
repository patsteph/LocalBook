"""Provenance → canvas edges (Wave A, feature 2).

The rows have existed since 2026-07-31 and the edge state has always been styled; the only
missing half was the map-join. These pin the join, the drop rules, and the guarantee that a
derived edge's endpoints agree with the ids the layout store will actually write.
"""
import uuid

from services import canvas_provenance as cp
from storage import canvas_layout_store as cl


def _node(ref_type, ref_id, nid=None):
    return {"id": nid or f"n:{ref_type}:{ref_id}", "ref_type": ref_type, "ref_id": ref_id}


def _row(atype, aid, sid, stype="source"):
    return {"artifact_type": atype, "artifact_id": aid, "source_type": stype, "source_id": sid}


def test_joins_source_to_artifact():
    nodes = [_node("document", "doc1"), _node("source", "s1"), _node("source", "s2")]
    edges = cp.derive_edges(nodes, [_row("document", "doc1", "s1"), _row("document", "doc1", "s2")])
    assert len(edges) == 2
    assert {e["source"] for e in edges} == {"n:source:s1", "n:source:s2"}
    assert all(e["target"] == "n:document:doc1" for e in edges)
    assert all(e["state"] == "provenance" for e in edges)


def test_question_origin_joins_with_no_special_casing():
    """`source_type="exploration_query"` is the zero-migration hook for question→artifact."""
    nodes = [_node("quiz", "q1"), _node("exploration_query", "eq7")]
    edges = cp.derive_edges(nodes, [_row("quiz", "q1", "eq7", stype="exploration_query")])
    assert len(edges) == 1
    assert edges[0]["source"] == "n:exploration_query:eq7"


def test_drops_rows_whose_node_is_not_on_the_map():
    """Sources only surface when a learning query referenced them, so an artifact built from a
    never-discussed source has nothing to point at. Must not emit a dangling edge."""
    nodes = [_node("document", "doc1")]
    edges = cp.derive_edges(nodes, [_row("document", "doc1", "ghost")])
    assert edges == []
    # ...and the mirror case: the artifact itself absent.
    assert cp.derive_edges([_node("source", "s1")], [_row("document", "gone", "s1")]) == []


def test_caps_fan_in_per_artifact():
    nodes = [_node("audio", "a1")] + [_node("source", f"s{i}") for i in range(20)]
    rows = [_row("audio", "a1", f"s{i}") for i in range(20)]
    edges = cp.derive_edges(nodes, rows, max_per_artifact=3)
    assert len(edges) == 3
    # first-N in row order (generators append in citation order)
    assert [e["source"] for e in edges] == ["n:source:s0", "n:source:s1", "n:source:s2"]


def test_dedups_and_respects_skip_pairs():
    nodes = [_node("document", "doc1"), _node("source", "s1")]
    dup = [_row("document", "doc1", "s1"), _row("document", "doc1", "s1")]
    assert len(cp.derive_edges(nodes, dup)) == 1
    # a tie the user already drew by hand must not be duplicated in aqua
    skipped = cp.derive_edges(nodes, dup, skip_pairs={("n:source:s1", "n:document:doc1")})
    assert skipped == []


def test_edge_ids_are_stable_across_populates():
    nodes = [_node("document", "doc1"), _node("source", "s1")]
    rows = [_row("document", "doc1", "s1")]
    assert cp.derive_edges(nodes, rows)[0]["id"] == cp.derive_edges(nodes, rows)[0]["id"]


def test_ignores_self_edges_and_malformed_rows():
    nodes = [_node("document", "doc1"), _node("source", "s1")]
    assert cp.derive_edges(nodes, [{}, {"artifact_type": "document"}, None or {}]) == []
    # an artifact recorded as its own source would be a self-loop
    assert cp.derive_edges(nodes, [_row("document", "doc1", "doc1", stype="document")]) == []


def test_endpoints_match_the_ids_the_store_will_write():
    """The whole point of routing both through `assign_node_ids`: a derived edge must never
    reference an id that differs from the node row actually persisted."""
    nb = "nb-abc"
    nodes = cl.assign_node_ids(nb, [
        {"ref_type": "document", "ref_id": "doc1"},
        {"ref_type": "source", "ref_id": "s1"},
    ])
    edges = cp.derive_edges(nodes, [_row("document", "doc1", "s1")])
    assert len(edges) == 1
    expected_src = str(uuid.uuid5(cl._NODE_NS, f"{nb}:source:s1"))
    expected_tgt = str(uuid.uuid5(cl._NODE_NS, f"{nb}:document:doc1"))
    assert edges[0]["source"] == expected_src
    assert edges[0]["target"] == expected_tgt
    # and they survive a re-derive on the next populate (same ids in, same ids out)
    again = cl.assign_node_ids(nb, [
        {"ref_type": "source", "ref_id": "s1"},
        {"ref_type": "document", "ref_id": "doc1"},
    ])
    assert cp.derive_edges(again, [_row("document", "doc1", "s1")])[0]["id"] == edges[0]["id"]


def test_assign_node_ids_is_idempotent_and_keeps_caller_ids():
    nb = "nb-1"
    nodes = cl.assign_node_ids(nb, [
        {"ref_type": "topic", "ref_id": "t1", "id": "caller-wins"},
        {"ref_type": "source", "ref_id": "s1"},
        {"kind": "note"},  # user-placed, no ref → random id
    ])
    assert nodes[0]["id"] == "caller-wins"
    first = nodes[1]["id"]
    assert cl.assign_node_ids(nb, nodes)[1]["id"] == first
    assert nodes[2]["id"]


def test_assign_node_ids_survives_a_duplicate_ref():
    """`id` is the PRIMARY KEY — two nodes on the same ref must not collide and abort the save."""
    nodes = cl.assign_node_ids("nb-1", [
        {"ref_type": "source", "ref_id": "s1"},
        {"ref_type": "source", "ref_id": "s1"},
    ])
    assert nodes[0]["id"] != nodes[1]["id"]
