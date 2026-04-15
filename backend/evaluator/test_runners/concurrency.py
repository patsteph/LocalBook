"""Concurrency & Load test runner — tests how the model handles concurrent inference requests."""

import time
import asyncio
from datetime import datetime
from evaluator.models import EvalResult
import logging
logger = logging.getLogger(__name__)

async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Execute multiple identical requests concurrently and measure processing efficiency."""
    from services.ollama_client import ollama_client
    from config import settings
    
    main_model = getattr(settings, 'ollama_model', 'olmo-3:7b-instruct')
    test_config = config.get("concurrency_test", {})
    num_queries = test_config.get("num_concurrent_queries", 3)
    prompt = test_config.get("prompt", "Explain quantum entanglement.")
    
    result = EvalResult(
        test_id="concurrency_load",
        category="concurrency",
        test_name=f"Concurrent Load ({num_queries} simultaneous requests)",
        model_combo=combo_name,
        model_used=main_model,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat() + "Z",
    )
    
    # Pre-warm model to ensure cold-start doesn't skew concurrency math
    try:
        await ollama_client.generate(prompt="hi", model=main_model, num_predict=1)
    except Exception as _e:
        logger.debug(f"[concurrency] {type(_e).__name__}: {_e}")

    start = time.time()
    
    # Fire off concurrent tasks
    tasks = []
    task_starts = []
    
    for _ in range(num_queries):
        tasks.append(
            ollama_client.generate(
                prompt=prompt,
                model=main_model,
                temperature=0.4,
                num_predict=200
            )
        )
        task_starts.append(time.time())

    try:
        responses = await asyncio.gather(*tasks)
        elapsed = (time.time() - start) * 1000
        
        # Calculate theoretical sequential time vs actual concurrent time
        total_eval_duration_ms = 0
        total_tokens = 0
        for resp in responses:
            total_eval_duration_ms += (resp.get("eval_duration", 0) / 1_000_000)
            total_tokens += resp.get("eval_count", 0)
            
        result.total_time_ms = elapsed
        result.eval_duration_ns = int(total_eval_duration_ms * 1_000_000)  # Total combined compute
        result.completion_tokens = total_tokens
        
        # Calculate tokens per second (throughput under load)
        if elapsed > 0:
            result.tokens_per_second = (total_tokens / elapsed) * 1000
            
        # Score based on successful completion and efficiency
        # An excellent handler should complete 3 queries in less than 2x the time of a single query (batching)
        # or at worst 3x (strictly queued). 
        if total_tokens > 0:
            result.actual_output_preview = f"Processed {num_queries} queries, total {total_tokens} tokens in {elapsed/1000:.1f}s. Combined compute time: {total_eval_duration_ms/1000:.1f}s"
            
            result.accuracy_score = 100  # They all completed successfully
            # Speed score is based on total wall-clock time 
            speed_score = max(0, min(100, int(100 - (elapsed - 15000) / 300)))
            
            result.overall_score = int(result.accuracy_score * 0.7 + speed_score * 0.3)
            result.passed = result.overall_score >= 50
        else:
            raise ValueError("No tokens generated across concurrent requests.")
            
    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        
    print(f"[EVAL-CONCURRENCY] Score={result.overall_score}, tokens/sec={result.tokens_per_second:.1f}")
    return [result]
