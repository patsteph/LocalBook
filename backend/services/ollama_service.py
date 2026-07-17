"""Centralized Ollama Service — Single point of contact for all LLM calls.

Replaces the fragmented pattern of 50+ files each creating their own
httpx.AsyncClient for Ollama API calls. Provides:

1. Connection pooling (one shared httpx.AsyncClient)
2. Token recording on every call (via rag_metrics)
3. Model registry option lookup (per-model temperature, top_k, etc.)
4. Model warmup tracking (mark_*_model_used)
5. keep_alive policy (main=30m, fast=10m)
6. Consistent error handling and logging
7. Per-model concurrency caps (P14.H.3, 2026-06-11) — prevents Ollama
   queue collapse when many background paths fan out concurrent calls
   (per-article entity extraction, curator brain inference, memory
   consolidation, IMAP-driven classification, etc.). gemma4 can only
   serve ~1 request at a time on Apple Silicon before tail latency
   explodes; embeddings parallelize better. The semaphores act as a
   process-wide rate limiter that protects ALL callers, including the
   ones we can't easily refactor (curator brain handlers fire as
   asyncio.create_task and bypass any application-level lock).

Migration guide:
  OLD:  async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{settings.ollama_base_url}/api/generate", ...)
  NEW:  from services.ollama_service import ollama_service
        result = await ollama_service.generate(prompt=..., model=..., temperature=...)
"""
import asyncio
import heapq
import itertools
import json
import logging
import os
import time
import traceback
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)


# ── P14.H.3 — Per-model concurrency semaphores ──────────────────────────
# These cap how many concurrent in-flight calls to each model are allowed.
# Calls beyond the cap queue (asyncio.Semaphore is FIFO).
#
# Tuning rationale (Apple Silicon, gemma4:e4b 9.6 GB + phi4-mini 3 GB +
# embed 1 GB on a 16 GB Mac):
#   - Main model (gemma4): 1. Multi-call concurrency causes tail-latency
#     explosion (we observed 603s gemma4 generate when 5+ tasks queued).
#   - Fast model (phi4-mini): 2. Smaller working set, handles 2 concurrent
#     reasonably; protects against the article pipeline + curator stance
#     scoring colliding.
#   - Embedding model: 4. Snowflake-arctic-embed2 is small and fast; the
#     bottleneck is mostly HTTP/IPC. Cap mostly exists to prevent total
#     Ollama queue overflow.
#
# ── Priority lanes (replaces plain FIFO Semaphore) ──────────────────────
# A plain asyncio.Semaphore is strictly FIFO, so a flood of *background*
# work (PDF image-description, community-summary rebuilds, per-article
# analysis) can fully starve a user-initiated *foreground* request: the
# foreground call simply queues behind every background call already in
# line. PriorityLane keeps the same concurrency cap but serves waiters by
# priority — lower number first, FIFO within a priority. Foreground chat /
# visual / doc-gen (NORMAL or FOREGROUND) thus jumps ahead of the
# BACKGROUND ingest flood the moment a slot frees.
PRIORITY_FOREGROUND = 0   # user is actively waiting (chat, visual, doc-gen)
PRIORITY_NORMAL = 1       # default — most callers
PRIORITY_BACKGROUND = 2   # bulk ingest fan-out; yields to everyone else


class PriorityLane:
    """Concurrency limiter like asyncio.Semaphore, but waiters are served
    by priority instead of FIFO. Lower priority value is served first;
    ties break FIFO via a monotonic counter. Drop-in for the acquire/
    release pattern used by the model semaphores."""

    def __init__(self, value: int):
        self._value = value
        self._waiters: list = []  # heap of (priority, seq, future)
        self._counter = itertools.count()

    def locked(self) -> bool:
        return self._value == 0

    async def acquire(self, priority: int = PRIORITY_NORMAL) -> bool:
        # Fast path: a slot is free and nobody is already queued. (Also the
        # cross-loop-safe path — a caller in a separate event loop hits this
        # without touching loop-bound futures, since the lane is uncontended
        # there.)
        if self._value > 0 and not self._waiters:
            self._value -= 1
            return True
        # get_running_loop() (not the deprecated get_event_loop) so the future
        # binds to the loop actually awaiting it.
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        heapq.heappush(self._waiters, (priority, next(self._counter), fut))
        try:
            await fut
        except asyncio.CancelledError:
            # If we were granted the slot just as we got cancelled, hand it
            # on to the next waiter rather than leaking it.
            if fut.done() and not fut.cancelled():
                self._value += 1
                self._wake_next()
            raise
        return True

    def release(self) -> None:
        self._value += 1
        self._wake_next()

    def _wake_next(self) -> None:
        while self._waiters and self._value > 0:
            _priority, _seq, fut = heapq.heappop(self._waiters)
            if fut.cancelled():
                continue  # waiter gave up; skip it
            self._value -= 1
            fut.set_result(True)
            return


# Lanes are lazy-initialized so they bind to the running event loop.
_MODEL_SEMAPHORES: Dict[str, PriorityLane] = {}
_SEMAPHORE_CAPS = {
    "main": 1,    # gemma4 / olmo — see _main_lane_cap() (memory-aware)
    "fast": 2,    # phi4-mini
    "embed": 4,   # embedding models
}


def _main_lane_cap() -> int:
    """Concurrency cap for the heavy (gemma/main) model — MEMORY-AWARE.

    cap=1 is the safe floor: on ≤18 GB boxes 2 concurrent gemma calls cause the
    Ollama tail-latency/thrash that crashed the 18 GB Mac (P14.H.3). On roomy
    machines (≥24 GB) there's headroom for 2 concurrent gemma contexts, which
    un-serializes the big throughput sinks (vision-describe + chat + doc-gen)
    that otherwise queue one-at-a-time. Env override: LOCALBOOK_GEMMA_LANE_CAP.

    (When the MLX text engine lands it replaces this with its own memory-guarded
    thread-per-model scheduler — the RAM *awareness* carries over; only the value
    changes. See READFIRST/in-progress/local-ai-engine-strategy.md.)
    """
    override = os.getenv("LOCALBOOK_GEMMA_LANE_CAP")
    if override and override.strip().isdigit():
        return max(1, int(override))
    try:
        import psutil
        total_gb = psutil.virtual_memory().total / (1024 ** 3)
        return 2 if total_gb >= 24 else 1
    except Exception:
        return 1  # psutil missing → assume constrained, stay safe


# ── Ollama-activity tracker (for SYSTEM-idle gating of enrichment) ──────
# `memory_steward.await_idle` originally gated deferred enrichment (image
# description, HyDE) on USER-idleness only. But a PDF upload kicks off a
# multi-minute BACKGROUND flood — embeddings + community-detection + entity
# extraction — that is NOT user activity, so the user-idle clock ticks past
# its threshold while Ollama is still saturated. Enrichment then fired into
# that flood and stacked 90 s gemma-vision timeouts on the cap-1 lane,
# blocking the chat query (observed 2026-06-23: chat answered only after the
# flood drained). So we also expose "seconds since Ollama last did ANY work":
# every routed call bumps this via `_semaphore_for_model` (the single
# chokepoint for generate/chat/embed/stream). During the dense ingest flood
# it stays fresh; when the flood drains it goes stale → enrichment proceeds on
# a quiet system (fast, no stacking). Warmup pings use raw httpx (not this
# path) so they don't keep the system falsely "busy".
_last_ollama_activity_ts: float = 0.0


def _note_ollama_activity() -> None:
    global _last_ollama_activity_ts
    _last_ollama_activity_ts = time.monotonic()


def seconds_since_ollama_activity() -> float:
    """Seconds since the last Ollama call started (large == Ollama idle)."""
    return time.monotonic() - _last_ollama_activity_ts


def _semaphore_for_model(model: str) -> PriorityLane:
    """Return the priority lane for a model name, picking the right bucket
    by matching against settings. Initialized lazily."""
    _note_ollama_activity()  # every routed call funnels here → system-busy signal
    if model == settings.embedding_model:
        bucket = "embed"
    elif model == settings.ollama_fast_model:
        bucket = "fast"
    else:
        # Default: treat unknown / main model as the heavy bucket.
        bucket = "main"
    if bucket not in _MODEL_SEMAPHORES:
        cap = _main_lane_cap() if bucket == "main" else _SEMAPHORE_CAPS[bucket]
        if bucket == "main":
            logger.info(f"[OllamaService] gemma lane cap={cap} (memory-aware)")
        _MODEL_SEMAPHORES[bucket] = PriorityLane(cap)
    return _MODEL_SEMAPHORES[bucket]


@asynccontextmanager
async def model_lane(model: str, priority: int = PRIORITY_NORMAL):
    """Public accessor to a model's priority lane, for inference callers that
    own their own httpx streaming (e.g. llm_service's chat stream) or haven't been
    fully migrated to generate()/chat() yet. Acquiring this makes a raw-httpx
    call serialize on the SAME lane as generate/chat/embed, so it can't run as
    a 2nd concurrent call to the heavy model (the thrash the lane prevents) and
    it honors foreground/background priority. Hold the block only around the
    network call — the lane is held for the whole `async with` body.

    Usage:
        async with model_lane(model, PRIORITY_FOREGROUND):
            async with client.stream(...) as r: ...
    """
    sem = _semaphore_for_model(model)
    await sem.acquire(priority)
    try:
        yield
    finally:
        sem.release()


def _get_caller() -> str:
    """Return 'file:function' of the external caller (skip ollama_service frames)."""
    for frame in traceback.extract_stack():
        if "ollama_service" not in frame.filename:
            continue
    # Walk backwards to find the first frame NOT in this file
    for frame in reversed(traceback.extract_stack()):
        if "ollama_service" not in frame.filename and frame.name != "<module>":
            fname = frame.filename.rsplit("/", 1)[-1]
            return f"{fname}:{frame.name}"
    return "unknown"


def _get_model_options(model_name: str) -> dict:
    """Look up per-model optimal Ollama generation parameters from the registry.

    Returns the model's ollama_options dict (temperature, top_p, top_k, etc.)
    or an empty dict if the model is unknown. These serve as base defaults
    that can be overridden by per-call parameters.
    """
    try:
        from evaluator.model_registry import model_registry
        info = model_registry.get_model(model_name)
        if info and info.ollama_options:
            return dict(info.ollama_options)
    except Exception as _e:
        logger.debug(f"[ollama-service] {type(_e).__name__}: {_e}")
    return {}


# ── PB-2a: rag_profile overlay (ported from ollama_client) ───────────────────
# Applies the active model's rag_profile (num_ctx_cap + stop_sequences into the
# options dict; `think` returned for the payload) so Gemma-family callers get
# their tuned context cap / thinking suppression / stop sequences without each
# call site managing it. No-op for models without a profile (olmo/phi/llama),
# for vision calls (images), and when the caller opts out (respect=False).
#
# FEATURE FLAG (A/B for PB-2a/2c, droppable once 2c settles): default ON
# (flipped 2026-06-19, after the no-op path validated + the 2c-generate callers
# migrated onto ollama_service). KILL-SWITCH: LOCALBOOK_OLLAMA_RAG_PROFILE=0
# reverts to the old no-overlay behavior for instant rollback. Audit: 10_plan PB-2a.
_RAG_PROFILE_ENABLED = os.getenv("LOCALBOOK_OLLAMA_RAG_PROFILE", "1") != "0"


def _apply_rag_profile(
    model: str, options: dict, respect: bool, images: Optional[list]
) -> Optional[bool]:
    """Apply the model's rag_profile to `options` in place; return the `think`
    override (or None). See the block comment above for semantics + the flag."""
    if images or not (respect and _RAG_PROFILE_ENABLED):
        return None
    try:
        from evaluator.model_registry import model_registry
        info = model_registry.get_model(model)
        rp = dict(getattr(info, "rag_profile", None) or {}) if info else {}
    except Exception as _e:
        logger.debug(f"[ollama-service] rag_profile lookup failed: {_e}")
        return None
    cap = rp.get("num_ctx_cap")
    if cap and "num_ctx" in options:
        # Cap at the RAM-tier-aware effective cap (NOT the raw base cap) so this
        # overlay doesn't undo num_ctx scaling on bigger-RAM machines.
        options["num_ctx"] = min(options["num_ctx"], effective_num_ctx_cap(model))
    stops = rp.get("stop_sequences")
    if stops and "stop" not in options:
        options["stop"] = list(stops)
    return rp.get("think")


# ── num_ctx sizing (2026-07-01) — ONE source of truth for the context window ──
# Root fix for the "~2048 default" clog: callers through ollama_service never set
# num_ctx, so Ollama fell back to its small default and truncated large prompts /
# long JSON output (the quiz "1090-token" truncation, empty-SVG diagrams, choked
# ingest). This mirrors llm_service's auto-size formula and is shared by both wrappers.
# The cap is RAM-tier-aware: 16-18GB machines keep the safe 16K/8K baseline; bigger
# Macs step up (2x / 4x), bounded by the model's native context window.
_TOTAL_RAM_GB: Optional[float] = None


def _total_ram_gb() -> float:
    global _TOTAL_RAM_GB
    if _TOTAL_RAM_GB is None:
        try:
            import psutil
            _TOTAL_RAM_GB = psutil.virtual_memory().total / (1024 ** 3)
        except Exception:
            logger.warning("[ollama-service] psutil unavailable; assuming 16 GB for num_ctx tiering")
            _TOTAL_RAM_GB = 16.0
    return _TOTAL_RAM_GB


def _ram_ctx_multiplier() -> float:
    """Scale the num_ctx cap + context-assembly budget CONTINUOUSLY by system RAM.
    16GB = 1.0x baseline, linear in RAM, capped at 8.0x (bounded anyway by each
    model's native window). A smooth ramp — no artificial tier cliffs — so every
    extra GB of hardware translates to proportionally more capability, and the
    evaluator's 'soft testing' reflects the SAME window a box actually gets."""
    ram = _total_ram_gb()
    return max(1.0, min(8.0, ram / 16.0))


def _model_ctx_limits(model: str) -> tuple:
    """(base num_ctx_cap for 16-18GB, native context_window) from the registry."""
    base_cap, native = 8192, 131072
    try:
        from evaluator.model_registry import model_registry
        info = model_registry.get_model(model)
        if info:
            rp = getattr(info, "rag_profile", None) or {}
            base_cap = rp.get("num_ctx_cap") or base_cap
            native = getattr(info, "context_window", None) or native
    except Exception as _e:
        logger.debug(f"[ollama-service] ctx limits lookup failed: {_e}")
    return base_cap, native


def effective_num_ctx_cap(model: str) -> int:
    """RAM-aware num_ctx cap: baseline cap × continuous RAM multiplier, bounded by
    the model's native window. On 16GB this equals the known_models.json cap; it
    scales smoothly upward with RAM."""
    base_cap, native = _model_ctx_limits(model)
    return min(int(base_cap * _ram_ctx_multiplier()), native)


def compute_num_ctx(model: str, prompt_text: str, num_predict: Optional[int]) -> Optional[int]:
    """Auto-size the Ollama context window so input + output fit, capped at the
    RAM-tier-aware effective cap. Returns None (leave Ollama's default) only for
    genuinely tiny calls (short prompt AND small output) to conserve KV-cache
    memory. Triggers on large INPUT too (not just num_predict>500), so big-input
    ingest calls aren't silently truncated."""
    np = num_predict or 0
    est_prompt_tokens = len(prompt_text or "") // 3  # ~1 token / 3 chars (conservative)
    needed = est_prompt_tokens + np + 512
    if needed <= 1536:
        return None
    return min(max(8192, needed), effective_num_ctx_cap(model))


def clamp_num_predict(prompt_text: str, num_predict: Optional[int], num_ctx: Optional[int]) -> Optional[int]:
    """P4: when the RAM-tier cap binds num_ctx below prompt+output, cap num_predict to
    what actually fits the window (leaving room for the prompt). Prevents requesting
    more output tokens than the context can hold on small-RAM boxes. No-op when num_ctx
    is unset (Ollama default) or the request already fits — so it never shortens a
    generation that fits its window (e.g. a large quiz on a 16K-cap box)."""
    if not num_predict or not num_ctx:
        return num_predict
    est_prompt_tokens = len(prompt_text or "") // 3
    available = max(256, num_ctx - est_prompt_tokens - 128)  # floor + small safety margin
    if num_predict > available:
        logger.debug(
            f"[OllamaService] clamped num_predict {num_predict}→{available} to fit num_ctx={num_ctx}"
        )
        return available
    return num_predict


def _record_tokens(data: dict):
    """Extract and record token usage from an Ollama response/final chunk."""
    try:
        prompt_tokens = data.get("prompt_eval_count", 0) or 0
        completion_tokens = data.get("eval_count", 0) or 0
        eval_duration_ns = data.get("eval_duration", 0) or 0
        if prompt_tokens > 0 or completion_tokens > 0:
            from services.rag_metrics import rag_metrics
            rag_metrics.record_tokens(prompt_tokens, completion_tokens, eval_duration_ns)
    except Exception as _e:
        logger.debug(f"[ollama-service] {type(_e).__name__}: {_e}")


def _mark_model_used(model: str):
    """Track model usage for warmup service."""
    try:
        from services.model_warmup import mark_fast_model_used, mark_main_model_used
        if model == settings.ollama_fast_model:
            mark_fast_model_used()
        else:
            mark_main_model_used()
    except Exception as _e:
        logger.debug(f"[ollama-service] {type(_e).__name__}: {_e}")


def _keep_alive_for(model: str):
    """Return keep_alive policy: 5m for all models — warmup loop re-pings active ones."""
    return "5m"


class OllamaService:
    """Shared Ollama API client with connection pooling and cross-cutting concerns.

    All LLM calls in the application should go through this service.
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared httpx client with connection pooling."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, read=600.0),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=5,
                    keepalive_expiry=60,
                ),
            )
        return self._client

    async def close(self):
        """Close the shared client. Called during app shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── Non-streaming generate (/api/generate) ────────────────────────

    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        num_predict: Optional[int] = None,
        timeout: Optional[float] = None,
        extra_options: Optional[Dict[str, Any]] = None,
        format: Optional[str] = None,
        images: Optional[List[str]] = None,
        keep_alive: Optional[Any] = None,
        voice_modifier: bool = True,
        think: Optional[bool] = None,  # explicit override of the rag_profile think flag
        respect_rag_profile: bool = True,
        priority: int = PRIORITY_NORMAL,
    ) -> Dict[str, Any]:
        """Non-streaming generate call to Ollama /api/generate.

        Args:
            prompt: The user prompt text.
            model: Ollama model name. Defaults to settings.ollama_model.
            system: System prompt prepended to the prompt.
            temperature: Override model registry default temperature.
            num_predict: Max tokens to generate.
            timeout: Read timeout in seconds (default 600s).
            extra_options: Additional Ollama options merged last.
            format: Set to "json" for JSON mode.
            images: List of base64-encoded images (for vision models).
            keep_alive: Override default keep_alive policy.
            voice_modifier: Prepend the active model's voice/tone instruction
                to the system prompt. Defaults True. Set False for callers
                that produce structured output (JSON / SVG / Mermaid /
                vision OCR transcription) where prose-tone guidance would
                contaminate format-sensitive output. Auto-disabled when
                format='json' or when images are present (vision call).

        Returns:
            Full Ollama response dict (with 'response', token stats, etc.)
        """
        use_model = model or settings.ollama_model
        model_defaults = _get_model_options(use_model)
        options = {**model_defaults}
        if num_predict is not None:
            options["num_predict"] = num_predict
        if temperature is not None:
            options["temperature"] = temperature
        if extra_options:
            options.update(extra_options)

        # Auto-size the context window (input+output), RAM-tier-capped. Without this
        # the caller gets Ollama's ~2048 default and truncates large prompts / long
        # JSON output. Skip if the caller set num_ctx explicitly via extra_options.
        if "num_ctx" not in options:
            _nc = compute_num_ctx(use_model, f"{system or ''}\n\n{prompt or ''}", num_predict)
            if _nc:
                options["num_ctx"] = _nc

        # P4: cap num_predict to the resolved window so we never ask for more output
        # tokens than num_ctx can hold (the RAM-tier cap binds on small-RAM boxes).
        if options.get("num_predict") and options.get("num_ctx"):
            options["num_predict"] = clamp_num_predict(
                f"{system or ''}\n\n{prompt or ''}", options["num_predict"], options["num_ctx"]
            )

        # PB-2a: rag_profile overlay (num_ctx cap / stop sequences / think).
        _profile_think = _apply_rag_profile(use_model, options, respect_rag_profile, images)

        # Voice modifier: inject family-specific tone instruction unless
        # the caller is producing structured output (JSON, vision OCR).
        if voice_modifier and not format and not images and system:
            from services.voice_modifier import voiced_system as _voiced
            system = _voiced(system, model_name=use_model)

        full_prompt = f"{system}\n\n{prompt}" if system else prompt

        payload: Dict[str, Any] = {
            "model": use_model,
            "prompt": full_prompt,
            "stream": False,
            "keep_alive": keep_alive if keep_alive is not None else _keep_alive_for(use_model),
            "options": options,
        }
        if format:
            payload["format"] = format
        if images:
            payload["images"] = images
        _final_think = think if think is not None else _profile_think
        if _final_think is not None:
            payload["think"] = _final_think

        # Wave 9.2b — MLX engine route for text + STRUCTURED (dual-engine). structured_llm's
        # JSON methods call this with the main model + format="json"; when main_engine=mlx we
        # generate in-process via mlx-vlm (gemma) and let the caller's robust_json_parse handle
        # validity (prompt+parse validated 9/9 — no Outlines dep needed). Vision (images) routes
        # through vision_describe (9.3), not here. Falls back to Ollama on error.
        if not images:
            try:
                from services.mlx_engine import mlx_engine, mlx_model_for_role
                _mlx_id = mlx_model_for_role(use_model)
            except Exception:
                _mlx_id = None
            if _mlx_id and mlx_engine.available():
                try:
                    _res = await mlx_engine.generate(
                        prompt, model=_mlx_id, system=system,
                        temperature=options.get("temperature", 0.3),
                        num_predict=options.get("num_predict", 500),
                        num_ctx=options.get("num_ctx"), format=format, stop=None)
                    _record_tokens(_res)
                    _mark_model_used(use_model)
                    logger.info(f"[OllamaService] MLX generate OK model={use_model}→{_mlx_id} "
                                f"format={format} tokens={_res.get('eval_count', '?')}")
                    return _res
                except Exception as _mlx_e:
                    logger.warning(f"[OllamaService] MLX generate failed ({use_model}→{_mlx_id}); "
                                   f"Ollama fallback: {_mlx_e}")

        client = self._get_client()
        read_timeout = timeout or 600.0
        _caller = _get_caller()
        _t0 = time.time()
        # v1.7.0: resolve provider. Ollama path is byte-identical to pre-provider code.
        from services.llm_provider import (
            resolve as _resolve_provider,
            ollama_to_openai_payload,
            openai_non_stream_to_ollama_response,
        )
        route = _resolve_provider(use_model)
        # P14.H.3 — acquire per-model semaphore before the network call.
        # Caps concurrent in-flight calls so background fan-out (curator
        # brain, per-article entity extraction, memory consolidation)
        # can't collapse Ollama's queue.
        sem = _semaphore_for_model(use_model) if route.api_style == "ollama" else None
        try:
            if sem is not None:
                await sem.acquire(priority)
            if route.api_style == "ollama":
                response = await client.post(
                    f"{route.base_url}/api/generate",
                    json=payload,
                    timeout=httpx.Timeout(10.0, read=read_timeout),
                )
                response.raise_for_status()
                result = response.json()
            else:
                openai_payload = ollama_to_openai_payload(payload, is_chat=False)
                response = await client.post(
                    f"{route.base_url}/v1/chat/completions",
                    json=openai_payload,
                    timeout=httpx.Timeout(10.0, read=read_timeout),
                )
                response.raise_for_status()
                result = openai_non_stream_to_ollama_response(response.json(), is_chat=False)
            _record_tokens(result)
            _mark_model_used(use_model)
            _elapsed = time.time() - _t0
            logger.info(f"[OllamaService] generate OK model={use_model} provider={route.provider.value} caller={_caller} {_elapsed:.1f}s tokens={result.get('eval_count', '?')} ctx={options.get('num_ctx', 'def')}")
            return result
        except httpx.TimeoutException:
            _elapsed = time.time() - _t0
            logger.error(f"[OllamaService] generate TIMEOUT model={use_model} caller={_caller} {_elapsed:.1f}s")
            return {"response": ""}
        except httpx.HTTPStatusError as e:
            _elapsed = time.time() - _t0
            logger.error(f"[OllamaService] generate HTTP {e.response.status_code} model={use_model} caller={_caller} {_elapsed:.1f}s: {e.response.text[:200]}")
            return {"response": ""}
        except Exception as e:
            _elapsed = time.time() - _t0
            logger.error(f"[OllamaService] generate FAILED model={use_model} caller={_caller} {_elapsed:.1f}s: {e}")
            return {"response": ""}
        finally:
            if sem is not None:
                sem.release()

    # ── Non-streaming chat (/api/chat) ────────────────────────────────

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
        extra_options: Optional[Dict[str, Any]] = None,
        images: Optional[List[str]] = None,
        keep_alive: Optional[Any] = None,
        voice_modifier: bool = True,
        respect_rag_profile: bool = True,
        priority: int = PRIORITY_NORMAL,
        think: Optional[bool] = None,  # explicit override of the rag_profile think flag
    ) -> Dict[str, Any]:
        """Non-streaming chat call to Ollama /api/chat.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            model: Ollama model name. Defaults to settings.ollama_model.
            temperature: Override model registry default temperature.
            timeout: Read timeout in seconds.
            extra_options: Additional Ollama options.
            images: Injected into the last user message.
            keep_alive: Override default keep_alive policy.
            voice_modifier: Prepend the active model's voice instruction
                to the first system message. Auto-disabled if images are
                present (vision call). Defaults True.

        Returns:
            Full Ollama response dict (with 'message', token stats, etc.)
        """
        use_model = model or settings.ollama_model
        model_defaults = _get_model_options(use_model)
        options = {**model_defaults}
        if temperature is not None:
            options["temperature"] = temperature
        if extra_options:
            options.update(extra_options)

        # Auto-size the context window. Chat has no num_predict param — read it from
        # options; estimate input from the concatenated message text. Skip if the
        # caller set num_ctx explicitly.
        if "num_ctx" not in options:
            _msg_text = "\n".join(str(m.get("content") or "") for m in messages)
            _nc = compute_num_ctx(use_model, _msg_text, options.get("num_predict"))
            if _nc:
                options["num_ctx"] = _nc

        # PB-2a: rag_profile overlay (num_ctx cap / stop sequences / think).
        _profile_think = _apply_rag_profile(use_model, options, respect_rag_profile, images)

        # Voice modifier: prepend tone instruction to the FIRST system
        # message. Skips for vision calls (images present) where the model
        # is doing OCR / scene description, not prose generation.
        if voice_modifier and not images and messages:
            from services.voice_modifier import voiced_system as _voiced
            for msg in messages:
                if msg.get("role") == "system":
                    voiced = _voiced(msg.get("content", ""), model_name=use_model)
                    if voiced:
                        msg["content"] = voiced
                    break

        if images:
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    msg["images"] = images
                    break

        payload: Dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "stream": False,
            "keep_alive": keep_alive if keep_alive is not None else _keep_alive_for(use_model),
            "options": options,
        }
        _final_think = think if think is not None else _profile_think
        if _final_think is not None:
            payload["think"] = _final_think

        client = self._get_client()
        read_timeout = timeout or 600.0
        _caller = _get_caller()
        _t0 = time.time()
        # v1.7.0: resolve provider. Ollama path is byte-identical to pre-provider code.
        from services.llm_provider import (
            resolve as _resolve_provider,
            ollama_to_openai_payload,
            openai_non_stream_to_ollama_response,
        )
        route = _resolve_provider(use_model)
        # P14.H.3 — per-model semaphore (see generate())
        sem = _semaphore_for_model(use_model) if route.api_style == "ollama" else None
        try:
            if sem is not None:
                await sem.acquire(priority)
            if route.api_style == "ollama":
                response = await client.post(
                    f"{route.base_url}/api/chat",
                    json=payload,
                    timeout=httpx.Timeout(10.0, read=read_timeout),
                )
                response.raise_for_status()
                result = response.json()
            else:
                openai_payload = ollama_to_openai_payload(payload, is_chat=True)
                response = await client.post(
                    f"{route.base_url}/v1/chat/completions",
                    json=openai_payload,
                    timeout=httpx.Timeout(10.0, read=read_timeout),
                )
                response.raise_for_status()
                result = openai_non_stream_to_ollama_response(response.json(), is_chat=True)
            _record_tokens(result)
            _mark_model_used(use_model)
            _elapsed = time.time() - _t0
            logger.info(f"[OllamaService] chat OK model={use_model} provider={route.provider.value} caller={_caller} {_elapsed:.1f}s tokens={result.get('eval_count', '?')} ctx={options.get('num_ctx', 'def')}")
            return result
        except httpx.TimeoutException:
            _elapsed = time.time() - _t0
            logger.error(f"[OllamaService] chat TIMEOUT model={use_model} caller={_caller} {_elapsed:.1f}s")
            return {"message": {"content": ""}}
        except Exception as e:
            _elapsed = time.time() - _t0
            logger.error(f"[OllamaService] chat FAILED model={use_model} caller={_caller} {_elapsed:.1f}s: {e}")
            return {"message": {"content": ""}}
        finally:
            if sem is not None:
                sem.release()

    # ── Vision (dispatches to generate/chat) ──────────────────────────

    async def vision_describe(
        self,
        image_b64: str,
        prompt: str,
        model: Optional[str] = None,
        api_style: str = "generate",
        timeout: float = 90.0,
        num_predict: Optional[int] = None,
        num_ctx: Optional[int] = None,
        temperature: Optional[float] = None,
        priority: int = PRIORITY_NORMAL,
        ocr_mode: bool = False,
    ) -> str:
        """Universal vision dispatcher — routes to /api/generate or /api/chat
        per the model's required api_style. Ported from ollama_client (PB-2b).

        ocr_mode: when True the call is pure document/page TEXT EXTRACTION, so
        we try free on-device Apple Vision OCR first (no model load, no lane,
        no RAM) and only fall back to the LLM vision path if Vision is
        unavailable or errors. Leave False for scene/chart DESCRIPTION, which
        needs the model's understanding, not raw OCR.

        Param resolution per arg: explicit > vision_profile > global default
        (num_predict=1500, num_ctx=8192, temperature=0.3). Vision calls skip
        the rag_profile overlay (images present) and the voice modifier.
        """
        # Apple Vision OCR fast-path for text-extraction calls.
        if ocr_mode:
            try:
                from services.apple_vision_ocr import recognize_text as _av_ocr
                _txt = await _av_ocr(image_b64)
                if _txt is not None:  # "" (no text found) still counts as success
                    logger.info(f"[OllamaService] vision OCR via Apple Vision ({len(_txt)} chars, no model load)")
                    return _txt
            except Exception as _e:
                logger.debug(f"[apple-vision] fast-path skipped: {_e}")

        # No explicit model → Option A vision routing (a vision-capable main
        # model absorbs the slot; granite fallback only when it can't), so a
        # model-less caller doesn't 404 on a machine without granite. Mirrors
        # multimodal_extractor / scan_pipeline.
        if not model:
            try:
                from evaluator.model_registry import model_registry
                model = model_registry.resolve_vision_model(settings.ollama_model, settings.vision_model)
            except Exception:
                model = settings.vision_model

        # Wave 9.3 — MLX semantic-vision route (dual-engine). OCR already went to Apple Vision
        # (fast-path above); this is chart/diagram/photo DESCRIPTION → mlx-vlm gemma when
        # vision_engine=mlx (same one gemma load as text). Falls back to Ollama on error.
        try:
            from services.mlx_engine import mlx_engine, mlx_vision_model_if_enabled
            _mlx_vid = mlx_vision_model_if_enabled()
        except Exception:
            _mlx_vid = None
        if _mlx_vid and mlx_engine.available():
            try:
                _res = await mlx_engine.vision_describe(
                    image_b64, prompt, model=_mlx_vid, num_predict=num_predict or 400)
                _mark_model_used(_mlx_vid)
                _desc = _res.get("response", "")
                logger.info(f"[OllamaService] MLX vision OK model→{_mlx_vid} ({len(_desc)} chars)")
                return _desc
            except Exception as _mlx_e:
                logger.warning(f"[OllamaService] MLX vision failed (→{_mlx_vid}); Ollama fallback: {_mlx_e}")

        profile: Dict[str, Any] = {}
        try:
            from evaluator.model_registry import model_registry
            info = model_registry.get_model(model)
            if info and getattr(info, "vision_profile", None):
                profile = dict(info.vision_profile)
        except Exception as _e:
            logger.debug(f"[vision] profile lookup failed: {_e}")

        final_num_predict = num_predict if num_predict is not None else profile.get("num_predict", 1500)
        final_num_ctx = num_ctx if num_ctx is not None else profile.get("num_ctx", 8192)
        final_temp = temperature if temperature is not None else profile.get("temperature", 0.3)

        try:
            if api_style == "chat":
                # Gemma 4 / Llama 3.2 — images go inside chat messages.
                result = await self.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    temperature=final_temp,
                    timeout=timeout,
                    extra_options={"num_predict": final_num_predict, "num_ctx": final_num_ctx},
                    images=[image_b64],
                    voice_modifier=False,
                    priority=priority,
                    think=False,  # 2026-07-07: gemma4 & other thinking-capable vision
                    # models route the WHOLE description to the `thinking` field when
                    # think is on, leaving content empty. We want the description.
                )
                _msg = result.get("message") or {}
                return _msg.get("content") or _msg.get("thinking") or "" 
            else:
                # Granite / LLaVA — images are top-level in /api/generate.
                result = await self.generate(
                    prompt=prompt,
                    model=model,
                    temperature=final_temp,
                    timeout=timeout,
                    num_predict=final_num_predict,
                    extra_options={"num_ctx": final_num_ctx},
                    images=[image_b64],
                    voice_modifier=False,
                    priority=priority,
                    think=False,  # see chat path note above
                )
                return result.get("response") or result.get("thinking") or "" 
        except Exception as e:
            logger.error(f"[OllamaService] vision_describe FAILED model={model}: {e}")
            return f"Error: {str(e)}"

    # ── Embeddings (/api/embed) ───────────────────────────────────────

    async def embed(
        self,
        text: str,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        keep_alive: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Get embeddings from Ollama /api/embed.

        Args:
            text: Text to embed.
            model: Embedding model. Defaults to settings.embedding_model.
            timeout: Read timeout in seconds.
            keep_alive: Override default keep_alive.

        Returns:
            Full Ollama response dict (with 'embeddings' key).
        """
        use_model = model or settings.embedding_model

        payload = {
            "model": use_model,
            "input": text,
            "keep_alive": keep_alive if keep_alive is not None else "5m",
        }

        client = self._get_client()
        read_timeout = timeout or 120.0
        _caller = _get_caller()
        _t0 = time.time()
        # P14.H.3 — per-model semaphore (see generate())
        sem = _semaphore_for_model(use_model)
        try:
            await sem.acquire()
            response = await client.post(
                f"{settings.ollama_base_url}/api/embed",
                json=payload,
                timeout=httpx.Timeout(10.0, read=read_timeout),
            )
            response.raise_for_status()
            _elapsed = time.time() - _t0
            logger.info(f"[OllamaService] embed OK model={use_model} caller={_caller} {_elapsed:.1f}s")
            return response.json()
        except Exception as e:
            _elapsed = time.time() - _t0
            logger.error(f"[OllamaService] embed FAILED model={use_model} caller={_caller} {_elapsed:.1f}s: {e}")
            return {}
        finally:
            sem.release()

    async def embed_batch(
        self,
        texts: List[str],
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        keep_alive: Optional[Any] = None,
        max_batch: int = 64,
    ) -> List[List[float]]:
        """Embed many texts in the FEWEST round-trips.

        Ollama's /api/embed accepts ``input`` as a list and returns one vector per
        item in a single response. Callers used to fire one HTTP request per chunk
        (thousands per big ingest → the 2026-06-26 loop-freeze); this issues one
        request per ``max_batch`` slice instead. Order preserved; a failed or
        shape-mismatched sub-batch falls back to zero vectors (logged) so retrieval
        gaps stay visible rather than silently corrupting the index.
        """
        if not texts:
            return []
        use_model = model or settings.embedding_model
        read_timeout = timeout or 120.0
        client = self._get_client()
        sem = _semaphore_for_model(use_model)
        zero = [0.0] * settings.embedding_dim
        out: List[List[float]] = []
        for start in range(0, len(texts), max_batch):
            sub = texts[start:start + max_batch]
            _caller = _get_caller()
            _t0 = time.time()
            try:
                await sem.acquire()
                response = await client.post(
                    f"{settings.ollama_base_url}/api/embed",
                    json={
                        "model": use_model,
                        "input": sub,
                        "keep_alive": keep_alive if keep_alive is not None else "5m",
                    },
                    timeout=httpx.Timeout(10.0, read=read_timeout),
                )
                response.raise_for_status()
                embs = response.json().get("embeddings") or []
                _elapsed = time.time() - _t0
                logger.info(
                    f"[OllamaService] embed_batch OK model={use_model} n={len(sub)} "
                    f"caller={_caller} {_elapsed:.1f}s"
                )
                if len(embs) == len(sub):
                    out.extend(e if (e and len(e) == settings.embedding_dim) else zero for e in embs)
                else:
                    logger.error(
                        f"[OllamaService] embed_batch shape mismatch {len(embs)}≠{len(sub)} — zero-filling"
                    )
                    out.extend(zero for _ in sub)
            except Exception as e:
                _elapsed = time.time() - _t0
                logger.error(
                    f"[OllamaService] embed_batch FAILED model={use_model} n={len(sub)} "
                    f"caller={_caller} {_elapsed:.1f}s: {e}"
                )
                out.extend(zero for _ in sub)
            finally:
                sem.release()
        return out

    # ── Streaming generate (/api/generate, stream=True) ───────────────

    async def stream_generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        num_predict: Optional[int] = None,
        timeout: Optional[float] = None,
        extra_options: Optional[Dict[str, Any]] = None,
        stop: Optional[List[str]] = None,
        keep_alive: Optional[Any] = None,
        priority: int = PRIORITY_NORMAL,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Streaming generate — yields parsed JSON chunks from Ollama.

        Each chunk is the raw Ollama JSON dict. The caller can extract
        chunk["response"] for tokens and check chunk["done"] for the final chunk.

        Args:
            prompt: The user prompt.
            model: Ollama model name.
            system: System prompt.
            temperature: Override temperature.
            num_predict: Max tokens.
            timeout: Read timeout.
            extra_options: Merged last into options.
            stop: Stop sequences.
            keep_alive: Override keep_alive.

        Yields:
            Parsed JSON dicts from the Ollama streaming response.
        """
        use_model = model or settings.ollama_model
        model_defaults = _get_model_options(use_model)
        options = {**model_defaults}
        if num_predict is not None:
            options["num_predict"] = num_predict
        if temperature is not None:
            options["temperature"] = temperature
        if extra_options:
            options.update(extra_options)

        # Auto-size the context window (input+output), RAM-tier-capped — same as
        # non-streaming generate. Skip if the caller set num_ctx explicitly.
        if "num_ctx" not in options:
            _nc = compute_num_ctx(use_model, f"{system or ''}\n\n{prompt or ''}", num_predict)
            if _nc:
                options["num_ctx"] = _nc

        full_prompt = f"{system}\n\n{prompt}" if system else prompt

        payload: Dict[str, Any] = {
            "model": use_model,
            "prompt": full_prompt,
            "stream": True,
            "keep_alive": keep_alive if keep_alive is not None else _keep_alive_for(use_model),
            "options": options,
        }
        if stop:
            payload["stop"] = stop

        _mark_model_used(use_model)
        client = self._get_client()
        read_timeout = timeout or 600.0
        _caller = _get_caller()
        _t0 = time.time()
        # v1.7.0: provider routing for streaming
        from services.llm_provider import (
            resolve as _resolve_provider,
            ollama_to_openai_payload,
            openai_stream_chunk_to_ollama,
        )
        route = _resolve_provider(use_model)
        # P14.H.3 — semaphore for streaming generate too. Held for the
        # full duration of the stream (which is short for interactive
        # chat). Without this, a streaming chat could start while a
        # bg gemma4 call holds the non-streaming semaphore → still 2
        # concurrent calls hitting Ollama.
        sem = _semaphore_for_model(use_model) if route.api_style == "ollama" else None
        if sem is not None:
            await sem.acquire(priority)
        try:
            if route.api_style == "ollama":
                async with client.stream(
                    "POST",
                    f"{route.base_url}/api/generate",
                    json=payload,
                    timeout=httpx.Timeout(10.0, read=read_timeout),
                ) as response:
                    async for line in response.aiter_lines():
                        if line:
                            data = json.loads(line)
                            yield data
                            if data.get("done"):
                                _record_tokens(data)
                                _elapsed = time.time() - _t0
                                logger.info(f"[OllamaService] stream OK model={use_model} provider={route.provider.value} caller={_caller} {_elapsed:.1f}s tokens={data.get('eval_count', '?')}")
            else:
                openai_payload = ollama_to_openai_payload(payload, is_chat=False)
                async with client.stream(
                    "POST",
                    f"{route.base_url}/v1/chat/completions",
                    json=openai_payload,
                    timeout=httpx.Timeout(10.0, read=read_timeout),
                ) as response:
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload_text = line[5:].strip()
                        if not payload_text or payload_text == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(payload_text)
                        except Exception:
                            continue
                        translated = openai_stream_chunk_to_ollama(chunk, is_chat=False)
                        if translated is None:
                            continue
                        yield translated
                        if translated.get("done"):
                            _record_tokens(translated)
                            _elapsed = time.time() - _t0
                            logger.info(f"[OllamaService] stream OK model={use_model} provider={route.provider.value} caller={_caller} {_elapsed:.1f}s tokens={translated.get('eval_count', '?')}")
        except httpx.TimeoutException:
            _elapsed = time.time() - _t0
            logger.error(f"[OllamaService] stream TIMEOUT model={use_model} caller={_caller} {_elapsed:.1f}s")
            raise
        except Exception as e:
            _elapsed = time.time() - _t0
            logger.error(f"[OllamaService] stream FAILED model={use_model} caller={_caller} {_elapsed:.1f}s: {e}")
            raise
        finally:
            if sem is not None:
                sem.release()

    # ── Utility: model info / availability ────────────────────────────

    async def check_model(self, model: str, timeout: float = 10.0) -> bool:
        """Quick check if a model is available in Ollama."""
        client = self._get_client()
        try:
            response = await client.post(
                f"{settings.ollama_base_url}/api/show",
                json={"name": model},
                timeout=httpx.Timeout(timeout),
            )
            return response.status_code == 200
        except Exception:
            return False

    async def list_models(self, timeout: float = 10.0) -> List[Dict[str, Any]]:
        """List all locally available Ollama models."""
        client = self._get_client()
        try:
            response = await client.get(
                f"{settings.ollama_base_url}/api/tags",
                timeout=httpx.Timeout(timeout),
            )
            response.raise_for_status()
            return response.json().get("models", [])
        except Exception as e:
            logger.error(f"[OllamaService] list_models failed: {e}")
            return []


ollama_service = OllamaService()
