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
import logging
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


@asynccontextmanager
async def foreground_guard(reason: str = "foreground") -> AsyncIterator[None]:
    """Pause background AI floods for the duration of a foreground generation.
    Calls made to `await_background_clearance` while held block until the guard
    exits. Re-entrant-safe via a depth counter so nested or concurrent
    foreground pipelines don't resume background work early."""
    global _bg_pause_depth
    _bg_pause_depth += 1
    _bg_allowed.clear()
    logger.info(f"[memory-steward] background paused for foreground ({reason}, depth={_bg_pause_depth})")
    try:
        yield
    finally:
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
    foreground pipeline can never permanently deadlock background ingest."""
    if _bg_allowed.is_set():
        return
    try:
        await asyncio.wait_for(_bg_allowed.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "[memory-steward] background clearance wait timed out "
            f"({timeout}s); proceeding anyway"
        )


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
