"""CI-pure tests for the quiz/flashcard CAPTURE LOOP (`POST /quiz/review/deck`).

Context (verified 2026-08-12): the app shipped a complete FSRS engine, three endpoints and a typed
API client — with no path from the UI to any of it. Study UIs graded into component state and threw
the results away, so cards sat at `reps=0` forever and the scheduler never ran. These tests pin the
endpoint that closes that loop, because an unreachable scheduler is exactly the kind of thing that
regresses silently.

Uses a temp card store via monkeypatched `_get_quiz_dir` — never touches the production data dir
(`settings.data_dir` on a dev venv IS production; see COLLABORATION_NOTES 2026-07-22).
"""
import asyncio
import json

import pytest

from api import quiz as q


@pytest.fixture
def card_store(tmp_path, monkeypatch):
    """Isolated card store seeded with three unreviewed cards, as generation leaves them."""
    monkeypatch.setattr(q, "_get_quiz_dir", lambda: tmp_path)
    nb = "nb-test"
    data = {"cards": {}, "reviews": []}
    for i in (1, 2, 3):
        data["cards"][f"quiz1_q{i}"] = {
            "question": f"Q{i}", "answer": f"A{i}", "explanation": "",
            "difficulty": 5.0, "stability": 0.0, "reps": 0,
            "due": "2026-08-12T00:00:00", "created": "2026-08-12T00:00:00",
        }
    (tmp_path / f"{nb}_cards.json").write_text(json.dumps(data))
    return nb, tmp_path


def _load(tmp_path, nb):
    return json.loads((tmp_path / f"{nb}_cards.json").read_text())


def _deck(nb, results):
    return q.DeckReviewRequest(
        notebook_id=nb, results=[q.DeckCardResult(**r) for r in results])


def test_deck_review_records_every_card(card_store):
    nb, tmp = card_store
    res = asyncio.run(q.review_deck(_deck(nb, [
        {"card_id": "quiz1_q1", "correct": True},
        {"card_id": "quiz1_q2", "correct": False},
        {"card_id": "quiz1_q3", "correct": True},
    ])))
    assert res["recorded"] == 3 and res["skipped"] == 0 and res["total_reviews"] == 3

    data = _load(tmp, nb)
    assert len(data["reviews"]) == 3
    # The whole point: cards are no longer stuck at reps=0 with zero stability.
    for card in data["cards"].values():
        assert card["reps"] == 1
        assert card["stability"] > 0.0
        assert "last_review" in card


def test_correct_and_wrong_diverge_in_scheduling(card_store):
    """A right answer must earn a longer interval than a wrong one — otherwise the loop is
    recording data but not actually scheduling."""
    nb, tmp = card_store
    asyncio.run(q.review_deck(_deck(nb, [
        {"card_id": "quiz1_q1", "correct": True},
        {"card_id": "quiz1_q2", "correct": False},
    ])))
    cards = _load(tmp, nb)["cards"]
    assert cards["quiz1_q1"]["stability"] > cards["quiz1_q2"]["stability"]
    assert cards["quiz1_q1"]["due"] > cards["quiz1_q2"]["due"]


def test_explicit_rating_overrides_the_correct_map(card_store):
    nb, tmp = card_store
    asyncio.run(q.review_deck(_deck(nb, [{"card_id": "quiz1_q1", "correct": True, "rating": 4}])))
    assert _load(tmp, nb)["reviews"][0]["rating"] == 4  # Easy, not the default Good(3)


def test_rating_map_is_conservative():
    assert q._rating_from_correct(True) == 3    # Good
    assert q._rating_from_correct(False) == 1   # Again


def test_unknown_card_is_skipped_not_fatal(card_store):
    """A stale id from a regenerated deck must not cost the rest of the run."""
    nb, tmp = card_store
    res = asyncio.run(q.review_deck(_deck(nb, [
        {"card_id": "quiz1_q1", "correct": True},
        {"card_id": "gone_q9", "correct": True},
    ])))
    assert res["recorded"] == 1 and res["skipped"] == 1
    assert len(_load(tmp, nb)["reviews"]) == 1


def test_repeat_reviews_accumulate(card_store):
    nb, tmp = card_store
    for _ in range(3):
        asyncio.run(q.review_deck(_deck(nb, [{"card_id": "quiz1_q1", "correct": True}])))
    card = _load(tmp, nb)["cards"]["quiz1_q1"]
    assert card["reps"] == 3
    assert len(_load(tmp, nb)["reviews"]) == 3


def test_empty_deck_writes_nothing(card_store):
    nb, tmp = card_store
    before = (tmp / f"{nb}_cards.json").read_text()
    res = asyncio.run(q.review_deck(_deck(nb, [])))
    assert res["recorded"] == 0
    assert (tmp / f"{nb}_cards.json").read_text() == before


def test_stats_reflect_recorded_reviews(card_store):
    """The pre-existing /quiz/stats endpoint should light up once capture works."""
    nb, _ = card_store
    assert asyncio.run(q.get_quiz_stats(nb))["cards_reviewed"] == 0
    asyncio.run(q.review_deck(_deck(nb, [{"card_id": "quiz1_q1", "correct": True}])))
    stats = asyncio.run(q.get_quiz_stats(nb))
    assert stats["cards_reviewed"] == 1 and stats["total_reviews"] == 1


def test_single_card_endpoint_shares_the_same_math(card_store):
    """`/review` and `/review/deck` must never fork the scheduling math."""
    nb, tmp = card_store
    asyncio.run(q.review_card(q.FSRSRating(card_id="quiz1_q1", rating=3)))
    asyncio.run(q.review_deck(_deck(nb, [{"card_id": "quiz1_q2", "correct": True}])))
    cards = _load(tmp, nb)["cards"]
    assert cards["quiz1_q1"]["stability"] == cards["quiz1_q2"]["stability"]
    assert cards["quiz1_q1"]["difficulty"] == cards["quiz1_q2"]["difficulty"]
