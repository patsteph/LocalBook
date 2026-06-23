"""Memory Steward — pipeline-aware Ollama model eviction.

Why this exists
---------------
Ollama keeps models resident in (V)RAM after each call (governed by
`keep_alive`). On a 16-18 GB Apple-Silicon box the user's typical working
set — main chat model (olmo-3:7b ~6.3 GB) + fast model (phi4-mini ~3.1 GB)
+ embeddings (snowflake-arctic ~1.1 GB) — already consumes ~10.5 GB. Adding
a 4-5 GB vision model on top of that pushes Ollama past its runner budget
and the model runner crashes with the deceptively-cryptic message
"model runner has unexpectedly stopped, this may be due to resource
limitations or an internal error".

The fix is intelligence, not brute-forced retries: BEFORE we ask the
vision model to do work, we look at what's loaded, figure out what we
actually need for the upcoming pipeline (OCR vision + downstream text
cleanup + embeddings), and politely evict everything else by sending an
empty `/api/generate` with `keep_alive: 0`. After the scan finishes the
user's next chat triggers a normal cold-load of the main model — a one-
time ~3-5 s pause in exchange for scans that *work*.

Public surface (all coroutines):

    loaded_ollama_models()              -> list[dict]
    unload_ollama_model(name)           -> bool
    free_for_pipeline(keep, *, reason)  -> list[str]    # what we evicted

Designed to be safe for the common case (no Ollama / Ollama down): every
function logs and returns a defensible default rather than raising. The
scan pipeline must continue even if memory mgmt fails.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterable, List, Optional, Set

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Single global lock so two concurrent scans don't fight each other when
# computing the eviction set. Lock contention is irrelevant in practice
# (capture queues are serial per session and global concurrency is low).
_lock = asyncio.Lock()

# ── Foreground guard: quiesce background AI work during foreground gen ──
# On an 18 GB swap-mode box the resident working set (gemma4 9.6 GB + phi4
# 3 GB + embed 1 GB) leaves only ~4 GB of headroom. When the user kicks off a
# foreground generation (visual / doc / quiz) the pipeline loads a reranker +
# builds a large context — and if the background AI floods (community-summary
# storm, per-article analysis, PDF-vision batch) are running concurrently, the
# combined footprint tips into swap. The machine then thrashes so hard the
# whole event loop stalls, /health stops responding, and Tauri SIGTERM-restarts
# the backend (observed 2026-06-20: 2m48s of total silence mid context-build).
#
# The fix the user approved: pause background AI work for the *whole* duration
# of a foreground generation, not just the final image phase. Foreground
# generators hold `foreground_guard(reason)` from the very start (before
# context-build); background flood callers `await_background_clearance()`
# before their LLM work and yield until the guard releases.
# Event semantics: SET = background allowed (normal), CLEAR = paused.
_bg_allowed = asyncio.Event()
_bg_allowed.set()
_bg_pause_depth = 0

# Per-task marker: True inside a foreground_guard (and any task spawned within
# it — contextvars propagate to child tasks). Makes await_background_clearance
# deadlock-proof: an LLM call that is *part of* a foreground op (e.g. the query
# path's entity extraction, called transitively from a guarded visual gen)
# must NOT wait for the guard it is itself inside. Only genuinely independent
# background work (spawned outside any guard) actually waits.
_in_foreground: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "lb_in_foreground", default=False
)


@asynccontextmanager
async def foreground_guard(reason: str = "foreground") -> AsyncIterator[None]:
    """Pause background AI floods for the duration of a foreground generation.
    Calls made to `await_background_clearance` from *independent* background
    tasks block until the guard exits; calls from within this op's own task
    tree pass straight through (see `_in_foreground`). Re-entrant-safe via a
    depth counter so nested/concurrent foreground pipelines don't resume early."""
    global _bg_pause_depth, _last_activity_ts
    _bg_pause_depth += 1
    _bg_allowed.clear()
    _last_activity_ts = time.monotonic()  # a foreground op IS user activity
    _tok = _in_foreground.set(True)
    logger.info(f"[memory-steward] background paused for foreground ({reason}, depth={_bg_pause_depth})")
    try:
        yield
    finally:
        _in_foreground.reset(_tok)
        _bg_pause_depth -= 1
        if _bg_pause_depth <= 0:
            _bg_pause_depth = 0
            _bg_allowed.set()
            logger.info(f"[memory-steward] background resumed ({reason})")


# Back-compat alias — visual_composer's Klein-phase pause still calls this.
# Nesting under the endpoint-level guard is harmless (depth-counted).
pause_background_gemma = foreground_guard


async def await_background_clearance(timeout: float = 300.0) -> None:
    """Background flood callers await this before their LLM work. Returns
    immediately when not paused; otherwise waits (bounded) until the foreground
    guard releases. The timeout is a safety valve so a crashed/disconnected
    foreground pipeline can never permanently deadlock background ingest.

    Deadlock-proof: if the caller is *inside* a foreground op (its task tree is
    marked via `_in_foreground`), this returns immediately — a foreground op
    must never block on its own guard. So this is safe to call from dual-use
    code (e.g. entity extraction that runs at ingest AND in the query path)."""
    if _bg_allowed.is_set() or _in_foreground.get():
        return
    try:
        await asyncio.wait_for(_bg_allowed.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "[memory-steward] background clearance wait timed out "
            f"({timeout}s); proceeding anyway"
        )
    except RuntimeError:
        # The module-level Event binds to the loop that first awaited it. A
        # caller running in a DIFFERENT event loop (e.g. a background job runner
        # that spins its own loop) would otherwise crash with "bound to a
        # different event loop". The guard is a best-effort throttle, not a
        # correctness primitive — just proceed rather than fail the LLM call.
        return


# Per-task marker: True when we're inside a long-running AUTONOMOUS pipeline
# that is safe to pause MID-FLIGHT at its own checkpoints (the scheduled
# collection's deep-dive loop). User-initiated "Collect Now" runs the same code
# but WITHOUT this marker, so it never yields. Set by the scheduler; propagates
# to the awaited pipeline via contextvars.
_yieldable_bg: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "lb_yieldable_bg", default=False
)


@asynccontextmanager
async def yieldable_background(reason: str = "scheduled") -> AsyncIterator[None]:
    """Mark the enclosed autonomous pipeline as yieldable so its internal
    `yield_if_background()` checkpoints will defer to foreground work. Only the
    scheduler wraps calls in this — user-triggered runs of the same code stay
    unmarked and run straight through."""
    tok = _yieldable_bg.set(True)
    try:
        yield
    finally:
        _yieldable_bg.reset(tok)


async def yield_if_background(timeout: float = 300.0) -> None:
    """Mid-flight pause point for long autonomous pipelines (scheduled-collection
    deep-dive loop). No-op unless we're in a `yieldable_background` context. When
    we are, it defers to any active foreground op exactly like
    `await_background_clearance` (and is likewise deadlock-proof)."""
    if _yieldable_bg.get():
        await await_background_clearance(timeout)


# ── User-activity tracker (for idle-gating autonomous schedulers) ───────
# Tracks when the user last did something resource-meaningful, so autonomous
# work (scheduled collection) can wait for the app to be IDLE before running —
# never colliding with active use. Bumped on (a) any non-GET HTTP request
# (chat / upload / generate / config = real work; GET polls & passive reading
# don't count) and (b) every foreground_guard enter. Monotonic clock so it's
# immune to wall-clock changes. Initialized to "now" so launch counts as
# activity (combines with the scheduler's startup settle delay).
_last_activity_ts: float = time.monotonic()


def mark_user_activity() -> None:
    """Record that the user just did something resource-meaningful."""
    global _last_activity_ts
    _last_activity_ts = time.monotonic()


def seconds_since_activity() -> float:
    """Seconds since the last user activity (large == idle)."""
    return time.monotonic() - _last_activity_ts


# ── Idle-gate for deferred ENRICHMENT (image description, HyDE) ─────────
# Some background work is pure *enrichment*: the document is already searchable
# on its text embeddings the moment ingest finishes, and image descriptions /
# HyDE questions only sharpen retrieval at the margins. That work is also the
# most expensive (multi-minute gemma vision calls; hundreds of phi4 question
# batches) and — critically — gemma image description shares the SINGLE gemma
# lane with the user's chat query, which cannot preempt a call already in
# flight (observed 2026-06-23: a 283 s describe_image blocked the chat query
# until it timed out). So we gate enrichment behind app-idleness: it runs ONLY
# after the user has been quiet for `min_idle` seconds. Combined with the
# foreground guard this means enrichment never *starts* while the user is
# active, so it can't get in front of — or hold the lane against — a chat query.
# Embeddings (core searchability) are NOT idle-gated; only enrichment is.
IDLE_GATE_DEFAULT = 20.0   # seconds of USER quiet before enrichment may run
OLLAMA_QUIET_DEFAULT = 12.0  # seconds of OLLAMA quiet (ingest flood drained)


async def await_idle(
    min_idle: float = IDLE_GATE_DEFAULT,
    ollama_quiet: float = OLLAMA_QUIET_DEFAULT,
    timeout: float = 1800.0,
    poll: float = 5.0,
) -> None:
    """Block until the app is idle on BOTH axes, so deferred enrichment (image
    description, HyDE) backfills only when nothing is waiting on it AND the
    system can actually do the work fast:

      • USER-idle: no non-GET user activity for `min_idle`s (and no foreground
        guard held) — the user isn't actively waiting on anything.
      • SYSTEM-idle: Ollama has done no work for `ollama_quiet`s — the upload's
        background ingest flood (embeds + community-detection + entity
        extraction) has DRAINED. This is the fix for 2026-06-23: user-idle
        alone let enrichment fire into the still-running flood, where gemma
        vision stacked 90s timeouts on the cap-1 lane and blocked the chat
        query. Gating on Ollama-quiet makes enrichment wait for the flood to
        finish, then run on a free system (fast, no stacking, chat unblocked).

    Bounded by `timeout` so enrichment still eventually completes on a
    perpetually-busy app. Deadlock-proof: returns immediately when called from
    within a foreground op's own task tree (same contextvar guard as
    await_background_clearance); loop-agnostic and never raises."""
    if _in_foreground.get():
        return
    from services.ollama_service import seconds_since_ollama_activity
    deadline = time.monotonic() + timeout
    while True:
        # Never run while a foreground generation holds the guard.
        await await_background_clearance(timeout=timeout)
        user_idle = seconds_since_activity()
        sys_idle = seconds_since_ollama_activity()
        if user_idle >= min_idle and sys_idle >= ollama_quiet:
            return
        if time.monotonic() >= deadline:
            logger.info(
                f"[memory-steward] await_idle timeout {timeout:.0f}s reached "
                f"(user_idle={user_idle:.0f}s sys_idle={sys_idle:.0f}s); "
                "proceeding to avoid stranding enrichment"
            )
            return
        # Sleep no longer than the nearest unmet threshold (so we wake promptly
        # once both clear), but at least 0.5s and at most `poll`.
        wait = max(0.5, min(poll, min_idle - user_idle, ollama_quiet - sys_idle))
        await asyncio.sleep(wait)


def _ollama_base() -> str:
    return settings.ollama_base_url.rstrip("/")


def _normalize(name: str) -> str:
    """Strip the ``:latest`` tag and lowercase for tolerant comparison.

    Ollama reports models as ``phi4-mini:latest`` in /api/ps but config
    files often store them as ``phi4-mini``. We compare normalized forms
    so a config string like ``snowflake-arctic-embed2`` matches the
    registry's ``snowflake-arctic-embed2:latest``.
    """
    if not name:
        return ""
    s = name.strip().lower()
    if s.endswith(":latest"):
        s = s[: -len(":latest")]
    return s


def _normalize_set(names: Iterable[str]) -> Set[str]:
    return {_normalize(n) for n in names if n}


async def loaded_ollama_models(timeout: float = 2.0) -> List[dict]:
    """Return the raw `/api/ps` payload (one dict per loaded model).

    Returns [] on any failure (Ollama down, network error). Never raises.
    """
    url = f"{_ollama_base()}/api/ps"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json().get("models", []) or []
    except Exception as e:
        logger.debug(f"[memory-steward] /api/ps failed: {e}")
        return []


async def unload_ollama_model(name: str, timeout: float = 5.0) -> bool:
    """Force Ollama to evict ``name`` by sending keep_alive=0.

    Returns True if Ollama acknowledged the unload. False on any error
    (model wasn't loaded, network failed, etc.). Never raises.
    """
    if not name:
        return False
    url = f"{_ollama_base()}/api/generate"
    payload = {"model": name, "keep_alive": 0}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.debug(
                    f"[memory-steward] unload {name} returned HTTP {resp.status_code}"
                )
                return False
            data = resp.json()
            done_reason = data.get("done_reason", "")
            ok = done_reason == "unload" or data.get("done") is True
            if ok:
                logger.info(f"[memory-steward] Evicted {name} (reason={done_reason})")
            return ok
    except Exception as e:
        logger.debug(f"[memory-steward] unload {name} failed: {e}")
        return False


async def free_for_pipeline(
    keep: Iterable[str],
    *,
    reason: str = "scan",
) -> List[str]:
    """Evict every Ollama model NOT in the ``keep`` set.

    Comparison is tolerant of the ``:latest`` tag — passing
    ``"phi4-mini"`` correctly preserves the loaded
    ``"phi4-mini:latest"``.

    Args:
        keep:    Model names that MUST stay resident (vision + cleanup +
                 embeddings, typically). Empty / None entries ignored.
        reason:  Free-form label used in log lines; helps trace which
                 pipeline step triggered the eviction.

    Returns:
        The list of model names that were actually unloaded (empty if
        nothing needed evicting or Ollama is unreachable).
    """
    keep_norm = _normalize_set(keep)
    evicted: List[str] = []

    async with _lock:
        loaded = await loaded_ollama_models()
        if not loaded:
            return evicted

        candidates = []
        for m in loaded:
            name = m.get("name", "")
            if not name:
                continue
            if _normalize(name) in keep_norm:
                continue
            candidates.append(name)

        if not candidates:
            logger.debug(
                f"[memory-steward] {reason}: nothing to evict "
                f"(loaded: {[m.get('name') for m in loaded]}, keep: {sorted(keep_norm)})"
            )
            return evicted

        logger.info(
            f"[memory-steward] {reason}: evicting {candidates} "
            f"(keep set: {sorted(keep_norm)})"
        )
        for name in candidates:
            if await unload_ollama_model(name):
                evicted.append(name)

    return evicted
