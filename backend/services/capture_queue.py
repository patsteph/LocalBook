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
    # Lifecycle state. Starts as "processing" when the worker dequeues
    # the page; flipped to "complete" or "error" once OCR finishes.
    # Defaulted because the worker constructs the result BEFORE the OCR
    # call so it has somewhere to attach error metadata if process_fn
    # raises — making this required would (and did, for hours) crash
    # the worker on the very first dequeue with TypeError, silently
    # killing the entire capture pipeline.
    status: str = "processing"   # "processing" | "complete" | "error"
    content_type: str = ""   # "document" | "math" | "whiteboard" | "drawing" | "photo"
    ocr_text: str = ""
    error: str = ""
    # Failure category — lets the frontend surface model-specific guidance
    # instead of a generic "backend error". Populated by _process_loop
    # from PipelineModelError subclasses raised by scan_pipeline.
    #   ""             — success / no error
    #   "vision_model" — the vision model itself failed (load/inference)
    #   "cleanup_model"— the downstream text cleanup model failed
    #   "timeout"      — hit the 180s per-page wall clock
    #   "generic"      — anything else (file I/O, unexpected exception)
    error_type: str = ""
    # Name of the model that failed, when known. Empty otherwise.
    error_model: str = ""
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
            logger.info(
                f"[capture-queue:{self.session_id}] start() called but worker "
                f"already running (task={self._task!r})"
            )
            return
        self._task = asyncio.create_task(self._process_loop(process_fn))
        # Add a done-callback so we can see if the worker dies silently
        # for any reason — without this, a crashed worker leaves no trace
        # and the queue silently swallows every uploaded page forever.
        def _on_worker_exit(task: asyncio.Task):
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                logger.info(f"[capture-queue:{self.session_id}] worker cancelled")
                return
            except Exception as e:  # task wasn't done yet, etc.
                logger.warning(f"[capture-queue:{self.session_id}] worker exit-callback error: {e}")
                return
            if exc is None:
                logger.info(f"[capture-queue:{self.session_id}] worker exited cleanly")
            else:
                logger.error(
                    f"[capture-queue:{self.session_id}] worker DIED with unhandled exception: "
                    f"{type(exc).__name__}: {exc}",
                    exc_info=exc,
                )
        self._task.add_done_callback(_on_worker_exit)
        logger.info(
            f"[capture-queue:{self.session_id}] worker started (task={self._task!r})"
        )

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
        logger.info(f"[capture-queue:{self.session_id}] worker loop entered, awaiting first item")
        try:
            while True:
                file_path, page_index = await self._queue.get()
                logger.info(
                    f"[capture-queue:{self.session_id}] dequeued page {page_index} "
                    f"({file_path}) — invoking process_fn"
                )
                result = CapturePageResult(
                    page_index=page_index,
                    file_path=file_path,
                )
                try:
                    # 3-minute timeout per page — prevents one stuck model
                    # call from blocking the entire queue forever.
                    content_type, ocr_text = await asyncio.wait_for(
                        process_fn(file_path),
                        timeout=180.0,
                    )
                    result.status = "complete"
                    result.content_type = content_type
                    result.ocr_text = ocr_text
                    self.pages_processed += 1
                    self.total_chars += len(ocr_text)
                except asyncio.TimeoutError:
                    logger.error(
                        f"[capture-queue:{self.session_id}] Page {page_index} "
                        f"timed out after 180s"
                    )
                    result.status = "error"
                    result.error = "Processing timed out (3 minutes). Try recapturing this page."
                    result.error_type = "timeout"
                    self.errors += 1
                except Exception as e:
                    logger.error(
                        f"[capture-queue:{self.session_id}] Page {page_index} "
                        f"failed: {e}"
                    )
                    result.status = "error"
                    result.error = str(e)[:300]
                    # Lift typed-error metadata out of scan_pipeline's
                    # PipelineModelError subclasses so the frontend can
                    # render "Vision model X failed — try a different one"
                    # instead of a generic message. Imported lazily to
                    # avoid a circular import at module load.
                    err_type = getattr(e, "error_type", None)
                    err_model = getattr(e, "model", None)
                    if err_type and err_model:
                        result.error_type = err_type
                        result.error_model = err_model
                    else:
                        result.error_type = "generic"
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
