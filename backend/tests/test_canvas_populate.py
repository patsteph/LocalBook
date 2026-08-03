"""CI-pure unit tests for the Journey Canvas populate pipeline (2.2.0 Crawl P2).

Pure functions only (no store I/O): build_nodes / seed_layout / merge_populate / populate_layout.
"""
from services import canvas_populate as cp


def _journey(queries):
    return {"notebook_id": "nb1", "queries": queries, "topics_explored": [], "sources_accessed": []}


def test_build_nodes_maps_queries_and_sources():
    # Source s1 is REFERENCED by q1 (sources_used) → surfaced as a base-layer node.
    journey = _journey([
        {"id": "q1", "query": "What is X?", "answer_preview": "X is...", "topics": ["alpha"],
         "timestamp": "t1", "sources_used": ["s1"]},
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


def test_build_nodes_source_title_from_source_titles_map():
    # Real title comes from the source_store lookup (payload lacks one → would be bare "Source").
    journey = _journey([{"id": "q1", "query": "Q", "sources_used": ["9"]}])
    src = [{"id": 9, "ts": "t", "payload": {"source_id": "9"}}]
    node = next(n for n in cp.build_nodes(journey, src, {"9": "Real Name.pdf"}) if n["kind"] == "source")
    assert node["ref_id"] == "9" and node["title"] == "Real Name.pdf"


def test_build_nodes_drops_unreferenced_sources():
    # A source no learning query referenced is an orphan → stays off the map.
    journey = _journey([{"id": "q1", "query": "What is X?", "sources_used": []}])
    src = [{"id": 9, "ts": "t", "payload": {"source_id": "s9", "title": "Doc"}}]
    nodes = cp.build_nodes(journey, src)
    assert [n for n in nodes if n["kind"] == "source"] == []


def test_title_truncation():
    long = "x" * 200
    node = cp.build_nodes(_journey([{"id": "q", "query": long, "topics": []}]), [])[0]
    assert len(node["title"]) <= 60 and node["title"].endswith("…")


def test_seed_layout_columns_by_group_sources_last():
    journey = _journey([
        {"id": "q1", "query": "a", "topics": ["alpha"], "sources_used": ["s1"]},
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
    journey = _journey([{"id": "q1", "query": "a", "topics": ["t"], "sources_used": ["s1"]}])
    src = [{"id": 1, "ts": "x", "payload": {"source_id": "s1", "title": "S.pdf"}}]
    empty = {"nodes": [], "edges": [], "viewport": {}}
    merged1, added1 = cp.populate_layout(journey, src, empty)
    assert added1 == 2
    # feeding the result back in adds nothing
    _merged2, added2 = cp.populate_layout(journey, src, {"nodes": merged1})
    assert added2 == 0


# ─── Learning-vs-noise filter (2.2.0) — deterministic, no LLM ────────────────

def test_is_learning_query_plain_question_is_learning():
    assert cp.is_learning_query({"query": "What is a transformer?"}) is True
    assert cp.is_learning_query({"query": "explain gradient descent to me"}) is True


def test_is_learning_query_empty_is_noise():
    assert cp.is_learning_query({"query": ""}) is False
    assert cp.is_learning_query({"query": "   "}) is False
    assert cp.is_learning_query({}) is False


def test_is_learning_query_research_always_learning():
    # @research pulls / deep-dives / site-searches are always learning, even
    # when the wording coincidentally resembles an admin verb.
    assert cp.is_learning_query({"query": "@research deep dive on RAG evaluation"}) is True
    assert cp.is_learning_query({"query": "@research site:arxiv.org status of MoE models"}) is True


def test_is_learning_query_rag_answer_with_sources_is_learning():
    # A plain turn that consulted sources is unambiguously learning.
    assert cp.is_learning_query(
        {"query": "summarize the findings", "sources_used": ["s1", "s2"]}
    ) is True


def test_is_learning_query_curator_admin_is_noise():
    for msg in (
        "@curator brain status",
        "@curator show profile",
        "@curator set name Athena",
        "@curator set personality be terse",
        "@curator toggle overwatch on",
        "@curator exclude notebook Finance",
        "@curator include notebook Finance",
        "@curator collection schedule",
        "@curator note themes",
        "@curator approve connection 3",
        "@curator dismiss connection 3",
    ):
        assert cp.is_learning_query({"query": msg}) is False, msg


def test_is_learning_query_collector_admin_is_noise():
    for msg in (
        "@collector show status",
        "@collector show pending",
        "@collector show history",
        "@collector source health",
        "@collector set mode automatic",
        "@collector set approval trust_me",
        "@collector set schedule daily",
        "@collector set filters max_age_days 30",
        "@collector status",  # terse whole-message command
    ):
        assert cp.is_learning_query({"query": msg}) is False, msg


def test_is_learning_query_correspondent_admin_is_noise():
    for msg in (
        "@correspondent show status",
        "@correspondent sync now",
        "@correspondent show queue",
        "@correspondent show scorecards",
        "@correspondent show routing",
        "@correspondent digest mode Stratechery",
    ):
        assert cp.is_learning_query({"query": msg}) is False, msg


def test_is_learning_query_collector_source_add_is_learning():
    # Source-adding collector commands ARE learning (they grow the notebook).
    for msg in (
        "@collector add https://example.com/post",
        "@collector subscribe https://blog.example.com/rss daily",
        "@collector add a note about what we discussed",
        "@collector add keyword quantum computing",
    ):
        assert cp.is_learning_query({"query": msg}) is True, msg


def test_is_learning_query_curator_question_is_learning():
    # A genuine cross-notebook question addressed to the curator is learning —
    # only admin/status/config verbs are noise, and "status of X" is not one.
    assert cp.is_learning_query({"query": "@curator what did I save about GDPR?"}) is True
    assert cp.is_learning_query({"query": "@curator what is the status of the merger?"}) is True


def test_is_learning_query_bare_admin_command_without_prefix_is_noise():
    assert cp.is_learning_query({"query": "brain status"}) is False
    assert cp.is_learning_query({"query": "show pending"}) is False


def test_intent_field_takes_precedence_when_present():
    # Future-proofing: if a record ever carries a classified intent, trust it.
    assert cp.is_learning_query({"query": "anything", "intent": "brain_status"}) is False
    assert cp.is_learning_query({"query": "anything", "intent": "cross_notebook_search"}) is True


def test_build_nodes_filters_noise_keeps_learning_and_referenced_sources():
    journey = _journey([
        {"id": "q1", "query": "What is X?", "topics": ["alpha"], "timestamp": "t1",
         "sources_used": ["s1"]},                                                    # learning, refs s1
        {"id": "q2", "query": "@curator brain status", "topics": [], "timestamp": "t2"},  # noise
        {"id": "q3", "query": "@collector show pending", "topics": [], "timestamp": "t3"},  # noise
        {"id": "q4", "query": "@research deep dive on MoE", "topics": ["moe"], "timestamp": "t4"},  # learning
    ])
    src = [{"id": 1, "ts": "t5", "payload": {"source_id": "s1", "title": "Paper.pdf"}}]
    nodes = cp.build_nodes(journey, src)
    chat_ids = {n["ref_id"] for n in nodes if n["kind"] == "chat_turn"}
    assert chat_ids == {"q1", "q4"}                       # noise q2/q3 dropped
    assert any(n["kind"] == "source" and n["ref_id"] == "s1" for n in nodes)  # referenced source kept


def test_status_readout_with_sources_is_still_noise():
    # Regression: an admin status readout whose answer LISTED sources must not leak in as
    # "learning" — the admin check runs before the sources_used check.
    journey = _journey([
        {"id": "n1", "query": "collection status", "sources_used": ["s1"], "topics": []},
        {"id": "n2", "query": "morning brief", "sources_used": ["s2"], "topics": []},
        {"id": "n3", "query": "note themes", "sources_used": ["s3"], "topics": []},
    ])
    nodes = cp.build_nodes(journey, [])
    assert [n for n in nodes if n["kind"] == "chat_turn"] == []  # all status readouts filtered


def test_orphan_sources_dropped_when_all_chat_is_noise():
    journey = _journey([
        {"id": "n1", "query": "@curator show profile", "topics": [], "timestamp": "t"},
        {"id": "n2", "query": "@correspondent sync now", "topics": [], "timestamp": "t"},
    ])
    src = [{"id": 7, "ts": "t", "payload": {"source_id": "s9", "title": "Doc"}}]
    nodes = cp.build_nodes(journey, src)
    assert [n["kind"] for n in nodes if n["kind"] == "chat_turn"] == []  # all chat filtered
    assert [n["ref_id"] for n in nodes if n["kind"] == "source"] == []  # no query referenced s9 → dropped
