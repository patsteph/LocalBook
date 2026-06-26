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


# Inter-unit trickle (seconds to rest between consecutive BACKGROUND work units)
# keyed to tier — the within-loop analogue of the worker's between-jobs dose. A
# long inline background loop (e.g. building dozens of community summaries inside
# one worker job) should leave room between units so OTHER background work on the
# same Ollama lane can interleave, instead of draining as a tight burst that
# starves it (observed 2026-06-24: a community-summary burst monopolized the
# cap-2 phi4 lane and timed out concurrent background stance/graph tasks). AWAY =
# drain hard (matches the worker's AWAY dose intent).
_BACKGROUND_PACE = {
    Tier.ACTIVE: 5.0,
    Tier.SHORT_IDLE: 5.0,
    Tier.LONG_IDLE: 2.0,
    Tier.AWAY: 0.0,
}


def background_pace_seconds() -> float:
    """Seconds an inline background loop should rest between consecutive units,
    scaled to how 'away' the user is. Single authority for 'how gently should
    background trickle' — set to 0.0 everywhere to revert to burst behavior."""
    return _BACKGROUND_PACE.get(current_tier(), 2.0)


# ── Memory-pressure gate (Phase 5b, 2026-06-26) ────────────────────────
# Per-model Ollama lane caps (gemma 1 / phi4 2 / embed 4) bound concurrency
# PER MODEL but not total model RESIDENCY. On an ≤18 GB box, gemma (9.6 GB) +
# phi4 + embed + a foreground Klein image all wanting RAM at once tips the box
# into swap (`mode=swap`); the event-loop thread is then CPU/paging-starved and
# can stall past the Tauri watchdog → silent kill (the 2026-06-25 daytime soak).
# No queue or per-model cap prevents this — we need a gate on AGGREGATE memory
# state that parks ALL background work (incl. NIGHT) while the box is thrashing.
_MEM_PRESSURE_PCT = _envf("LOCALBOOK_MEM_PRESSURE_PCT", 92.0)    # hard RAM ceiling (%)
_MEM_SWAP_DELTA_MB = _envf("LOCALBOOK_MEM_SWAP_DELTA_MB", 50.0)  # active-paging signal
_MEM_SWAP_WINDOW_S = 15.0                                        # delta only valid within this gap
_last_swap_used = {"bytes": 0, "ts": 0.0}


def memory_pressure() -> bool:
    """True when the box is RAM-constrained or ACTIVELY SWAPPING — the worker
    parks ALL background work (even NIGHT) until this clears. Prevents the
    gemma+image+embed co-residency swap-thrash that froze the loop 2026-06-25.

    Two signals, OR'd:
      1. virtual_memory().percent ≥ ceiling — a hard "no headroom" stop.
      2. swap USED growing ≥ delta within the window — the LIVE "thrashing right
         now" signal. A steady high-but-stable swap from a resident model is NOT
         pressure (it's normal on a constrained box); only the GROWTH is. This is
         why the delta — not raw swap size — is the primary live signal.

    Set LOCALBOOK_MEM_PRESSURE_PCT / _MEM_SWAP_DELTA_MB absurdly high to disable.
    """
    try:
        import psutil
    except Exception:
        return False  # can't measure → don't over-gate (fail open)

    try:
        if psutil.virtual_memory().percent >= _MEM_PRESSURE_PCT:
            return True

        sw = psutil.swap_memory()
        now = time.monotonic()
        prev_b, prev_t = _last_swap_used["bytes"], _last_swap_used["ts"]
        _last_swap_used["bytes"], _last_swap_used["ts"] = sw.used, now
        if prev_t and (now - prev_t) <= _MEM_SWAP_WINDOW_S:
            grew_mb = (sw.used - prev_b) / (1024 * 1024)
            if grew_mb >= _MEM_SWAP_DELTA_MB:
                return True
    except Exception:
        return False  # any psutil hiccup → fail open, never block on a metrics error
    return False
