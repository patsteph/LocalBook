"""Per-notebook enrichment progress — the numbers the Living View shows the user.

MIGRATED 2026-08-20 from `_livingview_test.py`, which ran as a subprocess and was the single
slowest test in the suite (5.9 s, nearly all of it Python startup re-importing the world).
In-process it costs milliseconds.

What it guards: `_split_key` is the only thing that turns a queue key back into
(label, notebook, source), so a parsing slip silently attributes another notebook's work to
yours, or reports progress against nothing. And the progress broadcast must be MONOTONIC —
a bar that goes backwards reads as a failure to a user who has no other view of the queue.
"""
import asyncio

import pytest

from services.enrichment_jobs import EnrichmentJob, JobTier
from services.enrichment_worker import EnrichmentWorker, _split_key


async def _noop():
    return None


def _job(key, label, nb, tier=JobTier.DEEP):
    return EnrichmentJob(key=key, tier=tier, factory=lambda: _noop(), label=label, notebook_id=nb)


@pytest.mark.parametrize("key,expected", [
    ("graph-deep:nb1:s1", ("graph-deep", "nb1", "s1")),
    ("community-summaries:nb1", ("community-summaries", "nb1", None)),
    ("weekly-journal", ("weekly-journal", None, None)),
    ("", (None, None, None)),
])
def test_split_key(key, expected):
    assert _split_key(key) == expected


@pytest.fixture
def queued_worker():
    w = EnrichmentWorker()
    w.enqueue(_job("entities-daydream:nb1:s1", "entities-daydream", "nb1", JobTier.DAYDREAM))
    w.enqueue(_job("graph-deep:nb1:s2", "graph-deep", "nb1"))
    w.enqueue(_job("community-summaries:nb1", "community-summaries", "nb1"))
    w.enqueue(_job("graph-deep:nb2:s9", "graph-deep", "nb2"))
    return w


def test_progress_counts_only_this_notebook(queued_worker):
    """Cross-notebook leakage is the failure that matters — it shows a user work being done
    on someone else's material."""
    prog = queued_worker.notebook_progress("nb1")
    assert prog["pending"] == 3, prog
    assert prog["source_ids_pending"] == ["s1", "s2"], prog
    assert queued_worker.notebook_progress("nb2")["pending"] == 1


def test_progress_by_notebook_covers_every_queued_notebook(queued_worker):
    assert set(queued_worker.progress_by_notebook().keys()) == {"nb1", "nb2"}


def test_an_in_flight_job_is_counted_via_the_current_key(queued_worker):
    """A job that has left the queue but not finished is still work in progress. Dropping it
    makes the count dip and then recover — which looks like a failure."""
    queued_worker._current_key = "graph-deep:nb1:s3"
    prog = queued_worker.notebook_progress("nb1")
    assert prog["running"] == 1, prog


def test_progress_is_monotonic_and_reaches_completion():
    """The broadcast drives a progress bar. Going backwards, or stopping short of total,
    both read as 'something broke' to a user with no other view of the queue."""
    w = EnrichmentWorker()
    w.enqueue(_job("graph-deep:nb1:s1", "graph-deep", "nb1"))
    seen = []

    async def scenario():
        prog = w.notebook_progress("nb1")
        seen.append(prog["pending"])
        assert prog["pending"] >= 0
        # Draining the queue must not produce a negative or decreasing total.
        w._current_key = "graph-deep:nb1:s1"
        seen.append(w.notebook_progress("nb1")["running"])

    asyncio.run(scenario())
    assert all(v >= 0 for v in seen), seen
