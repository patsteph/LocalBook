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


def test_shared_source_alone_binds_question_to_its_source():
    # Topic-clustering fix: a question and the SOURCE it drew on share nothing textually
    # (embed 0, concept 0) but shared-source overlap = 1.0 must still cluster them —
    # otherwise the map segregates by node TYPE. Two questions sharing a source join too.
    nodes = [_n(x) for x in ["q1", "q2", "s1", "far"]]
    pairs = [
        {"a": "q1", "b": "s1", "embed": 0.0, "concept": 0.0, "shared": 1.0},
        {"a": "q2", "b": "s1", "embed": 0.0, "concept": 0.0, "shared": 1.0},
        {"a": "q1", "b": "far", "embed": 0.1, "concept": 0.0, "shared": 0.0},
    ]
    sizes = sorted(len(c) for c in cluster_nodes(nodes, pairs))
    assert sizes == [1, 3]   # {q1,q2,s1} one topic cluster; far is a singleton


def test_journey_clustering_no_source_hub_megamerge():
    # Two UNRELATED topics (q1,q2 embed-similar; q3,q4 embed-similar; not across) both cite a
    # common base source S. Old union-find on shared merged all 5 via S. Two-phase must keep
    # the topics separate and attach S to whichever topic cites it more.
    from services.canvas_cluster import cluster_journey_nodes
    nodes = [
        {"id": "q1", "ref_type": "exploration_query"},
        {"id": "q2", "ref_type": "exploration_query"},
        {"id": "q3", "ref_type": "exploration_query"},
        {"id": "q4", "ref_type": "exploration_query"},
        {"id": "S", "ref_type": "source"},
    ]
    pairs = [
        {"a": "q1", "b": "q2", "embed": 0.9, "shared": 0.0},   # topic A
        {"a": "q3", "b": "q4", "embed": 0.9, "shared": 0.0},   # topic B
        {"a": "q1", "b": "q3", "embed": 0.2, "shared": 0.0},   # A/B not similar
        {"a": "S", "b": "q1", "embed": 0.0, "shared": 1.0},    # S cited by A (x2) and B (x1)
        {"a": "S", "b": "q2", "embed": 0.0, "shared": 1.0},
        {"a": "S", "b": "q3", "embed": 0.0, "shared": 1.0},
    ]
    clusters = cluster_journey_nodes(nodes, pairs, threshold=0.7)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [2, 3]                      # {q3,q4} topic B ; {q1,q2,S} topic A (+attached S)
    big = max(clusters, key=len)
    assert {n["id"] for n in big} == {"q1", "q2", "S"}   # S attached to the topic that cites it most


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


def test_cluster_seed_layout_positions_and_strips_temp_keys():
    from services.canvas_populate import cluster_seed_layout
    nodes = [
        {"kind": "source", "ref_type": "source", "ref_id": "s1", "title": "A", "snapshot": "", "_group": "src"},
        {"kind": "chat", "ref_type": "exploration_query", "ref_id": "q1", "title": "Q1", "snapshot": "", "created_at": "2026-07-31", "_group": "t"},
        {"kind": "chat", "ref_type": "exploration_query", "ref_id": "q2", "title": "Q2", "snapshot": "", "created_at": "2026-07-30", "_group": "t"},
    ]
    pairs = [{"a": "exploration_query:q1", "b": "exploration_query:q2", "embed": 0.9}]
    out = cluster_seed_layout(nodes, pairs)
    assert len(out) == 3
    for m in out:
        assert {"x", "y", "z"} <= set(m)
        assert "_group" not in m and "id" not in m   # temp keys stripped, not persisted
        assert m.get("ref_id")                        # real fields preserved


def test_cluster_seed_layout_falls_back_on_empty_pairs():
    from services.canvas_populate import cluster_seed_layout
    nodes = [{"kind": "source", "ref_type": "source", "ref_id": "s1", "title": "A", "snapshot": "", "_group": "src"}]
    out = cluster_seed_layout(nodes, [])   # no signal → grid fallback, still positioned
    assert len(out) == 1 and {"x", "y"} <= set(out[0]) and "_group" not in out[0]


def test_relayout_existing_repositions_and_keeps_all_nodes():
    from services.canvas_populate import relayout_existing
    nodes = [{"id": "a", "ref_type": "source", "ref_id": "s1", "x": 0, "y": 0},
             {"id": "b", "ref_type": "source", "ref_id": "s2", "x": 0, "y": 0},
             {"id": "c", "ref_type": "exploration_query", "ref_id": "q1", "x": 0, "y": 0}]
    out = relayout_existing(nodes, [{"a": "a", "b": "b", "embed": 0.9}])
    assert len(out) == 3                                   # keeps every node
    assert len({(n["x"], n["y"]) for n in out}) >= 2       # actually re-positioned


def test_relayout_grid_fallback_on_empty_pairs():
    from services.canvas_populate import relayout_existing
    out = relayout_existing([{"id": str(i), "x": 0, "y": 0} for i in range(6)], [])
    assert len({(n["x"], n["y"]) for n in out}) == 6       # grid → all distinct


def test_degrees_from_pairs():
    nodes = [_n(x) for x in ["a", "b", "c"]]
    pairs = [{"a": "a", "b": "b", "embed": 0.9}, {"a": "b", "b": "c", "embed": 0.9},
             {"a": "a", "b": "c", "embed": 0.1}]
    deg = degrees_from_pairs(nodes, pairs)
    assert deg["b"] == 2 and deg["a"] == 1 and deg["c"] == 1
