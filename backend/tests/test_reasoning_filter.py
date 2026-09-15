"""Reasoning traces must never reach a user — in batch OR in a stream.

Until 2026-09-14 the only thing stopping a model's private deliberation from appearing in chat
answers was a hand-written `stop_sequences` entry per model in `known_models.json`. gemma has
`<|channel>thought`; a model with no row, or one that reasons in `<think>`, had nothing.

The Evaluator, meanwhile, has stripped reasoning before scoring since the Locker rebuild. So the
harness was MORE forgiving than the app: a reasoning model could score well — the scorer never
saw the reasoning — and then leak `<think>…</think>` into answers, documents and podcast
scripts. An evaluator that passes a model the app cannot use is worse than none.

The streaming filter is the delicate half. A tag can arrive split across chunks, and text
already yielded is already on the user's screen — there is no retracting it.
"""
import pytest

from utils.reasoning import (
    ReasoningStreamFilter,
    looks_like_reasoning,
    strip_reasoning,
)


# ── Batch ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("<think>deliberation</think>The answer is 4.", "The answer is 4."),
    ("<reasoning>hmm</reasoning>Yes.", "Yes."),
    ("<thought>a</thought>b", "b"),
    ("◁think▷kimi style◁/think▷Done.", "Done."),
    ("No markers at all.", "No markers at all."),
    ("<THINK>upper</THINK>ok", "ok"),                      # case-insensitive
    ("a<think>x</think>b<think>y</think>c", "abc"),        # multiple blocks
])
def test_closed_blocks_are_removed(raw, expected):
    assert strip_reasoning(raw) == expected


def test_an_unclosed_block_yields_no_answer():
    """Reasoning that hit the token cap produced no final answer. Showing the deliberation
    would be presenting the model's scratchpad as its response."""
    assert strip_reasoning("Here goes <think>I should consider") == "Here goes"
    assert strip_reasoning("<think>only reasoning, never closed") == ""


def test_empty_and_non_string_are_safe():
    assert strip_reasoning("") == ""
    assert strip_reasoning(None) == ""


def test_looks_like_reasoning_is_a_fit_signal():
    assert looks_like_reasoning("<think>x</think>ok") is True
    assert looks_like_reasoning("plain answer") is False


# ── Streaming ───────────────────────────────────────────────────────────────

def _run_stream(chunks):
    f = ReasoningStreamFilter()
    out = "".join(f.feed(c) for c in chunks)
    return out + f.flush()


def test_a_stream_without_reasoning_passes_through_unchanged():
    chunks = ["The ", "answer ", "is ", "42."]
    assert _run_stream(chunks) == "The answer is 42."


def test_a_reasoning_block_is_removed_from_a_stream():
    chunks = ["<think>", "let me consider", "</think>", "Final answer."]
    assert _run_stream(chunks) == "Final answer."


def test_a_tag_split_across_chunks_is_never_emitted():
    """THE streaming hazard: `<think>` arriving as `<thi` + `nk>`. Emitting `<thi` and
    deciding afterwards is not an option — it is already on screen."""
    chunks = ["Hello ", "<thi", "nk>", "secret", "</thi", "nk>", "World"]
    assert _run_stream(chunks) == "Hello World"


def test_a_closing_tag_split_across_chunks():
    chunks = ["<think>hidden</th", "ink>", "Visible"]
    assert _run_stream(chunks) == "Visible"


def test_character_by_character_streaming():
    """The worst case — one character per chunk, which is what a token stream can look like."""
    text = "A<think>b</think>C"
    assert _run_stream(list(text)) == "AC"


def test_text_that_merely_resembles_a_tag_is_still_delivered():
    """A held partial that never becomes a tag MUST be released, or real output vanishes."""
    assert _run_stream(["compare a < b ", "and c < d"]) == "compare a < b and c < d"
    assert _run_stream(["ends with <"]) == "ends with <"
    assert _run_stream(["<thi"]) == "<thi"
    assert _run_stream(["x<thinking about it>"]) == "x<thinking about it>"


def test_a_stream_ending_inside_a_block_emits_nothing_after_the_opener():
    """Matches the batch contract: unclosed reasoning means no answer was produced."""
    assert _run_stream(["Answer: ", "<think>", "still thinking..."]) == "Answer: "
    assert _run_stream(["<think>", "never closed"]) == ""


def test_multiple_blocks_in_one_stream():
    chunks = ["a", "<think>1</think>", "b", "<think>2</think>", "c"]
    assert _run_stream(chunks) == "abc"


def test_streaming_matches_batch_for_the_same_text():
    """The two paths must not disagree — a user should see the same answer whether it streamed
    or not."""
    for text in [
        "<think>x</think>Answer.",
        "Plain text.",
        "a<think>1</think>b<think>2</think>c",
        "Before <think>unclosed",
        "◁think▷k◁/think▷done",
    ]:
        streamed = _run_stream(list(text))          # one char at a time
        assert streamed.strip() == strip_reasoning(text), f"divergence on {text!r}"


def test_the_held_buffer_cannot_grow_without_bound():
    """A pathological stream of `<` must not accumulate — the hold is bounded by the longest
    opener."""
    f = ReasoningStreamFilter()
    for _ in range(1000):
        f.feed("<")
    assert len(f._held) <= 16


# ── The vision seam (2026-09-15) ────────────────────────────────────────────
#
# `scan_pipeline` and `multimodal_extractor` called `llm_runtime.vision_describe` directly,
# bypassing `llm_service.generate_with_vision`. That looked harmless because the seam was a
# bare pass-through — it added nothing to skip. It does now: vision output is shown to the user
# verbatim (image descriptions, scanned receipts, capture modes), so a reasoning model would
# otherwise put its scratchpad into the document.

def test_the_vision_seam_strips_reasoning():
    import asyncio

    from services import llm_service

    async def _fake(image_b64, prompt, **kwargs):
        return "<think>the image seems to show a receipt</think>Total: $42.00"

    class _FakeRuntime:
        vision_describe = staticmethod(_fake)

    import services.llm_runtime as lr
    real = lr.llm_runtime
    lr.llm_runtime = _FakeRuntime()
    try:
        out = asyncio.run(llm_service.generate_with_vision("b64", "describe"))
        assert out == "Total: $42.00", f"reasoning reached the user: {out!r}"
        ocr = asyncio.run(llm_service.ocr_backend("b64", "read"))
        assert "<think>" not in ocr, "OCR text goes straight into a document the user reads"
    finally:
        lr.llm_runtime = real


def test_production_vision_callers_use_the_seam():
    """The shortcut must not creep back: only llm_service may call the runtime directly."""
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    offenders = []
    for f in (backend / "services").glob("*.py"):
        if f.name in ("llm_service.py", "llm_runtime.py"):
            continue
        if "llm_runtime.vision_describe(" in f.read_text():
            offenders.append(f.name)
    assert not offenders, (
        f"{offenders} bypass llm_service.generate_with_vision — they would skip the reasoning "
        f"strip and token recording"
    )
