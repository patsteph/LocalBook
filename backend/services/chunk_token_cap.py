"""Split chunks that are too long to embed on every device.

The phone's Neural Engine embedder is compiled for at most 512 tokens (BOS and EOS included),
and a vector only means the same thing on both devices if both saw the same text. Cutting an
over-long chunk loses its tail from search; splitting it keeps every word searchable.

`cap_chunks` is pure: it takes a `count_tokens(text) -> int` callable, so it is testable without
a model. `make_token_counter` builds the real one from the embedder's own tokenizer.
"""
from typing import Callable, List

# 512 minus BOS and EOS.
DEFAULT_MAX_CONTENT_TOKENS = 510

# Coarse to fine. Each level keeps as much of the text's own structure as the limit allows.
_SEPARATORS = ("\n\n", "\n", ". ", " ")


def cap_chunks(
    chunks: List[str],
    count_tokens: Callable[[str], int],
    max_tokens: int = DEFAULT_MAX_CONTENT_TOKENS,
) -> List[str]:
    """Return `chunks` with every chunk over `max_tokens` split into pieces that fit.

    Chunks already within the limit are returned unchanged and in order, so their stored
    vectors stay valid. Nothing is dropped: joining a split chunk's pieces with the separator
    used at each break reproduces its text.
    """
    out: List[str] = []
    for chunk in chunks:
        out.extend(_split(chunk, count_tokens, max_tokens, 0))
    return out


def _split(text: str, count: Callable[[str], int], limit: int, level: int) -> List[str]:
    if count(text) <= limit:
        return [text]
    if level >= len(_SEPARATORS):
        # No separator left: halve by characters. A single character always fits, so stop there.
        if len(text) <= 1:
            return [text]
        mid = len(text) // 2
        return _split(text[:mid], count, limit, level) + _split(text[mid:], count, limit, level)

    sep = _SEPARATORS[level]
    parts = text.split(sep)
    if len(parts) == 1:
        return _split(text, count, limit, level + 1)

    out: List[str] = []
    buf = ""
    for i, part in enumerate(parts):
        # Keep the separator with the part before it so sentences keep their full stop.
        piece = part + (sep if i < len(parts) - 1 else "")
        candidate = buf + piece
        if buf and count(candidate) > limit:
            out.append(buf)
            buf = ""
            candidate = piece
        if count(candidate) <= limit:
            buf = candidate
        else:
            if buf:
                out.append(buf)
                buf = ""
            out.extend(_split(piece, count, limit, level + 1))
    if buf:
        out.append(buf)
    return out


def make_token_counter(tokenizer) -> Callable[[str], int]:
    """Token count as the embedder sees it, minus the BOS/EOS the model adds itself."""
    def count(text: str) -> int:
        return len(tokenizer(text, add_special_tokens=False)["input_ids"])
    return count
