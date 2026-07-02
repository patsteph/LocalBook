"""Context Capacity test runner (Needle in a Haystack)."""

import time
import math
from datetime import datetime
from evaluator.models import EvalResult
from evaluator.capabilities import capabilities_for

async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Test model's ability to retain and locate specific information inside massive context windows."""
    from services.rag_engine import rag_engine
    from config import settings

    main_model = getattr(settings, 'ollama_model', 'gemma4:e4b')
    test_config = config.get("needle_haystack_test", {})

    needle = test_config.get("needle", "The secret database override passphrase is 'Tangerine-Sky-44'.")
    question = test_config.get("question", "What is the secret database override passphrase?")
    expected_answer = test_config.get("expected_answer", "Tangerine-Sky-44")
    # Stress the DEPLOYED context window on THIS hardware — capabilities_for now
    # reports the RAM-scaled effective cap the app actually gives the model — so the
    # test reflects TRUE per-hardware capacity: a 48GB box exercises ~49k tokens, a
    # 16GB box ~12k, instead of a fixed 8k that under-tests big boxes and misaligns
    # perception vs reality. An explicit `padding_target_tokens` still works as a
    # fixed override, but is never allowed to exceed the deployed window.
    caps = capabilities_for(main_model)
    window = caps.context_window or 8192
    degraded_note = ""
    explicit = test_config.get("padding_target_tokens")
    if explicit:
        target_tokens = min(int(explicit), int(window * 0.9))
        if int(explicit) > int(window * 0.9):
            degraded_note = (
                f"Requested {int(explicit)} tokens exceeds {main_model}'s deployed window "
                f"({window}); stress-tested at {target_tokens} instead — longer input would truncate."
            )
            print(f"[EVAL-NEEDLE] {degraded_note}")
    else:
        frac = float(test_config.get("window_fraction", 0.75))
        target_tokens = int(window * frac)
    target_tokens = max(2000, target_tokens)
    print(f"[EVAL-NEEDLE] Stressing {main_model} at {target_tokens} tokens "
          f"(deployed window {window}, ~{int(100 * target_tokens / max(1, window))}%)")

    result = EvalResult(
        test_id="needle_haystack",
        category="needle_haystack",
        test_name=f"Context Stress (~{target_tokens} tokens)",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat() + "Z",
    )
    result.stamp_provider(main_model)
    if degraded_note:
        result.mark_degraded(degraded_note)
    
    # Generate Haystack (approx 4 chars per token)
    base_text = "The LocalBook RAG framework is designed for privacy-first AI. " * 50
    base_text += "It uses lanceDB for fast local embeddings and Ollama for inference. " * 50
    base_text += "Apple Silicon unified memory allows large models to run efficiently. " * 50
    
    # How many times to repeat our base_text to achieve target token count
    # Let's say base_text is ~1400 tokens (1000 * 5.5 = ~5500 chars).
    base_tokens = len(base_text) / 4.0
    repeats = max(1, math.ceil(target_tokens / base_tokens))
    
    haystack_parts = [base_text] * repeats
    
    # Insert Needle at roughly 65% depth (the notorious drop-off zone for LLMs)
    insert_index = int(len(haystack_parts) * 0.65)
    haystack_parts.insert(insert_index, f"\n\n{needle}\n\n")
    
    context_block = "\n".join(haystack_parts)
    actual_chars = len(context_block)
    
    # Split into a system prompt and a user prompt so /api/chat-routed
    # models (e.g. Gemma4 with use_chat_endpoint=true in rag_profile) get
    # the right template. Going through rag_engine._call_ollama (instead
    # of ollama_client.generate directly) ensures we exercise the SAME
    # routing the production code uses — so the score reflects what users
    # actually experience, not an artificial /api/generate path.
    system_prompt = (
        "You answer questions strictly from the supplied context. "
        "Output the answer in one short sentence. No preamble."
    )
    user_prompt = (
        f"CONTEXT:\n\n{context_block}\n\n"
        f"Answer this question using ONLY the context above: {question}"
    )

    start = time.time()
    try:
        # voice_modifier=False — the test scores on a literal needle match,
        # not tone, and the modifier prefix could push the model toward
        # paraphrasing the secret rather than reproducing it verbatim.
        # extra_options carries num_ctx because rag_engine sizes context
        # automatically but we want a hard guarantee the haystack fits.
        answer = await rag_engine._call_ollama(
            system_prompt=system_prompt,
            prompt=user_prompt,
            model=main_model,
            num_predict=100,
            num_ctx=target_tokens + 2000,
            temperature=0.0,
            voice_modifier=False,
        )
        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed

        answer = (answer or "").strip()
        result.actual_output_preview = answer[:200]
        result.input_chars = actual_chars
        result.output_chars = len(answer)
        # eval_duration / eval_count not surfaced when going through
        # rag_engine._call_ollama (it returns just the text). That's OK —
        # we still measure wall-clock latency in total_time_ms.
        result.eval_duration_ns = 0
        
        # ── Graduated grading ──────────────────────────────────────────
        # Industry best practice: distinguish between full retrieval, partial
        # retrieval (key fragment present), and total miss. The needle here
        # is "Tangerine-Sky-44" — checking for either the full passphrase OR
        # its memorable parts gives a more nuanced view of context retention.
        answer_lower = answer.lower()
        expected_lower = expected_answer.lower()
        
        is_full_match = expected_lower in answer_lower
        # Partial credit for finding distinctive fragments (handles minor
        # transcription errors like "Tangerine Sky 44" without dashes)
        partial_fragments = ["tangerine", "sky-44", "sky 44", "sky_44"]
        partial_hits = sum(1 for frag in partial_fragments if frag in answer_lower)
        is_partial = not is_full_match and partial_hits >= 2
        
        if is_full_match:
            result.accuracy_score = 100
        elif is_partial:
            result.accuracy_score = 60  # Partial credit for retention failure with recall hint
        else:
            result.accuracy_score = 0
        
        # Penalize if it just generated a thousand tokens rambling
        length_penalty = 0 if len(answer) < 150 else max(0, min(50, int((len(answer) - 150) / 10)))

        # Throughput tracking removed — rag_engine._call_ollama returns
        # the text only, not the eval_count. Total wall-clock time is
        # still in total_time_ms above.
            
        result.overall_score = max(0, int(result.accuracy_score) - length_penalty)
        result.passed = result.overall_score >= 80
        
        # Track sub-scores for dashboard visibility
        result.sub_scores = {
            "context_size": target_tokens,
            "deployed_window": window,
            "context_pct_of_window": int(100 * target_tokens / max(1, window)),
            "needle_depth_pct": 65,
            "full_match": is_full_match,
            "partial_match": is_partial,
        }

        if not is_full_match:
            if is_partial:
                result.failure_reason = (
                    f"Partial recall at ~{target_tokens} tokens — model found fragments "
                    f"but couldn't reproduce the full needle. Indicates degraded long-context retention."
                )
            else:
                result.failure_reason = (
                    f"Total recall failure at ~{target_tokens} tokens (needle at 65% depth). "
                    f"Model cannot reliably extract specific details from long contexts. "
                    f"Note: 'lost in the middle' is a known weakness for small/edge models."
                )
            
    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        
    print(f"[EVAL-NEEDLE] Score={result.overall_score}, context={actual_chars} chars, time={result.total_time_ms/1000:.1f}s")
    return [result]
