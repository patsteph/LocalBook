"""Notice the encrypted volume disappearing while the app runs — LB-11 matrix row
"force-eject".

The gate is decided once, at boot. Without this, a volume ejected mid-session
(Finder, a pulled drive, `hdiutil detach -force`) leaves the mount point as an
ordinary empty directory and the running app carries on writing into it:
plaintext, outside the encryption, and in the way of the next mount. This loop
turns that into the same honest LOCKED state a failed mount at boot produces.

A `stat` of the sentinel every few seconds, and only when encryption is on — the
common case (off) costs one flag check.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SCHEDULE_ID = "volume-watch"
DEFAULT_INTERVAL_SECONDS = 15
MIN_INTERVAL_SECONDS = 5


class VolumeWatch:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._task is not None:
            return
        self._running = True
        from utils.tasks import safe_create_task

        self._task = safe_create_task(self._loop(), name=SCHEDULE_ID)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            interval = DEFAULT_INTERVAL_SECONDS
            try:
                from services import volume_gate
                from services.schedule_store import schedule_store

                if schedule_store.is_enabled(SCHEDULE_ID):
                    await asyncio.to_thread(volume_gate.check_still_mounted)
                interval = schedule_store.get_interval(SCHEDULE_ID, DEFAULT_INTERVAL_SECONDS)
            except Exception as exc:
                logger.warning("[volume-watch] check failed (continuing): %s", exc)
            try:
                await asyncio.sleep(max(MIN_INTERVAL_SECONDS, float(interval)))
            except asyncio.CancelledError:
                break


volume_watch = VolumeWatch()
