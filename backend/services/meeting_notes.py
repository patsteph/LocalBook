"""Parser for Meeting Notes files (github.com/kvango/Meeting-Summarizer).

These arrive through a linked folder as plain markdown, and treating them as
plain markdown wastes most of what is in them. The file is already structured —
somebody's LLM did the work of separating what was decided from what was merely
said — and flattening that back into prose throws the distinction away.

The shape, verified against a real output file:

    # Meeting Notes - {title}
    *Generated locally on 2026-06-04 15:24 - transcription 7s - summary 5s*
    ## TL;DR / ## Key Points / ## Decisions
    ## Action Items
    - [ ] Draft the release notes (George)
    - [ ] Run end-to-end testing (Juan, George)
    - [ ] Update support docs (unassigned)
    ## Open Questions
    ## Full Transcript
    {one very large blob}

Two details that bite:

  * Headings carry TRAILING DOUBLE SPACES (`## TL;DR  `) — markdown hard-break
    style. Matching on the raw line fails; everything is stripped first.

  * Assignees come in two shapes. The generated files use a trailing
    parenthetical, `(Juan, George)`; the project's own README documents an
    em-dash form, `— Priya (Friday)`, where the parenthetical is a DUE DATE and
    not a person. Reading the second as a name would invent a participant called
    "Friday". Both are handled, and the em-dash form wins when present.

Why it matters beyond tidiness: the transcript is roughly 80% of the file. The
decisions and action items — the part anyone actually wants back — are a few
dozen lines competing with thousands of words of chatter for retrieval.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Section heading → canonical key. Matched case-insensitively on the stripped
# heading text, so a renamed-but-recognisable section still lands.
_SECTIONS = {
    "tl;dr": "tldr", "tldr": "tldr", "summary": "tldr",
    "key points": "key_points", "highlights": "key_points",
    "decisions": "decisions",
    "action items": "action_items", "actions": "action_items",
    "open questions": "open_questions", "questions": "open_questions",
    "full transcript": "transcript", "transcript": "transcript",
}

_GENERATED_RE = re.compile(
    r"\*?generated locally on\s+(\d{4}-\d{2}-\d{2})[ T]+(\d{2}:\d{2})", re.IGNORECASE)
_TITLE_RE = re.compile(r"^#\s*meeting notes\s*[-–—:]\s*(.+)$", re.IGNORECASE)
_CHECKBOX_RE = re.compile(r"^[-*]\s*\[( |x|X)\]\s*(.+)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.+)$")
_TRAILING_PARENS_RE = re.compile(r"\(([^()]{1,80})\)\s*$")
_SPEAKER_RE = re.compile(r"^(You|Them)\s*(?:\[[^\]]*\])?\s*:", re.IGNORECASE)

# Assignee tokens that are roles, not names. "You"/"Them" are meaningful on an
# action item but are not people we can name, so they never become participants.
_NOT_A_NAME = {"unassigned", "none", "n/a", "tbd", "everyone", "all", "team",
               "you", "them", "us", "both"}


@dataclass
class ActionItem:
    text: str
    assignees: List[str] = field(default_factory=list)
    done: bool = False
    due: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "assignees": self.assignees,
                "done": self.done, "due": self.due}


@dataclass
class MeetingNotes:
    title: str = ""
    generated_at: Optional[str] = None
    tldr: str = ""
    key_points: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    action_items: List[ActionItem] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    transcript: str = ""
    speaker_labelled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title, "generated_at": self.generated_at,
            "tldr": self.tldr, "key_points": self.key_points,
            "decisions": self.decisions,
            "action_items": [a.to_dict() for a in self.action_items],
            "open_questions": self.open_questions,
            "transcript_chars": len(self.transcript),
            "speaker_labelled": self.speaker_labelled,
        }

    @property
    def has_structure(self) -> bool:
        return bool(self.tldr or self.decisions or self.action_items
                    or self.key_points or self.open_questions)


def looks_like_meeting_notes(text: str) -> bool:
    """Cheap sniff before doing any real work.

    Deliberately requires the generator line OR the title AND at least one known
    section — a document that merely happens to contain "## Decisions" is not
    one of these files, and mis-parsing it would fabricate structure.
    """
    if not text:
        return False
    head = text[:2000]
    if _GENERATED_RE.search(head):
        return True
    if not _TITLE_RE.match(head.lstrip().splitlines()[0] if head.strip() else ""):
        return False
    return any(f"## {name}" in head.lower() for name in ("tl;dr", "action items", "decisions"))


def _split_assignees(raw: str) -> List[str]:
    parts = re.split(r",| and | & |/", raw)
    out = []
    for p in parts:
        name = p.strip().strip(".").strip()
        if not name or len(name) > 40:
            continue
        out.append(name)
    return out


def _parse_action_item(body: str, done: bool) -> ActionItem:
    """Pull the assignees off an action item without inventing any.

    The em-dash form is checked FIRST and wins: in `— Priya (Friday)` the
    parenthetical is a due date, and reading it as a name would produce a
    participant called "Friday".
    """
    text, assignees, due = body.strip(), [], None

    dash = re.search(r"\s+[—–]\s+(.+)$", text)
    if dash:
        tail = dash.group(1).strip()
        text = text[: dash.start()].strip()
        paren = _TRAILING_PARENS_RE.search(tail)
        if paren:
            due = paren.group(1).strip()
            tail = tail[: paren.start()].strip()
        assignees = _split_assignees(tail)
    else:
        paren = _TRAILING_PARENS_RE.search(text)
        if paren:
            assignees = _split_assignees(paren.group(1))
            text = text[: paren.start()].strip()

    assignees = [a for a in assignees if a]
    return ActionItem(text=text, assignees=assignees, done=done, due=due)


def parse(text: str) -> Optional[MeetingNotes]:
    """Parse a Meeting Notes file. Returns None if this isn't one."""
    if not looks_like_meeting_notes(text):
        return None

    notes = MeetingNotes()
    lines = text.splitlines()

    first = next((l for l in lines if l.strip()), "")
    m = _TITLE_RE.match(first.strip())
    if m:
        notes.title = m.group(1).strip()

    m = _GENERATED_RE.search(text[:2000])
    if m:
        try:
            notes.generated_at = datetime.strptime(
                f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M").isoformat()
        except ValueError:
            notes.generated_at = m.group(1)

    current: Optional[str] = None
    buckets: Dict[str, List[str]] = {}
    for raw in lines:
        line = raw.rstrip()                    # headings carry trailing spaces
        if line.lstrip().startswith("##"):
            heading = line.lstrip("#").strip().lower().rstrip(":")
            current = _SECTIONS.get(heading)
            if current:
                buckets.setdefault(current, [])
            continue
        if current:
            buckets[current].append(raw)

    def _bullets(key: str) -> List[str]:
        out = []
        for l in buckets.get(key, []):
            m = _BULLET_RE.match(l.strip())
            if m:
                item = m.group(1).strip()
                if item and item != "---":
                    out.append(item)
        return out

    notes.tldr = "\n".join(
        l.strip() for l in buckets.get("tldr", [])
        if l.strip() and l.strip() != "---").strip()
    notes.key_points = _bullets("key_points")
    notes.decisions = _bullets("decisions")
    notes.open_questions = _bullets("open_questions")

    for l in buckets.get("action_items", []):
        m = _CHECKBOX_RE.match(l.strip())
        if m:
            notes.action_items.append(
                _parse_action_item(m.group(2), done=m.group(1).lower() == "x"))
        else:
            b = _BULLET_RE.match(l.strip())
            if b and b.group(1).strip() != "---":
                notes.action_items.append(_parse_action_item(b.group(1), done=False))

    transcript = "\n".join(buckets.get("transcript", [])).strip()
    notes.transcript = transcript.lstrip("-").strip() if transcript.startswith("---") else transcript
    notes.speaker_labelled = any(
        _SPEAKER_RE.match(l.strip()) for l in notes.transcript.splitlines()[:200])
    return notes


def participants(notes: MeetingNotes) -> Tuple[List[str], str]:
    """Who was in the meeting, and where that came from.

    Named action-item assignees are the best signal in the file: somebody
    committed to something, so they were demonstrably present, and the name was
    written by the summariser rather than guessed by us. "You"/"Them"/
    "unassigned" are roles rather than names and never become participants —
    filing a recording under a person called "Unassigned" would be absurd.
    """
    seen, out = set(), []
    for item in notes.action_items:
        for a in item.assignees:
            key = a.strip().lower()
            if key in _NOT_A_NAME or key in seen:
                continue
            if not re.match(r"^[A-Za-z][A-Za-z.'’\- ]*$", a.strip()):
                continue
            seen.add(key)
            out.append(a.strip())
    if out:
        return out[:8], "action-item assignees"

    # The title often names the other person: "Meeting Notes - Sarah 1:1".
    if notes.title:
        from services.smart_folder import extract_participants as _fallback
        people, src = _fallback("", notes.title + ".md")
        if people:
            return people, "meeting title"
    return [], "none"


def summary_markdown(notes: MeetingNotes) -> str:
    """A compact restatement of everything EXCEPT the transcript.

    Prepended at ingest so the part people actually ask for — what was decided,
    who owes what — leads the document instead of competing with thousands of
    words of chatter for retrieval. The transcript is kept in full below it;
    nothing is discarded.
    """
    parts: List[str] = []
    if notes.title:
        parts.append(f"# {notes.title}")
    if notes.generated_at:
        parts.append(f"*Meeting recorded {notes.generated_at[:16].replace('T', ' ')}*")
    people, _ = participants(notes)
    if people:
        parts.append(f"**Participants:** {', '.join(people)}")
    if notes.tldr:
        parts.append(f"\n## Summary\n{notes.tldr}")
    if notes.decisions:
        parts.append("\n## Decisions\n" + "\n".join(f"- {d}" for d in notes.decisions))
    if notes.action_items:
        lines = []
        for a in notes.action_items:
            who = f" — {', '.join(a.assignees)}" if a.assignees else ""
            due = f" (due {a.due})" if a.due else ""
            lines.append(f"- {'[x]' if a.done else '[ ]'} {a.text}{who}{due}")
        parts.append("\n## Action items\n" + "\n".join(lines))
    if notes.open_questions:
        parts.append("\n## Open questions\n" + "\n".join(f"- {q}" for q in notes.open_questions))
    if notes.key_points:
        parts.append("\n## Key points\n" + "\n".join(f"- {k}" for k in notes.key_points))
    return "\n".join(parts).strip()
