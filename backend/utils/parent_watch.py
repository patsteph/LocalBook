"""Stop the backend when the app that started it is gone.

A normal Quit stops the backend (`RunEvent::Exit` in lib.rs). A Force Quit or a
crash skips that, and the backend kept running with no window — collecting,
scraping, syncing — invisible to the user (seen on the MBP, 2026-10-01: the
backend outlived the app by 35 minutes, reparented to launchd).

The app passes its pid in LOCALBOOK_PARENT_PID. A daemon thread (not a task:
it must work even if the event loop is stuck) checks it every few seconds and,
once the app is gone, sends this process SIGTERM — the same graceful shutdown a
normal Quit gets. Without the variable (tests, scripts, a hand-started backend)
it does nothing.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time

logger = logging.getLogger(__name__)

INTERVAL = 3.0


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                       # exists, owned by someone else


def app_gone(parent: int) -> bool:
    """The app is gone if we were reparented, or its pid no longer exists."""
    return os.getppid() != parent or not _alive(parent)


def start() -> bool:
    raw = os.environ.get("LOCALBOOK_PARENT_PID", "").strip()
    if not raw.isdigit():
        return False
    parent = int(raw)

    def _watch():
        while True:
            time.sleep(INTERVAL)
            if app_gone(parent):
                logger.warning("[parent-watch] LocalBook (pid %d) is gone — shutting the backend down", parent)
                os.kill(os.getpid(), signal.SIGTERM)
                return

    threading.Thread(target=_watch, name="parent-watch", daemon=True).start()
    logger.info("[parent-watch] watching app pid %d", parent)
    return True
