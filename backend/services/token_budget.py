"""Keep every chunk inside the embedder's shared token limit — by splitting, not cutting.

The embedder profile's `max_tokens` (EmbeddingGemma 2: 512) is the longest text any device in a
library embeds; the engine cuts there so one text has one vector everywhere. A cut loses the
tail of the chunk from search (measured 2026-10-18: cosine 0.94–0.99 against the full text), so
ingest splits an over-long chunk into pieces that each fit, and nothing is dropped.

The chunker sizes in characters; dense text (math, symbols, code, tables) tokenizes heavily, so
a 1000-character chunk can be 500+ tokens. The count here is the embedder's real tokenizer
when its tokenizer.json is in the local HF cache, and a deliberately pessimistic character
estimate otherwise (it only ever splits earlier than needed).
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# What a stored chunk adds around its raw text when embedded: the document template
# ("title: none | text: ", BOS/EOS) and the HyDE block ("Questions this answers:" plus a few
# generated questions). Measured headroom, kept generous so a chunk that fits here still fits
# once enriched.
ENRICHMENT_RESERVE_TOKENS = 112
_FALLBACK_CHARS_PER_TOKEN = 2.0

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@lru_cache(maxsize=8)
def token_counter(model_id: str) -> Callable[[str], int]:
    """Tokens in `text` as the embedder would count them (no special tokens)."""
    try:
        from huggingface_hub import try_to_load_from_cache
        from tokenizers import Tokenizer
        path = try_to_load_from_cache(model_id, "tokenizer.json")
        if isinstance(path, str):
            tok = Tokenizer.from_file(path)
            return lambda text: len(tok.encode(text, add_special_tokens=False).ids)
    except Exception as exc:
        logger.debug("[token-budget] no tokenizer for %s: %s", model_id, exc)
    return lambda text: int(len(text) / _FALLBACK_CHARS_PER_TOKEN) + 1


def _pieces(text: str, count: Callable[[str], int], budget: int) -> List[str]:
    """Break `text` at the coarsest boundary that leaves every piece within `budget`."""
    if count(text) <= budget:
        return [text]
    for sep, join in (("\n\n", "\n\n"), ("\n", "\n"), (None, " ")):
        parts = _SENTENCE.split(text) if sep is None else text.split(sep)
        parts = [p for p in parts if p.strip()]
        if len(parts) > 1:
            return _pack(parts, join, count, budget)
    words = text.split()                      # one unbroken run: split on words, then characters
    if len(words) > 1:
        return _pack(words, " ", count, budget)
    step = max(1, int(budget * _FALLBACK_CHARS_PER_TOKEN) // 2)
    return [text[i:i + step] for i in range(0, len(text), step)]


def _pack(parts: List[str], join: str, count: Callable[[str], int], budget: int) -> List[str]:
    out: List[str] = []
    buf = ""
    for part in parts:
        for piece in _pieces(part, count, budget):   # a part may itself be over budget
            trial = f"{buf}{join}{piece}" if buf else piece
            if buf and count(trial) > budget:
                out.append(buf)
                buf = piece
            else:
                buf = trial
    if buf:
        out.append(buf)
    return out


def split_to_budget(text: str, budget: int, count: Callable[[str], int]) -> List[str]:
    return [p.strip() for p in _pieces(text, count, budget) if p.strip()]


def enforce(chunks: List[str], model_id: Optional[str] = None) -> List[str]:
    """`chunks` with any chunk over the embedder's limit split into pieces that fit."""
    from services import embedding_profile
    prof = embedding_profile.for_model(model_id) if model_id is not None else embedding_profile.active()
    if prof.max_tokens <= 0 or not chunks:
        return chunks
    budget = max(32, prof.max_tokens - ENRICHMENT_RESERVE_TOKENS)
    count = token_counter(prof.model_id)
    out: List[str] = []
    split = 0
    for chunk in chunks:
        if count(chunk) <= budget:
            out.append(chunk)
            continue
        pieces = split_to_budget(chunk, budget, count)
        out.extend(pieces)
        split += 1
    if split:
        logger.info("[token-budget] split %d of %d chunks over %d tokens (%d -> %d chunks)",
                    split, len(chunks), budget, len(chunks), len(out))
    return out
