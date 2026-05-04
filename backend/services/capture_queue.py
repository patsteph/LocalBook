"""Capture Queue — async FIFO processing for phone-captured images.

Each active capture session gets its own queue. Images are processed in
arrival order (preserving page sequence). Results are pushed to connected
WebSocket subscribers as they complete.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CapturePageResult:
    """Result of OCR processing for a single captured page."""
    page_index: int
    status: str              # "complete" | "error"
    content_type: str = ""   # "document" | "math" | "whiteboard" | "drawing" | "photo"
    ocr_text: str = ""
    error: str = ""
    file_path: str = ""


@dataclass
class CaptureQueue:
    """FIFO processing queue for a single capture session."""

    session_id: str
    _queue: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)
    _results: List[CapturePageResult] = field(default_factory=list)
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    _subscribers: List[Callable[[CapturePageResult], Coroutine]] = field(
        default_factory=list, repr=False,
    )
    pages_received: int = 0
    pages_processed: int = 0
    total_chars: int = 0
    errors: int = 0

    def subscribe(self, callback: Callable[[CapturePageResult], Coroutine]):
        """Register a callback for page completion events."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable):
        """Remove a previously registered callback."""
        self._subscribers = [s for s in self._subscribers if s is not callback]

    async def enqueue(self, file_path: str, page_index: int):
        """Add an image to the processing queue."""
        self.pages_received += 1
        await self._queue.put((file_path, page_index))
        logger.info(
            f"[capture-queue:{self.session_id}] Enqueued page {page_index} "
            f"({self.pages_received} total)"
        )

    def start(self, process_fn: Callable):
        """Start the background processing loop."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._process_loop(process_fn))

    async def stop(self):
        """Stop the processing loop (waits for current item to finish)."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _process_loop(self, process_fn: Callable):
        """Process items from the queue in FIFO order."""
        try:
            while True:
                file_path, page_index = await self._queue.get()
                result = CapturePageResult(
                    page_index=page_index,
                    file_path=file_path,
                )
                try:
                    content_type, ocr_text = await process_fn(file_path)
                    result.status = "complete"
                    result.content_type = content_type
                    result.ocr_text = ocr_text
                    self.pages_processed += 1
                    self.total_chars += len(ocr_text)
                except Exception as e:
                    logger.error(
                        f"[capture-queue:{self.session_id}] Page {page_index} "
                        f"failed: {e}"
                    )
                    result.status = "error"
                    result.error = str(e)[:300]
                    self.errors += 1

                self._results.append(result)
                # Notify all subscribers
                for cb in self._subscribers:
                    try:
                        await cb(result)
                    except Exception as e:
                        logger.debug(f"[capture-queue] subscriber error: {e}")

                self._queue.task_done()
        except asyncio.CancelledError:
            logger.info(f"[capture-queue:{self.session_id}] Stopped")

    @property
    def results(self) -> List[CapturePageResult]:
        return list(self._results)

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "pages_received": self.pages_received,
            "pages_processed": self.pages_processed,
            "total_chars": self.total_chars,
            "errors": self.errors,
            "pending": self._queue.qsize(),
        }
