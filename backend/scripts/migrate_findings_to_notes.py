"""One-time migration: convert Findings → Notes.

Tier 5 (2026-06-01) — Findings are being removed from the app. Each
existing Finding becomes a Note with `source_type='chat_answer'` so the
user keeps their saved content and can now edit it (Notes are editable;
Findings were write-once bookmarks).

Run once after deploying the Save-as-Note refactor. Idempotent: re-runs
skip findings that already have a corresponding note (matched by ID +
content hash).

Usage:
    python -m backend.scripts.migrate_findings_to_notes
    # or
    cd backend && python scripts/migrate_findings_to_notes.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Add backend/ to the path so storage modules resolve.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _build_markdown(finding) -> str:
    """Convert a Finding's content blob to the markdown body of a Note.

    Findings have heterogeneous shapes — `type='answer'` carries
    {question, answer, citations}; `type='visual'` carries raw SVG; etc.
    The migration produces a sensible markdown body per type, preserving
    everything the user actually saw when they bookmarked it.
    """
    content = finding.content or {}
    t = finding.type

    if t == "answer":
        question = content.get("question") or ""
        answer = content.get("answer") or ""
        citations = content.get("citations") or []
        parts = []
        if question:
            parts.append(f"**Q:** {question}\n")
        if answer:
            parts.append(answer)
        if citations:
            parts.append("\n---\n**Citations:**")
            for i, c in enumerate(citations, start=1):
                if isinstance(c, dict):
                    src = c.get("source") or c.get("filename") or "Unknown"
                    snip = c.get("snippet") or c.get("text") or ""
                    parts.append(f"- [{i}] **{src}** — {snip[:200]}")
                else:
                    parts.append(f"- [{i}] {str(c)[:200]}")
        return "\n".join(parts).strip()

    if t == "visual":
        svg = content.get("svg") or content.get("svg_markup") or ""
        title = content.get("title") or finding.title
        if svg:
            return f"# {title}\n\n```svg\n{svg[:8000]}\n```"
        return f"# {title}"

    if t == "highlight":
        text = content.get("text") or content.get("highlighted_text") or ""
        src = content.get("source") or content.get("filename") or ""
        return f"> {text}\n\n— {src}" if src else f"> {text}"

    # source / note / unknown — best-effort dump
    if isinstance(content, dict):
        try:
            return f"```json\n{json.dumps(content, indent=2)[:4000]}\n```"
        except Exception:
            return str(content)[:4000]
    return str(content)[:4000]


async def migrate() -> dict:
    """Walk findings_store, create one note per finding. Returns counts."""
    from storage.findings_store import get_findings_store
    from storage.note_store import note_store
    from storage.notebook_store import notebook_store

    findings_store = get_findings_store()

    counts = {
        "notebooks_scanned": 0,
        "findings_total": 0,
        "notes_created": 0,
        "already_migrated": 0,
        "errors": 0,
    }

    # Load existing migration marker file so we don't double-migrate.
    marker_path = ROOT / "data" / "_migrated_findings.json"
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        migrated_ids: set[str] = set()
        if marker_path.exists():
            with open(marker_path) as f:
                migrated_ids = set(json.load(f) or [])
    except Exception:
        migrated_ids = set()

    notebooks = await notebook_store.list()
    for nb in notebooks:
        nb_id = nb.get("id") or nb.get("notebook_id")
        if not nb_id:
            continue
        counts["notebooks_scanned"] += 1

        findings = await findings_store.get_findings(nb_id, limit=10000)
        for f in findings:
            counts["findings_total"] += 1
            if f.id in migrated_ids:
                counts["already_migrated"] += 1
                continue

            try:
                body = _build_markdown(f)
                title = f.title or f"Saved {f.type}"
                tags = list(f.tags or []) + [f"migrated-from-finding"]
                await note_store.create(
                    notebook_id=nb_id,
                    title=title,
                    content_markdown=body,
                    source_type="chat_answer" if f.type == "answer" else "typed",
                    note_type="note",
                    tags=tags,
                )
                migrated_ids.add(f.id)
                counts["notes_created"] += 1
            except Exception as e:
                counts["errors"] += 1
                print(f"[migrate-findings] {nb_id}/{f.id} failed: {e}")

    # Persist marker so re-runs are idempotent.
    try:
        with open(marker_path, "w") as f:
            json.dump(sorted(migrated_ids), f)
    except Exception as e:
        print(f"[migrate-findings] warning: couldn't write marker: {e}")

    return counts


if __name__ == "__main__":
    counts = asyncio.run(migrate())
    print("[migrate-findings] complete:")
    for k, v in counts.items():
        print(f"  {k}: {v}")
