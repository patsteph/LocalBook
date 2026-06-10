"""weekly_journal — Phase 13 of v2-information-cortex.

Capability K: once a week per IMAP account, compose a "what you learned
this week" HTML page from `curator_brain` events and SMTP it to the user
via Phase 8's `correspondent_smtp.send_message`.

Server-side composition (no LLM call); deterministic Tailwind-subset
layout matching Phases 10/12/13.A. Failure tolerant — anywhere we hit a
debug-skipped error we keep going so the journal still ships with what
we have.
"""
from __future__ import annotations

import html as _html
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

JOURNAL_INTERVAL_DAYS = 7
JOURNAL_HOUR_WINDOW = (7, 11)  # UTC; tighten with TZ later


def _esc(s: Any) -> str:
    return _html.escape(str(s or ""), quote=True)


async def compose_journal_html(account_email: str) -> Optional[str]:
    """Compose the weekly journal HTML. Returns None when there's nothing
    substantive to send (empty-week guard — we don't email a blank page).
    """
    try:
        from services.curator_brain import curator_brain
        from services.consensus_detector import detect_consensus
        from storage.notebook_store import notebook_store
    except Exception as e:
        logger.debug(f"[weekly_journal] required imports failed: {e}")
        return None

    since_iso = (datetime.now(timezone.utc) - timedelta(days=JOURNAL_INTERVAL_DAYS)).isoformat()
    # P14.C (2026-06-10) — articles are now first-class ingest signals.
    # Pull both `source_ingested` (PDFs, web captures, single-article
    # newsletters) AND `article_ingested` (multi-article newsletters →
    # one event per content article). Track them separately so the
    # journal can show "50 articles across 5 newsletters" instead of
    # "5 new sources" which understates activity tenfold.
    events = curator_brain.recent_events(limit=500, since_iso=since_iso) or []
    raw_source_events = [e for e in events if e.get("action") == "source_ingested"]
    article_events = [e for e in events if e.get("action") == "article_ingested"]
    deep_read_events = [e for e in events if e.get("action") == "deep_read_triggered"]
    ingest_events = raw_source_events + article_events  # for downstream aggregation

    if not ingest_events and not deep_read_events:
        # Empty week — skip the send entirely.
        return None

    # Aggregate by notebook for "Most active notebooks"
    nb_counts: Counter = Counter()
    for ev in ingest_events:
        nbid = ev.get("notebook_id") or "?"
        nb_counts[nbid] += 1
    top_notebooks: List[Dict[str, Any]] = []
    for nbid, count in nb_counts.most_common(5):
        try:
            nb = await notebook_store.get(nbid)
            title = (nb or {}).get("title") or nbid
        except Exception:
            title = nbid
        top_notebooks.append({"notebook_id": nbid, "title": title, "count": count})

    # Consensus over the week
    try:
        consensus = await detect_consensus(since_days=JOURNAL_INTERVAL_DAYS, min_cluster_size=2)
    except Exception as e:
        logger.debug(f"[weekly_journal] consensus skipped: {e}")
        consensus = []

    # Compose
    parts: List[str] = []
    parts.append('<div class="lb-html-artifact p-4 max-w-3xl mx-auto" style="font-family:-apple-system,BlinkMacSystemFont,Roboto,sans-serif">')
    if article_events:
        activity_line = (
            f"{len(article_events)} article(s) from your newsletters, "
            f"plus {len(raw_source_events)} other source(s), across {len(nb_counts)} notebook(s)."
        )
    else:
        activity_line = f"{len(raw_source_events)} new source(s) across {len(nb_counts)} notebook(s)."
    parts.append(
        '<div class="mb-4">'
        '<p class="text-xs uppercase tracking-wide text-gray-500 mb-1">LocalBook · Weekly journal</p>'
        f'<p class="text-base font-semibold text-gray-900 mb-1">Here\'s what came through this week</p>'
        f'<p class="text-sm text-gray-700">{activity_line}'
        + (f' {len(deep_read_events)} deep-reads fired automatically.' if deep_read_events else '')
        + '</p></div>'
    )

    if consensus:
        parts.append(
            '<h3 class="text-base font-semibold text-gray-800 mb-2">Top converging topics</h3>'
            '<ul class="mb-4">'
        )
        for cl in consensus[:6]:
            parts.append(
                '<li class="text-sm text-gray-800 mb-1">'
                f'<strong>{_esc(cl.topic_label or "(unlabeled)")}</strong> '
                f'<span class="text-xs text-gray-500">— {cl.size} sources</span></li>'
            )
        parts.append('</ul>')

    if top_notebooks:
        parts.append(
            '<h3 class="text-base font-semibold text-gray-800 mb-2">Most active notebooks</h3>'
            '<ul class="mb-4">'
        )
        for nb in top_notebooks:
            parts.append(
                '<li class="text-sm text-gray-800 mb-1">'
                f'<strong>{_esc(nb["title"])}</strong> '
                f'<span class="text-xs text-gray-500">— {nb["count"]} new sources</span></li>'
            )
        parts.append('</ul>')

    if deep_read_events:
        parts.append(
            '<h3 class="text-base font-semibold text-gray-800 mb-2">Deep reads triggered</h3>'
            '<ul class="mb-4">'
        )
        for ev in deep_read_events[:6]:
            payload = ev.get("payload") or {}
            query = payload.get("query") or "(topic)"
            parts.append(
                '<li class="text-sm text-gray-800 mb-1">'
                f'Researching <strong>{_esc(query)}</strong></li>'
            )
        parts.append('</ul>')

    parts.append(
        '<p class="text-xs text-gray-500 mt-6">— LocalBook Correspondent</p>'
        '</div>'
    )
    return "".join(parts)


def should_send_now(account: Dict[str, Any]) -> bool:
    """Gate: enabled + ≥ 7 days since last send + UTC hour in 7-11 window."""
    if not account.get("weekly_journal_enabled", True):
        return False
    if not account.get("smtp_host"):
        return False
    last_iso = account.get("last_journal_at")
    now = datetime.now(timezone.utc)
    if last_iso:
        try:
            last = datetime.fromisoformat(last_iso)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < JOURNAL_INTERVAL_DAYS * 24 * 3600:
                return False
        except Exception:
            # Bad date — treat as never sent.
            pass
    return JOURNAL_HOUR_WINDOW[0] <= now.hour <= JOURNAL_HOUR_WINDOW[1]


async def send_journal_for_account(account: Dict[str, Any]) -> bool:
    """Compose + SMTP-send for one account. Updates last_journal_at on
    success. Returns True if a journal was actually emailed."""
    if not should_send_now(account):
        return False
    html = await compose_journal_html(account.get("email", ""))
    if not html:
        return False
    try:
        from services.correspondent_smtp import send_message
        from services.credential_locker import update_imap_state
        ok = await send_message(
            account,
            to=account.get("email", ""),
            subject="Your LocalBook weekly journal",
            body_text=html,
        )
        if ok:
            await update_imap_state(
                email=account["email"],
                last_journal_at=datetime.now(timezone.utc).isoformat(),
            )
        return ok
    except Exception as e:
        logger.warning(f"[weekly_journal] send failed for {account.get('email')}: {e}")
        return False
