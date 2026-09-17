"""Background check for upstream changes to installed companions.

Pinning a companion's installer to a commit protects the user from a moving
`curl | bash` target. Without something like this it also silently freezes
them: upstream could fix a real bug and nobody would ever hear about it.

So the pin stays and the check is what moves — daily, cheap, and it never
advances anything on its own. Accepting an update remains an explicit click
with the diff in front of the user.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SCHEDULE_ID = "companion-updates"
CHECK_INTERVAL_SECONDS = 24 * 3600


class CompanionUpdateChecker:
    def __init__(self) -> None:
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start_background_task(self) -> None:
        if self._running:
            return
        self._running = True
        from utils.tasks import safe_create_task
        self._task = safe_create_task(self._loop(), name="companion-updates")

    def stop(self) -> None:
        self._running = False

    async def _loop(self) -> None:
        # Well after startup: this is the least urgent thing the app does, and
        # it makes an outbound request.
        await asyncio.sleep(300)
        from services.enrichment_jobs import EnrichmentJob, JobTier
        from services.enrichment_worker import enrichment_worker

        async def _run():
            try:
                from services.companions import check_all_for_updates
                await asyncio.to_thread(check_all_for_updates)
            except Exception as e:
                logger.warning(f"[companion-updates] check failed: {e}")

        while self._running:
            try:
                from services.schedule_store import schedule_store
                if schedule_store.is_enabled(SCHEDULE_ID):
                    enrichment_worker.enqueue(EnrichmentJob(
                        key=SCHEDULE_ID,
                        tier=JobTier.DAYDREAM,
                        factory=_run,
                        label="companion-updates",
                    ))
                interval = schedule_store.get_interval(SCHEDULE_ID, CHECK_INTERVAL_SECONDS)
            except Exception as e:
                logger.warning(f"[companion-updates] loop error (continuing): {e}")
                interval = CHECK_INTERVAL_SECONDS
            await asyncio.sleep(max(600, float(interval)))


companion_update_checker = CompanionUpdateChecker()
