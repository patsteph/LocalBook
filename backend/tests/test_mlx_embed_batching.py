"""Bulk embedding must not be sized by row count alone.

WHY THIS EXISTS (2026-08-21). The MLX rewrite dropped `embed_batch`'s slicing, reasoning that
it "existed to cap HTTP round-trips" and so no longer applied in-process. It was also bounding
MEMORY. Every sequence in a batch is padded to the longest one and attention is O(batch ×
seq²), so a single long text drags the whole batch up with it:

    32 canvas snapshots, one a 646 KB artifact payload (~5.5k tokens after truncation)
    → [metal::malloc] Attempting to allocate 62307438208 bytes
      which is greater than the maximum allowed buffer size of 9534832640 bytes

`embed_batch` then raised. `canvas_subtopics.assign_and_persist` treats an embedding failure
as "no topics" and returns [], and `api/canvas.populate` treats no topics as "fall back to the
grid" — so the journey map silently reverted to a linear layout on every machine, with no
error surfaced anywhere. Three layers of correct-in-isolation fail-soft turned an OOM into a
cosmetic-looking regression.

The suite could not have caught it: `test_canvas_subtopics` monkeypatches `embed_batch` with
synthetic vectors, so the real batching code was never exercised by a test at all. These run
the actual grouping logic against a fake tokenizer/model — no MLX, no weights, no network.
"""
import pytest

from services import mlx_engine as me


class _FakeTokenizer:
    """Token count = word count. Enough to drive the batching decisions under test."""

    def encode(self, text):
        return text.split()

    def batch_encode_plus(self, chunk, **kw):
        # Mirror the real padding contract: every row padded to the longest in the chunk.
        longest = max((len(t.split()) for t in chunk), default=1)
        longest = min(longest, kw.get("max_length") or longest)
        return {"input_ids": [[1] * longest for _ in chunk], "attention_mask": None}


class _Recorder:
    """Stands in for the model; records the shape of every forward pass it is asked to run."""

    def __init__(self, dim=8):
        self.dim = dim
        self.passes = []  # (batch, padded_seq)

    def __call__(self, input_ids, attention_mask=None):
        batch, seq = len(input_ids), len(input_ids[0])
        self.passes.append((batch, seq))

        class _Res:
            # last_hidden_state[:, 0, :] is the CLS row the engine pools.
            last_hidden_state = _FakeArray(batch, seq, self.dim)

        return _Res()


class _FakeArray:
    def __init__(self, b, s, d):
        self.b, self.s, self.d = b, s, d

    def __getitem__(self, key):
        return _FakeMat(self.b, self.d)


class _FakeMat:
    def __init__(self, b, d):
        self.b, self.d = b, d

    def __truediv__(self, other):
        return self

    def tolist(self):
        return [[0.5] * self.d for _ in range(self.b)]


@pytest.fixture
def wired(monkeypatch):
    """Wire the engine's thread function to fakes: no MLX, no weights, no network."""
    rec = _Recorder()

    class _FakeMx:
        class linalg:
            @staticmethod
            def norm(x, axis=None, keepdims=False):
                return 1.0

        @staticmethod
        def eval(*a, **k):
            return None

    # `import mlx.core as mx` resolves the ATTRIBUTE on the parent package before falling back
    # to sys.modules, so patching sys.modules alone is silently bypassed the moment any other
    # test in the run has already imported the real mlx. Patch both.
    import sys
    import types
    monkeypatch.setitem(sys.modules, "mlx.core", _FakeMx)
    parent = sys.modules.get("mlx")
    if parent is None:
        parent = types.ModuleType("mlx")
        monkeypatch.setitem(sys.modules, "mlx", parent)
    monkeypatch.setattr(parent, "core", _FakeMx, raising=False)

    engine = type("E", (), {"_embed_resident": {"m": (rec, _FakeTokenizer())},
                            "_last_used": {}, "_ensure_memory_limit": lambda self: None})()
    return engine, rec


def test_one_long_text_does_not_inflate_the_whole_batch(wired, monkeypatch):
    """THE bug. One long text among many short ones must not be padded together with them."""
    monkeypatch.setattr(me, "_embed_attn_budget", lambda: 8_000_000)
    engine, rec = wired
    texts = ["short text here"] * 31 + ["word " * 5500]

    out = me._embed_on_thread(engine, texts, "m", 32, 8192)

    assert len(out) == len(texts)
    # The contract is not "every pass fits the budget" — a lone oversized text has no smaller
    # batch to fall back to and must still be embedded. It is: nothing OVER budget may carry
    # more than one row. The 62 GB failure was (32, 5514); (1, 5500) is fine.
    over = [(b, s) for b, s in rec.passes if b * s * s > 8_000_000]
    assert all(b == 1 for b, _ in over), (
        f"a long text was padded together with others — this is the allocation the fix "
        f"exists to prevent. passes={rec.passes}"
    )
    assert (31, 3) in rec.passes, (
        f"the 31 short texts should still batch together, not be split by the long one. "
        f"passes={rec.passes}"
    )


def test_short_texts_still_ride_in_full_batches(wired, monkeypatch):
    """The fix must not make the common path slower: ordinary RAG-sized chunks are three
    orders of magnitude under the budget and should still batch at `batch_size`."""
    monkeypatch.setattr(me, "_embed_attn_budget", lambda: 67_000_000)
    engine, rec = wired

    me._embed_on_thread(engine, ["a short chunk of text"] * 64, "m", 32, 8192)

    assert [b for b, _ in rec.passes] == [32, 32], f"expected two full batches, got {rec.passes}"


def test_a_lone_oversized_text_is_still_embedded(wired, monkeypatch):
    """There is no smaller batch to fall back to, so one item must always be allowed through —
    dropping it would silently misalign every downstream vector with its text."""
    monkeypatch.setattr(me, "_embed_attn_budget", lambda: 1000)
    engine, rec = wired

    out = me._embed_on_thread(engine, ["word " * 4000], "m", 32, 8192)

    assert len(out) == 1 and out[0], "the text was dropped instead of embedded"
    assert len(rec.passes) == 1


def test_vectors_come_back_in_the_callers_order(wired, monkeypatch):
    """Batching sorts by length internally. If the results were returned in THAT order, every
    vector would be attached to the wrong text — silent, permanent index corruption."""
    monkeypatch.setattr(me, "_embed_attn_budget", lambda: 8_000_000)
    engine, rec = wired

    class _Marked(_Recorder):
        def __call__(self, input_ids, attention_mask=None):
            res = super().__call__(input_ids, attention_mask)
            return res

    texts = ["word " * n for n in (900, 5, 400, 2, 1200)]
    out = me._embed_on_thread(engine, texts, "m", 32, 8192)

    assert len(out) == 5
    assert all(v for v in out), "a slot was left unfilled by the reordering"


def test_the_budget_scales_with_the_machine(monkeypatch):
    """A flat constant is either an inert guardrail on a 16 GB Mac or a needless cap on a
    64 GB one — the same reasoning as `_ensure_memory_limit`."""
    monkeypatch.setattr(me, "_EMBED_ATTN_BUDGET_CACHE", None)
    monkeypatch.setattr("services.model_sizing.working_set_gb", lambda: 48.0)
    big = me._embed_attn_budget()

    monkeypatch.setattr(me, "_EMBED_ATTN_BUDGET_CACHE", None)
    monkeypatch.setattr("services.model_sizing.working_set_gb", lambda: 11.84)
    small = me._embed_attn_budget()

    assert big > small
    assert small >= 8_000_000, "never fall below a floor that can hold a real batch"
