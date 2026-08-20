"""LLM service — the task-facing engine seam (doc-20 abstraction).

This is THE single seam the engine-strategy decision (AUDIT_2026-06-15/20) calls
for: `generate_text` / `stream_text` / `generate_with_vision` / `ocr_backend`.
Engines swap BEHIND these names — today they route to Ollama; the Wave-9 MLX
text port (and any future engine) plugs in here, invisible to callers.

History: this file IS the former services/rag_llm.py (git-mv'd, S3/C1
2026-07-03) — call_ollama→generate_text, stream_ollama→stream_text, plus the
two vision seam functions. Logic is byte-preserved from rag_llm; the deeper
options-builder unification with llm_runtime is deferred to Wave 9 (it
needs live-Ollama runtime testing). llm_runtime remains the Ollama-engine
client (lanes, num_ctx math, embeddings); this module is the task router.
"""
import json
from typing import AsyncGenerator, Optional


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
            # Run-scoped throughput. Both engines reach here with Ollama-shaped fields
            # (mlx_engine emits eval_count/eval_duration deliberately), so ONE hook makes
            # every generation a speed sample instead of the 2 that time themselves.
            from services.throughput_meter import record as _tp_record
            _tp_record(completion_tokens, eval_duration_ns, prompt_tokens)
    except Exception:
        pass  # Never let metrics recording break LLM calls


def _record_engine_fallback(detail: str, role_model: str, severity: str = "error") -> None:
    """Best-effort quality signal when a generation could not be served (never raises).

    Named for the era when MLX degraded to Ollama. There is no second engine now, so this
    fires on a HARD failure rather than a silent slowdown — hence severity defaults to
    "error". Still the signal the Rough-edges rollup surfaces; the meaning got worse, not
    different.
    """
    try:
        from services.quality_signals import record_signal
        record_signal("fallback", "llm_service", detail, severity=severity, key=str(role_model))
    except Exception:
        pass


# ─── Non-Streaming ───────────────────────────────────────────────────────────────

async def generate_text(
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
    # avoids a circular dependency with llm_runtime.
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    else:
        from services.llm_runtime import compute_num_ctx
        _nc = compute_num_ctx(use_model, f"{system_prompt}\n\n{prompt}", num_predict)
        if _nc:
            options["num_ctx"] = _nc
    # P4: cap output to what the resolved window can hold (small-RAM cap-bound case).
    # ⚠️ This clamp is OLLAMA-SPECIFIC: `num_ctx` above comes from the OLLAMA model's cap
    # (`effective_num_ctx_cap` — 16384 for gemma, 8192 for phi). MLX ignores `num_ctx`
    # entirely and uses the MLX model's own window, so applying an Ollama-derived clamp to an
    # MLX generation silently shortens long-form output for no reason. Keep the caller's
    # request for the MLX branch below.
    _requested_num_predict = options.get("num_predict")
    if options.get("num_predict") and options.get("num_ctx"):
        from services.llm_runtime import clamp_num_predict
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

    # Generate IN-PROCESS via MLX, reusing the options/num_ctx/temperature computed above and
    # recording tokens identically. The `options` dict keeps its Ollama-shaped key names
    # because the model registry and the rag_profiles are keyed that way; mlx_engine reads
    # the few it honours (temperature, num_predict, num_ctx, stop) and ignores the rest.
    # ⚠️ That means the Mirostat / repeat_penalty / repeat_last_n tuning above is INERT —
    # it is Ollama sampler configuration with no mlx-lm equivalent. Left in place because
    # the registry still supplies it and removing it is a tuning decision (Stage 5), not
    # part of the excise.
    try:
        from services.mlx_engine import mlx_engine, mlx_model_for_role
        _mlx_id = mlx_model_for_role(use_model)
    except Exception:
        _mlx_id = None
    if _mlx_id and mlx_engine.available():
        try:
            _res = await mlx_engine.generate(
                prompt, model=_mlx_id, system=system_prompt,
                temperature=options.get("temperature", 0.3),
                # The caller's request, NOT the Ollama-window-clamped value (see above).
                num_predict=_requested_num_predict or num_predict,
                num_ctx=options.get("num_ctx"),
                stop=rag_profile.get("stop_sequences"),
            )
            _record_ollama_tokens(_res)
            try:
                from services.model_warmup import mark_fast_model_used, mark_main_model_used
                (mark_fast_model_used if use_model == settings.ollama_fast_model
                 else mark_main_model_used)()
            except Exception:
                pass
            print(f"[mlx-engine] {use_model}→{_mlx_id} generate OK "
                  f"({_res.get('eval_count', 0)} tok, {_res.get('eval_duration', 0)/1e9:.1f}s)")
            return _res.get("response", "")
        except Exception as _mlx_e:
            logger.error(f"[llm_service] generate FAILED ({use_model}→{_mlx_id}): {_mlx_e}")
            _record_engine_fallback(
                f"generate failed, no fallback engine ({type(_mlx_e).__name__})", use_model)
            return ""

    # No engine resolved. Under MLX-only this means the role points at a model that isn't
    # downloaded or MLX itself failed to initialise — a misconfiguration, not a transient.
    # Returns a string because every caller treats the result as prose; raising here would
    # convert one missing model into unhandled exceptions across the synthesis pipelines.
    logger.error(
        f"[llm_service] generate UNSERVICEABLE model={use_model} — no MLX model resolved "
        f"for this role. Check /system/model-readiness."
    )
    _record_engine_fallback("no engine resolved for role", use_model)
    return ""


# ─── Streaming ───────────────────────────────────────────────────────────────────

async def stream_text(
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
    
    mode_str = " [Deep Think]" if deep_think else (" [Fast]" if use_fast_model else "")
    print(f"Streaming with model: {model}{mode_str} (temp={temperature}, num_predict={effective_num_predict})")
    
    # Auto-size context window via the shared helper (one sizing rule app-wide);
    # floor at 8192 for streaming (chat) exactly as before.
    from services.llm_runtime import compute_num_ctx, clamp_num_predict
    effective_num_ctx = compute_num_ctx(model, f"{system_prompt}\n\n{prompt}", effective_num_predict) or 8192
    # P4: cap output to what the resolved window can hold (small-RAM cap-bound case).
    # Ollama-specific — see the note on the non-streaming path. MLX gets the unclamped value.
    _requested_num_predict = effective_num_predict
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

    # Visibility: streaming also bypasses llm_runtime — log the ctx so the
    # streamed chat/doc answer shows its window (otherwise it's invisible).
    logger.info(
        f"[llm_service] stream start model={model} "
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

    # Wave 9.2 — MLX main streaming route (dual-engine). Stream in-process (gemma via
    # mlx-vlm / phi via mlx-lm) when the model's role is engine=mlx. Yields token
    # strings like the Ollama path + records tokens on done. Falls back to Ollama ONLY
    # if MLX fails before emitting any token (can't cleanly resume mid-stream).
    try:
        from services.mlx_engine import mlx_engine, mlx_model_for_role
        _mlx_id = mlx_model_for_role(model)
    except Exception:
        _mlx_id = None
    if _mlx_id and mlx_engine.available():
        _emitted = False
        try:
            async for _chunk in mlx_engine.stream_generate(
                prompt, model=_mlx_id, system=system_prompt,
                temperature=stream_options.get("temperature", 0.3),
                # The caller's request, NOT the Ollama-window-clamped value.
                num_predict=_requested_num_predict,
                num_ctx=stream_options.get("num_ctx"),
                stop=stop_sequences or None,
            ):
                _t = _chunk.get("response")
                if _t:
                    _emitted = True
                    yield _t
                if _chunk.get("done"):
                    _record_ollama_tokens(_chunk)
                    # The streaming guard ABORTED on degeneration. Nothing consumed this
                    # flag, so a truncated answer looked like a short one — invisible to
                    # the user, to the logs, and (critically) to any quality measurement.
                    # With no Ollama fallback after the cutover this guard IS the safety
                    # system, so its firing has to be recorded.
                    if _chunk.get("degenerate"):
                        try:
                            from services.quality_signals import record_signal
                            record_signal(
                                "degraded", "mlx_engine",
                                f"streaming aborted on degeneration ({model}→{_mlx_id}) after "
                                f"{_chunk.get('eval_count', 0)} tokens — output truncated",
                                severity="warn", key="streaming_degeneration",
                            )
                        except Exception:
                            pass
            print(f"[mlx-engine] {model}→{_mlx_id} stream OK")
            return
        except Exception as _mlx_e:
            logger.error(f"[llm_service] stream FAILED ({model}→{_mlx_id}): {_mlx_e}")
            _record_engine_fallback(
                f"stream failed{' mid-output' if _emitted else ''}, no fallback engine "
                f"({type(_mlx_e).__name__})", model)
            return

    # No engine resolved — see generate_text for why this is a misconfiguration rather
    # than a transient. A generator returns by yielding nothing; callers already handle
    # an empty stream (it is what a stopped model looked like before).
    logger.error(
        f"[llm_service] stream UNSERVICEABLE model={model} — no MLX model resolved for "
        f"this role. Check /system/model-readiness."
    )
    _record_engine_fallback("no engine resolved for role", model)
    return


# Simplification S1/B2 (2026-07-03): the call_openai/call_anthropic cloud escape
# hatches were removed — LocalBook is 100% local; no UI ever surfaced a cloud
# provider. `anthropic` left requirements.in with them. (`openai` stays: BERTopic's
# representation model uses its client pointed at LOCAL Ollama.)


# ─── Vision / OCR seam (doc-20) ─────────────────────────────────────────────

async def generate_with_vision(image_b64: str, prompt: str, **kwargs):
    """Engine-routed vision generation (currently Ollama; resolve_vision_model
    picks gemma4 on Option-A boxes, the configured fallback otherwise)."""
    from services.llm_runtime import llm_runtime
    return await llm_runtime.vision_describe(image_b64, prompt, **kwargs)


async def ocr_backend(image_b64: str, prompt: str, **kwargs):
    """Engine-routed OCR slice (currently Apple Vision with Ollama fallback,
    via vision_describe's ocr_mode routing)."""
    from services.llm_runtime import llm_runtime
    kwargs.setdefault("ocr_mode", True)
    return await llm_runtime.vision_describe(image_b64, prompt, **kwargs)
