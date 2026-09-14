"""Output filters + RunProfile — what the scorer actually sees.

MIGRATED 2026-08-20 from `_runprofile_test.py`, which was **orphaned**: it sat on disk and no
test runner referenced it, so its 27 assertions had not guarded anything for months. (It was
even "fixed" during the MLX cutover — a fix nothing would have validated.)

This is the only coverage of `evaluator/output_filters.py`, and it earns its place: a scorer
that sees a model's `<think>` block instead of its answer marks a correct answer wrong, which
silently distorts every eval comparison built on top of it.
"""
import pytest

from evaluator.output_filters import apply_filters, extract_json, strip_thinking, trim_preamble
from evaluator.run_profile import RunProfile, derive_run_profile


# ── strip_thinking ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("<think>let me reason\nstep 2</think>The answer is 42.", "The answer is 42."),
    ("<think>reasoning</think>\n\nParis is the capital.", "Paris is the capital."),
    ("Just a plain answer.", "Just a plain answer."),
    # A model with thinking "off" still emits the empty block.
    ("<think></think>Final.", "Final."),
    ("<reasoning>hmm</reasoning>Done.", "Done."),
])
def test_strip_thinking(raw, expected):
    assert strip_thinking(raw) == expected


def test_an_unclosed_think_block_yields_no_answer():
    """Hitting the token cap mid-reasoning means there IS no answer. Returning the reasoning
    would score a truncated run as if the model had answered."""
    assert strip_thinking("<think>reasoning that never closed and ran on") == ""


def test_multiple_blocks_are_all_removed():
    assert strip_thinking("<think>a</think>X<think>b</think>Y").replace(" ", "") == "XY"


def test_strip_thinking_is_none_safe():
    assert strip_thinking(None) == ""


# ── extract_json ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1, "b": 2}', '{"a": 1, "b": 2}'),
    ('Here:\n```json\n{"x": 5}\n```\ndone', '{"x": 5}'),
    ('<think>plan</think>{"ok": true}', '{"ok": true}'),
    ('Sure! {"n": 3} hope that helps', '{"n": 3}'),
    ('[1, 2, 3]', '[1, 2, 3]'),
])
def test_extract_json(raw, expected):
    assert extract_json(raw) == expected


@pytest.mark.parametrize("raw", ["no json here at all", "this {is not} json"])
def test_extract_json_returns_none_rather_than_guessing(raw):
    """None lets the scorer fail cleanly. Half-parsed braces would score as a malformed
    answer from a model that may have answered fine."""
    assert extract_json(raw) is None


# ── trim_preamble + the pipeline ────────────────────────────────────────────────

def test_trim_preamble_strips_only_the_preamble():
    assert trim_preamble("Sure, here is the list:\n- a\n- b").startswith("- a")
    assert trim_preamble("The capital is Paris.") == "The capital is Paris."


def test_the_json_pipeline_strips_thinking_then_extracts():
    assert apply_filters('<think>x</think>```json\n{"v":1}\n```',
                         ["strip_thinking", "json"]) == '{"v":1}'


def test_the_json_filter_empties_when_there_is_no_json():
    assert apply_filters("just prose", ["json"]) == ""


# ── RunProfile ──────────────────────────────────────────────────────────────────

def test_stop_sequences_merge_without_duplicates():
    rp = RunProfile(model="x", thinking_capable=True, stop_sequences=["<|end|>"])
    opts = rp.to_ollama_options({"stop": ["\n\n"], "temperature": 0.3})
    assert opts["stop"] == ["\n\n", "<|end|>"]
    assert opts["temperature"] == 0.3


def test_thinking_is_off_for_evaluation_and_always_stripped():
    """Evaluation scores the FINAL answer, so reasoning is off by default — and the
    normalizer strips it either way, because "off" is a request a model may ignore."""
    rp = RunProfile(model="x", thinking_capable=True)
    assert rp.thinking_enabled is False
    assert "strip_thinking" in rp.normalize_filters


def test_a_derived_profile_carries_the_curated_stops_and_the_real_engine():
    """`thinking_capable` used to come from Ollama's /api/show capabilities array. An MLX
    checkpoint's config.json has no equivalent and the registry stores only the SETTING
    (`rag_profile.think`), so the capability is no longer discoverable — which costs nothing,
    since `think` was an Ollama request field and has been inert since the cutover."""
    from config import settings

    p = derive_run_profile(settings.main_model)
    assert p.provider == "mlx"
    assert p.thinking_capable is False
    assert len(p.stop_sequences) > 0, "curated stop sequences must still apply"
