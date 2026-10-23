"""Chunks are split to fit the embedder's shared token limit — never cut, never dropped."""
from services import embedding_profile as ep
from services import token_budget as tb

GEMMA2 = "mlx-community/embeddinggemma-2-bf16"
ARCTIC = "mlx-community/snowflake-arctic-embed-l-v2.0-bf16"
BUDGET = 512 - tb.ENRICHMENT_RESERVE_TOKENS


def _count(text):                      # deterministic stand-in: one token per 2 characters
    return len(text) // 2 + 1


def test_a_chunk_within_budget_is_untouched():
    assert tb.split_to_budget("short text", BUDGET, _count) == ["short text"]


def test_long_text_splits_at_paragraphs_and_every_piece_fits():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 120 for i in range(8))
    pieces = tb.split_to_budget(text, BUDGET, _count)
    assert len(pieces) > 1 and all(_count(p) <= BUDGET for p in pieces)
    assert "".join(pieces).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")


def test_an_unbroken_run_still_fits():
    pieces = tb.split_to_budget("x" * 6000, BUDGET, _count)
    assert all(_count(p) <= BUDGET for p in pieces) and "".join(pieces) == "x" * 6000


def test_enforce_leaves_models_without_a_limit_alone():
    chunks = ["y" * 9000]
    assert tb.enforce(chunks, ARCTIC) == chunks


def test_enforce_splits_for_a_limited_model(monkeypatch):
    monkeypatch.setattr(tb, "token_counter", lambda model_id: _count)
    chunks = ["short", "z. " * 1500]
    out = tb.enforce(chunks, GEMMA2)
    assert out[0] == "short" and len(out) > 2 and all(_count(c) <= BUDGET for c in out)
