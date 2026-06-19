"""digest_composer — Phase 4 Tier 2 / G (2026-06-10).

Weekly digest composer for senders in `bundle_mode='weekly_digest'`.

Pipeline (per design G):
  1. Worker wakes every 6h
  2. Find senders whose digest_day == today_isoweekday and have pending items
  3. For each such sender:
     a. Parse each pending raw_bytes
     b. Build a server-composed HTML digest (Tailwind subset, strict)
        listing each newsletter as a section
     c. Ingest the digest as a single newsletter source (uses existing
        correspondent_processor.ingest_newsletter)
     d. Clear pending_digest rows
"""
from __future__ import annotations

import asyncio
import base64
import html as _html
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.tasks import safe_create_task

logger = logging.getLogger(__name__)

# Worker cadence: wake every 6h. Senders ship their digest on the matching
# ISO weekday once per cycle. We DON'T need a precise scheduler — 6h
# granularity is fine for weekly cadence.
WORKER_INTERVAL_SECONDS = 6 * 60 * 60


async def _maybe_ship_digest_for_sender(sender_email: str) -> bool:
    """Check if today is `sender_email`'s digest day and there are
    pending items. If yes, compose + ingest + clear. Returns True if
    a digest shipped."""
    from services.sender_frequency import get_settings, list_pending, clear_pending
    from services.correspondent_processor import parse_email, classify_email, ingest_newsletter

    settings = get_settings(sender_email)
    if not settings or settings.get("bundle_mode") != "weekly_digest":
        return False
    digest_day = int(settings.get("digest_day") or 1)
    today_iso_dow = datetime.utcnow().isoweekday()
    if today_iso_dow != digest_day:
        return False

    pending = list_pending(sender_email)
    if not pending:
        return False

    # Parse all pending items
    parsed_items: List[Dict[str, Any]] = []
    notebook_id: Optional[str] = None
    for p in pending:
        try:
            raw = base64.b64decode(p.get("raw_bytes_b64") or "")
        except Exception:
            continue
        parsed = parse_email(raw)
        if not parsed:
            continue
        parsed_items.append({
            "id": p["id"],
            "parsed": parsed,
            "received_at": p.get("received_at"),
            "email_account": p.get("email_account"),
            "notebook_id_hint": p.get("notebook_id"),
        })
        notebook_id = notebook_id or p.get("notebook_id")

    if not parsed_items:
        # All garbage — still clear so we don't keep failing.
        clear_pending([p["id"] for p in pending])
        return False

    # Compose the HTML body
    digest_html = _compose_digest_html(sender_email, parsed_items)
    digest_text = _compose_digest_text(sender_email, parsed_items)
    period_start = min(it["received_at"] for it in parsed_items if it["received_at"])[:10]
    period_end = datetime.utcnow().date().isoformat()
    subject = f"Weekly digest: {sender_email} ({period_start} → {period_end})"

    # Build a synthetic ParsedEmail-like envelope. The existing
    # ingest_newsletter takes a ParsedEmail, so we use the most recent
    # pending item as the "carrier" and override its body to be the
    # composed digest.
    carrier = parsed_items[-1]["parsed"]
    # Mutate text/html in-place — caller doesn't need the original.
    try:
        carrier.text_body = digest_text
        carrier.html_body = digest_html
        carrier.subject = subject
        # message_id stays the carrier's — keeps dedup sane.
    except AttributeError:
        # Some parsed_email implementations use dataclass / frozen objects;
        # synthesize a fresh one with the same fields.
        try:
            from dataclasses import replace
            carrier = replace(
                carrier,
                text_body=digest_text,
                html_body=digest_html,
                subject=subject,
            )
        except Exception:
            logger.warning(f"[digest_composer] couldn't override parsed carrier for {sender_email}")
            return False

    # Classify + route. We use the existing pipeline so the digest gets
    # the same auto-route treatment as a normal newsletter, including
    # sender bias (which by definition matches this sender's history).
    try:
        classification = await classify_email(carrier)
    except Exception as e:
        logger.warning(f"[digest_composer] classify failed for {sender_email}: {e}")
        return False

    # Determine target notebook: use notebook_id from any held item if
    # present, else let the router decide via ingest_newsletter (caller
    # is on the IMAP side which normally does routing). Here we shortcut
    # by reusing the most-recent notebook association.
    if not notebook_id:
        # Fall through to letting the router pick. We can't call
        # ingest_newsletter directly without a notebook, so do a quick
        # router lookup.
        try:
            from services.notebook_router import route as route_email
            decision = await route_email(
                classification_summary=classification.summary,
                topic_tags=classification.topic_tags,
                sender=sender_email,
            )
            if decision.decision == "route" and decision.top:
                notebook_id = decision.top.notebook_id
        except Exception as e:
            logger.debug(f"[digest_composer] router lookup failed: {e}")

    if not notebook_id:
        logger.info(f"[digest_composer] no target notebook for {sender_email}; deferring")
        return False

    try:
        source_id = await ingest_newsletter(notebook_id, carrier, classification)
    except Exception as e:
        logger.warning(f"[digest_composer] ingest failed for {sender_email}: {e}")
        return False

    if not source_id:
        return False

    # Success — clear the buffer
    clear_pending([p["id"] for p in pending])
    logger.info(
        f"[digest_composer] shipped digest for {sender_email}: "
        f"{len(parsed_items)} items → source {source_id[:8]}"
    )
    return True


def _compose_digest_html(sender_email: str, items: List[Dict[str, Any]]) -> str:
    """Server-composed HTML (Tailwind subset). One section per buffered
    newsletter. Used by ingest_newsletter as the display body."""
    parts: List[str] = []
    parts.append('<div class="lb-html-artifact p-4 max-w-3xl mx-auto">')
    parts.append(
        '<p class="text-xs uppercase tracking-wide text-gray-500 mb-1">Weekly digest</p>'
        f'<p class="text-lg font-semibold text-gray-900 mb-4">{_html.escape(sender_email)}</p>'
    )
    parts.append(
        f'<p class="text-sm text-gray-700 mb-4">'
        f'{len(items)} newsletter(s) bundled this week.'
        '</p>'
    )
    for i, it in enumerate(items, 1):
        parsed = it["parsed"]
        subj = getattr(parsed, "subject", "") or "(no subject)"
        body_text = getattr(parsed, "text_body", "") or ""
        if not body_text and getattr(parsed, "html_body", ""):
            from services.correspondent_processor import html_to_clean_text
            body_text = html_to_clean_text(parsed.html_body)
        excerpt = body_text[:600] + ("…" if len(body_text) > 600 else "")
        received = (it.get("received_at") or "")[:10]
        parts.append(
            '<div class="rounded-lg border border-gray-200 bg-white p-3 mb-3">'
            f'<p class="text-xs uppercase tracking-wide text-gray-500 mb-1">{_html.escape(received)} · #{i}</p>'
            f'<p class="text-sm font-medium text-gray-800 mb-2">{_html.escape(subj)}</p>'
            f'<p class="text-sm text-gray-700">{_html.escape(excerpt)}</p>'
            '</div>'
        )
    parts.append('</div>')
    return "".join(parts)


def _compose_digest_text(sender_email: str, items: List[Dict[str, Any]]) -> str:
    """Plain-text variant for RAG indexing. Concatenates all source
    bodies with separator markers."""
    lines: List[str] = [
        f"Weekly digest: {sender_email}",
        f"{len(items)} newsletter(s) bundled this week.",
        "",
    ]
    for i, it in enumerate(items, 1):
        parsed = it["parsed"]
        subj = getattr(parsed, "subject", "") or "(no subject)"
        body_text = getattr(parsed, "text_body", "") or ""
        if not body_text and getattr(parsed, "html_body", ""):
            from services.correspondent_processor import html_to_clean_text
            body_text = html_to_clean_text(parsed.html_body)
        received = (it.get("received_at") or "")[:10]
        lines.append(f"--- #{i} · {received} · {subj} ---")
        lines.append("")
        lines.append(body_text)
        lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# Scheduler — background task started from main.py lifespan
# ─────────────────────────────────────────────────────────────────────────


class DigestSchedulerAgent:
    """Wakes every WORKER_INTERVAL_SECONDS; ships digests for any sender
    whose digest_day == today and has pending items."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = safe_create_task(self._loop(), name="digest-scheduler")
        logger.info("[digest_composer] scheduler started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[digest_composer] scheduler stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[digest_composer] tick failed: {e}")
            try:
                await asyncio.sleep(WORKER_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        """One scheduler tick — check every sender in weekly_digest mode."""
        from services.sender_frequency import list_settings
        all_settings = list_settings()
        digest_senders = [s for s in all_settings if (s.get("bundle_mode") == "weekly_digest")]
        for s in digest_senders:
            try:
                await _maybe_ship_digest_for_sender(s["sender_email"])
            except Exception as e:
                logger.debug(f"[digest_composer] ship failed for {s.get('sender_email')}: {e}")


digest_scheduler = DigestSchedulerAgent()


async def force_ship_now(sender_email: str) -> bool:
    """Manual trigger for testing / @correspondent force-digest. Bypasses
    the digest_day check and ships whatever's pending right now."""
    from services.sender_frequency import get_settings, list_pending, clear_pending
    from services.correspondent_processor import parse_email, classify_email, ingest_newsletter
    from services.notebook_router import route as route_email

    settings = get_settings(sender_email)
    if not settings or settings.get("bundle_mode") != "weekly_digest":
        return False
    pending = list_pending(sender_email)
    if not pending:
        return False

    parsed_items: List[Dict[str, Any]] = []
    notebook_id: Optional[str] = None
    for p in pending:
        try:
            raw = base64.b64decode(p.get("raw_bytes_b64") or "")
        except Exception:
            continue
        parsed = parse_email(raw)
        if not parsed:
            continue
        parsed_items.append({
            "id": p["id"],
            "parsed": parsed,
            "received_at": p.get("received_at"),
        })
        notebook_id = notebook_id or p.get("notebook_id")
    if not parsed_items:
        clear_pending([p["id"] for p in pending])
        return False

    digest_html = _compose_digest_html(sender_email, parsed_items)
    digest_text = _compose_digest_text(sender_email, parsed_items)
    period_start = min(it["received_at"] for it in parsed_items if it["received_at"])[:10]
    subject = f"Weekly digest: {sender_email} ({period_start} → {datetime.utcnow().date().isoformat()})"
    carrier = parsed_items[-1]["parsed"]
    try:
        carrier.text_body = digest_text
        carrier.html_body = digest_html
        carrier.subject = subject
    except AttributeError:
        from dataclasses import replace
        carrier = replace(carrier, text_body=digest_text, html_body=digest_html, subject=subject)

    classification = await classify_email(carrier)
    if not notebook_id:
        decision = await route_email(
            classification_summary=classification.summary,
            topic_tags=classification.topic_tags,
            sender=sender_email,
        )
        if decision.decision == "route" and decision.top:
            notebook_id = decision.top.notebook_id
    if not notebook_id:
        return False
    source_id = await ingest_newsletter(notebook_id, carrier, classification)
    if source_id:
        clear_pending([p["id"] for p in pending])
        return True
    return False
