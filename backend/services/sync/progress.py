"""What sync is doing right now, for the screen and the menu bar (LB-12).

A sync used to be one long request behind a spinner: no phase, no count, no way
to know whether stopping was safe. Every run now records where it is:

    initiated  this Mac started it (Start sync / Sync now / the background loop)
    incoming   another Mac is pulling from or pushing to this one
    index      this Mac is making synced sources searchable

Stopping is always safe — every page commits atomically and every file resumes
from its `.part` — so Stop is cooperative: the run checks between pages, chunks
and sources, and the next sync continues where it left off.

In memory only. A restart ends every run, and the next sync starts over from
the version vectors, which are the real record of what has arrived.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

PHASES = {
    "connect": "Connecting",
    "backup": "Safety backup",
    "match": "Matching notebooks",
    "receive": "Receiving changes",
    "send": "Sending changes",
    "files": "Files",
    "index": "Making sources searchable",
}
INCOMING_IDLE = 30          # an incoming run with no traffic for this long is over
KEEP_FINISHED = 600         # how long a finished run stays on screen

_lock = threading.Lock()
_runs: Dict[str, "Run"] = {}          # key → run: "initiated", "index", "incoming:<device>"


class Cancelled(Exception):
    """The user pressed Stop. Raised between units of work, never mid-write."""


class Run:
    def __init__(self, key: str, kind: str, device_id: Optional[str], name: Optional[str],
                 phases: List[str], quiet: bool = False):
        self.key, self.kind, self.device_id, self.name = key, kind, device_id, name
        self.run_id = uuid.uuid4().hex[:12]
        self.phases = list(phases)
        self.quiet = quiet
        self.phase: Optional[str] = None
        self.done_phases: List[str] = []
        self.done = self.total = 0
        self.unit = ""
        self.detail = ""
        self.started_at = self.touched_at = time.time()
        self.finished_at: Optional[float] = None
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.cancel_requested = False

    # ── progress ──
    def step(self, phase: str, total: int = 0, unit: str = "") -> None:
        with _lock:
            if self.phase and self.phase != phase and self.phase not in self.done_phases:
                self.done_phases.append(self.phase)
            if phase not in self.phases:
                self.phases.append(phase)
            if self.phase != phase:
                self.done, self.total, self.unit, self.detail = 0, int(total), unit, ""
            self.phase = phase
            self.touched_at = time.time()

    def add_total(self, n: int) -> None:
        with _lock:
            self.total += max(0, int(n))
            self.touched_at = time.time()

    def advance(self, n: int = 1, detail: Optional[str] = None) -> None:
        with _lock:
            self.done += int(n)
            if self.total and self.done > self.total:
                self.total = self.done
            if detail is not None:
                self.detail = detail
            self.touched_at = time.time()

    def check(self) -> None:
        if self.cancel_requested:
            raise Cancelled("stopped")

    def finish(self, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
        with _lock:
            if self.phase and self.phase not in self.done_phases and not error:
                self.done_phases.append(self.phase)
            self.result, self.error = result, error
            self.finished_at = time.time()

    @property
    def running(self) -> bool:
        return self.finished_at is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id, "kind": self.kind, "device_id": self.device_id, "name": self.name,
            "running": self.running, "quiet": self.quiet,
            "phases": [{"id": p, "label": PHASES.get(p, p),
                        "state": "done" if p in self.done_phases else
                                 ("current" if p == self.phase and self.running else
                                  ("failed" if p == self.phase and self.error else "pending"))}
                       for p in self.phases],
            "phase": self.phase, "label": PHASES.get(self.phase or "", ""),
            "done": self.done, "total": self.total, "unit": self.unit, "detail": self.detail,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "elapsed": round((self.finished_at or time.time()) - self.started_at, 1),
            "result": self.result, "error": self.error, "stopped": self.cancel_requested,
        }


class _Null:
    """A run that records nothing — for callers outside any run (preview, tests)."""

    def step(self, *a, **k): pass
    def add_total(self, *a, **k): pass
    def advance(self, *a, **k): pass
    def check(self): pass


NULL = _Null()


def begin(key: str, kind: str, phases: List[str], device_id: Optional[str] = None,
          name: Optional[str] = None, quiet: bool = False) -> Run:
    run = Run(key, kind, device_id, name, phases, quiet)
    with _lock:
        _runs[key] = run
    return run


def get(key: str) -> Optional[Run]:
    with _lock:
        return _runs.get(key)


def active(key: str) -> Optional[Run]:
    r = get(key)
    return r if r and r.running else None


def incoming(device_id: str, name: Optional[str]) -> Run:
    """The run another Mac's requests are counted against; opened on first use."""
    key = f"incoming:{device_id}"
    r = active(key)
    if r is None:
        r = begin(key, "incoming", [], device_id=device_id, name=name)
    return r


def cancel() -> bool:
    """Stop what this Mac is running (an incoming run belongs to the other Mac)."""
    hit = False
    with _lock:
        for r in _runs.values():
            if r.running and r.kind != "incoming":
                r.cancel_requested = True
                hit = True
    return hit


def snapshot() -> Dict[str, Any]:
    now = time.time()
    with _lock:
        runs = list(_runs.items())
    out = []
    for key, r in runs:
        if r.kind == "incoming" and r.running and now - r.touched_at > INCOMING_IDLE:
            r.finish(result={"done": r.done})
        if not r.running and now - (r.finished_at or now) > KEEP_FINISHED:
            continue
        out.append(r.to_dict())
    out.sort(key=lambda d: d["started_at"], reverse=True)
    running = [d for d in out if d["running"]]
    return {"running": bool(running), "runs": out}


def summary() -> Dict[str, Any]:
    """One line for the menu bar."""
    snap = snapshot()
    for d in snap["runs"]:
        if not d["running"]:
            continue
        pct = f" {int(100 * d['done'] / d['total'])}%" if d["total"] else ""
        if d["kind"] == "index":
            return {"running": True, "label": f"Indexing synced sources…{pct}"}
        who = d.get("name") or "another Mac"
        return {"running": True, "label": f"Syncing with {who}…{pct}"}
    return {"running": False, "label": ""}


def _reset_for_tests() -> None:
    with _lock:
        _runs.clear()
