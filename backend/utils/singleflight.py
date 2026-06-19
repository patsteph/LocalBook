"""KeyedSingleflight — per-key in-flight task tracking with dedup.

Replaces the ad-hoc per-notebook in-flight maps that have been reinvented
several times across the codebase (`_inflight_builders` in
`services/community_detection.py`, and the equivalent dedup dances in
`agents/correspondent.py`, `api/chat.py`, `api/articles.py`,
`services/coaching_insights.py`).

Usage:

    sf = KeyedSingleflight(name="community-summary-builder")

    sf.spawn(notebook_id, lambda: community_detector.build_missing_summaries(
        notebook_id, entity_graph,
    ))

If a task for the same key is already running, the new spawn is a no-op and
returns the existing task. Done callbacks clean up the slot.

Audit ref: 09_verification_log V1; 10_plan_of_attack PB-1.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Dict, Optional

from utils.tasks import safe_create_task

logger = logging.getLogger(__name__)


class KeyedSingleflight:
    """Per-key in-flight task registry.

    One instance per logical job type (community-summary-builder,
    mental-model-infer, stance-score, …). Keys are typically a notebook_id,
    or a composite like ``f"{notebook_id}:{source_id}"`` when dedup needs to
    be finer-grained.
    """

    def __init__(self, name: str):
        self._name = name
        self._inflight: Dict[str, asyncio.Task] = {}

    def spawn(
        self,
        key: str,
        coro_factory: Callable[[], Awaitable],
        *,
        suffix: Optional[str] = None,
    ) -> asyncio.Task:
        """Spawn ``coro_factory()`` unless a task for ``key`` is already running.

        Returns the existing in-flight task on dedup, or the newly-created task
        otherwise. Callers can ``await`` the return if they need completion;
        most fire-and-forget callers ignore it.

        ``suffix`` is appended to the task name for diagnostics (e.g. a short
        source id) — it does not affect dedup.
        """
        existing = self._inflight.get(key)
        if existing is not None and not existing.done():
            return existing

        task_name = f"{self._name}-{key[:8]}"
        if suffix:
            task_name += f"-{suffix[:8]}"

        task = safe_create_task(coro_factory(), name=task_name)
        self._inflight[key] = task

        def _cleanup(_t: asyncio.Task, _key: str = key):
            # Only clear if the slot still points at the task that finished, so a
            # newer spawn for the same key (after this one completed) isn't
            # evicted by this callback.
            if self._inflight.get(_key) is _t:
                self._inflight.pop(_key, None)

        task.add_done_callback(_cleanup)
        return task

    def is_running(self, key: str) -> bool:
        """True if a non-done task is registered for ``key``."""
        existing = self._inflight.get(key)
        return existing is not None and not existing.done()

    def active_count(self) -> int:
        """Number of non-done tasks currently registered (diagnostics)."""
        return sum(1 for t in self._inflight.values() if not t.done())
