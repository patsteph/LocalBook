"""CI-pure tests for canvas clustering + spatial layout (Walk W1/W2). Pure — no KG."""
from services.canvas_cluster import (
    cluster_nodes, layout_clusters, significant_nodes, degrees_from_pairs,
)


def _n(i, kind="chat", created_at="2026-07-31"):
    return {"id": i, "kind": kind, "created_at": created_at}


def test_cluster_connected_components_embedding_drives_it():
    # The key fix: embedding similarity alone must cluster (it can't in the candidate
    # blend, where embed is weighted only 0.35). a-b-c linked, d-e linked, a-e not.
    nodes = [_n(x) for x in ["a", "b", "c", "d", "e"]]
    pairs = [
        {"a": "a", "b": "b", "embed": 0.9},
        {"a": "b", "b": "c", "embed": 0.8},
        {"a": "d", "b": "e", "embed": 0.7},
        {"a": "a", "b": "e", "embed": 0.2},   # below 0.6 → no link
    ]
    sizes = sorted(len(c) for c in cluster_nodes(nodes, pairs))
    assert sizes == [2, 3]


def test_singletons_when_no_links():
    nodes = [_n(x) for x in ["a", "b", "c"]]
    assert sorted(len(c) for c in cluster_nodes(nodes, [])) == [1, 1, 1]


def test_layout_positions_are_distinct_and_gridded():
    nodes = [_n(f"a{i}") for i in range(4)] + [_n(f"b{i}") for i in range(4)]
    pairs = ([{"a": f"a{i}", "b": f"a{j}", "embed": 0.9} for i in range(4) for j in range(i + 1, 4)]
             + [{"a": f"b{i}", "b": f"b{j}", "embed": 0.9} for i in range(4) for j in range(i + 1, 4)])
    pos = layout_clusters(cluster_nodes(nodes, pairs))
    assert len(pos) == 8
    coords = {(round(p["x"]), round(p["y"])) for p in pos.values()}
    assert len(coords) == 8            # no two nodes overlap


def test_significant_nodes_keeps_sources_and_caps_queries():
    sources = [_n(f"s{i}", kind="source") for i in range(3)]
    queries = [_n(f"q{i}", kind="chat", created_at=f"2026-07-{i:02d}") for i in range(1, 20)]
    degrees = {f"q{i}": (20 - i) for i in range(1, 20)}   # q1 most connected
    out = significant_nodes(sources + queries, degrees, max_query_nodes=5)
    kinds = [n["kind"] for n in out]
    assert kinds.count("source") == 3      # every source kept
    assert kinds.count("chat") == 5        # queries capped
    assert out[3]["id"] == "q1"            # most-connected query ranks first


def test_degrees_from_pairs():
    nodes = [_n(x) for x in ["a", "b", "c"]]
    pairs = [{"a": "a", "b": "b", "embed": 0.9}, {"a": "b", "b": "c", "embed": 0.9},
             {"a": "a", "b": "c", "embed": 0.1}]
    deg = degrees_from_pairs(nodes, pairs)
    assert deg["b"] == 2 and deg["a"] == 1 and deg["c"] == 1
