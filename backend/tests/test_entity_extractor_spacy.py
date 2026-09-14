"""The spaCy NER path — the drop-in contract for entity extraction.

MIGRATED 2026-08-20 from `_wave2_spacy_test.py` (subprocess wrapper).

Why spaCy rather than the fast model: entity extraction runs per-article during ingest, so an
LLM call there is both slow and a background LLM caller — the class of thing that re-pins a
model and reintroduces memory pressure on a 16 GB Mac. spaCy is deterministic and free, which
is why `use_spacy_extractor` defaults on.

Skips when `en_core_web_sm` is absent (the build bundles it; a bare dev venv may not).
"""
import pytest

from services.entity_extractor import Entity, _get_spacy_nlp, entity_extractor

pytestmark = pytest.mark.skipif(_get_spacy_nlp() is None,
                                reason="spaCy/en_core_web_sm not installed in this venv")

TEXT = "Tim Cook is the CEO of Apple Inc. He often mentions Microsoft and Nvidia."


@pytest.fixture(scope="module")
def entities():
    return entity_extractor._extract_with_spacy(TEXT)


def test_returns_well_formed_entity_objects(entities):
    """The contract the rest of the pipeline codes against — a dict or a bare string here
    fails much later, inside graph building."""
    assert entities
    assert all(isinstance(e, Entity) for e in entities)
    assert all(e.name and e.mentions >= 1 for e in entities)
    assert all(isinstance(e.context_snippets, list) for e in entities)


def test_only_retained_types_are_emitted(entities):
    """spaCy labels far more than we keep (DATE, CARDINAL, ORDINAL…). Letting those through
    floods the knowledge graph with nodes nobody searches for."""
    assert all(e.type in {"person", "company", "product"} for e in entities)


def test_it_actually_finds_the_obvious_entities(entities):
    """A guard against a silently-empty extractor: the contract tests above all pass on an
    empty list."""
    names = {e.name for e in entities}
    assert any("Tim Cook" in n for n in names), names
    assert any("Apple" in n for n in names), names
