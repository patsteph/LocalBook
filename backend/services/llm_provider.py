"""
LLM Provider — tightly-scoped routing layer for multi-backend LLM support.

Phase 1 of the Bonsai-8B integration plan (see READFIRST/BONSAI_INTEGRATION_PLAN.md).

Responsibilities:
- Given a model name, resolve the correct backend (Ollama vs llama-server sidecar)
  along with the API style and base URL.
- Health-check each backend with a short timeout and no retries.
- Translate Ollama-native payloads to/from OpenAI-compatible shapes so the
  existing chat-streaming code paths can target llama-server with minimal diff.

Safety invariants enforced by this module:
- When no sidecar models are registered, `resolve()` always returns the Ollama
  endpoint — behaviour is byte-identical to the pre-existing code path.
- Every caller gets the same `ProviderRoute` dataclass; branching on api_style
  is done in exactly two places (llm_service.stream_text, ollama_service) and
  nowhere else in the codebase.
- Health checks are opt-in and cheap; they never block request-time calls.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import settings

logger = logging.getLogger(__name__)


# ─── Provider enum ──────────────────────────────────────────────────────────────

class Provider(str, Enum):
    OLLAMA = "ollama"                 # default — native /api/generate, /api/chat
    LLAMA_SERVER = "llama_server"     # OpenAI-compatible sidecar (/v1/chat/completions)

    @classmethod
    def from_str(cls, value: Optional[str]) -> "Provider":
        if not value:
            return cls.OLLAMA
        try:
            return cls(value.lower())
        except ValueError:
            return cls.OLLAMA


# ─── Route dataclass ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProviderRoute:
    """Resolved backend target for a given model.

    api_style determines which payload shape the caller should emit:
      - "ollama": POST {base_url}/api/generate or /api/chat with Ollama's native JSON
      - "openai": POST {base_url}/v1/chat/completions with OpenAI-compatible JSON

    supports_keep_alive is False for llama-server (always resident by design).
    reports_native_tokens is True for Ollama (eval_count), False for OpenAI
    (usage.{prompt,completion}_tokens lives in a different field).
    """
    provider: Provider
    base_url: str              # no trailing slash
    api_style: str             # "ollama" | "openai"
    supports_keep_alive: bool
    reports_native_tokens: bool


# ─── Config ─────────────────────────────────────────────────────────────────────

# Default sidecar URL — matches the research doc's recommended llama-server port.
# Override with LOCALBOOK_LLAMA_SERVER_URL env var if needed.
DEFAULT_LLAMA_SERVER_URL = os.environ.get(
    "LOCALBOOK_LLAMA_SERVER_URL", "http://127.0.0.1:8090"
).rstrip("/")


def _ollama_route() -> ProviderRoute:
    """Build the Ollama route from current settings."""
    return ProviderRoute(
        provider=Provider.OLLAMA,
        base_url=settings.ollama_base_url.rstrip("/"),
        api_style="ollama",
        supports_keep_alive=True,
        reports_native_tokens=True,
    )


def _llama_server_route() -> ProviderRoute:
    return ProviderRoute(
        provider=Provider.LLAMA_SERVER,
        base_url=DEFAULT_LLAMA_SERVER_URL,
        api_style="openai",
        supports_keep_alive=False,
        reports_native_tokens=False,
    )


# ─── Resolver ───────────────────────────────────────────────────────────────────

def _lookup_provider_for_model(model_name: str) -> Provider:
    """Decide which provider a model name belongs to.

    Lookup order:
      1. Static registry entry with `provider` field (known_models.json).
      2. Default: OLLAMA.

    Does NOT consult Ollama or llama-server health — that is the caller's
    responsibility when it matters (e.g. Locker pre-flight).
    """
    if not model_name:
        return Provider.OLLAMA
    try:
        from evaluator.model_registry import model_registry
        info = model_registry.get_model(model_name)
        if info is not None:
            raw = getattr(info, "provider", None) or "ollama"
            return Provider.from_str(raw)
    except Exception as _e:
        logger.debug(f"[llm-provider] registry lookup failed: {_e}")
    return Provider.OLLAMA


def resolve(model_name: str) -> ProviderRoute:
    """Resolve the backend route for a given model name.

    Safe fallback: any unknown model returns the Ollama route so existing
    behaviour is preserved. The llama-server route is ONLY returned when the
    model is explicitly tagged `provider: "llama_server"` in known_models.json.
    """
    provider = _lookup_provider_for_model(model_name)
    if provider is Provider.LLAMA_SERVER:
        return _llama_server_route()
    return _ollama_route()


# ─── Health checks ──────────────────────────────────────────────────────────────

# Tiny TTL cache so repeated calls (e.g. model list enrichment) don't hammer
# the endpoint. Reset on-demand via `invalidate_health_cache()`.
_HEALTH_TTL_SECONDS = 10
_health_cache: Dict[Provider, Tuple[float, bool]] = {}


def invalidate_health_cache() -> None:
    _health_cache.clear()


async def health_check(provider: Provider, timeout: float = 1.5) -> bool:
    """Return True iff the backend for `provider` responds to a health probe.

    - Ollama: GET /api/version
    - llama-server: GET /health (returns {"status":"ok"} when model loaded)

    Uses a short timeout + in-memory TTL cache. Never raises.
    """
    now = time.monotonic()
    cached = _health_cache.get(provider)
    if cached and (now - cached[0]) < _HEALTH_TTL_SECONDS:
        return cached[1]

    route = _ollama_route() if provider is Provider.OLLAMA else _llama_server_route()
    probe_path = "/api/version" if provider is Provider.OLLAMA else "/health"
    url = f"{route.base_url}{probe_path}"

    ok = False
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            ok = resp.status_code == 200
    except Exception as _e:
        logger.debug(f"[llm-provider] health_check({provider}) failed: {_e}")
        ok = False

    _health_cache[provider] = (now, ok)
    return ok


def health_check_sync(provider: Provider, timeout: float = 1.5) -> bool:
    """Synchronous variant for callers that can't await (model_registry refresh)."""
    now = time.monotonic()
    cached = _health_cache.get(provider)
    if cached and (now - cached[0]) < _HEALTH_TTL_SECONDS:
        return cached[1]

    route = _ollama_route() if provider is Provider.OLLAMA else _llama_server_route()
    probe_path = "/api/version" if provider is Provider.OLLAMA else "/health"
    url = f"{route.base_url}{probe_path}"

    ok = False
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
            ok = resp.status_code == 200
    except Exception as _e:
        logger.debug(f"[llm-provider] health_check_sync({provider}) failed: {_e}")
        ok = False

    _health_cache[provider] = (now, ok)
    return ok


# ─── Payload translation: Ollama ↔ OpenAI ───────────────────────────────────────

# Ollama generate options → OpenAI params we can represent.
# llama.cpp's OpenAI-compatible server (llama-server) accepts many
# sampler params as extended top-level fields beyond the OpenAI spec.
# We pass through everything that has a known llama-server equivalent;
# unknown keys are dropped (llama-server would reject or ignore them).
_OLLAMA_TO_OPENAI_OPTION_MAP = {
    "temperature": "temperature",
    "top_p": "top_p",
    "num_predict": "max_tokens",
    # v1.8.1: llama-server extensions — critical for 1-bit / sub-4B models
    # whose quality collapses without their tuned sampler profile.
    "top_k": "top_k",
    "repeat_penalty": "repeat_penalty",
    "min_p": "min_p",
    "seed": "seed",
    "tfs_z": "tfs_z",
    "typical_p": "typical_p",
    "mirostat": "mirostat",
    "mirostat_tau": "mirostat_tau",
    "mirostat_eta": "mirostat_eta",
    # "num_ctx" is set at llama-server startup, not per-request.
}


def ollama_to_openai_payload(
    ollama_payload: Dict[str, Any],
    *,
    is_chat: bool,
) -> Dict[str, Any]:
    """Translate an Ollama /api/generate or /api/chat payload to OpenAI chat shape.

    - generate payload has {prompt, system?} → becomes a single-message chat.
    - chat payload has {messages} → passed through.
    - `options` dict is flattened into top-level OpenAI params (temperature, etc).
    - `stop` sequences pass through.
    - `stream` and `model` pass through.
    - keep_alive, format, images, num_ctx, mirostat, etc. are dropped.
    """
    out: Dict[str, Any] = {
        "model": ollama_payload.get("model", ""),
        "stream": bool(ollama_payload.get("stream", False)),
    }

    if is_chat:
        messages = list(ollama_payload.get("messages", []))
    else:
        # Synthesize a chat from prompt/system
        prompt_text = ollama_payload.get("prompt", "") or ""
        system_text = ollama_payload.get("system", "") or ""
        messages = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": prompt_text})
    out["messages"] = messages

    # Flatten Ollama options into top-level OpenAI params
    options = ollama_payload.get("options", {}) or {}
    for ollama_key, openai_key in _OLLAMA_TO_OPENAI_OPTION_MAP.items():
        if ollama_key in options:
            out[openai_key] = options[ollama_key]

    # Pass through stop sequences if present
    if ollama_payload.get("stop"):
        out["stop"] = ollama_payload["stop"]

    # v1.8.1: Translate Ollama's `format: "json"` to OpenAI `response_format`
    # so callers that request JSON mode get it on llama-server too.
    _fmt = ollama_payload.get("format")
    if _fmt == "json":
        out["response_format"] = {"type": "json_object"}

    # For non-streaming we want usage stats; llama-server emits them by default
    # with stream_options={"include_usage": true} when streaming.
    if out["stream"]:
        out["stream_options"] = {"include_usage": True}

    return out


def openai_non_stream_to_ollama_response(
    openai_resp: Dict[str, Any],
    *,
    is_chat: bool,
) -> Dict[str, Any]:
    """Translate a non-streaming OpenAI response back to Ollama-native shape.

    This lets existing callers that read `result["response"]` (generate) or
    `result["message"]["content"]` (chat) work unchanged when the backend was
    actually llama-server.
    """
    content = ""
    try:
        choices = openai_resp.get("choices", []) or []
        if choices:
            msg = choices[0].get("message", {}) or {}
            content = msg.get("content", "") or ""
    except Exception:
        content = ""

    usage = openai_resp.get("usage", {}) or {}
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)

    # Use Ollama's field names so _record_tokens() works as-is
    out: Dict[str, Any] = {
        "prompt_eval_count": prompt_tokens,
        "eval_count": completion_tokens,
        "eval_duration": 0,  # llama-server doesn't report this
        "done": True,
    }
    if is_chat:
        out["message"] = {"role": "assistant", "content": content}
    else:
        out["response"] = content
    return out


def openai_stream_chunk_to_ollama(
    openai_chunk: Dict[str, Any],
    *,
    is_chat: bool,
) -> Optional[Dict[str, Any]]:
    """Translate a single OpenAI streaming SSE chunk to Ollama shape.

    Returns None for chunks with no content (e.g. role-only first chunk).
    Finish chunks (finish_reason set) return {..., "done": True}.
    Final chunks that carry usage info also populate token counts.
    """
    if not openai_chunk:
        return None

    choices = openai_chunk.get("choices", []) or []
    usage = openai_chunk.get("usage") or {}

    content = ""
    done = False
    if choices:
        choice = choices[0] or {}
        delta = choice.get("delta", {}) or {}
        content = delta.get("content", "") or ""
        if choice.get("finish_reason"):
            done = True

    out: Dict[str, Any] = {}
    if is_chat:
        if content:
            out["message"] = {"role": "assistant", "content": content}
    else:
        if content:
            out["response"] = content

    if done or usage:
        out["done"] = True
        if usage:
            out["prompt_eval_count"] = int(usage.get("prompt_tokens", 0) or 0)
            out["eval_count"] = int(usage.get("completion_tokens", 0) or 0)
            out["eval_duration"] = 0

    if not out:
        return None
    return out


# ─── Info helper for UI / diagnostics ───────────────────────────────────────────

def _run_smoke_tests() -> None:
    """Lightweight assertions for the translator and resolver.

    Run with (from backend/):
        python3 -m services.llm_provider
    Exits non-zero on first failure. Does not require Ollama or the sidecar
    to be running — pure in-memory checks.
    """
    # ── 1. Resolver defaults unknown models to Ollama ───────────────────
    r = resolve("some-unknown-model:latest")
    assert r.provider is Provider.OLLAMA, f"unknown-model should default to OLLAMA, got {r.provider}"
    assert r.api_style == "ollama"

    # Empty string is also OLLAMA
    assert resolve("").provider is Provider.OLLAMA

    # ── 2. Provider.from_str is tolerant ────────────────────────────────
    assert Provider.from_str(None) is Provider.OLLAMA
    assert Provider.from_str("") is Provider.OLLAMA
    assert Provider.from_str("ollama") is Provider.OLLAMA
    assert Provider.from_str("LLAMA_SERVER") is Provider.LLAMA_SERVER
    assert Provider.from_str("bogus") is Provider.OLLAMA  # safe fallback

    # ── 3. Ollama → OpenAI translator: generate (prompt + system) ───────
    ollama_gen = {
        "model": "bonsai-8b",
        "prompt": "Say hi.",
        "system": "You are terse.",
        "stream": False,
        "options": {"temperature": 0.5, "top_p": 0.85, "num_predict": 40,
                    "repeat_penalty": 1.1, "num_ctx": 4096, "mirostat": 2},
        "keep_alive": "5m",
    }
    openai_gen = ollama_to_openai_payload(ollama_gen, is_chat=False)
    assert openai_gen["model"] == "bonsai-8b"
    assert openai_gen["stream"] is False
    assert openai_gen["temperature"] == 0.5
    assert openai_gen["top_p"] == 0.85
    assert openai_gen["max_tokens"] == 40, "num_predict must map to max_tokens"
    assert openai_gen["messages"] == [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "Say hi."},
    ]
    # v1.8.1: llama-server extended samplers pass through so per-model
    # tuning (e.g. Bonsai's top_k=20, repeat_penalty=1.1) survives translation.
    assert openai_gen["repeat_penalty"] == 1.1, "repeat_penalty must pass through"
    assert openai_gen.get("mirostat") == 2, "mirostat must pass through"
    # num_ctx is a startup-time sidecar arg, not per-request; keep_alive is Ollama-only.
    for k in ("num_ctx", "keep_alive"):
        assert k not in openai_gen, f"{k} should have been stripped"

    # ── 4. Translator: chat messages pass through ──────────────────────
    ollama_chat = {
        "model": "bonsai-8b",
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": True,
        "options": {"temperature": 0.7},
    }
    openai_chat = ollama_to_openai_payload(ollama_chat, is_chat=True)
    assert openai_chat["messages"] == [{"role": "user", "content": "Hi"}]
    assert openai_chat["stream"] is True
    assert openai_chat.get("stream_options") == {"include_usage": True}

    # ── 5. Translator: stop sequences pass through ─────────────────────
    ollama_stop = {"model": "x", "messages": [], "stop": ["\n\n"], "options": {}}
    assert ollama_to_openai_payload(ollama_stop, is_chat=True)["stop"] == ["\n\n"]

    # ── 6. Non-streaming response translator ───────────────────────────
    openai_resp = {
        "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    back_chat = openai_non_stream_to_ollama_response(openai_resp, is_chat=True)
    assert back_chat["message"]["content"] == "Hello!"
    assert back_chat["eval_count"] == 2
    assert back_chat["prompt_eval_count"] == 5
    assert back_chat["done"] is True

    back_gen = openai_non_stream_to_ollama_response(openai_resp, is_chat=False)
    assert back_gen["response"] == "Hello!"
    assert "message" not in back_gen

    # Malformed response doesn't crash
    empty = openai_non_stream_to_ollama_response({}, is_chat=True)
    assert empty["message"]["content"] == ""
    assert empty["eval_count"] == 0

    # ── 7. Stream chunk translator ─────────────────────────────────────
    role_chunk = {"choices": [{"delta": {"role": "assistant"}}]}
    assert openai_stream_chunk_to_ollama(role_chunk, is_chat=False) is None, \
        "role-only chunks should yield None"

    content_chunk = {"choices": [{"delta": {"content": "Hel"}}]}
    out = openai_stream_chunk_to_ollama(content_chunk, is_chat=False)
    assert out == {"response": "Hel"}

    finish_with_usage = {
        "choices": [{"delta": {"content": "!"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10},
    }
    final = openai_stream_chunk_to_ollama(finish_with_usage, is_chat=True)
    assert final["done"] is True
    assert final["message"]["content"] == "!"
    assert final["eval_count"] == 10

    # Empty payload returns None
    assert openai_stream_chunk_to_ollama({}, is_chat=True) is None

    # ── 8. Sidecar manager config resolution (no subprocess spawn) ──────
    try:
        from services.sidecar_manager import resolve_config, sidecar_manager
        cfg = resolve_config()
        # Fields are all present and types correct
        assert isinstance(cfg.port, int) and cfg.port > 0, "port must be positive int"
        assert isinstance(cfg.ctx_size, int) and cfg.ctx_size > 0
        assert isinstance(cfg.threads, int) and cfg.threads >= 1
        # Status is async-callable and returns expected keys without spawn
        import asyncio as _asyncio
        snap = _asyncio.run(sidecar_manager.status())
        for k in ("running", "owned", "healthy", "pid", "uptime_seconds",
                  "binary_path", "model_path", "model_exists", "port", "last_error"):
            assert k in snap, f"status() missing key {k}"
        # Running/owned must always be booleans
        assert isinstance(snap["running"], bool) and isinstance(snap["owned"], bool)
    except ImportError:
        # sidecar_manager optional in bundled environments
        pass

    print("[llm_provider] All smoke tests passed.")


async def providers_status() -> List[Dict[str, Any]]:
    """Return a status summary for all known providers. Used by /evaluator/providers.

    Shape:
      [
        {"provider": "ollama",       "base_url": "...", "healthy": true},
        {"provider": "llama_server", "base_url": "...", "healthy": false},
      ]
    """
    results: List[Dict[str, Any]] = []
    for p in (Provider.OLLAMA, Provider.LLAMA_SERVER):
        route = _ollama_route() if p is Provider.OLLAMA else _llama_server_route()
        healthy = await health_check(p)
        results.append({
            "provider": p.value,
            "base_url": route.base_url,
            "healthy": healthy,
        })
    return results


if __name__ == "__main__":
    _run_smoke_tests()
