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
from typing import Iterable, List, Optional, Set

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Single global lock so two concurrent scans don't fight each other when
# computing the eviction set. Lock contention is irrelevant in practice
# (capture queues are serial per session and global concurrency is low).
_lock = asyncio.Lock()


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
