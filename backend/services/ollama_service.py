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
import json
import logging
import os
import time
import traceback
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
# Semaphores are lazy-initialized so they bind to the running event loop.
_MODEL_SEMAPHORES: Dict[str, asyncio.Semaphore] = {}
_SEMAPHORE_CAPS = {
    "main": 1,    # gemma4 / olmo / similar large models
    "fast": 2,    # phi4-mini
    "embed": 4,   # embedding models
}


def _semaphore_for_model(model: str) -> asyncio.Semaphore:
    """Return the semaphore for a model name, picking the right bucket
    by matching against settings. Initialized lazily."""
    if model == settings.embedding_model:
        bucket = "embed"
    elif model == settings.ollama_fast_model:
        bucket = "fast"
    else:
        # Default: treat unknown / main model as the heavy bucket.
        bucket = "main"
    if bucket not in _MODEL_SEMAPHORES:
        _MODEL_SEMAPHORES[bucket] = asyncio.Semaphore(_SEMAPHORE_CAPS[bucket])
    return _MODEL_SEMAPHORES[bucket]


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
# FEATURE FLAG (A/B during PB-2a, droppable after PB-2c): default OFF so this is
# a pure no-op port — existing ollama_service callers stay byte-identical to
# before. Set LOCALBOOK_OLLAMA_RAG_PROFILE=1 to enable and A/B against the old
# path. Once the enabled path is validated, flip the default to ON, migrate the
# ollama_client callers (PB-2c), then drop the flag. Audit ref: 10_plan PB-2a.
_RAG_PROFILE_ENABLED = os.getenv("LOCALBOOK_OLLAMA_RAG_PROFILE", "0") == "1"


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
        options["num_ctx"] = min(options["num_ctx"], cap)
    stops = rp.get("stop_sequences")
    if stops and "stop" not in options:
        options["stop"] = list(stops)
    return rp.get("think")


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
        respect_rag_profile: bool = True,
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
        if _profile_think is not None:
            payload["think"] = _profile_think

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
                await sem.acquire()
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
            logger.info(f"[OllamaService] generate OK model={use_model} provider={route.provider.value} caller={_caller} {_elapsed:.1f}s tokens={result.get('eval_count', '?')}")
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
        if _profile_think is not None:
            payload["think"] = _profile_think

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
                await sem.acquire()
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
            logger.info(f"[OllamaService] chat OK model={use_model} provider={route.provider.value} caller={_caller} {_elapsed:.1f}s tokens={result.get('eval_count', '?')}")
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
    ) -> str:
        """Universal vision dispatcher — routes to /api/generate or /api/chat
        per the model's required api_style. Ported from ollama_client (PB-2b);
        signature mirrored exactly so PB-2c migration is a pure rename.

        Param resolution per arg: explicit > vision_profile > global default
        (num_predict=1500, num_ctx=8192, temperature=0.3). num_ctx is passed
        through extra_options since generate/chat don't take it directly. Vision
        calls skip the rag_profile overlay (images present) and the voice
        modifier automatically. Audit ref: 10_plan_of_attack PB-2b.
        """
        model = model or settings.vision_model

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
                )
                return (result.get("message") or {}).get("content", "")
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
                )
                return result.get("response", "")
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
            await sem.acquire()
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
