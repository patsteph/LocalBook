"""Parsing Meeting Notes files (github.com/kvango/Meeting-Summarizer).

These land in a linked folder as markdown. Treating them as plain markdown
throws away the one thing that makes them valuable: somebody's LLM already
separated what was DECIDED from what was merely said, and flattening puts it
back into undifferentiated prose.

The fixtures below cover both assignee shapes the project produces — the
generated files use a trailing parenthetical, the README documents an em-dash
form where the parenthetical is a DUE DATE. Reading the second as a name would
invent a participant called "Friday".
"""
import pytest

from services.meeting_notes import (
    looks_like_meeting_notes,
    parse,
    participants,
    summary_markdown,
)

REAL_SHAPE = """# Meeting Notes - Sprint sync

*Generated locally on 2026-06-04 15:24 - transcription 7s - summary 5s*

## TL;DR  
Launch moved to Tuesday; the billing fix must land Friday.

---

## Key Points  
- Authentication edge cases persist.  
- UI redesign is nearly complete.  

---

## Decisions  
- Delay the launch to next Tuesday.  
- Log remaining edge cases as known issues.  

---

## Action Items  
- [ ] Draft and distribute release notes (George)  
- [x] Recheck authentication scenarios (Juan)  
- [ ] Run final end-to-end testing (Juan, George)  
- [ ] Update support documentation (unassigned)  

---

## Open Questions  
- Who owns the press release?  

---

## Full Transcript

Alright team, let's dive in. We're getting closer to the release date.
"""

README_SHAPE = """# Meeting Notes - Billing call

*Generated locally on 2026-09-16 09:30 - transcription 4s - summary 3s*

## TL;DR
Launch moved to Tuesday.

## Action Items
- [ ] Ship the billing fix — Priya (Friday)
- [ ] Draft the press release — You

## Full Transcript

You: we should ship Friday.
Them: agreed.
"""


# ── recognition ─────────────────────────────────────────────────────────────

def test_a_real_file_is_recognised():
    assert looks_like_meeting_notes(REAL_SHAPE)
    assert parse(REAL_SHAPE) is not None


@pytest.mark.parametrize("text", [
    "",
    "# Just some notes\n\nNothing structured here.",
    # A document that merely MENTIONS these sections is not one of these files;
    # parsing it would fabricate decisions that were never recorded.
    "# Project plan\n\n## Decisions\n- use postgres\n\n## Action Items\n- [ ] do it",
])
def test_ordinary_markdown_is_not_mistaken_for_meeting_notes(text):
    assert not looks_like_meeting_notes(text)
    assert parse(text) is None


# ── structure ───────────────────────────────────────────────────────────────

def test_every_section_is_pulled_apart():
    n = parse(REAL_SHAPE)
    assert n.title == "Sprint sync"
    assert n.generated_at.startswith("2026-06-04T15:24")
    assert "Launch moved to Tuesday" in n.tldr
    assert len(n.key_points) == 2
    assert n.decisions == ["Delay the launch to next Tuesday.",
                           "Log remaining edge cases as known issues."]
    assert n.open_questions == ["Who owns the press release?"]
    assert "Alright team" in n.transcript


def test_headings_with_trailing_double_spaces_still_match():
    """The generator writes `## TL;DR  ` with markdown hard-break spacing.
    Matching the raw line finds nothing and every section comes back empty."""
    assert "## TL;DR  \n" in REAL_SHAPE, "fixture no longer reproduces the quirk"
    assert parse(REAL_SHAPE).tldr


def test_section_separators_are_not_read_as_content():
    n = parse(REAL_SHAPE)
    assert "---" not in n.tldr
    assert all(d != "---" for d in n.decisions)


# ── action items ────────────────────────────────────────────────────────────

def test_checked_items_are_recorded_as_done():
    items = {a.text: a for a in parse(REAL_SHAPE).action_items}
    assert items["Recheck authentication scenarios"].done is True
    assert items["Draft and distribute release notes"].done is False


def test_parenthetical_assignees_are_read():
    items = {a.text: a for a in parse(REAL_SHAPE).action_items}
    assert items["Draft and distribute release notes"].assignees == ["George"]
    assert items["Run final end-to-end testing"].assignees == ["Juan", "George"]


def test_the_em_dash_form_treats_the_parenthetical_as_a_due_date():
    """`— Priya (Friday)`. Reading the parenthetical as a name here would
    produce a participant called "Friday" and file the meeting under them."""
    items = {a.text: a for a in parse(README_SHAPE).action_items}
    ship = items["Ship the billing fix"]
    assert ship.assignees == ["Priya"]
    assert ship.due == "Friday"
    assert "Friday" not in participants(parse(README_SHAPE))[0]


def test_an_unassigned_item_keeps_its_text():
    items = {a.text: a for a in parse(REAL_SHAPE).action_items}
    assert "Update support documentation" in items


# ── participants ────────────────────────────────────────────────────────────

def test_participants_come_from_named_assignees():
    """The best signal in the file: somebody committed to something, so they
    were demonstrably there, and the summariser wrote the name — we did not
    guess it."""
    people, source = participants(parse(REAL_SHAPE))
    assert people == ["George", "Juan"]
    assert source == "action-item assignees"


def test_roles_are_never_treated_as_people():
    """Filing a recording under a person called "Unassigned" would be absurd;
    "You" and "Them" are real assignees but not names we can file under."""
    people, _ = participants(parse(REAL_SHAPE))
    assert "unassigned" not in [p.lower() for p in people]
    people2, _ = participants(parse(README_SHAPE))
    assert [p.lower() for p in people2] == ["priya"]


def test_speaker_labelled_transcripts_are_flagged():
    assert parse(README_SHAPE).speaker_labelled is True
    assert parse(REAL_SHAPE).speaker_labelled is False


# ── the retrieval reshape ───────────────────────────────────────────────────

def test_the_summary_restates_everything_except_the_transcript():
    """The transcript is ~80% of a real file. What people ask for — what was
    decided, who owes what — has to lead the document, not compete with
    thousands of words of chatter."""
    md = summary_markdown(parse(REAL_SHAPE))
    assert "Sprint sync" in md
    assert "George, Juan" in md
    assert "Delay the launch" in md
    assert "Who owns the press release?" in md
    assert "Alright team" not in md, "the transcript must not be duplicated into the summary"


def test_the_summary_is_a_small_fraction_of_the_file():
    md = summary_markdown(parse(REAL_SHAPE))
    assert len(md) < len(REAL_SHAPE)


def test_a_file_with_no_structure_yields_nothing_to_prepend():
    thin = ("# Meeting Notes - Quick call\n\n"
            "*Generated locally on 2026-09-16 10:00 - transcription 1s - summary 1s*\n\n"
            "## Full Transcript\n\nJust some talking.\n")
    n = parse(thin)
    assert n is not None and n.has_structure is False
    assert "Just some talking" not in summary_markdown(n)
