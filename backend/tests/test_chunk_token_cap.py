"""cap_chunks must keep every character and every chunk within the limit."""
from services.chunk_token_cap import cap_chunks

LIMIT = 10


def words(text: str) -> int:
    """A stand-in tokenizer: one token per whitespace-separated word, one per 4 characters of a
    long unbroken run (the case that sent a 2204-token chunk past the cap)."""
    return sum(max(1, -(-len(w) // 4)) for w in text.split())


def _joined(pieces):
    return "".join(pieces)


def test_chunks_within_the_limit_are_returned_unchanged():
    chunks = ["one two three", "four five"]
    assert cap_chunks(chunks, words, LIMIT) == chunks


def test_long_chunk_is_split_and_loses_nothing():
    text = " ".join(f"w{i}" for i in range(50))
    out = cap_chunks([text], words, LIMIT)
    assert len(out) > 1
    assert all(words(p) <= LIMIT for p in out)
    assert _joined(out) == text


def test_prefers_paragraph_breaks():
    a = " ".join(["aa"] * 8)
    b = " ".join(["bb"] * 8)
    out = cap_chunks([f"{a}\n\n{b}"], words, LIMIT)
    assert out == [f"{a}\n\n", b]


def test_unbroken_run_is_halved_by_characters():
    text = "x" * 400
    out = cap_chunks([text], words, LIMIT)
    assert all(words(p) <= LIMIT for p in out)
    assert _joined(out) == text


def test_order_and_untouched_neighbours_are_preserved():
    short1, short2 = "keep me", "and me"
    long = " ".join(f"w{i}" for i in range(40))
    out = cap_chunks([short1, long, short2], words, LIMIT)
    assert out[0] == short1 and out[-1] == short2
    assert _joined(out[1:-1]) == long


def test_empty_and_single_character_inputs_terminate():
    assert cap_chunks([""], words, LIMIT) == [""]
    assert cap_chunks(["a"], lambda t: 99, LIMIT) == ["a"]
