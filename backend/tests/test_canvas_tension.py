"""Tension edges — "these two sources disagree" (Wave A, feature 4).

Source choice is the interesting part and is pinned here: the durable `source_stances` rows,
NOT `topic_perspectives` (whose `contested` label is unreachable for >=3 sources) and NOT
`contradiction_detector` (whose cache is an in-process dict that dies on restart).
"""
from services import canvas_tension as ct


def _node(ref_type, ref_id, nid=None):
    return {"id": nid or f"n:{ref_type}:{ref_id}", "ref_type": ref_type, "ref_id": ref_id}


def _stance(sid, stance, conf=0.9, rationale=""):
    return {"source_id": sid, "stance": stance, "confidence": conf, "rationale": rationale}


NODES = [_node("source", "a"), _node("source", "b"), _node("source", "c")]


def test_pairs_a_contradicting_source_with_the_supporting_ones():
    edges = ct.derive_edges(NODES, [
        _stance("a", "contradicts"), _stance("b", "supports"), _stance("c", "supports"),
    ])
    assert len(edges) == 2
    assert all(e["state"] == "tension" for e in edges)
    assert {frozenset((e["source"], e["target"])) for e in edges} == {
        frozenset(("n:source:a", "n:source:b")),
        frozenset(("n:source:a", "n:source:c")),
    }


def test_no_contradicting_source_means_no_edges():
    """The common case on a real notebook — everything agrees with the thesis."""
    assert ct.derive_edges(NODES, [_stance("a", "supports"), _stance("b", "supports")]) == []


def test_tangential_and_off_topic_are_not_disagreement():
    """`off_topic` is the most common non-supporting stance in real data. It means the source
    is irrelevant, NOT that it argues the other way — drawing tension for it would be a lie."""
    edges = ct.derive_edges(NODES, [
        _stance("a", "off_topic"), _stance("b", "tangential"), _stance("c", "supports"),
    ])
    assert edges == []


def test_low_confidence_stances_are_ignored():
    """Do not tell the user two sources disagree on a coin-flip."""
    assert ct.derive_edges(NODES, [
        _stance("a", "contradicts", conf=0.2), _stance("b", "supports", conf=0.9),
    ]) == []
    assert ct.derive_edges(NODES, [
        _stance("a", "contradicts", conf=0.9), _stance("b", "supports", conf=0.1),
    ]) == []


def test_a_source_not_on_the_map_draws_nothing():
    """Stances exist for every scored source; only the ones the user actually discussed have
    nodes to attach to."""
    assert ct.derive_edges([_node("source", "a")], [
        _stance("a", "contradicts"), _stance("ghost", "supports"),
    ]) == []


def test_fan_in_is_capped_and_keeps_the_strongest_supporters():
    nodes = [_node("source", "opp")] + [_node("source", f"s{i}") for i in range(10)]
    stances = [_stance("opp", "contradicts")] + [
        _stance(f"s{i}", "supports", conf=0.5 + i / 100) for i in range(10)
    ]
    edges = ct.derive_edges(nodes, stances, max_per_source=3)
    assert len(edges) == 3
    # highest-confidence supporters win (s9, s8, s7)
    assert {e["target"] for e in edges} == {"n:source:s9", "n:source:s8", "n:source:s7"}


def test_edge_id_is_symmetric_and_stable():
    """Tension has no direction, so the pair must yield one id whichever way round it is seen."""
    assert ct.edge_id("x", "y") == ct.edge_id("y", "x")
    a = ct.derive_edges(NODES, [_stance("a", "contradicts"), _stance("b", "supports")])
    b = ct.derive_edges(NODES, [_stance("a", "contradicts"), _stance("b", "supports")])
    assert a[0]["id"] == b[0]["id"]


def test_carries_the_rationale_so_the_edge_explains_itself():
    edges = ct.derive_edges(NODES, [
        _stance("a", "contradicts", rationale="Claims the opposite effect size."),
        _stance("b", "supports"),
    ])
    assert edges[0]["meta"]["rationale"] == "Claims the opposite effect size."
    assert edges[0]["meta"]["confidence"] == 0.9


def test_skip_pairs_avoids_duplicating_an_existing_edge():
    pair = ("n:source:a", "n:source:b")
    edges = ct.derive_edges(NODES, [_stance("a", "contradicts"), _stance("b", "supports")],
                            skip_pairs={pair})
    assert edges == []


def test_never_raises_on_junk():
    assert ct.derive_edges([], []) == []
    assert ct.derive_edges(NODES, [{}, {"stance": "contradicts"}]) == []
    assert ct.derive_edges(NODES, [_stance("a", "contradicts", conf=None), _stance("b", "supports")]) == []
