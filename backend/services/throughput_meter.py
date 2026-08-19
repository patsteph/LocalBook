"""Throughput meter — measure EVERY generation, not the two tests that happen to time themselves.

Stage 2 follow-up (2026-08-19). The first engine A/B could not judge speed: `perf_samples` was
2 and 3, because only `streaming.py` and `concurrency.py` record `tokens_per_second`, and
`streaming` had timed out. Two samples cannot support a 30 %-regression threshold.

The fix is not "make 18 runners time themselves" — it is to measure at the seam every engine
already passes through. `llm_service._record_ollama_tokens` receives `eval_count` and
`eval_duration` from BOTH engines (mlx_engine deliberately emits Ollama-shaped fields so
tokens/sec computes identically). So one accumulator there turns a whole evaluation run —
every RAG answer, every document, every classification — into the sample set.

Scoped by an explicit start/stop rather than a global counter: an Evaluator run needs "tokens
generated during THIS run", not since boot. Thread-safe because generation happens on the MLX
executor thread, background workers, and the event loop concurrently.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_ACTIVE: Optional["Session"] = None


class Session:
    """One measurement window. Records a sample per generation."""

    def __init__(self, label: str = ""):
        self.label = label
        self.started = time.time()
        self.samples: List[Dict[str, float]] = []

    def add(self, completion_tokens: int, eval_duration_ns: int, prompt_tokens: int = 0) -> None:
        # A generation with no duration tells us nothing about speed (cache replay, error path).
        if completion_tokens <= 0 or eval_duration_ns <= 0:
            return
        secs = eval_duration_ns / 1e9
        self.samples.append({
            "tps": completion_tokens / secs,
            "completion_tokens": float(completion_tokens),
            "prompt_tokens": float(prompt_tokens),
            "seconds": secs,
        })

    def summary(self) -> Dict[str, Any]:
        if not self.samples:
            return {"generations": 0}
        tps = sorted(s["tps"] for s in self.samples)

        def pct(p: float) -> float:
            k = max(0, min(len(tps) - 1, int(round((p / 100.0) * (len(tps) - 1)))))
            return round(tps[k], 2)

        total_tok = sum(s["completion_tokens"] for s in self.samples)
        total_sec = sum(s["seconds"] for s in self.samples)
        return {
            "generations": len(self.samples),
            "completion_tokens": int(total_tok),
            "generation_seconds": round(total_sec, 1),
            # Aggregate throughput — total tokens over total generation time. Less flattering
            # than a mean of per-call rates (which over-weights tiny fast calls) and closer to
            # what a user experiences across a session.
            "tokens_per_sec": round(total_tok / total_sec, 2) if total_sec else 0.0,
            "tps_mean": round(sum(tps) / len(tps), 2),
            "tps_p50": pct(50),
            "tps_p05": pct(5),      # the slow tail is where a regression shows first
            "tps_p95": pct(95),
        }


def start(label: str = "") -> "Session":
    global _ACTIVE
    with _LOCK:
        _ACTIVE = Session(label)
        return _ACTIVE


def stop() -> Dict[str, Any]:
    global _ACTIVE
    with _LOCK:
        s, _ACTIVE = _ACTIVE, None
    return s.summary() if s else {"generations": 0}


def record(completion_tokens: int, eval_duration_ns: int, prompt_tokens: int = 0) -> None:
    """Called from the LLM seam on every generation. Never raises; a no-op when idle."""
    try:
        with _LOCK:
            if _ACTIVE is not None:
                _ACTIVE.add(completion_tokens, eval_duration_ns, prompt_tokens)
    except Exception:
        pass


def active() -> bool:
    return _ACTIVE is not None
