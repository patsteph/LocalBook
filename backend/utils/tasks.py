"""Background task utilities for safe asyncio task management."""

import asyncio
import traceback


def _log_task_exception(task: asyncio.Task):
    """Callback for background tasks — logs exceptions instead of silently swallowing them."""
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        print(f"[BG-TASK] {task.get_name()} FAILED: {exc}")
        traceback.print_exception(type(exc), exc, exc.__traceback__)


def safe_create_task(coro, *, name: str = None) -> asyncio.Task:
    """Create an asyncio task with automatic exception logging.
    
    Drop-in replacement for asyncio.create_task that ensures background
    task failures are logged instead of silently swallowed.
    """
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_log_task_exception)
    return task
