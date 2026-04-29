"""Context Capacity test runner (Needle in a Haystack)."""

import time
import math
from datetime import datetime
from evaluator.models import EvalResult
from evaluator.capabilities import capabilities_for

async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Test model's ability to retain and locate specific information inside massive context windows."""
    from services.ollama_client import ollama_client
    from config import settings

    main_model = getattr(settings, 'ollama_model', 'olmo-3:7b-instruct')
    test_config = config.get("needle_haystack_test", {})

    needle = test_config.get("needle", "The secret database override passphrase is 'Tangerine-Sky-44'.")
    question = test_config.get("question", "What is the secret database override passphrase?")
    expected_answer = test_config.get("expected_answer", "Tangerine-Sky-44")
    target_tokens = test_config.get("padding_target_tokens", 8000)

    # v1.8.2: adapt the needle test to the model's actual context window so a
    # small-ctx model (e.g. Bonsai 4K) isn't scored against an 8K haystack it
    # physically cannot ingest. We trim target_tokens to ~75% of the window,
    # leaving headroom for the prompt framing and completion. The test STILL
    # RUNS and scores honestly — the adaptation is surfaced as a degraded
    # note so the user sees the true state of affairs.
    caps = capabilities_for(main_model)
    original_target = target_tokens
    degraded_note = ""
    if caps.context_window and target_tokens > caps.context_window * 0.75:
        adjusted = int(caps.context_window * 0.75)
        degraded_note = (
            f"Adapted haystack {original_target}→{adjusted} tokens to fit "
            f"{main_model} context window ({caps.context_window}). "
            f"Production behavior at {original_target} tokens is UNTESTED — "
            f"the app would truncate or error if given longer input."
        )
        print(f"[EVAL-NEEDLE] {degraded_note}")
        target_tokens = adjusted

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
    
    prompt = f"Given the following context documentation:\n\n{context_block}\n\nAnswer the question concisely based ONLY on the context: {question}"

    start = time.time()
    try:
        response = await ollama_client.generate(
            prompt=prompt,
            model=main_model,
            temperature=0.0,
            num_predict=100,
            extra_options={"num_ctx": target_tokens + 2000}
        )
        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed
        
        answer = response.get("response", "").strip()
        result.actual_output_preview = answer[:200]
        result.input_chars = actual_chars
        result.output_chars = len(answer)
        result.eval_duration_ns = response.get("eval_duration", 0)
        
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
        
        # Evaluate throughput
        eval_count = response.get("eval_count", 0)
        if eval_count > 0 and result.eval_duration_ns > 0:
            result.tokens_per_second = eval_count / (result.eval_duration_ns / 1e9)
            
        result.overall_score = max(0, int(result.accuracy_score) - length_penalty)
        result.passed = result.overall_score >= 80
        
        # Track sub-scores for dashboard visibility
        result.sub_scores = {
            "context_size": target_tokens,
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
