"""Deterministic check for the Wave 2 spaCy entity extractor.

Run:  cd backend && .venv/bin/python _wave2_spacy_test.py

Verifies the spaCy NER path loads en_core_web_sm, maps labels to our retained
types, and returns well-formed Entity objects (the drop-in contract). Falls back
to regex if spaCy/model is unavailable — in that case this test reports SKIP.
"""
import sys

from services.entity_extractor import entity_extractor, Entity, _get_spacy_nlp

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def main():
    print("Wave 2 — spaCy entity extractor checks\n")
    if _get_spacy_nlp() is None:
        print("  ! spaCy/en_core_web_sm unavailable in this venv — SKIP (build bundles it)")
        return 0

    text = "Tim Cook is the CEO of Apple Inc. He often mentions Microsoft and Nvidia."
    ents = entity_extractor._extract_with_spacy(text)

    check("returns Entity objects", ents and all(isinstance(e, Entity) for e in ents))
    check("only retained types emitted", all(e.type in {"person", "company", "product"} for e in ents))
    check("every entity has a name + mentions>=1", all(e.name and e.mentions >= 1 for e in ents))
    check("context_snippets is a list", all(isinstance(e.context_snippets, list) for e in ents))
    names = {e.name for e in ents}
    check("found the person (Tim Cook)", any("Tim Cook" in n for n in names))
    check("found a company (Apple)", any("Apple" in n for n in names))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
