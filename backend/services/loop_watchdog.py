"""Live fatal-freeze watchdog — captures the blocking stack the loop-monitor can't.

`loop_monitor` runs on the event loop it watches, so it can only log AFTER the
loop frees — a fatal (>120 s) stall gets the process Tauri-watchdog-killed before
it regains control. The three 2026-06-25 daytime kills were exactly this: silent,
no preceding loop-monitor line (see READFIRST/in-progress/enrichment-worker-night-shift.md).

This watchdog catches that case. It does NOT use a Python polling thread — a pure-
Python sync loop holds the GIL, so a Python thread couldn't run during the freeze
either. Instead it uses `faulthandler.dump_traceback_later()`, a C-level timer that
fires and dumps EVERY thread's Python stack even while the GIL is held / the loop is
frozen. A healthy event loop re-arms (cancels + resets) the timer every WATCHDOG_S/2;
if the loop freezes longer than WATCHDOG_S, the timer fires first and writes the real
blocking stack — the exact line — to the backend log before the kill.

Honest limit (state it plainly): this does NOT prevent or unblock the freeze — you
cannot cancel a synchronous call. It converts a silent mystery-kill into a logged
stack so the NEXT culprit is a one-read find. Prevention is the memory-pressure gate
(Phase 5b) + offloading blocking work with asyncio.to_thread.

Complements loop_monitor (keep both): loop_monitor owns sub-fatal early-warning +
task names; this owns the fatal stack.
"""
from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import sys

logger = logging.getLogger(__name__)

# Fire the dump if the loop fails to re-arm within this long. Below Tauri's ~120 s
# kill, above any legitimate GC/import/model-load pause.
_WATCHDOG_S = float(os.getenv("LOCALBOOK_WATCHDOG_S", "25.0"))


class LoopWatchdog:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False
        self._fh = None  # file object faulthandler writes the dump to

    def _dump_target(self):
        """Pick a stable file for faulthandler to write tracebacks to. Prefer the
        backend log's underlying stream (so the dump lands where we already look);
        fall back to stderr. faulthandler needs a real fileno()."""
        try:
            for h in logging.getLogger().handlers:
                stream = getattr(h, "stream", None)
                if stream is not None and hasattr(stream, "fileno"):
                    try:
                        stream.fileno()
                        return stream
                    except (OSError, ValueError):
                        continue
        except Exception:
            pass
        return sys.stderr

    async def start(self) -> None:
        if self._running:
            return
        if not hasattr(faulthandler, "dump_traceback_later"):
            logger.warning("[loop-watchdog] faulthandler unavailable — not started")
            return
        self._running = True
        self._fh = self._dump_target()
        self._task = asyncio.create_task(self._loop(), name="loop-watchdog")
        logger.info(
            f"[loop-watchdog] started (fatal-freeze dump after {_WATCHDOG_S}s)"
        )

    async def stop(self) -> None:
        self._running = False
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:
            pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass

    async def _loop(self) -> None:
        # Re-arm at half the timeout so a healthy loop always resets the C timer
        # before it can fire. exit=False so a dump does NOT kill the process —
        # Tauri's own watchdog owns the kill; we only want the stack on the way out.
        rearm = max(1.0, _WATCHDOG_S / 2.0)
        while self._running:
            try:
                faulthandler.dump_traceback_later(
                    _WATCHDOG_S, repeat=False, file=self._fh, exit=False
                )
            except Exception as e:
                logger.warning(f"[loop-watchdog] could not arm dump: {e}")
                return
            try:
                await asyncio.sleep(rearm)
            except asyncio.CancelledError:
                break
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:
            pass


# Module-level singleton.
loop_watchdog = LoopWatchdog()
