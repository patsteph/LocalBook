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
