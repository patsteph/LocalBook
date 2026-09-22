"""Quantized KV cache: the only part of inference that grows with conversation.

Weights are fixed once a model loads. The KV cache is not — it grows with every
token, and at long context it can rival the weights. For Qwen3-30B-A3B at 128k
it is 12 GB on top of a 16 GB model.

mlx-lm can store that cache at 4 or 8 bits instead of fp16. LocalBook uses 8,
starting at 4096 tokens. Both halves of that default were measured, not guessed:

  * **8 bits, not 4.** Forced on from token zero, 8-bit produced output
    byte-identical to fp16 on this machine's fast model; 4-bit diverged
    ("which means" → "meaning"). Halving the cache is the uncontroversial win.

  * **From 4096 tokens, not zero.** Below the threshold the cache is untouched
    fp16, so ordinary short exchanges are bit-identical to before the feature
    existed. Quantization begins only where the memory matters.

Set `mlx_kv_bits` to None to turn it off entirely.
"""
import pytest

from services import mlx_engine, model_sizing


# ── the arguments handed to mlx-lm ──────────────────────────────────────────

def test_the_configured_settings_reach_the_engine():
    kw = mlx_engine._kv_kwargs()
    assert kw["kv_bits"] == 8
    assert kw["kv_group_size"] == 64
    assert kw["quantized_kv_start"] == 4096


def test_short_exchanges_are_untouched():
    """The safety property. `quantized_kv_start` above zero means a normal chat
    produces exactly what it did before this feature existed — verified by
    generating with and without and getting identical text."""
    assert mlx_engine._kv_kwargs()["quantized_kv_start"] > 0


def test_it_can_be_switched_off_entirely(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "mlx_kv_bits", None, raising=False)
    assert mlx_engine._kv_kwargs() == {}


def test_a_missing_setting_never_raises(monkeypatch):
    """A build of mlx-lm without these parameters, or a config that lost them,
    must degrade to an fp16 cache rather than breaking generation."""
    import config
    monkeypatch.delattr(config.settings, "mlx_kv_bits", raising=False)
    assert mlx_engine._kv_kwargs() == {}


def test_every_generation_path_gets_it():
    """Four call sites — streaming and non-streaming, text and vision. One left
    out is a path that silently keeps an fp16 cache while the sizing math has
    already promised the saving."""
    import inspect
    src = inspect.getsource(mlx_engine)
    assert src.count("_kv_kwargs()") >= 5      # the definition plus four uses


# ── what it costs, and what it saves ────────────────────────────────────────

def test_bytes_per_element_accounts_for_scales_and_biases():
    """The same accounting as quantized weights, because it is the same
    quantization: bits/8 plus a scale and a bias per group. 8-bit at group 64
    is 1.0625 bytes — an 1.88× saving, not the clean 2× a naive reading gives."""
    assert model_sizing.kv_bytes_per_element(None) == 2.0
    assert model_sizing.kv_bytes_per_element(8, 64) == pytest.approx(1.0625)
    assert model_sizing.kv_bytes_per_element(4, 64) == pytest.approx(0.5625)


QWEN_MOE = {"num_hidden_layers": 48, "num_attention_heads": 32,
            "num_key_value_heads": 4, "head_dim": 128,
            "hidden_size": 2048, "max_position_embeddings": 40960}


def test_the_saving_is_real_at_long_context():
    """12 GB → 6.4 GB for a 30B MoE at 128k — headroom that decides whether a
    model runs at all."""
    fp16 = model_sizing.kv_cache_gb(QWEN_MOE, 131072)
    eight = model_sizing.kv_cache_gb(QWEN_MOE, 131072, kv_bits=8)
    assert fp16 == pytest.approx(12.0, rel=0.05)
    assert eight == pytest.approx(fp16 / 1.88, rel=0.05)


def test_sizing_reflects_what_the_engine_is_configured_to_do(monkeypatch):
    """Reporting an fp16 cache while the engine quantizes it overstates the
    requirement and rejects models that would run — the same class of error as
    sizing quantized weights at full width."""
    assert model_sizing.configured_kv_bits() == 8
    from config import settings
    monkeypatch.setattr(settings, "mlx_kv_bits", None, raising=False)
    assert model_sizing.configured_kv_bits() is None


def test_an_explicit_bytes_per_elem_still_wins():
    """Callers that pass it directly keep working."""
    a = model_sizing.kv_cache_gb(QWEN_MOE, 16384, 2)
    b = model_sizing.kv_cache_gb(QWEN_MOE, 16384, kv_bits=None)
    assert a == b


def test_quantization_never_makes_the_cache_bigger():
    for bits in (4, 8):
        for ctx in (4096, 16384, 131072):
            assert model_sizing.kv_cache_gb(QWEN_MOE, ctx, kv_bits=bits) < \
                model_sizing.kv_cache_gb(QWEN_MOE, ctx)
