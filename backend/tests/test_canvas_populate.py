"""CI-pure unit tests for the Journey Canvas populate pipeline (2.2.0 Crawl P2).

Pure functions only (no store I/O): build_nodes / seed_layout / merge_populate / populate_layout.
"""
from services import canvas_populate as cp


def _journey(queries):
    return {"notebook_id": "nb1", "queries": queries, "topics_explored": [], "sources_accessed": []}


def test_build_nodes_maps_queries_and_sources():
    journey = _journey([
        {"id": "q1", "query": "What is X?", "answer_preview": "X is...", "topics": ["alpha"], "timestamp": "t1"},
    ])
    src = [{"id": 5, "ts": "t2", "payload": {"source_id": "s1", "title": "Paper.pdf"}}]
    nodes = cp.build_nodes(journey, src)
    chat = next(n for n in nodes if n["kind"] == "chat_turn")
    source = next(n for n in nodes if n["kind"] == "source")
    assert chat["ref_type"] == "exploration_query" and chat["ref_id"] == "q1"
    assert chat["snapshot"]["type"] == "markdown" and "What is X?" in chat["snapshot"]["payload"]
    assert chat["_group"] == "alpha" and chat["created_at"] == "t1"
    assert source["ref_type"] == "source" and source["ref_id"] == "s1" and source["title"] == "Paper.pdf"
    assert source["_group"] == "__sources__"


def test_build_nodes_source_falls_back_to_event_id():
    src = [{"id": 9, "ts": "t", "payload": {}}]
    node = cp.build_nodes(_journey([]), src)[0]
    assert node["ref_id"] == "9" and node["title"] == "Source"


def test_title_truncation():
    long = "x" * 200
    node = cp.build_nodes(_journey([{"id": "q", "query": long, "topics": []}]), [])[0]
    assert len(node["title"]) <= 60 and node["title"].endswith("…")


def test_seed_layout_columns_by_group_sources_last():
    journey = _journey([
        {"id": "q1", "query": "a", "topics": ["alpha"]},
        {"id": "q2", "query": "b", "topics": ["alpha"]},
        {"id": "q3", "query": "c", "topics": ["beta"]},
    ])
    src = [{"id": 1, "ts": "t", "payload": {"source_id": "s1", "title": "S"}}]
    laid = cp.seed_layout(cp.build_nodes(journey, src))
    # every node has numeric x/y and no leaked _group
    assert all("_group" not in n and isinstance(n["x"], float) for n in laid)
    # two alpha chat turns share a column, stacked in rows
    alpha = [n for n in laid if n["ref_id"] in ("q1", "q2")]
    assert alpha[0]["x"] == alpha[1]["x"] and alpha[0]["y"] != alpha[1]["y"]
    # source column is rightmost (max x)
    src_node = next(n for n in laid if n["kind"] == "source")
    assert src_node["x"] == max(n["x"] for n in laid)


def test_merge_preserves_existing_and_adds_new():
    existing = [
        {"id": "keep", "x": 99.0, "y": 99.0, "kind": "chat_turn",
         "ref_type": "exploration_query", "ref_id": "q1"},
        {"id": "usernode", "x": 1.0, "y": 1.0, "kind": "note", "ref_type": None, "ref_id": None},
    ]
    new = [
        {"ref_type": "exploration_query", "ref_id": "q1", "x": 0.0, "y": 0.0},  # dup → skip
        {"ref_type": "exploration_query", "ref_id": "q2", "x": 5.0, "y": 5.0},  # new → add
    ]
    merged, added = cp.merge_populate(new, existing)
    assert added == 1 and len(merged) == 3
    # existing q1 keeps its position (not clobbered by the seed 0,0)
    q1 = next(n for n in merged if n.get("ref_id") == "q1")
    assert q1["x"] == 99.0 and q1["id"] == "keep"
    # the user-added note survives
    assert any(n["id"] == "usernode" for n in merged)


def test_populate_layout_is_idempotent():
    journey = _journey([{"id": "q1", "query": "a", "topics": ["t"]}])
    src = [{"id": 1, "ts": "x", "payload": {"source_id": "s1"}}]
    empty = {"nodes": [], "edges": [], "viewport": {}}
    merged1, added1 = cp.populate_layout(journey, src, empty)
    assert added1 == 2
    # feeding the result back in adds nothing
    _merged2, added2 = cp.populate_layout(journey, src, {"nodes": merged1})
    assert added2 == 0
