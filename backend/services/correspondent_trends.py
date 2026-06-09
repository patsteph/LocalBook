"""correspondent_trends — hot/cold topic computation across newsletter sources.

I6 (2026-06-09): Aggregates `topic_tags` and senders across all Correspondent-
ingested sources (format='email' or 'forward'). Compares the last N days to
the prior N-day window to produce a delta per topic. Used by the
@correspondent `whats_hot` / `whats_cold` / `summarize_recent` chat intents.

Cheap: pure SQLite/file reads + Python aggregation. No LLM hops. Runs in
millis on typical workloads.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def _gather_newsletter_sources(since: datetime) -> List[Dict[str, Any]]:
    """All sources where format in ('email', 'forward') AND created_at >= since.

    Returns enriched dicts including topic_tags, sender, created_at,
    notebook_id, id, subject. Best-effort: returns [] on any failure.

    Exported (despite the underscore prefix) — `_stream_correspondent`
    uses it for recent / show_sender / quiet_senders / move_source.
    """
    try:
        from storage.source_store import source_store
        all_by_nb = await source_store.list_all()
    except Exception as e:
        logger.debug(f"[correspondent_trends] source list failed: {e}")
        return []

    out: List[Dict[str, Any]] = []
    since_iso = since.isoformat()
    for nb_id, sources in (all_by_nb or {}).items():
        for s in sources or []:
            fmt = (s.get("format") or "").lower()
            if fmt not in ("email", "forward"):
                continue
            created = s.get("created_at") or ""
            if created < since_iso:
                continue
            out.append({
                "id": s.get("id"),
                "notebook_id": nb_id,
                "sender": s.get("sender") or s.get("original_sender") or "",
                "subject": s.get("subject") or s.get("filename") or "",
                "summary": s.get("summary") or "",
                "topic_tags": s.get("topic_tags") or [],
                "created_at": created,
            })
    return out


async def compute_topic_trends(*, days: int = 7) -> List[Dict[str, Any]]:
    """Return per-topic stats over `days` recent vs prior `days` baseline.

    Each entry: `{topic, recent, baseline, delta}` where:
      - recent = mentions in the last `days` days
      - baseline = mentions in days `days+1` through `2*days`
      - delta = recent - baseline (positive = hot, negative = cold)

    Sorted by absolute delta descending. Capped at 30 entries.
    """
    now = datetime.utcnow()
    recent_cutoff = now - timedelta(days=days)
    baseline_cutoff = now - timedelta(days=2 * days)

    sources = await _gather_newsletter_sources(baseline_cutoff)
    if not sources:
        return []

    recent_counts: Counter[str] = Counter()
    baseline_counts: Counter[str] = Counter()
    recent_iso = recent_cutoff.isoformat()
    for s in sources:
        tags = [str(t).strip() for t in (s.get("topic_tags") or []) if t]
        if not tags:
            continue
        bucket = recent_counts if s["created_at"] >= recent_iso else baseline_counts
        for tag in tags:
            bucket[tag] += 1

    all_topics = set(recent_counts) | set(baseline_counts)
    out: List[Dict[str, Any]] = []
    for topic in all_topics:
        r = int(recent_counts.get(topic, 0))
        b = int(baseline_counts.get(topic, 0))
        # Filter noise: skip topics that appear once total
        if r + b < 2:
            continue
        out.append({"topic": topic, "recent": r, "baseline": b, "delta": r - b})

    out.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return out[:30]


async def summarize_recent_intake(*, days: int = 7) -> str:
    """Compose a markdown summary of newsletters ingested over `days`.

    Renders:
      - Counts (total, per sender, per notebook)
      - A mermaid mindmap (period root → top senders → recent subjects)
      - A short prose line about what's been busy

    Reply text is meant to be consumed by ChatMessageBubble (which routes
    through MarkdownArtifactRenderer for fence dispatch).
    """
    import re as _re

    def _label(s: str, n: int = 40) -> str:
        s = _re.sub(r"[\(\)\[\]\{\}\"`:,]+", " ", str(s or ""))
        s = _re.sub(r"\s+", " ", s).strip()
        return s[:n] or "—"

    since = datetime.utcnow() - timedelta(days=days)
    sources = await _gather_newsletter_sources(since)
    if not sources:
        return f"📭 No newsletter activity in the last {days} day(s)."

    # Counts
    sender_counts: Counter[str] = Counter()
    notebook_counts: Counter[str] = Counter()
    for s in sources:
        if s.get("sender"):
            sender_counts[s["sender"]] += 1
        if s.get("notebook_id"):
            notebook_counts[s["notebook_id"]] += 1

    top_senders = sender_counts.most_common(6)
    top_nbs = notebook_counts.most_common(5)

    # Resolve notebook names
    try:
        from storage.notebook_store import notebook_store
        nbs = {nb["id"]: nb.get("title") or "(unnamed)" for nb in (await notebook_store.list() or [])}
    except Exception:
        nbs = {}

    lines: List[str] = []
    lines.append(f"**📬 Last {days} days — {len(sources)} newsletter(s) ingested**\n")

    if top_nbs:
        per_nb = ", ".join(f"`{nbs.get(nb_id, nb_id[:8])}` ({n})" for nb_id, n in top_nbs)
        lines.append(f"**Routed to:** {per_nb}")

    if top_senders:
        lines.append(f"\n**Most active senders:**")
        for sender, count in top_senders:
            lines.append(f"- `{sender[:60]}` — {count} email{'s' if count != 1 else ''}")

    # Mindmap of period → senders → recent subjects
    mm = ["mindmap", f"  root((Last {days} days))"]
    for sender, _ in top_senders[:5]:
        mm.append(f"    {_label(sender, 36)}")
        subjects = [
            s["subject"] for s in sources
            if s.get("sender") == sender and s.get("subject")
        ][:3]
        for subj in subjects:
            mm.append(f"      {_label(subj, 50)}")
    lines.append("\n```mermaid\n" + "\n".join(mm) + "\n```")

    return "\n".join(lines)
