"""Event-loop lag monitor — names the culprit when the loop stalls.

A synchronous blocking call freezes the single asyncio thread: `/health` stops
answering and the Tauri watchdog kills the backend with NO in-process trace
(2026-06-25: a sync cross-notebook scan in curator consolidation froze the loop
~2 min → silent kill; we only found it by reading logs after the fact).

This monitor sleeps on a tight cadence and, whenever it wakes up LATE — which can
only happen if the loop was blocked between ticks — logs how long the stall was
and which asyncio tasks were alive, so the *next* blocking call self-reports
instead of mystery-crashing.

Honest limitation: the monitor runs on the same event loop it watches, so it
cannot log DURING a stall — only just after the loop frees. A stall long enough
to be fatal (>120 s) gets the process killed before the monitor regains control.
Its real value is EARLY WARNING: it surfaces the sub-fatal 1–10 s stalls that are
the same bug at smaller corpus size, so a `discover_cross_notebook_patterns`-style
hot spot shows up as a logged warning long before it grows into a kill. (A true
live detector would need a separate OS thread polling a heartbeat — a noted
follow-up if early warning proves insufficient.)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

# Expected wake cadence. The loop should hand control back to us every _INTERVAL.
_INTERVAL = float(os.getenv("LOCALBOOK_LOOPMON_INTERVAL_S", "0.5"))
# Log a stall when a tick is at least this many seconds late.
_WARN_LAG = float(os.getenv("LOCALBOOK_LOOPMON_WARN_S", "1.0"))


class LoopMonitor:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="loop-monitor")
        logger.info(
            f"[loop-monitor] started (cadence {_INTERVAL}s, warn ≥ {_WARN_LAG}s late)"
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass

    async def _loop(self) -> None:
        while self._running:
            t0 = time.monotonic()
            try:
                await asyncio.sleep(_INTERVAL)
            except asyncio.CancelledError:
                break
            lag = time.monotonic() - t0 - _INTERVAL
            if lag >= _WARN_LAG:
                # Name the tasks alive across the stall — the likely culprit is
                # whichever one was mid-synchronous-call when the loop froze.
                try:
                    names = sorted(
                        {t.get_name() for t in asyncio.all_tasks() if not t.done()}
                    )
                except Exception:
                    names = []
                logger.warning(
                    f"[loop-monitor] event loop stalled {lag:.1f}s — "
                    f"{len(names)} task(s) alive: {', '.join(names[:12])}"
                    + (" …" if len(names) > 12 else "")
                )


# Module-level singleton.
loop_monitor = LoopMonitor()
