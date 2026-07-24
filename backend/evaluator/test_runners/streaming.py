"""Streaming test runner — measures TTFT, throughput, and stream reliability."""

import time
import json
from datetime import datetime
from evaluator.models import EvalResult


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Run streaming generation test via the real RAG streaming pipeline."""
    from services.rag_engine import rag_engine
    from config import settings

    q = config["queries"]["simple"]
    result = EvalResult(
        test_id="streaming_generation",
        category="streaming",
        test_name="Streaming Chat Response",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result.stamp_provider(settings.ollama_model)

    try:
        start = time.time()
        first_token_time = None
        last_token_time = None
        token_count = 0
        answer_parts = []
        stream_errors = 0
        cache_hit = False

        # use_cache=False → measure a REAL retrieval+generation, not a ~20ms cache replay
        # (which inflated tok/s into the hundreds and TTFT to ~20ms — user report 2026-07-24).
        async for chunk in rag_engine.query_stream(
            notebook_id=notebook_id,
            question=q["question"],
            top_k=4,
            use_cache=False,
        ):
            ctype = chunk.get("type")
            if ctype == "token":
                now = time.time()
                if first_token_time is None:
                    first_token_time = now
                last_token_time = now
                token_count += 1
                answer_parts.append(chunk.get("content", ""))
            elif ctype == "done" and chunk.get("cache"):
                cache_hit = True  # defensive: should not happen with use_cache=False
            elif chunk.get("error"):
                stream_errors += 1

        elapsed = (time.time() - start) * 1000
        ttft_ms = ((first_token_time - start) * 1000) if first_token_time else elapsed

        answer = "".join(answer_parts)
        result.total_time_ms = elapsed
        result.time_to_first_token_ms = ttft_ms   # end-to-end: retrieval + prompt-eval + first token
        result.output_chars = len(answer)
        result.input_chars = len(q["question"])
        result.actual_output_preview = answer[:500]

        # Throughput = decode rate over the ACTUAL decode window (first→last token), which
        # excludes retrieval (before first token) AND follow-up-question generation (after the
        # last token) — both of which the old (elapsed - ttft) window wrongly included. Prefer
        # the real streamed-token count; on the cache-bypassed normal path the RAG stream emits
        # one chunk per model token, so token_count is accurate. Fall back to the ~4-chars/token
        # estimate only for single-chunk emissions (e.g. a structured/replace path returning the
        # whole answer at once). tok/s uses (token_count - 1) tokens over the decode window since
        # the first token marks the window's start.
        decode_sec = (last_token_time - first_token_time) if (first_token_time and last_token_time) else 0.0
        if token_count > 1 and decode_sec > 0.001:
            result.tokens_per_second = (token_count - 1) / decode_sec
        else:
            gen_time_sec = (elapsed - ttft_ms) / 1000.0
            result.tokens_per_second = (len(answer) / 4) / max(0.1, gen_time_sec)

        # Score
        ttft_score = 100 if ttft_ms < 2000 else max(0, int(100 - (ttft_ms - 2000) / 100))
        throughput_score = min(100, int(result.tokens_per_second * 10))  # 10 tok/s = 100
        completeness_score = 100 if len(answer) > 50 else max(0, int(len(answer) * 2))
        error_score = 100 if stream_errors == 0 else max(0, 100 - stream_errors * 30)

        result.overall_score = int(
            ttft_score * 0.30 + throughput_score * 0.30 + completeness_score * 0.25 + error_score * 0.15
        )
        result.passed = result.overall_score >= 40

        print(f"[EVAL-STREAM] TTFT={ttft_ms:.0f}ms, {result.tokens_per_second:.1f} tok/s "
              f"({token_count} tok over {(last_token_time - first_token_time) if (first_token_time and last_token_time) else 0:.2f}s decode)"
              f"{' [CACHE HIT!]' if cache_hit else ''}, score={result.overall_score}, {elapsed:.0f}ms total")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-STREAM] FAILED: {e}")

    return [result]
