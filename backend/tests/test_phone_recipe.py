"""The recipe a phone follows must be exactly what the Mac itself does."""
import re

from services import rag_generation
from services.sync import phone_recipe


def _apply(steps, text):
    for st in steps:
        flags = 0
        if "dotall" in st["flags"]:
            flags |= re.DOTALL
        if "ignorecase" in st["flags"]:
            flags |= re.IGNORECASE
        before = text
        text = re.sub(st["pattern"], st["replace"], text, flags=flags)
        if st.get("keep_original_if_empty") and not text.strip() and before.strip():
            text = before
    return text.strip()


def test_the_cleaning_steps_reproduce_the_macs_cleaner_exactly():
    for raw in phone_recipe.SAMPLE_ANSWERS + ["", "   ", "References:\nonly a bibliography", "A [1].\n\nSources:\nfoo"]:
        assert _apply(phone_recipe.CLEAN_STEPS, raw) == rag_generation.clean_llm_output(raw), raw


def test_the_recipe_carries_the_macs_own_prompts_and_rules():
    r = phone_recipe.recipe()
    for kind in ("factual", "synthesis", "complex"):
        assert r["system_prompts"][kind] == rag_generation.get_prompt_for_query_type(kind, 5, 0.5)
    assert r["classify"]["complex_patterns"] and r["classify"]["factual_patterns"]
    assert r["generation"]["temperature"] == 0.3 and r["generation"]["max_tokens"] == 500
    assert r["generation"]["top_p"] == 0.95 and r["generation"]["repetition_penalty"] == 1.15
    assert len(r["recipe_version"]) == 12 and phone_recipe.recipe()["recipe_version"] == r["recipe_version"]


def test_classification_vectors_cover_all_three_types():
    kinds = {v["type"] for v in phone_recipe.recipe()["conformance"]["classification"]}
    assert kinds == {"factual", "synthesis", "complex"}


def test_expansion_and_retrieval_numbers_come_from_the_macs_own_tables():
    from services import rag_query_analyzer
    r = phone_recipe.recipe()
    assert dict(map(tuple, r["query_expansion"]["expansions"])) == rag_query_analyzer.EXPANSIONS
    for v in r["conformance"]["expansion"]:
        assert rag_query_analyzer.expand_query(v["question"]) == v["expanded"]
    ret = r["retrieval"]
    assert ret["hybrid_k"] == 2 * ret["overcollect"] and ret["vector_candidates"] == 4 * ret["overcollect"]
    assert r["citations"]["min_confidence"] == 0.20


def test_citation_fix_drops_numbers_that_point_at_no_source():
    from services import rag_generation
    assert rag_generation.fix_citations("Fine [1] and [2, 3].", 3) == "Fine [1] and [2, 3]."
    assert rag_generation.fix_citations("Section [16.7.3] and [562].", 5) == "Section and."
    assert rag_generation.fix_citations("Mixed [1, 562] and [2].", 4) == "Mixed [1] and [2]."
    assert rag_generation.fix_citations("Words [see 2] and [1-3] stay.", 5) == "Words [see 2] and [1-3] stay."


def test_the_recipe_publishes_citation_vectors_and_a_length_hint():
    from services.sync import phone_recipe
    body = phone_recipe.recipe()
    body = body.get("recipe", body)
    assert body["conformance"]["citation_fix"]
    assert "words" in body["generation"]["length_hint"]


def test_the_recipe_carries_routing_format_and_entity_rules_with_vectors():
    body = phone_recipe.recipe()
    body = body.get("recipe", body)
    assert body["format_rules"] and body["routing"]["patterns"]["numeric"]
    assert body["personalization"]["boost_cap"] == 0.3
    c = body["conformance"]
    assert c["format"] and c["routing"] and c["personal"]["cases"]
    # the first matching rule of the Mac's own function is what the vectors say
    from services import rag_query_analyzer
    for v in c["format"]:
        assert rag_query_analyzer.detect_response_format(v["question"]) == v["hint"]


def test_the_recipe_publishes_the_macs_second_look_rules_with_vectors():
    from services import rag_query_analyzer as qa
    body = phone_recipe.recipe()
    body = body.get("recipe", body)
    assert body["quality"]["failure_phrases"] and body["variants"]["max"] == 3
    assert body["corrective"]["result_cap"] == 2 * body["retrieval"]["overcollect"]
    for v in body["conformance"]["quality"]:
        assert qa.check_answer_quality(v["question"], v["answer"], v["type"])[0] == v["ok"]
    for v in body["conformance"]["variants"]:
        assert qa.generate_query_variants(v["question"]) == v["variants"]


def test_a_better_reranked_passage_is_not_dropped_below_a_weaker_one():
    """Netflix/onboarding regression: rerank 0.0266 (best) used to score 0.14 and be cut at 0.20, while a
    0.00007 passage kept its RRF value of 0.5-0.9."""
    from services.rag_context import citation_confidence as conf
    best, weak = conf(0.0266, 0.5), conf(0.00007, 0.5)
    assert best >= 0.20 and best >= weak
    assert conf(0.5, 0.0) == 0.5 * 1.4 + 0.1          # unchanged where there is no RRF signal
    assert conf(None, 0.8) == 0.5 + 0.8 * 0.4


def test_the_recipe_says_how_confidence_combines_and_proves_it():
    body = phone_recipe.recipe()
    body = body.get("recipe", body)
    assert body["citations"]["combine"] == "max" and body["conformance"]["confidence"]


def test_citation_fix_handles_section_in_source_and_context_tags():
    from services import rag_generation as g
    assert g.fix_citations("A [16.2.1 in Source 2] and [16.8 in Source 5].", 5) == "A [2] and [5]."
    assert g.fix_citations("B [16.2 in Source 9], note [Local Context].", 4) == "B, note."
    assert g.fix_citations("C [1, 16.7.3 in Source 2, 562].", 4) == "C [1, 2]."


def test_focused_fallbacks_supplement_hybrid_results_and_scan_the_whole_table():
    """Netflix regression: the entity fallback looked at an arbitrary 500 rows and REPLACED the hybrid pool
    with the single matching chunk it happened to find."""
    from services import rag_search

    class Q:
        def __init__(self, rows): self.rows, self.w = rows, None
        def where(self, expr): self.w = expr; return self
        def limit(self, n): return self
        def to_list(self): return [r for r in self.rows if "netflix" in r["text"].lower()]

    class T:
        def __init__(self, rows): self.rows = rows
        def search(self, *a): return Q(self.rows)

    rows = [{"source_id": "x", "chunk_index": 0, "text": "Company: Netflix orientation"},
            {"source_id": "y", "chunk_index": 73, "text": "netflix mention"}, {"source_id": "z", "chunk_index": 1, "text": "other"}]
    found = rag_search._rows_containing(T(rows), ["Netflix"])
    assert {r["source_id"] for r in found} == {"x", "y"}
    hybrid = [{"source_id": "h", "chunk_index": 2, "text": "t", "_rrf_score": 0.9}]
    merged = rag_search._merge_results(found[:12], hybrid, 24)
    assert {r["source_id"] for r in merged} == {"x", "y", "h"}
    assert all(r.get("_rrf_score") for r in merged)      # exact matches carry a rank signal for the confidence step
