"""Smart Folders — suggestion vs. authorisation.

A 1:1 transcript can be the most sensitive document a user owns. A performance
conversation landing in a shared notebook is not a bug to fix next release —
it is a disclosure that cannot be taken back. So the design has exactly one
rule, and these tests exist to make it impossible to violate quietly:

    Learning improves the SUGGESTION. Only an explicit rule authorises the ACTION.

No confidence score, however high, may cause a file to be filed. Either a human
clicks, or a rule that human wrote matches.
"""
import asyncio

import pytest

from services import smart_folder
from storage.smart_folder_store import smart_folder_store


# ── who is in the recording ─────────────────────────────────────────────────

FRONTMATTER = """---
title: Weekly 1:1
participants: Sarah Chen, Dana Whitfield
date: 2026-09-16
---

We talked about the promotion timeline.
"""

TRANSCRIPT = """# 1:1

**Sarah:** I wanted to talk about the promotion track.
**Dana:** Good — where do you feel you are?
**Sarah:** Somewhere between senior and staff.
**Dana:** That matches my read.
Note: follow up in two weeks.
Action: draft the scope document.
"""


def test_frontmatter_participants_win():
    """The recorder app knew who was in the room — that beats any guess."""
    people, source = smart_folder.extract_participants(FRONTMATTER, "x.md")
    assert people == ["Sarah Chen", "Dana Whitfield"]
    assert source == "frontmatter"


def test_speaker_labels_are_read_when_there_is_no_frontmatter():
    people, source = smart_folder.extract_participants(TRANSCRIPT, "x.md")
    assert set(people) == {"Sarah", "Dana"}
    assert source == "speaker labels"


def test_note_and_action_lines_are_not_mistaken_for_people():
    """`Note:` and `Action:` have the exact shape of a speaker label. Filing a
    recording under a person called "Action" would be absurd and confusing."""
    people, _ = smart_folder.extract_participants(TRANSCRIPT, "x.md")
    assert "Note" not in people and "Action" not in people


def test_a_single_mention_is_not_enough_to_be_a_speaker():
    text = "# Notes\n\nSummary: we covered three things.\nDecision: ship it.\n"
    people, source = smart_folder.extract_participants(text, "notes.md")
    assert people == [] and source == "none"


def test_the_filename_is_the_last_resort():
    people, source = smart_folder.extract_participants(
        "no structure at all here", "2026-09-16 1-1 priya.md")
    assert people == ["Priya"]
    assert source == "filename"


def test_the_source_of_the_guess_is_always_reported():
    """The approval card says HOW we know. "from the file's own metadata" reads
    very differently from "guessed from the filename", and the user is being
    asked to confirm one of them."""
    for text, fn, expected in [
        (FRONTMATTER, "a.md", "frontmatter"),
        (TRANSCRIPT, "a.md", "speaker labels"),
        ("nothing", "1-1 dana.md", "filename"),
        ("nothing", "a.md", "none"),
    ]:
        assert smart_folder.extract_participants(text, fn)[1] == expected


# ── rules: the only thing that authorises ───────────────────────────────────

@pytest.fixture
def nb():
    from datetime import datetime
    from storage.database import get_db
    conn = get_db().get_connection()
    now = datetime.utcnow().isoformat()
    for nid in ("nb-sarah", "nb-team"):
        conn.execute(
            "INSERT OR IGNORE INTO notebooks (id, title, created_at, updated_at) "
            "VALUES (?,?,?,?)", (nid, nid, now, now))
    conn.commit()
    smart_folder_store.forget_link("lk")
    yield
    for r in smart_folder_store.list_rules():
        smart_folder_store.delete_rule(r["id"])
    smart_folder_store.forget_link("lk")


def test_a_rule_must_have_a_scope(nb):
    """An unscoped rule would route every recording ever seen — that is not a
    rule, it is an accident waiting to happen."""
    with pytest.raises(ValueError, match="route every recording"):
        smart_folder_store.create_rule(notebook_id="nb-sarah")


def test_a_participant_rule_matches_that_person(nb):
    smart_folder_store.create_rule(notebook_id="nb-sarah", participants=["Sarah Chen"])
    hit = smart_folder_store.match_rule(["Sarah Chen", "Dana"], ["goals"])
    assert hit and hit["notebook_id"] == "nb-sarah"
    assert smart_folder_store.match_rule(["Priya"], ["goals"]) is None


def test_matching_ignores_case_and_spacing(nb):
    smart_folder_store.create_rule(notebook_id="nb-sarah", participants=["Sarah Chen"])
    assert smart_folder_store.match_rule(["  sarah   chen "], []) is not None


def test_a_participant_rule_requires_every_named_person(nb):
    """"Recordings with Sarah AND Priya" must not fire on a Sarah-only call."""
    smart_folder_store.create_rule(notebook_id="nb-team",
                                   participants=["Sarah Chen", "Priya Raman"])
    assert smart_folder_store.match_rule(["Sarah Chen"], []) is None
    assert smart_folder_store.match_rule(["Sarah Chen", "Priya Raman", "Pat"], []) is not None


def test_a_topic_rule_matches_any_overlap(nb):
    smart_folder_store.create_rule(notebook_id="nb-team", topics=["hiring", "budget"])
    assert smart_folder_store.match_rule([], ["budget"]) is not None
    assert smart_folder_store.match_rule([], ["roadmap"]) is None


def test_the_narrower_rule_wins(nb):
    """A person+topic rule is a more specific authorisation than person alone,
    so it must take precedence — otherwise the broad rule silently swallows
    the case the user carved out."""
    smart_folder_store.create_rule(notebook_id="nb-team", participants=["Sarah Chen"])
    smart_folder_store.create_rule(notebook_id="nb-sarah",
                                   participants=["Sarah Chen"], topics=["promotion"])
    hit = smart_folder_store.match_rule(["Sarah Chen"], ["promotion"])
    assert hit["notebook_id"] == "nb-sarah"
    hit2 = smart_folder_store.match_rule(["Sarah Chen"], ["roadmap"])
    assert hit2["notebook_id"] == "nb-team"


def test_a_paused_rule_authorises_nothing(nb):
    r = smart_folder_store.create_rule(notebook_id="nb-sarah", participants=["Sarah Chen"])
    smart_folder_store.set_rule_enabled(r["id"], False)
    assert smart_folder_store.match_rule(["Sarah Chen"], []) is None


# ── triage: the safety contract ─────────────────────────────────────────────

@pytest.fixture
def no_llm(monkeypatch):
    """Analysis without the model. The runners here are testing the DECISION,
    not the summariser."""
    async def _sum(text, filename):
        return ("A 1:1 about the promotion timeline.", ["promotion", "goals"])
    monkeypatch.setattr(smart_folder, "summarize", _sum)


@pytest.fixture
def confident_suggestion(monkeypatch):
    """Route with near-certainty — the case that must STILL not auto-file."""
    async def _suggest(analysis):
        return {"suggested_id": "nb-sarah", "suggested_name": "nb-sarah",
                "confidence": 0.99, "alternatives": [], "reason": "test"}
    monkeypatch.setattr(smart_folder, "suggest", _suggest)


def _triage(**kw):
    defaults = dict(link_id="lk", abs_path="/tmp/a.md", filename="1-1 sarah.md",
                    text=FRONTMATTER, size=100, mtime=1.0, content_hash="h1")
    defaults.update(kw)
    return asyncio.run(smart_folder.triage(**defaults))


def test_certainty_does_not_authorise(nb, no_llm, confident_suggestion):
    """THE test. 0.99 confidence, no rule → it still waits for a human.
    If this ever fails, a recording can file itself, and the whole safety
    model is gone."""
    out = _triage()
    assert out["action"] == "pending", (
        "a 0.99-confidence suggestion auto-filed a recording — only a "
        "user-authored rule may do that"
    )
    assert out["suggested_id"] == "nb-sarah"
    assert out["confidence"] == 0.99
    queued = smart_folder_store.list_pending(link_id="lk")
    assert len(queued) == 1 and queued[0]["status"] == "pending"


def test_a_rule_authorises_and_says_which_one(nb, no_llm, confident_suggestion):
    rule = smart_folder_store.create_rule(notebook_id="nb-sarah",
                                          participants=["Sarah Chen", "Dana Whitfield"])
    out = _triage(abs_path="/tmp/b.md")
    assert out["action"] == "auto"
    assert out["notebook_id"] == "nb-sarah"
    assert out["rule_id"] == rule["id"], "an auto-route must name the rule that allowed it"
    assert smart_folder_store.list_pending(link_id="lk") == []


def test_an_auto_route_is_counted_against_its_rule(nb, no_llm, confident_suggestion):
    """A rule is a promise the user made. They must be able to see it kept."""
    rule = smart_folder_store.create_rule(notebook_id="nb-sarah",
                                          participants=["Sarah Chen"])
    _triage(abs_path="/tmp/c.md")
    _triage(abs_path="/tmp/d.md")
    assert smart_folder_store.get_rule(rule["id"])["hit_count"] == 2


def test_re_triaging_the_same_file_does_not_duplicate_the_card(
        nb, no_llm, confident_suggestion):
    _triage(abs_path="/tmp/e.md")
    _triage(abs_path="/tmp/e.md")
    assert len(smart_folder_store.list_pending(link_id="lk")) == 1


def test_no_suggestion_still_queues_rather_than_dropping(nb, no_llm, monkeypatch):
    """A recording we cannot place must never be silently discarded — it is the
    case most likely to need a NEW notebook."""
    async def _none(analysis):
        return {"suggested_id": None, "suggested_name": None,
                "confidence": 0.0, "alternatives": [], "reason": "no digests"}
    monkeypatch.setattr(smart_folder, "suggest", _none)
    out = _triage(abs_path="/tmp/f.md")
    assert out["action"] == "pending"
    assert smart_folder_store.list_pending(link_id="lk")[0]["suggested_id"] is None


def test_repeated_unplaceable_people_become_a_notebook_suggestion(nb, no_llm, monkeypatch):
    """The system noticing a relationship before the user has filed it."""
    async def _none(analysis):
        return {"suggested_id": None, "suggested_name": None,
                "confidence": 0.0, "alternatives": [], "reason": ""}
    monkeypatch.setattr(smart_folder, "suggest", _none)
    for i in range(4):
        _triage(abs_path=f"/tmp/priya-{i}.md", filename=f"1-1 priya {i}.md",
                text="no structure")
    hits = smart_folder_store.unrouted_by_participant(minimum=3)
    assert any(h["participant"] == "Priya" and h["count"] >= 4 for h in hits)


def test_dismissing_leaves_the_file_alone(nb, no_llm, confident_suggestion):
    out = _triage(abs_path="/tmp/g.md")
    smart_folder_store.resolve_pending(out["item_id"], status="dismissed")
    assert smart_folder_store.list_pending(link_id="lk") == []
    assert smart_folder_store.get_pending(out["item_id"])["status"] == "dismissed"


# ── a brand-new notebook must be reachable ──────────────────────────────────
#
# Measured on a real install 2026-09-16: seven notebooks, only TWO with a
# curator digest — the rest had no sources yet. `notebook_router` skips
# notebooks without a digest summary, so the primary use case was broken by
# construction: create "Sarah 1:1", link a Smart Folder, drop in a recording,
# and the new notebook could never be suggested. Every card would have read
# "no notebook looks like a fit" while the code worked exactly as written.

@pytest.fixture
def notebooks_without_digests(monkeypatch):
    """Notebooks that exist and have names, but no curator digest at all —
    which is the state every notebook is in on the day it is created."""
    async def _list():
        return [
            {"id": "nb-sarah", "title": "Sarah Chen 1:1", "description": ""},
            {"id": "nb-team", "title": "Team Planning", "description": "roadmap and hiring"},
            {"id": "nb-misc", "title": "Bookshelf", "description": ""},
        ]

    class _Store:
        list = staticmethod(_list)

    import storage.notebook_store as ns
    monkeypatch.setattr(ns, "notebook_store", _Store())

    async def _no_digest_route(*a, **kw):
        from services.notebook_router import RoutingDecision
        return RoutingDecision(decision="no_match", reason="no notebook digests available")

    import services.notebook_router as nr
    monkeypatch.setattr(nr, "route", _no_digest_route)


def test_a_notebook_named_after_the_person_is_suggested_with_no_history(
        notebooks_without_digests):
    """The notebook's NAME is a declaration of what it is for. It needs no
    sources to be the obvious destination for a recording with that person."""
    out = asyncio.run(smart_folder.suggest({
        "participants": ["Sarah Chen"], "topics": ["promotion"],
    }))
    assert out["suggested_id"] == "nb-sarah", out
    assert out["confidence"] >= 0.8
    assert "Sarah" in out["reason"], "the card must say WHY it picked this one"


def test_a_first_name_matches_a_fuller_notebook_title(notebooks_without_digests):
    """Speaker labels give "Sarah"; the notebook is "Sarah Chen 1:1"."""
    out = asyncio.run(smart_folder.suggest({"participants": ["Sarah"], "topics": []}))
    assert out["suggested_id"] == "nb-sarah"


def test_topics_can_match_a_notebook_by_name_too(notebooks_without_digests):
    out = asyncio.run(smart_folder.suggest({"participants": [], "topics": ["hiring"]}))
    assert out["suggested_id"] == "nb-team"
    assert out["confidence"] < 0.8, "a topic match is weaker evidence than a person match"


def test_no_plausible_notebook_still_yields_no_suggestion(notebooks_without_digests):
    """Reaching for a match that isn't there is worse than saying so."""
    out = asyncio.run(smart_folder.suggest({
        "participants": ["Nobody Here"], "topics": ["quantum gardening"],
    }))
    assert out["suggested_id"] is None


def test_a_deleted_notebook_is_never_suggested(monkeypatch):
    """Digest rows outlive their notebooks — this install had 21 digests for 7
    notebooks. Suggesting a notebook that no longer exists is worse than
    suggesting nothing, because approving it would fail."""
    async def _list():
        return [{"id": "nb-live", "title": "Live Notebook", "description": ""}]

    class _Store:
        list = staticmethod(_list)

    import storage.notebook_store as ns
    monkeypatch.setattr(ns, "notebook_store", _Store())

    async def _stale_route(*a, **kw):
        from services.notebook_router import RoutingCandidate, RoutingDecision
        return RoutingDecision(
            decision="route",
            top=RoutingCandidate("nb-deleted", "Ghost Notebook", 0.95),
            alternatives=[],
        )

    import services.notebook_router as nr
    monkeypatch.setattr(nr, "route", _stale_route)

    out = asyncio.run(smart_folder.suggest({"participants": [], "topics": []}))
    assert out["suggested_id"] != "nb-deleted", "suggested a notebook that no longer exists"
