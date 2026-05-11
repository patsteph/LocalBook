"""
Ollama Client - Shared LLM client for agents

Provides a simple interface for making Ollama API calls across the codebase.
"""
import asyncio
import httpx
import logging
from typing import Optional, Dict, Any

from config import settings

logger = logging.getLogger(__name__)


class OllamaClient:
    """Simple async client for Ollama API calls.

    Uses a SHARED httpx.AsyncClient with connection pooling so every
    /api/generate, /api/chat, and /api/embeddings call reuses the same
    TCP connection pool. Previously each call spun up a fresh
    AsyncClient (84 ephemeral clients across the codebase per the
    resource audit) — each new client costs 10-50ms in connection setup
    against localhost Ollama, multiplied across every chat token round
    trip and agent call. Pooling cuts that overhead to ~0.

    The shared client is lazy-initialised on first use and recreated if
    it gets closed (defensive — Python garbage collection of an idle
    client would otherwise force a per-call rebuild).
    """

    def __init__(self):
        self.base_url = settings.ollama_base_url.rstrip('/')
        self._shared_client: Optional[httpx.AsyncClient] = None
        # Single async-lock guards client creation across concurrent
        # first-use callers so we don't accidentally create two clients
        # in a race.
        self._client_lock = asyncio.Lock()

    async def _get_client(self, read_timeout: float) -> httpx.AsyncClient:
        """Return the shared httpx.AsyncClient, building/rebuilding as needed.

        Per-call timeout is applied on each request via httpx.Timeout in
        the call site (the shared client carries only a generous default)
        so a short status probe doesn't dictate the read timeout for a
        long /api/generate call sharing the pool.
        """
        if self._shared_client is not None and not self._shared_client.is_closed:
            return self._shared_client
        async with self._client_lock:
            if self._shared_client is not None and not self._shared_client.is_closed:
                return self._shared_client
            self._shared_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, read=max(read_timeout, 600.0)),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=5,
                    keepalive_expiry=60,
                ),
            )
            return self._shared_client

    async def close(self) -> None:
        """Close the shared client on app shutdown. Idempotent."""
        if self._shared_client is not None and not self._shared_client.is_closed:
            await self._shared_client.aclose()
        self._shared_client = None
    
    async def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.7,
        timeout: float = 300.0,
        num_predict: Optional[int] = None,
        num_ctx: Optional[int] = None,
        extra_options: Optional[Dict[str, Any]] = None,
        images: Optional[list] = None,
        think: Optional[bool] = None,
        response_format: Optional[Dict[str, Any]] = None,
        tools: Optional[list] = None,
        respect_rag_profile: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate a response from the LLM backend.

        Resolves the correct provider (Ollama native vs llama-server sidecar)
        per-call via services.llm_provider. Callers get the same return shape
        regardless of backend (`{"response": "...", ...}` with eval_count etc).

        Args:
            num_ctx: Context window size. None = Ollama default (~2048). Vision models
                     and dense documents typically need 8192+.
            think: Enable/disable thinking mode (Gemma 4). True=thinking on, False=thinking off.
            response_format: JSON schema for structured output, e.g. {"type": "json_object"}.
            tools: List of tool definitions for function calling (native Ollama tools).
            respect_rag_profile: When True (default), automatically apply the active
                model's rag_profile from known_models.json — specifically
                use_chat_endpoint (routes Gemma to /api/chat), think (suppresses
                channel tokens), and num_ctx_cap (Gemma's 16K performance cliff).
                Universal: every model gets ITS OWN tuning applied; for models
                without a rag_profile this is a no-op. Set False for callers
                that need raw /api/generate behaviour (preflight warmup,
                concurrency stress test, vision_describe which has its own
                profile path).
        """
        model = model or settings.ollama_model

        options: Dict[str, Any] = {"temperature": temperature}
        if num_predict is not None:
            options["num_predict"] = num_predict
        if num_ctx is not None:
            options["num_ctx"] = num_ctx
        if extra_options:
            options.update(extra_options)

        # ── Profile-aware overlay ─────────────────────────────────────
        # Apply the active model's rag_profile so a Gemma-family caller
        # gets /api/chat routing (its tuned chat template) without every
        # call site having to switch APIs manually. Same code path for
        # olmo/phi/llama because their rag_profile.use_chat_endpoint is
        # false — no behaviour change for them.
        rag_profile: Dict[str, Any] = {}
        if respect_rag_profile and not tools and not images:
            try:
                from evaluator.model_registry import model_registry
                info = model_registry.get_model(model)
                if info and getattr(info, "rag_profile", None):
                    rag_profile = dict(info.rag_profile)
            except Exception as _e:
                logger.debug(f"[ollama-client] rag_profile lookup failed: {_e}")
            # Apply num_ctx hard cap from profile (e.g. Gemma's 16K cliff).
            cap = rag_profile.get("num_ctx_cap")
            if cap and "num_ctx" in options:
                options["num_ctx"] = min(options["num_ctx"], cap)
            # Apply think:false from profile if caller didn't override.
            if think is None and "think" in rag_profile:
                think = rag_profile["think"]
            # Apply profile-specific stop sequences (olmo gets its aggressive
            # set, gemma gets its channel stops, etc.). Caller can override
            # via extra_options["stop"].
            profile_stops = rag_profile.get("stop_sequences")
            if profile_stops and "stop" not in options:
                options["stop"] = list(profile_stops)

        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if system:
            payload["system"] = system
        if images:
            payload["images"] = images
        if think is not None:
            payload["think"] = think
        if response_format is not None:
            payload["format"] = response_format
        if tools is not None:
            payload["tools"] = tools

        # v1.8.0: provider routing — identical semantics for Ollama path.
        from services.llm_provider import (
            resolve as _resolve_provider,
            ollama_to_openai_payload,
            openai_non_stream_to_ollama_response,
        )
        route = _resolve_provider(model)

        # Gemma-family routing: if rag_profile.use_chat_endpoint=true and
        # we're going to the Ollama backend, switch this generate() call to
        # the chat() path. The caller sees the SAME {"response": ...} shape
        # — we normalize the message.content back into "response" below.
        # tools is a generate-only feature so we keep that path.
        use_chat = (
            respect_rag_profile
            and rag_profile.get("use_chat_endpoint")
            and route.api_style == "ollama"
            and not tools
        )

        try:
            client = await self._get_client(read_timeout=timeout)
            per_call_timeout = httpx.Timeout(30.0, read=timeout)
            if use_chat:
                # Build a /api/chat payload from the same inputs.
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                user_msg: Dict[str, Any] = {"role": "user", "content": prompt}
                if images:
                    user_msg["images"] = images
                messages.append(user_msg)
                chat_payload: Dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": options,
                }
                if think is not None:
                    chat_payload["think"] = think
                if response_format is not None:
                    chat_payload["format"] = response_format
                response = await client.post(
                    f"{route.base_url}/api/chat",
                    json=chat_payload,
                    timeout=per_call_timeout,
                )
                response.raise_for_status()
                raw = response.json()
                # Normalize to /api/generate shape so callers checking
                # `data["response"]` keep working without modification.
                text = (raw.get("message") or {}).get("content", "")
                return {"response": text, **raw}
            elif route.api_style == "ollama":
                response = await client.post(
                    f"{route.base_url}/api/generate",
                    json=payload,
                    timeout=per_call_timeout,
                )
                response.raise_for_status()
                return response.json()
            else:
                # llama-server OpenAI-compatible route (images unsupported there;
                # Bonsai is text-only so this is acceptable).
                openai_payload = ollama_to_openai_payload(payload, is_chat=False)
                response = await client.post(
                    f"{route.base_url}/v1/chat/completions",
                    json=openai_payload,
                    timeout=per_call_timeout,
                )
                response.raise_for_status()
                return openai_non_stream_to_ollama_response(response.json(), is_chat=False)
        except httpx.TimeoutException:
            logger.error(f"LLM request timed out after {timeout}s (model={model}, provider={route.provider.value})")
            return {"response": "Request timed out"}
        except Exception as e:
            logger.error(f"LLM request failed (model={model}, provider={route.provider.value}): {e}")
            return {"response": f"Error: {str(e)}"}

    async def chat(
        self,
        messages: list,
        model: Optional[str] = None,
        temperature: float = 0.7,
        timeout: float = 300.0,
        num_predict: Optional[int] = None,
        num_ctx: Optional[int] = None,
        images: Optional[list] = None,
        think: Optional[bool] = None,
        response_format: Optional[Dict[str, Any]] = None,
        tools: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Chat completion against the resolved backend (Ollama or sidecar).
        Same return shape as Ollama's /api/chat: {"message": {"role": "...", "content": "..."}}.

        Args:
            num_predict: Max tokens to generate. Important for vision/long outputs.
            num_ctx: Context window size. Vision models with images often need 8192+.
            think: Enable/disable thinking mode (Gemma 4). True=thinking on, False=thinking off.
            response_format: JSON schema for structured output, e.g. {"type": "json_object"}.
            tools: List of tool definitions for function calling (native Ollama tools).
        """
        model = model or settings.ollama_model

        # If images are provided, inject them into the last user message
        if images:
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    msg["images"] = images
                    break

        chat_options: Dict[str, Any] = {"temperature": temperature}
        if num_predict is not None:
            chat_options["num_predict"] = num_predict
        if num_ctx is not None:
            chat_options["num_ctx"] = num_ctx

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": chat_options,
        }
        if think is not None:
            payload["think"] = think
        if response_format is not None:
            payload["format"] = response_format
        if tools is not None:
            payload["tools"] = tools

        # v1.8.0: provider routing
        from services.llm_provider import (
            resolve as _resolve_provider,
            ollama_to_openai_payload,
            openai_non_stream_to_ollama_response,
        )
        route = _resolve_provider(model)

        try:
            client = await self._get_client(read_timeout=timeout)
            per_call_timeout = httpx.Timeout(10.0, read=timeout)
            if route.api_style == "ollama":
                response = await client.post(
                    f"{route.base_url}/api/chat",
                    json=payload,
                    timeout=per_call_timeout,
                )
                response.raise_for_status()
                return response.json()
            else:
                openai_payload = ollama_to_openai_payload(payload, is_chat=True)
                response = await client.post(
                    f"{route.base_url}/v1/chat/completions",
                    json=openai_payload,
                    timeout=per_call_timeout,
                )
                response.raise_for_status()
                return openai_non_stream_to_ollama_response(response.json(), is_chat=True)
        except Exception as e:
            logger.error(f"LLM chat request failed (model={model}, provider={route.provider.value}): {e}")
            return {"message": {"content": f"Error: {str(e)}"}}

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
        """
        Universal vision dispatcher — routes to /api/generate or /api/chat
        depending on which API style the vision model requires.

        Parameter resolution priority (per param): explicit arg > vision_profile > global default.
        Global defaults: num_predict=1500, num_ctx=8192, temperature=0.3.
        Vision profile is per-model — see ModelInfo.vision_profile.

        Args:
            image_b64: Base64-encoded image data
            prompt: Text prompt to describe the image
            model: Vision model to use (defaults to settings.vision_model)
            api_style: "generate" for LLaVA/Granite, "chat" for Gemma4/Llama3.2
            timeout: Request timeout
            num_predict: Max tokens for the response. None = profile/global default.
            num_ctx: Context window size. None = profile/global default. Vision needs ≥4096.
            temperature: Sampling temperature. None = profile/global default (0.3).

        Returns:
            The model's text description of the image
        """
        model = model or settings.vision_model

        # Per-model vision_profile from registry (empty dict if none / lookup fails)
        profile: Dict[str, Any] = {}
        try:
            from evaluator.model_registry import model_registry
            info = model_registry.get_model(model)
            if info and getattr(info, "vision_profile", None):
                profile = dict(info.vision_profile)
        except Exception as _e:
            logger.debug(f"[vision] profile lookup failed: {_e}")

        # Resolve final params: explicit arg wins > profile > global default
        final_num_predict = num_predict if num_predict is not None else profile.get("num_predict", 1500)
        final_num_ctx = num_ctx if num_ctx is not None else profile.get("num_ctx", 8192)
        final_temp = temperature if temperature is not None else profile.get("temperature", 0.3)

        try:
            if api_style == "chat":
                # Gemma 4 / Llama 3.2 style — images go inside chat messages
                result = await self.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    temperature=final_temp,
                    timeout=timeout,
                    num_predict=final_num_predict,
                    num_ctx=final_num_ctx,
                    images=[image_b64],
                )
                return result.get("message", {}).get("content", "")
            else:
                # Granite / LLaVA style — images are top-level in /api/generate
                result = await self.generate(
                    prompt=prompt,
                    model=model,
                    temperature=final_temp,
                    timeout=timeout,
                    num_predict=final_num_predict,
                    num_ctx=final_num_ctx,
                    images=[image_b64],
                )
                return result.get("response", "")
        except Exception as e:
            logger.error(f"Vision describe failed: {e}")
            return f"Error: {str(e)}"


# Singleton instance
ollama_client = OllamaClient()

