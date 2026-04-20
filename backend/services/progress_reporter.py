"""
Progress Reporter — optional stage-by-stage progress channel for long-running
pipelines (currently: document upload + RAG ingestion).

Design:
- Callers instantiate a ProgressReporter bound to an asyncio.Queue.
- Pipeline functions accept an Optional[ProgressReporter] kwarg (default None);
  when None, a NoopReporter is used so every call site is safe and zero-cost.
- HTTP endpoints drain the queue as Server-Sent Events.

This module does NOT change any business logic — it only observes the pipeline.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ProgressEvent:
    """Single progress event emitted by the pipeline."""
    stage: str                 # machine-readable stage id (e.g. "extracting")
    percent: int               # 0..100 overall pipeline progress
    message: str               # human-readable, user-facing description
    details: Dict[str, Any] = field(default_factory=dict)  # optional structured extras

    def to_sse(self) -> str:
        """Serialize as a Server-Sent Events `data:` line."""
        payload = {
            "stage": self.stage,
            "percent": self.percent,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return f"data: {json.dumps(payload)}\n\n"


class ProgressReporter:
    """Queue-backed progress reporter.

    Emit calls are fire-and-forget from the pipeline's perspective (they await
    briefly on the queue put but never block on a consumer). If no consumer
    drains the queue, emits still succeed — the queue is unbounded by default.
    """

    def __init__(self, queue: Optional[asyncio.Queue] = None):
        self.queue: asyncio.Queue = queue or asyncio.Queue()
        self._last_percent = 0

    async def emit(
        self,
        stage: str,
        percent: int,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a progress event. Percent is clamped monotonically non-decreasing."""
        # Clamp to [0..100] and never go backwards (avoids jarring UI)
        pct = max(self._last_percent, min(100, max(0, int(percent))))
        self._last_percent = pct
        evt = ProgressEvent(stage=stage, percent=pct, message=message, details=details or {})
        await self.queue.put(evt)

    async def error(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Emit a terminal error event. Consumers should stop reading after this."""
        evt = ProgressEvent(stage="error", percent=self._last_percent, message=message, details=details or {})
        await self.queue.put(evt)

    async def complete(self, message: str = "Ready", details: Optional[Dict[str, Any]] = None) -> None:
        """Emit a terminal complete event at 100%."""
        self._last_percent = 100
        evt = ProgressEvent(stage="complete", percent=100, message=message, details=details or {})
        await self.queue.put(evt)

    async def close(self) -> None:
        """Signal end-of-stream to consumers."""
        await self.queue.put(None)


class NoopReporter(ProgressReporter):
    """Zero-cost reporter used when callers don't need progress.

    All emit/error/complete/close calls are no-ops. Lets pipeline code
    uniformly call `await reporter.emit(...)` without branching.
    """

    def __init__(self):
        # Don't allocate a queue
        self._last_percent = 0

    async def emit(self, *_args, **_kwargs) -> None:
        return None

    async def error(self, *_args, **_kwargs) -> None:
        return None

    async def complete(self, *_args, **_kwargs) -> None:
        return None

    async def close(self) -> None:
        return None


_NOOP = NoopReporter()


def get_noop_reporter() -> ProgressReporter:
    """Return the shared no-op reporter (safe to pass anywhere)."""
    return _NOOP


async def drain_as_sse(reporter: ProgressReporter):
    """Async generator that yields SSE-formatted strings from the reporter's queue.

    Stops when a None sentinel is enqueued (reporter.close()).
    """
    while True:
        evt = await reporter.queue.get()
        if evt is None:
            return
        yield evt.to_sse()
        if evt.stage in ("complete", "error"):
            # Still continue until close() is called, but after a terminal
            # event we expect the producer to close the stream shortly.
            continue
