"""Centralized Ollama Service — Single point of contact for all LLM calls.

Replaces the fragmented pattern of 50+ files each creating their own
httpx.AsyncClient for Ollama API calls. Provides:

1. Connection pooling (one shared httpx.AsyncClient)
2. Token recording on every call (via rag_metrics)
3. Model registry option lookup (per-model temperature, top_k, etc.)
4. Model warmup tracking (mark_*_model_used)
5. keep_alive policy (main=30m, fast=10m)
6. Consistent error handling and logging

Migration guide:
  OLD:  async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{settings.ollama_base_url}/api/generate", ...)
  NEW:  from services.ollama_service import ollama_service
        result = await ollama_service.generate(prompt=..., model=..., temperature=...)
"""
import json
import logging
import time
import traceback
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)


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
        try:
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
        try:
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
        try:
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
