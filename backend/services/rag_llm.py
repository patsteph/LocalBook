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
    """
    # Use very long timeout - LLM generation can take minutes for complex queries
    timeout = httpx.Timeout(10.0, read=600.0)  # 10s connect, 10 min read
    # Default to fast model for non-streaming calls - faster response times
    # Main model (olmo-3:7b-instruct) used for streaming queries
    use_model = model or settings.ollama_fast_model
    options = {"num_predict": num_predict}
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    elif num_predict > 500:
        # Auto-size context window for document generation:
        # estimate prompt tokens (~1 token per 4 chars) + generation headroom
        prompt_text = f"{system_prompt}\n\n{prompt}"
        estimated_prompt_tokens = len(prompt_text) // 3  # conservative estimate
        options["num_ctx"] = max(8192, estimated_prompt_tokens + num_predict + 512)
    if temperature is not None:
        options["temperature"] = temperature
    # Repetition / coherence control — strategy varies by output length:
    #
    # Long-form (>3000 tokens): Use Mirostat 2.0 adaptive sampling.
    #   Mirostat dynamically targets a perplexity level across the ENTIRE
    #   generation, preventing degenerative loops far more effectively than
    #   a fixed repeat_penalty window.  tau=4.0 balances coherence + diversity.
    #
    # Medium docs / Chat: Use repeat_penalty (simpler, sufficient for shorter output).
    # Dialogue/scripts should pass repeat_penalty=1.1 explicitly.
    if repeat_penalty is not None:
        # Caller explicitly set penalty — respect it (e.g. dialogue scripts)
        options["repeat_penalty"] = repeat_penalty
        options["repeat_last_n"] = 256 if num_predict > 500 else 64
    elif num_predict > 3000:
        # Long-form: Mirostat 2.0 replaces repeat_penalty
        options["mirostat"] = 2
        options["mirostat_tau"] = 4.0     # Target perplexity (coherent but diverse)
        options["mirostat_eta"] = 0.1     # Learning rate (stable adaptation)
        options["repeat_penalty"] = 1.15  # Light penalty as secondary safety net
        options["repeat_last_n"] = 512
    elif num_predict > 500:
        options["repeat_penalty"] = 1.3   # Medium docs
        options["repeat_last_n"] = 256
    else:
        options["repeat_penalty"] = 1.1   # Chat Q&A
        options["repeat_last_n"] = 64
    # Merge caller-supplied overrides LAST (e.g., Mirostat for outline-first sections)
    if extra_options:
        options.update(extra_options)
    async with httpx.AsyncClient(timeout=timeout) as client:
        print(f"Calling Ollama with model: {use_model}, num_predict: {num_predict}, num_ctx: {num_ctx or 'default'}")
        response = await client.post(
            f"{settings.ollama_base_url}/api/generate",
            json={
                "model": use_model,
                "prompt": f"{system_prompt}\n\n{prompt}",
                "stream": False,
                "keep_alive": -1,  # Keep model loaded indefinitely (Tier 1 optimization)
                "options": options
            }
        )
        result = response.json()
        # Track model usage for warmup service
        from services.model_warmup import mark_fast_model_used, mark_main_model_used
        if use_model == settings.ollama_fast_model:
            mark_fast_model_used()
        else:
            mark_main_model_used()
        # Record token usage for Health Portal token economy stats
        _record_ollama_tokens(result)
        print(f"Ollama response received, length: {len(result.get('response', ''))}")
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
) -> AsyncGenerator[str, None]:
    """Stream response from Ollama API with stop sequences to prevent citation lists.
    
    Args:
        deep_think: Use CoT prompting with lower temperature for thorough analysis
        use_fast_model: Use phi4-mini (System 1) instead of olmo-3:7b-instruct (System 2)
        num_predict: Override token limit. None = use defaults (800 chat / 1500 deep think).
                     Set higher (2000-4000) for document generation.
        temperature_override: Per-skill adaptive temperature. None = use model defaults.
        extra_options: Additional Ollama options merged last (e.g., Mirostat overrides).
    """
    timeout = httpx.Timeout(10.0, read=600.0)
    
    # Two-tier model selection:
    # - System 1 (phi4-mini): Factual queries, fast responses
    # - System 2 (olmo-3:7b-instruct): Synthesis, complex queries, Deep Think
    if use_fast_model and not deep_think:
        model = settings.ollama_fast_model
        temperature = temperature_override or 0.7
        top_p = 0.9
    else:
        model = settings.ollama_model
        # Lower temperature for Deep Think mode (more focused reasoning)
        temperature = temperature_override or (0.5 if deep_think else 0.7)
        top_p = 0.9
    
    # Stop sequences to prevent LLM from generating citation/reference lists
    # Only apply for chat Q&A, not document generation (which needs References sections)
    # Comprehensive list covers all observed LLM bibliography header variants
    stop_sequences = []
    if num_predict is None:
        stop_sequences = [
            # References variants
            "\n\nReferences",
            "\nReferences",
            "\n\n**References",
            "\n**References",
            # Sources variants
            "\n\nSources:",
            "\nSources:",
            "\n\nSources\n",
            "\n\n**Sources",
            "\n**Sources",
            # Citations variants (including "Supporting Citations" — confirmed leak)
            "\n\nCitations:",
            "\nCitations:",
            "\n\nCitations\n",
            "\nSupporting Citations",
            "\n\nSupporting Citations",
            "\nSupporting citations",
            # Other bibliography headers LLMs generate
            "\n\nBibliography",
            "\nBibliography",
            "\n\nCited Sources",
            "\nCited Sources",
            "\n\nKey References",
            "\nKey References",
            "\n\nFootnotes",
            "\nFootnotes",
            # Separator + citation list patterns
            "\n\n---\n[",
            "\n\n[1]:",
            "\n\n*Note",
            "\n*Note:",
        ]
    
    # Determine token limit
    if num_predict is not None:
        effective_num_predict = num_predict
    else:
        effective_num_predict = 1500 if deep_think else 800
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        mode_str = " [Deep Think]" if deep_think else (" [Fast]" if use_fast_model else "")
        print(f"Streaming from Ollama with model: {model}{mode_str} (temp={temperature}, num_predict={effective_num_predict})")
        
        # Auto-size context window for document generation
        is_doc_gen = num_predict is not None and num_predict > 500
        if is_doc_gen:
            prompt_text = f"{system_prompt}\n\n{prompt}"
            estimated_prompt_tokens = len(prompt_text) // 3
            effective_num_ctx = max(8192, estimated_prompt_tokens + effective_num_predict + 512)
        else:
            effective_num_ctx = 4096
        
        # Repetition / coherence control — same strategy as non-streaming path
        stream_options = {
            "temperature": temperature,
            "top_p": top_p,
            "num_predict": effective_num_predict,
            "num_ctx": effective_num_ctx,
        }
        if effective_num_predict > 3000:
            # Long-form: Mirostat 2.0 adaptive sampling
            stream_options["mirostat"] = 2
            stream_options["mirostat_tau"] = 4.0
            stream_options["mirostat_eta"] = 0.1
            stream_options["repeat_penalty"] = 1.15
            stream_options["repeat_last_n"] = 512
        elif is_doc_gen:
            stream_options["repeat_penalty"] = 1.3
            stream_options["repeat_last_n"] = 256
        else:
            stream_options["repeat_penalty"] = 1.1
            stream_options["repeat_last_n"] = 64
        # Merge caller-supplied overrides LAST (e.g., Mirostat for outline-first sections)
        if extra_options:
            stream_options.update(extra_options)

        # Tier 1 optimizations: keep_alive prevents cold start, num_predict caps runaway generation
        request_json = {
            "model": model,
            "prompt": f"{system_prompt}\n\n{prompt}",
            "stream": True,
            "keep_alive": -1,  # Keep model loaded indefinitely (Tier 1 optimization)
            "options": stream_options,
        }
        if stop_sequences:
            request_json["stop"] = stop_sequences
        
        # Track model usage for warmup service
        from services.model_warmup import mark_fast_model_used, mark_main_model_used
        if use_fast_model:
            mark_fast_model_used()
        else:
            mark_main_model_used()

        async with client.stream(
            "POST",
            f"{settings.ollama_base_url}/api/generate",
            json=request_json
        ) as response:
            async for line in response.aiter_lines():
                if line:
                    data = json.loads(line)
                    # olmo-3:7b-instruct streams response tokens directly
                    if data.get("response"):
                        yield data["response"]
                    # Final chunk contains token stats
                    if data.get("done"):
                        _record_ollama_tokens(data)


# ─── OpenAI ──────────────────────────────────────────────────────────────────────

async def call_openai(system_prompt: str, prompt: str) -> str:
    """Call OpenAI API."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    response = await client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content


# ─── Anthropic ───────────────────────────────────────────────────────────────────

async def call_anthropic(system_prompt: str, prompt: str) -> str:
    """Call Anthropic API."""
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    response = await client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text
