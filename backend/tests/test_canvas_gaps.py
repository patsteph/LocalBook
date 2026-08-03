"""CI-pure tests for canvas gap-detection (Run R3). Pure — no store."""
from services.canvas_gaps import find_gaps


def test_flags_misses_and_low_confidence_only():
    journey = {"queries": [
        {"id": "1", "query": "What is X?", "answer_preview": "I couldn't find this in the documents.", "confidence": 0.9, "topics": ["X"]},
        {"id": "2", "query": "What is Y?", "answer_preview": "Y is a thing.", "confidence": 0.3, "topics": ["Y"]},
        {"id": "3", "query": "What is Z?", "answer_preview": "Z is well covered and confident.", "confidence": 0.85},
        {"id": "4", "query": "", "answer_preview": "couldn't find", "confidence": 0.1},  # blank query skipped
    ]}
    gaps = find_gaps(journey)
    assert {g["query"] for g in gaps} == {"What is X?", "What is Y?"}
    assert all(g.get("reason") for g in gaps)


def test_dedups_by_query_and_caps():
    dup = {"queries": [{"id": str(i), "query": "same q", "answer_preview": "couldn't find", "confidence": 0.9} for i in range(5)]}
    assert len(find_gaps(dup)) == 1
    many = {"queries": [{"id": str(i), "query": f"q{i}", "answer_preview": "couldn't find"} for i in range(30)]}
    assert len(find_gaps(many, max_gaps=12)) == 12


def test_never_raises_on_bad_input():
    assert find_gaps({}) == []
    assert find_gaps({"queries": None}) == []
