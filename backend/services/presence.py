"""Presence model — what is the user doing right now?

Single source of truth for the Background Enrichment Worker's scheduling. Derives
a coarse presence TIER from signals that already exist:

  - foreground guard held       (memory_steward) — an explicit user op is running
  - seconds since user activity  (memory_steward) — bumped on every non-GET request
  - seconds since Ollama work    (ollama_service) — is the ingest flood still draining?
  - wall clock                   — the "night" window for deep sleep work

Tiers (IntEnum, higher = more freedom for background work):

  ACTIVE      user is here / a foreground op is running → background does NOTHING
  SHORT_IDLE  brief pause → light "daydream" jobs OK
  LONG_IDLE   sustained idle → heavier batch OK
  AWAY        long idle or overnight → deep work + backlog drain

This is the "is the host awake?" oracle the second-brain enrichment consults before
doing any work. See READFIRST/planning/enrichment-worker-night-shift.md.
"""
from __future__ import annotations

import os
import time
from enum import IntEnum


class Tier(IntEnum):
    ACTIVE = 0
    SHORT_IDLE = 1
    LONG_IDLE = 2
    AWAY = 3


def _envf(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Thresholds (seconds of USER quiet) — env-overridable for tuning per machine.
SHORT_AFTER = _envf("LOCALBOOK_PRESENCE_SHORT_S", 20.0)
LONG_AFTER = _envf("LOCALBOOK_PRESENCE_LONG_S", 120.0)
AWAY_AFTER = _envf("LOCALBOOK_PRESENCE_AWAY_S", 1800.0)

# Night window (local hours): deep "sleep" work runs freely here regardless of
# how recently the user was active, on the assumption they've stepped away.
NIGHT_START_HOUR = int(_envf("LOCALBOOK_NIGHT_START_HOUR", 1))   # 1am
NIGHT_END_HOUR = int(_envf("LOCALBOOK_NIGHT_END_HOUR", 6))       # 6am


def _is_night() -> bool:
    h = time.localtime().tm_hour
    if NIGHT_START_HOUR <= NIGHT_END_HOUR:
        return NIGHT_START_HOUR <= h < NIGHT_END_HOUR
    return h >= NIGHT_START_HOUR or h < NIGHT_END_HOUR


def current_tier() -> Tier:
    """Compute the current presence tier from live signals."""
    # Lazy imports: presence is consulted from inside the worker loop and from
    # ingest/curator paths; importing these at module load risks cycles.
    from services.memory_steward import seconds_since_activity, foreground_active

    if foreground_active():
        return Tier.ACTIVE

    user_idle = seconds_since_activity()
    if user_idle < SHORT_AFTER:
        return Tier.ACTIVE
    if _is_night():
        return Tier.AWAY
    if user_idle >= AWAY_AFTER:
        return Tier.AWAY
    if user_idle >= LONG_AFTER:
        return Tier.LONG_IDLE
    return Tier.SHORT_IDLE


def is_active() -> bool:
    """True when an explicit user op is running or the user just acted — the
    worker cancels in-flight background work the instant this becomes true."""
    return current_tier() == Tier.ACTIVE


def system_busy(quiet_s: float = 8.0) -> bool:
    """True if Ollama did ANY work within `quiet_s` — i.e. the ingest flood
    (embeds + whatever else) is still draining. The worker waits for this to go
    quiet before starting a job, so enrichment never stacks onto a live flood
    (the 2026-06-23 failure mode)."""
    from services.ollama_service import seconds_since_ollama_activity

    return seconds_since_ollama_activity() < quiet_s
