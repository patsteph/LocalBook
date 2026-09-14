"""Throughput must be measured at the seam, not by the two runners that time themselves.

The first engine A/B (2026-08-19) could not judge speed: `perf_samples` was 2 and 3, because
only `streaming.py` and `concurrency.py` record tokens/sec — and streaming had timed out. Two
samples cannot support the 30 %-regression abort threshold.

Every generation from BOTH engines already passes through `llm_service._record_ollama_tokens`
with `eval_count` / `eval_duration` (mlx_engine emits Ollama-shaped fields deliberately), so one
hook there makes the whole run the sample set.
"""
import threading

import pytest

from services import throughput_meter as tp


@pytest.fixture(autouse=True)
def clean():
    tp.stop()          # never inherit a session from another test
    yield
    tp.stop()


def test_records_only_inside_a_session():
    """A no-op when idle: normal app use must not accumulate unbounded samples."""
    tp.record(100, 1_000_000_000)
    assert tp.active() is False
    tp.start("x")
    tp.record(100, 1_000_000_000)
    assert tp.stop()["generations"] == 1


def test_aggregate_throughput_is_tokens_over_time_not_a_mean_of_rates():
    """A mean of per-call rates over-weights tiny fast calls. 150 tokens in 4 s = 37.5 tok/s,
    even though the per-call rates are 50 and 25."""
    tp.start("x")
    tp.record(100, 2_000_000_000)     # 50 tps
    tp.record(50, 2_000_000_000)      # 25 tps
    s = tp.stop()
    assert s["tokens_per_sec"] == 37.5
    assert s["tps_mean"] == 37.5      # (50+25)/2 — coincidentally equal here
    assert s["tps_p50"] == 25.0


def test_zero_duration_and_zero_token_calls_are_ignored():
    """A cache replay or an error path tells us nothing about generation speed and would
    otherwise inflate the numbers — the exact bug that made cached runs look 100x faster."""
    tp.start("x")
    tp.record(0, 1_000_000_000)
    tp.record(100, 0)
    tp.record(0, 0)
    assert tp.stop()["generations"] == 0


def test_percentiles_expose_the_slow_tail():
    tp.start("x")
    for tps in (10, 20, 30, 40, 100):
        tp.record(tps, 1_000_000_000)
    s = tp.stop()
    assert s["tps_p05"] == 10.0
    assert s["tps_p50"] == 30.0
    assert s["tps_p95"] == 100.0


def test_empty_session_reports_zero_generations_not_a_fake_rate():
    tp.start("x")
    assert tp.stop() == {"generations": 0}


def test_is_thread_safe():
    """Generation happens on the MLX executor thread, background workers and the event loop
    concurrently — a lost update would silently undercount."""
    tp.start("x")

    def worker():
        for _ in range(200):
            tp.record(10, 1_000_000_000)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert tp.stop()["generations"] == 800


def test_starting_a_new_session_discards_the_previous_one():
    """An Evaluator run measures THIS run, not since boot."""
    tp.start("first")
    tp.record(100, 1_000_000_000)
    tp.start("second")
    assert tp.stop()["generations"] == 0
