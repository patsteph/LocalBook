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


# ── Open loops are now a BADGE ON THE NODE, not just a side-panel list ──────────────────

def test_agent_chatter_is_not_an_open_loop():
    """A failing admin command can trip a miss-marker; a badge on the map saying the control
    panel is an unresolved question would be nonsense."""
    journey = {"queries": [
        {"id": "1", "query": "@collector show status",
         "answer_preview": "I couldn't find that setting.", "confidence": 0.9},
        {"id": "2", "query": "How does retrieval augmentation work?",
         "answer_preview": "I couldn't find this in the documents.", "confidence": 0.9},
    ]}
    assert [g["query"] for g in find_gaps(journey)] == ["How does retrieval augmentation work?"]


def test_empty_sources_alone_is_NOT_a_gap():
    """REGRESSION GUARD — reverted 2026-08-18 after real-data checking.

    "consulted no sources ⇒ open loop" sounds right and is wrong: on real notebooks every
    hit was an agent COMMAND ("collect now", "add a note …", "subscribe to <rss>"), which
    legitimately consults nothing and which the learning gate admits (@collector source adds
    count as learning). Do not re-add without a question-vs-command discriminator."""
    journey = {"queries": [
        {"id": "1", "query": "subscribe to https://news.ycombinator.com/rss",
         "answer_preview": "Subscribed.", "confidence": 0.7, "sources_used": []},
        {"id": "2", "query": "add a note about the overlap",
         "answer_preview": "Noted.", "confidence": 0.7, "sources_used": []},
    ]}
    assert find_gaps(journey) == []


def test_a_later_query_sharing_a_topic_does_NOT_suppress_a_gap():
    """REGRESSION GUARD — reverted 2026-08-18 after real-data checking.

    `topics` are NOT subjects, they are SOURCE TITLES (~5 per query), so "a later strong
    answer on the same topic closed this loop" matches almost any pair of queries in a
    notebook. It silently suppressed 2 of the 3 genuine gaps in a real notebook. Real
    resolution detection needs similarity over the query TEXT."""
    journey = {"queries": [
        {"id": "2", "query": "A later, well-answered question",
         "answer_preview": "Fully covered.", "confidence": 0.9,
         "topics": ["Some Long Source Title.pdf"], "sources_used": ["s1"]},
        {"id": "1", "query": "An earlier unanswered question",
         "answer_preview": "I couldn't find this in the documents.", "confidence": 0.9,
         "topics": ["Some Long Source Title.pdf"], "sources_used": ["s1"]},
    ]}
    assert [g["query"] for g in find_gaps(journey)] == ["An earlier unanswered question"]
