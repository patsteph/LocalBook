"""CI-pure unit tests for the Journey Canvas candidate-dot engine (2.2.0 Crawl P5).

Exercises the pure scoring + bounding math with INJECTED signal values — no live model,
no KG, no embeddings. Covers the blend weights, the 0.5 threshold, per-node top-K, the
global cap, deterministic ordering, and the two pure signal helpers.
"""
from services import canvas_candidates as cc


# ── Signal helpers ───────────────────────────────────────────────────────────────────
def test_cosine_identical_and_orthogonal():
    assert cc.cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cc.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_negative_clamped_and_degenerate():
    assert cc.cosine([1.0, 0.0], [-1.0, 0.0]) == 0.0   # negative → clamp to 0
    assert cc.cosine([], [1.0]) == 0.0                 # empty → 0
    assert cc.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0    # zero-norm → 0
    assert cc.cosine([1.0, 2.0], [1.0]) == 0.0         # length mismatch → 0


def test_overlap_coefficient():
    assert cc.overlap_coefficient({"s1"}, {"s1", "s2", "s3"}) == 1.0   # single source ⊂ set
    assert cc.overlap_coefficient({"a", "b"}, {"b", "c"}) == 0.5       # 1 / min(2,2)
    assert cc.overlap_coefficient(set(), {"a"}) == 0.0
    assert cc.overlap_coefficient({"a"}, {"b"}) == 0.0


def test_blend_weights_sum_to_one():
    assert cc.W_CONCEPT + cc.W_EMBED + cc.W_SHARED == 1.0
    assert cc.blended_score(1.0, 1.0, 1.0) == 1.0
    assert cc.blended_score(1.0, 0.0, 0.0) == cc.W_CONCEPT
    assert cc.blended_score(0.0, 1.0, 0.0) == cc.W_EMBED


def test_dominant_signal_and_ties():
    assert cc.dominant_signal(1.0, 0.0, 0.0) == "concept"
    assert cc.dominant_signal(0.0, 1.0, 0.0) == "embed"
    assert cc.dominant_signal(0.0, 0.0, 1.0) == "shared_source"
    # concept (0.5·1) beats embed (0.35·1) even both "on".
    assert cc.dominant_signal(1.0, 1.0, 1.0) == "concept"


# ── Threshold ────────────────────────────────────────────────────────────────────────
def test_threshold_filters_below_half():
    # A WEAK single signal (embed 0.3, no concept/shared) stays below min_score → filtered.
    assert cc.score_and_bound([{"a": "1", "b": "2", "concept": 0, "embed": 0.3, "shared": 0}]) == []
    # A STRONG embedding alone now qualifies — topic similarity is a valid latent link on a
    # chat-heavy canvas where concept is always 0 (else nothing ever surfaces). score = embed.
    out = cc.score_and_bound([{"a": "1", "b": "2", "concept": 0, "embed": 0.8, "shared": 0}])
    assert len(out) == 1 and out[0]["score"] == 0.8 and out[0]["signal"] == "embed"
    # concept 1.0 alone also qualifies (a densely graph-linked source pair).
    out = cc.score_and_bound([{"a": "1", "b": "2", "concept": 1.0, "embed": 0, "shared": 0}])
    assert len(out) == 1 and out[0]["score"] == 1.0 and out[0]["signal"] == "concept"


def test_pair_shape_and_rounding():
    # score = max(blend, embed, concept). Here concept=1.0 dominates → 1.0.
    out = cc.score_and_bound([{"a": "x", "b": "y", "concept": 1.0, "embed": 0.5, "shared": 1.0}])
    assert out == [{"a_node": "x", "b_node": "y", "score": 1.0, "signal": "concept"}]
    # A pure-blend win (no single signal ≥ blend): concept 0.6, embed 0.5, shared 1.0.
    out = cc.score_and_bound([{"a": "x", "b": "y", "concept": 0.6, "embed": 0.5, "shared": 1.0}])
    assert out[0]["score"] == round(0.3 + 0.175 + 0.15, 4)  # blend 0.625 > embed 0.5 > concept 0.6? -> 0.625


# ── Bounding: per-node top-K, global cap, ordering ───────────────────────────────────
def test_top_k_per_node():
    # Node "hub" wants 4 links but top_k=2 → keeps its 2 strongest. Distinct embeds so the
    # score = embed ordering is unambiguous under the max-of-signals rule.
    raw = [
        {"a": "hub", "b": "p1", "concept": 0, "embed": 0.55, "shared": 0},
        {"a": "hub", "b": "p2", "concept": 0, "embed": 0.80, "shared": 0},
        {"a": "hub", "b": "p3", "concept": 0, "embed": 0.60, "shared": 0},
        {"a": "hub", "b": "p4", "concept": 0, "embed": 0.90, "shared": 0},
    ]
    out = cc.score_and_bound(raw, top_k=2)
    hub_pairs = [p for p in out if "hub" in (p["a_node"], p["b_node"])]
    assert len(hub_pairs) == 2
    # The two strongest (p4=0.90, p2=0.80) win the budget.
    peers = {p["b_node"] for p in hub_pairs}
    assert peers == {"p4", "p2"}


def test_global_cap():
    # 20 disjoint qualifying pairs, cap=12 → exactly 12 returned.
    raw = [{"a": f"a{i}", "b": f"b{i}", "concept": 1.0, "embed": 0, "shared": 0} for i in range(20)]
    out = cc.score_and_bound(raw, global_cap=12)
    assert len(out) == 12


def test_ordering_is_score_desc_then_deterministic():
    raw = [
        {"a": "1", "b": "2", "concept": 1.0, "embed": 1.0, "shared": 0},   # 0.85
        {"a": "3", "b": "4", "concept": 1.0, "embed": 0, "shared": 0},     # 0.50
        {"a": "5", "b": "6", "concept": 1.0, "embed": 0.5, "shared": 0},   # 0.675
    ]
    out = cc.score_and_bound(raw)
    assert [p["score"] for p in out] == sorted((p["score"] for p in out), reverse=True)
    assert out[0]["a_node"] == "1"  # highest first


def test_self_pairs_ignored():
    assert cc.score_and_bound([{"a": "x", "b": "x", "concept": 1.0, "embed": 1.0, "shared": 1.0}]) == []


# ── build_raw_pairs assembly ─────────────────────────────────────────────────────────
def test_build_raw_pairs_blends_all_three_signals():
    nodes = [
        {"id": "A", "ref_type": "source", "ref_id": "s1"},
        {"id": "B", "ref_type": "source", "ref_id": "s2"},
    ]
    embeddings = {"A": [1.0, 0.0], "B": [1.0, 0.0]}            # cosine 1.0
    concept_counts = {("s1", "s2"): 5.0}                        # saturates → 1.0
    source_sets = {"A": {"s1"}, "B": {"s2"}}                    # no overlap → 0
    raw = cc.build_raw_pairs(nodes, embeddings, concept_counts, source_sets)
    assert len(raw) == 1
    p = raw[0]
    assert p["concept"] == 1.0 and p["embed"] == 1.0 and p["shared"] == 0.0
    out = cc.score_and_bound(raw)
    assert out[0]["score"] == 1.0  # concept 1.0 (or embed 1.0) wins under max-of-signals


def test_build_raw_pairs_concept_key_is_orderless():
    nodes = [
        {"id": "A", "ref_type": "source", "ref_id": "zzz"},
        {"id": "B", "ref_type": "source", "ref_id": "aaa"},
    ]
    # key stored as sorted tuple regardless of node order.
    concept_counts = {("aaa", "zzz"): 5.0}
    raw = cc.build_raw_pairs(nodes, {}, concept_counts, {})
    assert raw and raw[0]["concept"] == 1.0


def test_build_raw_pairs_drops_all_zero_pairs():
    nodes = [{"id": "A", "ref_id": "s1"}, {"id": "B", "ref_id": "s2"}]
    # no embeddings, no concepts, no shared sources → nothing to suggest
    assert cc.build_raw_pairs(nodes, {}, {}, {}) == []
