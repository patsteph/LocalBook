"""
Ollama Client - Shared LLM client for agents

Provides a simple interface for making Ollama API calls across the codebase.
"""
import httpx
import logging
from typing import Optional, Dict, Any

from config import settings

logger = logging.getLogger(__name__)


class OllamaClient:
    """Simple async client for Ollama API calls"""
    
    def __init__(self):
        self.base_url = settings.ollama_base_url.rstrip('/')
    
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
        """
        model = model or settings.ollama_model

        options: Dict[str, Any] = {"temperature": temperature}
        if num_predict is not None:
            options["num_predict"] = num_predict
        if num_ctx is not None:
            options["num_ctx"] = num_ctx
        if extra_options:
            options.update(extra_options)

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

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=timeout)) as client:
                if route.api_style == "ollama":
                    response = await client.post(
                        f"{route.base_url}/api/generate",
                        json=payload,
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
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=timeout)) as client:
                if route.api_style == "ollama":
                    response = await client.post(
                        f"{route.base_url}/api/chat",
                        json=payload,
                    )
                    response.raise_for_status()
                    return response.json()
                else:
                    openai_payload = ollama_to_openai_payload(payload, is_chat=True)
                    response = await client.post(
                        f"{route.base_url}/v1/chat/completions",
                        json=openai_payload,
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

