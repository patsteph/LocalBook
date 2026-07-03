"""
RAG LLM — LLM call wrappers for Ollama, OpenAI, and Anthropic.

Extracted from rag_engine.py Phase 2. Owns all LLM API communication,
model routing (two-tier fast/deep), streaming, stop sequences, and
parameter tuning (temperature, repeat penalty, context window sizing).

External callers continue to use rag_engine._call_ollama() etc. —
RAGEngine delegates here.
"""
import json
from typing import AsyncGenerator, Optional

import httpx

from config import settings
import logging
logger = logging.getLogger(__name__)


def _get_rag_profile(model_name: str) -> dict:
    """Return the model's RAG-specific tuning profile from the registry.

    Empty dict means: apply no overrides — global defaults stay intact.
    Only Gemma-family models currently carry a non-empty profile.
    """
    try:
        from evaluator.model_registry import model_registry
        info = model_registry.get_model(model_name)
        if info and info.rag_profile:
            return dict(info.rag_profile)
    except Exception as _e:
        logger.debug(f"[rag-llm] rag_profile lookup failed: {_e}")
    return {}


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
            return dict(info.ollama_options)  # Copy to avoid mutating registry
    except Exception as _e:
        logger.debug(f"[rag-llm] {type(_e).__name__}: {_e}")
    return {}


def _record_ollama_tokens(data: dict):
    """Extract and record token usage from an Ollama response/final chunk."""
    try:
        prompt_tokens = data.get("prompt_eval_count", 0) or 0
        completion_tokens = data.get("eval_count", 0) or 0
        eval_duration_ns = data.get("eval_duration", 0) or 0
        if prompt_tokens > 0 or completion_tokens > 0:
            from services.rag_metrics import rag_metrics
            rag_metrics.record_tokens(prompt_tokens, completion_tokens, eval_duration_ns)
    except Exception:
        pass  # Never let metrics recording break LLM calls


# ─── Ollama Non-Streaming ────────────────────────────────────────────────────────

async def call_ollama(
    system_prompt: str,
    prompt: str,
    model: str = None,
    num_predict: int = 500,
    num_ctx: int = None,
    temperature: float = None,
    repeat_penalty: float = None,
    extra_options: dict = None,
    voice_modifier: bool = True,
    priority: int = None,
) -> str:
    """Call Ollama API (non-streaming).

    Args:
        num_predict: Max tokens to generate. 500 for chat Q&A, 2000-4000 for documents.
        num_ctx: Context window size. None = Ollama default. Set higher (8192+) for long generation.
        temperature: LLM temperature. None = Ollama default (~0.7).
        repeat_penalty: Repetition penalty. None = auto (1.3 for docs, 1.1 for chat).
                        Use 1.1 for dialogue/scripts where natural repetition is expected.
        extra_options: Additional Ollama options merged LAST (overrides defaults).
                       Used by outline-first pipeline to inject Mirostat on sub-3000-token sections.
        voice_modifier: Prepend the active model's voice instruction to the system prompt.
                        Defaults True. Set False for structured/format-sensitive outputs.
        priority: Lane priority for the per-model concurrency limiter (FOREGROUND/
                  NORMAL/BACKGROUND). None → NORMAL. User-facing callers (chat
                  fallbacks, quick actions) should pass FOREGROUND so they jump
                  ahead of background ingest on the shared model lane.
    """
    # Use very long timeout - LLM generation can take minutes for complex queries
    timeout = httpx.Timeout(10.0, read=600.0)  # 10s connect, 10 min read
    # Default to fast model for non-streaming calls - faster response times
    # Main model (olmo-3:7b-instruct) used for streaming queries
    use_model = model or settings.ollama_fast_model
    # Start with model-specific defaults from registry (temperature, top_p, top_k)
    model_defaults = _get_model_options(use_model)
    rag_profile = _get_rag_profile(use_model)
    # Voice modifier: prepend family-tone instruction to the system prompt
    # so chat / RAG / content-gen output stays consistent across model swaps.
    if voice_modifier and system_prompt:
        from services.voice_modifier import voiced_system as _voiced
        system_prompt = _voiced(system_prompt, model_name=use_model) or system_prompt
    options = {**model_defaults, "num_predict": num_predict}
    # num_ctx sizing via the shared helper so chat/RAG and every structured caller
    # share ONE cap-aware, RAM-tier-aware rule (compute_num_ctx also applies the
    # per-model cap, so the old rag_profile cap line is folded in). Lazy import
    # avoids a circular dependency with ollama_service.
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    else:
        from services.ollama_service import compute_num_ctx
        _nc = compute_num_ctx(use_model, f"{system_prompt}\n\n{prompt}", num_predict)
        if _nc:
            options["num_ctx"] = _nc
    # P4: cap output to what the resolved window can hold (small-RAM cap-bound case).
    if options.get("num_predict") and options.get("num_ctx"):
        from services.ollama_service import clamp_num_predict
        options["num_predict"] = clamp_num_predict(
            f"{system_prompt}\n\n{prompt}", options["num_predict"], options["num_ctx"]
        )
    # Temperature priority: explicit arg > rag_profile > ollama_options
    if temperature is not None:
        options["temperature"] = temperature
    elif "temperature" in rag_profile:
        options["temperature"] = rag_profile["temperature"]
    # Repetition / coherence control — strategy varies by output length:
    #
    # Long-form (>3000 tokens): Use Mirostat 2.0 adaptive sampling.
    #   Mirostat dynamically targets a perplexity level across the ENTIRE
    #   generation, preventing degenerative loops far more effectively than
    #   a fixed repeat_penalty window.  tau=4.0 balances coherence + diversity.
    #
    # Medium docs / Chat: Use repeat_penalty (simpler, sufficient for shorter output).
    # Dialogue/scripts should pass repeat_penalty=1.1 explicitly.
    # rag_profile.repeat_penalty overrides the per-tier hardcoded defaults without
    # disabling Mirostat for long-form — profile value replaces the secondary penalty.
    _profile_penalty = rag_profile.get("repeat_penalty")
    if repeat_penalty is not None:
        # Caller explicitly set penalty — respect it (e.g. dialogue scripts)
        options["repeat_penalty"] = repeat_penalty
        options["repeat_last_n"] = 256 if num_predict > 500 else 64
    elif num_predict > 3000:
        # Long-form: Mirostat 2.0 replaces repeat_penalty
        options["mirostat"] = 2
        options["mirostat_tau"] = 4.0     # Target perplexity (coherent but diverse)
        options["mirostat_eta"] = 0.1     # Learning rate (stable adaptation)
        options["repeat_penalty"] = _profile_penalty if _profile_penalty is not None else 1.15
        options["repeat_last_n"] = 512
    elif num_predict > 500:
        options["repeat_penalty"] = _profile_penalty if _profile_penalty is not None else 1.3
        options["repeat_last_n"] = 256
    else:
        options["repeat_penalty"] = _profile_penalty if _profile_penalty is not None else 1.1
        options["repeat_last_n"] = 64
    # Merge caller-supplied overrides LAST (e.g., Mirostat for outline-first sections)
    if extra_options:
        options.update(extra_options)
    async with httpx.AsyncClient(timeout=timeout) as client:
        print(f"Calling LLM with model: {use_model}, num_predict: {num_predict}, num_ctx: {num_ctx or 'default'}")
        # Short keep_alive — warmup loop re-pings active models every 4 min
        _keep_alive = "5m"

        # v1.8.0: provider routing (identical Ollama path + translated OpenAI path)
        from services.llm_provider import (
            resolve as _resolve_provider,
            ollama_to_openai_payload,
            openai_non_stream_to_ollama_response,
        )
        _route = _resolve_provider(use_model)

        # Gemma-family models use /api/chat for proper system+user message structure.
        # All other Ollama models keep the /api/generate path unchanged.
        _use_chat = rag_profile.get("use_chat_endpoint", False) and _route.api_style == "ollama"

        # PB-2d / D4 (2026-06-23): join the shared per-model lane so this raw-httpx
        # call serializes on the SAME limiter as ollama_service.generate/chat/embed
        # and stream_ollama — it can't run as a 2nd concurrent call to the heavy
        # model, and user-facing callers (priority=FOREGROUND) preempt background
        # ingest. Mirrors stream_ollama, which already joins the lane. call_ollama
        # keeps its own bespoke coherence tuning (num_ctx auto-size / repeat_penalty
        # / Mirostat) that generate()/chat() don't replicate, so it lane-joins
        # rather than migrating. Lane held only around the network dispatch.
        from services.ollama_service import model_lane, PRIORITY_NORMAL
        _priority = priority if priority is not None else PRIORITY_NORMAL
        async with model_lane(use_model, _priority):
            if _use_chat:
                payload = {
                    "model": use_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "keep_alive": _keep_alive,
                    "options": options,
                }
                if "think" in rag_profile:
                    payload["think"] = rag_profile["think"]
                response = await client.post(f"{_route.base_url}/api/chat", json=payload)
                raw = response.json()
                # Normalize to the same shape as /api/generate so token tracking works
                result = {"response": raw.get("message", {}).get("content", ""), **raw}
            elif _route.api_style == "ollama":
                payload = {
                    "model": use_model,
                    "prompt": f"{system_prompt}\n\n{prompt}",
                    "stream": False,
                    "keep_alive": _keep_alive,
                    "options": options,
                }
                response = await client.post(f"{_route.base_url}/api/generate", json=payload)
                result = response.json()
            else:
                payload = {
                    "model": use_model,
                    "prompt": f"{system_prompt}\n\n{prompt}",
                    "stream": False,
                    "keep_alive": _keep_alive,
                    "options": options,
                }
                openai_payload = ollama_to_openai_payload(payload, is_chat=False)
                response = await client.post(
                    f"{_route.base_url}/v1/chat/completions",
                    json=openai_payload,
                )
                result = openai_non_stream_to_ollama_response(response.json(), is_chat=False)

        # Track model usage for warmup service
        from services.model_warmup import mark_fast_model_used, mark_main_model_used
        if use_model == settings.ollama_fast_model:
            mark_fast_model_used()
        else:
            mark_main_model_used()
        # Record token usage for Health Portal token economy stats
        _record_ollama_tokens(result)
        # Visibility: this httpx path does NOT go through ollama_service, so log the
        # ctx here too — otherwise doc/RAG/needle generations are invisible in the
        # ctx logs (only the small phi4 ollama_service calls show up).
        logger.info(
            f"[rag_llm] generate OK model={use_model} caller=call_ollama "
            f"ctx={options.get('num_ctx', 'def')} num_predict={options.get('num_predict')} "
            f"resp_chars={len(result.get('response', ''))}"
        )
        return result.get("response", "No response from LLM")


# ─── Ollama Streaming ────────────────────────────────────────────────────────────

async def stream_ollama(
    system_prompt: str,
    prompt: str,
    deep_think: bool = False,
    use_fast_model: bool = False,
    num_predict: Optional[int] = None,
    temperature_override: Optional[float] = None,
    extra_options: dict = None,
    voice_modifier: bool = True,
) -> AsyncGenerator[str, None]:
    """Stream response from Ollama API with stop sequences to prevent citation lists.

    Args:
        deep_think: Use CoT prompting with lower temperature for thorough analysis
        use_fast_model: Use phi4-mini (System 1) instead of olmo-3:7b-instruct (System 2)
        num_predict: Override token limit. None = use defaults (800 chat / 1500 deep think).
                     Set higher (2000-4000) for document generation.
        temperature_override: Per-skill adaptive temperature. None = use model defaults.
        extra_options: Additional Ollama options merged last (e.g., Mirostat overrides).
        voice_modifier: Prepend the active model's voice instruction to the system prompt.
                        Defaults True. Set False for structured/format-sensitive outputs.
    """
    timeout = httpx.Timeout(10.0, read=600.0)

    # Two-tier model selection:
    # - System 1 (phi4-mini): Factual queries, fast responses
    # - System 2 (olmo-3:7b-instruct): Synthesis, complex queries, Deep Think
    if use_fast_model and not deep_think:
        model = settings.ollama_fast_model
    else:
        model = settings.ollama_model

    # Voice modifier: prepend family-tone instruction so streaming chat
    # output stays consistent across model swaps.
    if voice_modifier and system_prompt:
        from services.voice_modifier import voiced_system as _voiced
        system_prompt = _voiced(system_prompt, model_name=model) or system_prompt

    # Load model-specific defaults from registry (temperature, top_p, top_k)
    model_defaults = _get_model_options(model)
    rag_profile = _get_rag_profile(model)
    # Temperature priority: rag_profile > ollama_options > fallback
    # rag_profile.temperature is the RAG-tuned override (e.g. 0.3 for Gemma4)
    _profile_temp = rag_profile.get("temperature")
    base_temp = _profile_temp if _profile_temp is not None else model_defaults.get("temperature", 0.7)
    top_p = model_defaults.get("top_p", 0.9)

    if temperature_override is not None:
        temperature = temperature_override
    elif deep_think:
        # Deep Think: use lower of model default and 0.5 for focused reasoning
        temperature = min(base_temp, 0.5)
    else:
        temperature = base_temp
    
    # Stop sequences to prevent LLM from generating citation/reference lists.
    # Family-aware: each model declares its own stop_sequences in rag_profile;
    # we fall back to a minimal shared list if the active model has none.
    # Stops only applied for chat Q&A, not document generation (which needs
    # References sections preserved).
    stop_sequences = []
    if num_predict is None:
        profile_stops = rag_profile.get("stop_sequences")
        if profile_stops:
            stop_sequences = list(profile_stops)
        else:
            # Minimal shared default — only the most common bibliography headers.
            # Tight enough to never clip a legitimate sentence; permissive enough
            # that any unknown model still gets baseline protection.
            stop_sequences = [
                "\n\nReferences",
                "\n\nBibliography",
                "\n\n[1]:",
            ]
    
    # Determine token limit
    if num_predict is not None:
        effective_num_predict = num_predict
    else:
        effective_num_predict = 1500 if deep_think else 800
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        mode_str = " [Deep Think]" if deep_think else (" [Fast]" if use_fast_model else "")
        print(f"Streaming from Ollama with model: {model}{mode_str} (temp={temperature}, num_predict={effective_num_predict})")
        
        # Auto-size context window via the shared helper (one sizing rule app-wide);
        # floor at 8192 for streaming (chat) exactly as before.
        from services.ollama_service import compute_num_ctx, clamp_num_predict
        effective_num_ctx = compute_num_ctx(model, f"{system_prompt}\n\n{prompt}", effective_num_predict) or 8192
        # P4: cap output to what the resolved window can hold (small-RAM cap-bound case).
        effective_num_predict = clamp_num_predict(
            f"{system_prompt}\n\n{prompt}", effective_num_predict, effective_num_ctx
        ) or effective_num_predict
        # doc-gen flag drives the repeat-penalty tier below (restored — it used to be
        # defined in the inline num_ctx block the shared helper replaced).
        is_doc_gen = num_predict is not None and num_predict > 500

        # Repetition / coherence control — same strategy as non-streaming path
        # Start with model-specific base options, then layer on call-specific params
        stream_options = {**model_defaults}
        stream_options.update({
            "temperature": temperature,
            "top_p": top_p,
            "num_predict": effective_num_predict,
            "num_ctx": effective_num_ctx,
        })
        _profile_penalty = rag_profile.get("repeat_penalty")
        if effective_num_predict > 3000:
            # Long-form: Mirostat 2.0 adaptive sampling
            stream_options["mirostat"] = 2
            stream_options["mirostat_tau"] = 4.0
            stream_options["mirostat_eta"] = 0.1
            stream_options["repeat_penalty"] = _profile_penalty if _profile_penalty is not None else 1.15
            stream_options["repeat_last_n"] = 512
        elif is_doc_gen:
            stream_options["repeat_penalty"] = _profile_penalty if _profile_penalty is not None else 1.3
            stream_options["repeat_last_n"] = 256
        else:
            stream_options["repeat_penalty"] = _profile_penalty if _profile_penalty is not None else 1.1
            stream_options["repeat_last_n"] = 64
        # Merge caller-supplied overrides LAST (e.g., Mirostat for outline-first sections)
        if extra_options:
            stream_options.update(extra_options)

        # Visibility: streaming also bypasses ollama_service — log the ctx so the
        # streamed chat/doc answer shows its window (otherwise it's invisible).
        logger.info(
            f"[rag_llm] stream start model={model} "
            f"ctx={stream_options.get('num_ctx')} num_predict={stream_options.get('num_predict')}"
        )

        # Short keep_alive — warmup loop re-pings active models every 4 min
        _keep_alive = "5m"

        # Track model usage for warmup service
        from services.model_warmup import mark_fast_model_used, mark_main_model_used
        if use_fast_model:
            mark_fast_model_used()
        else:
            mark_main_model_used()

        # ── v1.7.0: provider routing ─────────────────────────────────────
        # Resolve the backend for this model. Ollama-backed models keep the
        # existing /api/generate path byte-for-byte. Sidecar-backed models
        # (Bonsai via llama-server) translate to /v1/chat/completions.
        # Gemma-family models use /api/chat for proper system+user message structure.
        from services.llm_provider import (
            resolve as _resolve_provider,
            ollama_to_openai_payload,
            openai_stream_chunk_to_ollama,
        )
        _route = _resolve_provider(model)
        _use_chat = rag_profile.get("use_chat_endpoint", False) and _route.api_style == "ollama"

        # Hold the per-model priority lane for the whole stream at FOREGROUND
        # priority. stream_ollama is always user-initiated (chat answer or doc
        # generation), so it must (a) not run as a 2nd concurrent gemma call
        # against background work — the thrash the lane prevents — and (b) jump
        # ahead of background ingest. This is the rag_llm half of PB-2d: it owns
        # its own httpx streaming + stop-sequence logic, so it joins the lane
        # via model_lane() rather than migrating to ollama_service.stream_generate.
        from services.ollama_service import model_lane, PRIORITY_FOREGROUND
        async with model_lane(model, PRIORITY_FOREGROUND):
            if _use_chat:
                chat_payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": True,
                    "keep_alive": _keep_alive,
                    "options": stream_options,
                }
                if stop_sequences:
                    chat_payload["stop"] = stop_sequences
                if "think" in rag_profile:
                    chat_payload["think"] = rag_profile["think"]
                async with client.stream("POST", f"{_route.base_url}/api/chat", json=chat_payload) as response:
                    async for line in response.aiter_lines():
                        if line:
                            data = json.loads(line)
                            token = data.get("message", {}).get("content", "")
                            if token:
                                yield token
                            if data.get("done"):
                                _record_ollama_tokens(data)
            elif _route.api_style == "ollama":
                request_json = {
                    "model": model,
                    "prompt": f"{system_prompt}\n\n{prompt}",
                    "stream": True,
                    "keep_alive": _keep_alive,
                    "options": stream_options,
                }
                if stop_sequences:
                    request_json["stop"] = stop_sequences
                async with client.stream(
                    "POST",
                    f"{_route.base_url}/api/generate",
                    json=request_json,
                ) as response:
                    async for line in response.aiter_lines():
                        if line:
                            data = json.loads(line)
                            if data.get("response"):
                                yield data["response"]
                            if data.get("done"):
                                _record_ollama_tokens(data)
            else:
                # OpenAI-compatible streaming (llama-server sidecar).
                request_json = {
                    "model": model,
                    "prompt": f"{system_prompt}\n\n{prompt}",
                    "stream": True,
                    "keep_alive": _keep_alive,
                    "options": stream_options,
                }
                if stop_sequences:
                    request_json["stop"] = stop_sequences
                openai_payload = ollama_to_openai_payload(request_json, is_chat=False)
                async with client.stream(
                    "POST",
                    f"{_route.base_url}/v1/chat/completions",
                    json=openai_payload,
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
                        if not translated:
                            continue
                        if translated.get("response"):
                            yield translated["response"]
                        if translated.get("done"):
                            _record_ollama_tokens(translated)


# Simplification S1/B2 (2026-07-03): the call_openai/call_anthropic cloud escape
# hatches were removed — LocalBook is 100% local; no UI ever surfaced a cloud
# provider. `anthropic` left requirements.in with them. (`openai` stays: BERTopic's
# representation model uses its client pointed at LOCAL Ollama.)
