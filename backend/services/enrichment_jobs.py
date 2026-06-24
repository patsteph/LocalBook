"""Enrichment job model for the Background Enrichment Worker.

An EnrichmentJob is a unit of deferred, second-brain work — entity extraction,
graph relationships, community summaries, curator inference, stance scoring. It
is ENQUEUED on the worker (never spawned fire-and-forget), coalesced by `key`,
and runnable only when presence allows.

Two tiers, mapped to the presence tier at which they may run:

  DAYDREAM  source-local, one cheap LLM call → runs at SHORT_IDLE or better
  DEEP      corpus-global synthesis → runs at LONG_IDLE or better (and AWAY/night)

`factory` MUST return a FRESH coroutine each call: the worker cancels a job the
instant a foreground op starts and re-runs it later, and a coroutine can only be
awaited once. So pass a thunk (`lambda: do_work(...)`) or a no-arg async def, not
an already-created coroutine.

See READFIRST/planning/enrichment-worker-night-shift.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Awaitable, Callable, Optional

from services.presence import Tier


class JobTier(IntEnum):
    DAYDREAM = 1   # source-local, light; OK during brief idle
    DEEP = 2       # corpus-global; sustained idle / overnight


# Minimum presence tier required to run a job of each tier.
_MIN_PRESENCE = {
    JobTier.DAYDREAM: Tier.SHORT_IDLE,
    JobTier.DEEP: Tier.LONG_IDLE,
}


def min_presence_for(tier: JobTier) -> Tier:
    return _MIN_PRESENCE[tier]


@dataclass
class EnrichmentJob:
    key: str                            # unique identity → duplicates coalesce
    tier: JobTier
    factory: Callable[[], Awaitable]    # returns a FRESH coroutine each run
    label: str = "enrichment"
    notebook_id: Optional[str] = None
    attempts: int = 0                   # bumped each time the job is cancelled+requeued
