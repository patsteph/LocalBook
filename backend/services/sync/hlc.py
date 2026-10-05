"""Hybrid logical clock (LB-12).

A clock value is a string that sorts correctly as text:

    "<wall ms, 13 digits>.<counter, 6 digits>.<device id>"

Wall time keeps values close to real time (useful in the conflict queue); the
counter orders events inside one millisecond and after a clock that ran ahead;
the device id makes two clocks from different Macs never equal, so "higher
clock wins" is a total, deterministic order on every machine.

Assigned in Python at ship time — never in a SQLite trigger (12c: triggers use
built-in SQL only).
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


def _parse(value: str):
    wall, counter, device = value.split(".", 2)
    return int(wall), int(counter), device


def fmt(wall_ms: int, counter: int, device: str) -> str:
    return f"{wall_ms:013d}.{counter:06d}.{device}"


class Clock:
    def __init__(self, device: str, now_ms: Optional[Callable[[], int]] = None):
        self.device = device
        self._now = now_ms or (lambda: int(time.time() * 1000))
        self._wall = 0
        self._counter = 0
        self._lock = threading.Lock()

    def now(self) -> str:
        """A new clock value, strictly greater than any this clock has issued or seen."""
        with self._lock:
            wall = self._now()
            if wall > self._wall:
                self._wall, self._counter = wall, 0
            else:
                self._counter += 1
            return fmt(self._wall, self._counter, self.device)

    def observe(self, value: Optional[str]) -> None:
        """Move past a clock value received from another Mac."""
        if not value:
            return
        wall, counter, _ = _parse(value)
        with self._lock:
            if (wall, counter) > (self._wall, self._counter):
                self._wall, self._counter = wall, counter
