"""weekly_journal_agent — Phase 13 of v2-information-cortex.

Background singleton that periodically iterates each enabled IMAP account
and, when the weekly cadence is due, composes the journal HTML and sends
it via Phase 8's SMTP outbound.

Pattern mirrors `CorrespondentAgent`: asyncio task + interval sleep. The
weekly gate lives in `services/weekly_journal.should_send_now`; this
agent's only job is to wake every N hours and call the per-account path.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 6 * 60 * 60  # 6 hours


class WeeklyJournalAgent:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="weekly-journal-scheduler")
        logger.info("[weekly_journal] scheduler started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[weekly_journal] scheduler stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[weekly_journal] tick failed: {e}")
            try:
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        from services.credential_locker import list_imap_accounts, get_imap_account
        from services.weekly_journal import send_journal_for_account

        accounts = await list_imap_accounts()
        for acc in accounts:
            if not acc.enabled:
                continue
            full = await get_imap_account(acc.email)
            if not full:
                continue
            try:
                sent = await send_journal_for_account(full)
                if sent:
                    logger.info(f"[weekly_journal] sent journal to {acc.email}")
            except Exception as e:
                logger.debug(f"[weekly_journal] send for {acc.email} failed: {e}")


weekly_journal_agent = WeeklyJournalAgent()
