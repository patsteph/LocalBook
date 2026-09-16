"""Smart Folders — work out who and what a recording is about, then ask.

A Smart Folder is a linked folder with no notebook. Scanning is identical to
any other folder; only the destination decision differs.

THE SAFETY CONTRACT, which every function here is arranged around:

    Learning improves the SUGGESTION. Only an explicit rule authorises the ACTION.

A 1:1 transcript can be the most sensitive document a user owns. A performance
conversation landing in a shared notebook is not a bug to fix next release —
it is a disclosure that cannot be taken back. So no confidence score, however
high, ever causes a file to be filed. Either a human clicks, or a rule that
human wrote matches. There is no third path, and `triage()` has no branch that
could become one.

Participant extraction is heuristic and will sometimes be wrong. That is fine,
and is exactly why what it found is shown to the user on the approval card and
can be corrected — rather than being used silently.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# How many characters of a transcript to hand the fast model. A 1:1 is mostly
# front-loaded — who is here, what we're covering — and this is a background
# job on a 16 GB machine.
_SUMMARY_CHARS = 4000

_MAX_PARTICIPANTS = 8

# Words that look like speaker labels but are not people.
_NOT_A_PERSON = {
    "note", "notes", "summary", "action", "actions", "action items", "todo",
    "agenda", "attendees", "participants", "date", "time", "topic", "topics",
    "transcript", "recording", "speaker", "unknown", "background", "context",
    "next steps", "follow up", "follow-up", "decisions", "outcome", "title",
}

# Matches all three shapes transcripts actually use — `**Sarah:**`, `**Sarah**:`
# and bare `Sarah:` — because the emphasis markers land on either side of the
# colon depending on the tool that wrote the file.
_SPEAKER_RE = re.compile(
    r"^(?:\*\*|__)?\s*"
    r"([A-Z][\w.'\u2019-]*(?:\s+[A-Z][\w.'\u2019-]*){0,2})"
    r"\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s"
)
_FILENAME_RE = re.compile(
    r"(?:1[-_: ]?1|1on1|one[-_ ]?on[-_ ]?one|sync|catchup|catch[-_ ]?up|check[-_ ]?in)"
    r"[-_ ]+(?:with[-_ ]+)?([A-Za-z][A-Za-z'’-]*(?:[-_ ][A-Z][A-Za-z'’-]*)?)",
    re.IGNORECASE,
)


def _clean(name: str) -> str:
    return " ".join(str(name or "").replace("*", "").strip().split())


def _plausible_person(name: str) -> bool:
    n = _clean(name)
    if not n or len(n) < 2 or len(n) > 40:
        return False
    if n.lower() in _NOT_A_PERSON:
        return False
    if n.isupper() and len(n.split()) == 1 and len(n) > 4:
        return False          # HEADINGS, not people
    return bool(re.match(r"^[A-Za-z][A-Za-z.'’\- ]*$", n))


def _from_frontmatter(text: str) -> Tuple[List[str], Optional[str]]:
    """YAML frontmatter, if the recorder app writes it. Highest confidence:
    the app knew who was in the room; we are not guessing."""
    if not text.startswith("---"):
        return [], None
    end = text.find("\n---", 3)
    if end == -1:
        return [], None
    block = text[3:end]
    people: List[str] = []
    title: Optional[str] = None
    for line in block.splitlines():
        m = re.match(r"\s*(participants|attendees|people|with)\s*:\s*(.*)$",
                     line, re.IGNORECASE)
        if m:
            rest = m.group(2).strip()
            if rest.startswith("["):
                rest = rest.strip("[]")
            if rest:
                people += [p.strip().strip("'\"") for p in rest.split(",")]
            continue
        m = re.match(r"\s*-\s+(.+)$", line)
        if m and people is not None and not title:
            cand = m.group(1).strip().strip("'\"")
            if _plausible_person(cand):
                people.append(cand)
            continue
        m = re.match(r"\s*title\s*:\s*(.+)$", line, re.IGNORECASE)
        if m:
            title = m.group(1).strip().strip("'\"")
    return [p for p in map(_clean, people) if _plausible_person(p)], title


def _from_speaker_labels(text: str) -> List[str]:
    """`**Sarah:**` / `Sarah:` at line start — the shape most transcripts take.

    Requires a name to speak at least twice: a single colon-prefixed line is
    as likely to be `Note:` or `Action:` as a person.
    """
    counts: Dict[str, int] = {}
    for line in text.splitlines()[:400]:
        m = _SPEAKER_RE.match(line)
        if not m:
            continue
        name = _clean(m.group(1))
        if _plausible_person(name):
            counts[name] = counts.get(name, 0) + 1
    return [n for n, c in sorted(counts.items(), key=lambda kv: -kv[1]) if c >= 2]


def _from_filename(filename: str) -> List[str]:
    m = _FILENAME_RE.search(filename.rsplit(".", 1)[0].replace("_", " "))
    if not m:
        return []
    name = _clean(m.group(1))
    return [name.title()] if _plausible_person(name) else []


def extract_participants(text: str, filename: str) -> Tuple[List[str], str]:
    """Who is in this recording, and how confident we are in that answer.

    Precedence order, most trustworthy first. Returns (names, source) so the
    approval card can say WHY — "from the file's own metadata" reads very
    differently from "guessed from the filename", and the user deserves to
    know which one they are confirming.
    """
    people, _ = _from_frontmatter(text)
    if people:
        return people[:_MAX_PARTICIPANTS], "frontmatter"

    people = _from_speaker_labels(text)
    if people:
        return people[:_MAX_PARTICIPANTS], "speaker labels"

    people = _from_filename(filename)
    if people:
        return people[:_MAX_PARTICIPANTS], "filename"

    return [], "none"


async def summarize(text: str, filename: str) -> Tuple[str, List[str]]:
    """One line on what this is about, plus topic tags.

    Fast model, per the background-work rule — this runs per file and must not
    re-pin the main model's weights.
    """
    from config import settings
    from utils.json_repair import robust_json_parse
    from services.llm_service import generate_text

    _, title = _from_frontmatter(text)
    system = (
        "You summarise meeting recordings. Reply with JSON only — no prose, no "
        "code fences."
    )
    prompt = (
        "Summarise this meeting recording in one sentence, then list 3-6 short "
        "topic tags.\n\nReturn ONLY JSON: "
        '{"summary": "...", "topics": ["...", "..."]}\n\n'
        f"Filename: {filename}\n"
        f"{'Title: ' + title if title else ''}\n\n"
        f"{text[:_SUMMARY_CHARS]}"
    )
    try:
        # Fast model: this is per-file background work, and pinning the main
        # model's weights here would reintroduce swap pressure on 16 GB Macs.
        raw = await generate_text(
            system, prompt,
            model=settings.fast_model,
            num_predict=300,
            temperature=0.2,
            voice_modifier=False,   # structured output — no tone preamble
        )
        data = robust_json_parse(raw) or {}
        summary = str(data.get("summary") or "").strip()
        topics = [str(t).strip() for t in (data.get("topics") or []) if str(t).strip()]
        if not summary:
            summary = title or filename
        return summary, topics[:8]
    except Exception as e:
        logger.warning(f"[smart-folder] summarise failed for {filename}: {e}")
        return (title or filename), []


async def analyze(text: str, filename: str) -> Dict[str, Any]:
    """Everything we can say about a recording without deciding anything."""
    participants, source = extract_participants(text, filename)
    summary, topics = await summarize(text, filename)
    return {
        "participants": participants,
        "participant_source": source,
        "summary": summary,
        "topics": topics,
    }


async def _candidates_by_identity(analysis: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Score notebooks on what they ARE, not on what they already contain.

    `notebook_router` matches against curator digests, which is right for email
    but leaves Smart Folders unable to do the one job they exist for. Measured
    on a real install 2026-09-16: seven notebooks, only TWO with a digest
    summary — the other five had no sources yet, so they were invisible to
    routing entirely.

    That breaks the primary case by construction. A user creates "Sarah 1:1",
    links a Smart Folder, drops in a recording — and the brand-new notebook has
    no digest, so it can never be suggested. Every card would read "no notebook
    looks like a fit", and the feature would appear broken while working exactly
    as written.

    A notebook's NAME is the strongest signal available here and needs no
    history: a 1:1 notebook is named after the person in the recording. So match
    participants and topics against title and description directly. Digest
    similarity still applies on top — this only ensures an empty notebook is
    reachable at all.
    """
    from storage.notebook_store import notebook_store

    try:
        notebooks = await notebook_store.list() or []
    except Exception as e:
        logger.warning(f"[smart-folder] could not list notebooks: {e}")
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    people = [_norm_token(p) for p in analysis.get("participants") or []]
    topics = [_norm_token(t) for t in analysis.get("topics") or []]

    for nb in notebooks:
        nb_id = nb.get("id")
        title = str(nb.get("title") or nb.get("name") or "")
        haystack = _norm_token(f"{title} {nb.get('description') or ''}")
        if not nb_id or not haystack.strip():
            continue

        score, why = 0.0, []
        # A person's name in the notebook title is close to a declaration of
        # intent. Match on any name part so "Sarah" hits "Sarah Chen 1:1".
        for person in people:
            parts = [w for w in person.split() if len(w) > 2]
            if parts and any(f" {w} " in f" {haystack} " for w in parts):
                score = max(score, 0.90)
                why.append(f"{person.title()} is named in the notebook")
                break
        for topic in topics:
            if len(topic) > 3 and topic in haystack:
                score = max(score, 0.55)
                why.append(f"topic '{topic}' matches the notebook")
                break

        if score > 0:
            out[nb_id] = {"notebook_id": nb_id, "notebook_name": title,
                          "confidence": score, "why": why[0]}
    return out


def _norm_token(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(s or "").lower()).split())


async def _live_notebook_ids() -> set:
    """Digest rows outlive the notebooks they describe — this install had 21
    digests for 7 notebooks. Suggesting a deleted notebook would be worse than
    suggesting nothing."""
    try:
        from storage.notebook_store import notebook_store
        return {n.get("id") for n in (await notebook_store.list() or [])}
    except Exception:
        return set()


async def suggest(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Where this PROBABLY belongs. A suggestion, never an authorisation.

    Participants act as `sender` does for email, so the same correction-learning
    applies: redirect a person's recordings once or twice and their notebook
    starts winning. The bias moves the suggestion — it still cannot file
    anything on its own.
    """
    from services.notebook_router import route

    participant_key = (analysis.get("participants") or [None])[0]
    try:
        decision = await route(
            analysis.get("summary") or "",
            topic_tags=analysis.get("topics") or [],
            sender=participant_key,
        )
    except Exception as e:
        logger.warning(f"[smart-folder] routing failed: {e}")
        return {"suggested_id": None, "suggested_name": None,
                "confidence": 0.0, "alternatives": [], "reason": str(e)[:120]}

    # Merge digest similarity with notebook IDENTITY, keeping the better score
    # per notebook, and drop candidates whose notebook no longer exists.
    live = await _live_notebook_ids()
    merged: Dict[str, Dict[str, Any]] = {}
    for c in ([decision.top] if decision.top else []) + list(decision.alternatives or []):
        if live and c.notebook_id not in live:
            continue
        merged[c.notebook_id] = {"notebook_id": c.notebook_id,
                                 "notebook_name": c.notebook_name,
                                 "confidence": c.confidence,
                                 "why": "matches what the notebook contains"}
    for nb_id, cand in (await _candidates_by_identity(analysis)).items():
        if live and nb_id not in live:
            continue
        if nb_id not in merged or cand["confidence"] > merged[nb_id]["confidence"]:
            merged[nb_id] = cand

    ranked = sorted(merged.values(), key=lambda c: -c["confidence"])
    if not ranked:
        return {"suggested_id": None, "suggested_name": None, "confidence": 0.0,
                "alternatives": [], "reason": decision.reason or "no candidates"}

    top = ranked[0]
    return {
        "suggested_id": top["notebook_id"],
        "suggested_name": top["notebook_name"],
        "confidence": round(top["confidence"], 3),
        "alternatives": [
            {"notebook_id": a["notebook_id"], "notebook_name": a["notebook_name"],
             "confidence": round(a["confidence"], 3)}
            for a in ranked[1:3]
        ],
        "reason": top.get("why") or decision.reason,
    }


async def triage(*, link_id: str, abs_path: str, filename: str, text: str,
                 size: int, mtime: float,
                 content_hash: Optional[str] = None) -> Dict[str, Any]:
    """Decide what happens to one file from a Smart Folder.

    Exactly two outcomes exist, and there is deliberately no third:

      "auto"    a rule the user wrote matches → route it, and say which rule.
      "pending" everything else → a card for a human. Including 0.99 confidence.
                Especially 0.99 confidence.
    """
    from storage.smart_folder_store import smart_folder_store

    analysis = await analyze(text, filename)

    rule = smart_folder_store.match_rule(analysis["participants"], analysis["topics"])
    if rule:
        smart_folder_store.record_rule_hit(rule["id"])
        logger.info(f"[smart-folder] {filename} → rule {rule['id']} → {rule['notebook_id']}")
        return {"action": "auto", "notebook_id": rule["notebook_id"],
                "rule_id": rule["id"], **analysis}

    suggestion = await suggest(analysis)
    item = smart_folder_store.upsert_pending(
        link_id=link_id, abs_path=abs_path, filename=filename,
        size=size, mtime=mtime, content_hash=content_hash,
        participants=analysis["participants"], topics=analysis["topics"],
        summary=analysis["summary"],
        suggested_id=suggestion["suggested_id"],
        suggested_name=suggestion["suggested_name"],
        confidence=suggestion["confidence"],
        alternatives=suggestion["alternatives"],
    )
    logger.info(
        f"[smart-folder] {filename} queued for review "
        f"(participants={analysis['participants']} via {analysis['participant_source']}, "
        f"suggested={suggestion['suggested_name']} @ {suggestion['confidence']})"
    )
    return {"action": "pending", "item_id": item["id"], **analysis, **suggestion}
