"""@collector intents for linked folders and the smart-folder review queue.

Split out rather than added to `_collector.py`, which is already ~1,500 lines
and well past the 800-line target. Folder watching is a distinct responsibility
from source collection, so it gets its own module and `_collector.py` gains two
lines of dispatch.

Every handler here returns `(reply_markdown, follow_ups)` and does NOT mutate
anything the user did not ask for. In particular: **no intent in this file
approves a pending recording.** Approval lives in the review UI, where the user
can see who is in the recording and where it would go before deciding. A chat
message is too blunt an instrument to authorise filing a 1:1 transcript.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FOLDER_INTENTS = {"folder_status", "folder_scan_now", "folder_review_queue",
                  "folder_rules"}


def _pretty(path: str) -> str:
    try:
        return str(path).replace(str(Path.home()), "~", 1)
    except Exception:
        return str(path)


def _ago(iso: Optional[str]) -> str:
    if not iso:
        return "never"
    from datetime import datetime
    try:
        then = datetime.fromisoformat(iso)
        mins = int((datetime.utcnow() - then).total_seconds() // 60)
    except Exception:
        return "unknown"
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    if mins < 1440:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


async def handle(intent: str, params: Dict[str, Any],
                 notebook_id: str) -> Tuple[str, List[str]]:
    """Dispatch a folder intent. Returns (reply, follow_ups)."""
    from storage.folder_link_store import folder_link_store
    from storage.smart_folder_store import smart_folder_store

    if intent == "folder_status":
        return await _status(notebook_id, folder_link_store, smart_folder_store)
    if intent == "folder_scan_now":
        return await _scan_now(notebook_id, folder_link_store)
    if intent == "folder_review_queue":
        return await _review_queue(notebook_id, smart_folder_store)
    if intent == "folder_rules":
        return await _rules(smart_folder_store)
    return "", []


async def _status(notebook_id, folder_link_store, smart_folder_store):
    mine = folder_link_store.list_links(notebook_id)
    every = folder_link_store.list_links()
    waiting = smart_folder_store.count_pending()

    if not every:
        return (
            "No folders are linked yet.\n\n"
            "Linking one means anything that lands in it — recordings, notes, "
            "documents — becomes a source here automatically. "
            "Set it up in **Settings → Folders**, or right-click this notebook "
            "and choose **Linked folders**.",
            ["What can a linked folder do?", "Show my collection status"],
        )

    lines: List[str] = []
    if mine:
        lines.append(f"**{len(mine)} folder{'s' if len(mine) != 1 else ''} feeding this notebook:**\n")
        for l in mine:
            # `stats` and `exists` are decorations the API layer adds; the store
            # returns the raw row, so compute them here rather than assuming a
            # shape this caller never gets.
            stats = folder_link_store.stats(l["id"])
            bits = [f"`{_pretty(l['path'])}`"]
            bits.append(f"{stats['ingested']} file{'s' if stats['ingested'] != 1 else ''} added")
            bits.append(f"checked {_ago(l['last_scan_at'])}")
            if not l["enabled"]:
                bits.append("**paused**")
            if not Path(l["path"]).is_dir():
                bits.append("⚠️ **folder is missing** — the drive may be unmounted")
            if l["last_error"]:
                bits.append(f"⚠️ {l['last_error'][:80]}")
            lines.append(f"- {' · '.join(bits)}")
    else:
        lines.append("No folders are linked to **this** notebook.\n")

    others = [l for l in every if l.get("notebook_id") != notebook_id]
    if others:
        smart = [l for l in others if l["is_smart"]]
        lines.append(
            f"\nElsewhere: {len(others)} other linked folder"
            f"{'s' if len(others) != 1 else ''}"
            + (f", {len(smart)} of them smart" if smart else "") + "."
        )
    if waiting:
        lines.append(
            f"\n**{waiting} recording{'s' if waiting != 1 else ''} waiting for you** "
            f"in Settings → Folders → Review."
        )

    follow_ups = ["Scan my folders now"]
    if waiting:
        follow_ups.insert(0, "What's waiting for review?")
    follow_ups.append("Show my routing rules")
    return "\n".join(lines), follow_ups


async def _scan_now(notebook_id, folder_link_store):
    """Foreground scan — the user asked, so it runs now rather than at idle."""
    from services.folder_watcher import folder_watcher

    links = folder_link_store.list_links(notebook_id) or folder_link_store.list_links()
    if not links:
        return ("There are no linked folders to scan. You can set one up in "
                "**Settings → Folders**.", [])

    ingested = failed = review = 0
    problems: List[str] = []
    for l in links:
        if not l["enabled"]:
            continue
        report = await folder_watcher.scan_link(l["id"])
        ingested += report.ingested
        failed += report.failed
        review += report.pending_review
        if report.error:
            problems.append(f"`{_pretty(l['path'])}`: {report.error[:100]}")

    if ingested == 0 and review == 0 and not problems:
        return (f"Checked {len(links)} folder{'s' if len(links) != 1 else ''} — "
                f"nothing new.", ["Show my folder status"])

    parts = []
    if ingested:
        parts.append(f"**Added {ingested} file{'s' if ingested != 1 else ''}.**")
    if review:
        parts.append(f"{review} recording{'s' if review != 1 else ''} need"
                     f"{'s' if review == 1 else ''} a destination — "
                     f"see Settings → Folders → Review.")
    if failed:
        parts.append(f"{failed} could not be read.")
    if problems:
        parts.append("\n".join(f"⚠️ {p}" for p in problems))
    return " ".join(parts), ["What's waiting for review?", "Show my folder status"]


async def _review_queue(notebook_id, smart_folder_store):
    items = smart_folder_store.list_pending()
    if not items:
        return ("Nothing is waiting for review.", ["Show my folder status"])

    lines = [f"**{len(items)} recording{'s' if len(items) != 1 else ''} waiting "
             f"for a destination:**\n"]
    for it in items[:8]:
        who = ", ".join(it["participants"]) if it["participants"] else "no one identified"
        where = it["suggested_name"] or "no obvious notebook"
        conf = f" ({it['confidence']:.0%} match)" if it["confidence"] else ""
        lines.append(f"- **{it['filename']}** — {who} → suggested *{where}*{conf}")
    if len(items) > 8:
        lines.append(f"- …and {len(items) - 8} more")

    # Deliberately does NOT offer to approve from chat: the whole point of the
    # queue is that a human sees who is in the recording and where it would go
    # before it moves. A chat confirmation cannot show that.
    lines.append("\nApprove or redirect them in **Settings → Folders → Review** — "
                 "that view shows who was detected and lets you set a routing rule.")
    unrouted = smart_folder_store.unrouted_by_participant()
    for u in unrouted[:2]:
        lines.append(f"\n💡 {u['count']} unfiled recordings involve **{u['participant']}**. "
                     f"A notebook for them would give these somewhere to go.")
    return "\n".join(lines), ["Show my routing rules", "Show my folder status"]


async def _rules(smart_folder_store):
    rules = smart_folder_store.list_rules()
    if not rules:
        return (
            "No routing rules yet — so every recording from a smart folder waits "
            "for you to place it.\n\nWhen you approve one you can choose "
            "*always route these*, which creates a rule.",
            ["What's waiting for review?"],
        )

    from storage.notebook_store import notebook_store
    try:
        titles = {n["id"]: n.get("title") or "Untitled"
                  for n in (await notebook_store.list() or [])}
    except Exception:
        titles = {}

    lines = [f"**{len(rules)} routing rule{'s' if len(rules) != 1 else ''}** — "
             f"these are the only things that file a recording without asking:\n"]
    for r in rules:
        scope = []
        if r["scope_participants"]:
            scope.append("with " + " and ".join(p.title() for p in r["scope_participants"]))
        if r["scope_topics"]:
            scope.append("about " + ", ".join(r["scope_topics"][:3]))
        target = titles.get(r["notebook_id"], "a deleted notebook")
        state = "" if r["enabled"] else " *(paused)*"
        hits = (f"{r['hit_count']} routed" if r["hit_count"]
                else "nothing routed yet")
        lines.append(f"- Recordings {' '.join(scope)} → **{target}** — {hits}{state}")
    lines.append("\nPause or revoke any of them in **Settings → Folders → Rules**.")
    return "\n".join(lines), ["What's waiting for review?", "Show my folder status"]
