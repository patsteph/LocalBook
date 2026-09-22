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
    from config import settings
    kw = mlx_engine._kv_kwargs(settings.fast_model)
    assert kw["kv_bits"] == 8
    assert kw["kv_group_size"] == 64
    assert kw["quantized_kv_start"] == 4096


def test_short_exchanges_are_untouched():
    """The safety property. `quantized_kv_start` above zero means a normal chat
    produces exactly what it did before this feature existed — verified by
    generating with and without and getting identical text."""
    from config import settings
    assert mlx_engine._kv_kwargs(settings.fast_model)["quantized_kv_start"] > 0


def test_it_can_be_switched_off_entirely(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "mlx_kv_bits", None, raising=False)
    assert mlx_engine._kv_kwargs(settings.fast_model) == {}


def test_a_missing_setting_never_raises(monkeypatch):
    """A build of mlx-lm without these parameters, or a config that lost them,
    must degrade to an fp16 cache rather than breaking generation."""
    import config
    monkeypatch.delattr(config.settings, "mlx_kv_bits", raising=False)
    assert mlx_engine._kv_kwargs(config.settings.fast_model) == {}


def test_every_generation_path_gets_it():
    """Four call sites — streaming and non-streaming, text and vision. One left
    out is a path that silently keeps an fp16 cache while the sizing math has
    already promised the saving."""
    import inspect
    src = inspect.getsource(mlx_engine)
    assert src.count("_kv_kwargs(") >= 5       # the definition plus four uses


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


# ── the failure the Evaluator caught ────────────────────────────────────────
#
# Shipping `kv_bits=8` on its own broke structured output: the evaluator's JSON
# category fell 87 → 15, having produced ZERO questions in 3.7s where the
# baseline produced three in 43s. The cause was not quality at all:
#
#     RotatingKVCache Quantization NYI
#
# mlx-lm RAISES when asked to quantize a rotating cache, and the caller sees an
# empty response — not a worse answer, nothing. Models with sliding-window
# attention use a rotating cache, and gemma, our main AND vision model, is one:
# 35 of its 42 layers slide over a 512-token window.
#
# The default appeared to work only because every prompt was under the 4096
# token threshold. The first long conversation would have failed outright.

def test_a_sliding_window_model_is_never_asked_to_quantize():
    """gemma is the main model. Getting this wrong is an empty answer, not a
    slightly worse one."""
    from config import settings
    assert mlx_engine._uses_rotating_cache(settings.main_model) is True
    assert mlx_engine._kv_kwargs(settings.main_model) == {}


def test_a_model_without_a_sliding_window_still_gets_the_saving():
    """phi has no sliding window and quantizes fine — verified by generating
    with quantization forced on and getting normal output."""
    from config import settings
    assert mlx_engine._uses_rotating_cache(settings.fast_model) is False
    assert mlx_engine._kv_kwargs(settings.fast_model)["kv_bits"] == 8


@pytest.mark.parametrize("model_id", ["", "some/model-we-cannot-read", None])
def test_an_unreadable_model_forgoes_the_saving_rather_than_risking_it(model_id):
    """The two ways to be wrong are not symmetrical. Guessing 'rotating' costs a
    memory saving; guessing 'not rotating' costs every answer."""
    assert mlx_engine._kv_kwargs(model_id or "") == {}


def test_every_call_site_passes_the_model():
    """A call site that forgets the model id gets the unknown-model path, which
    is safe but silently disables the feature — and one that passes nothing
    would have been the original bug all over again."""
    import inspect
    src = inspect.getsource(mlx_engine)
    assert "_kv_kwargs()" not in src.replace("def _kv_kwargs()", ""), \
        "a generation path calls _kv_kwargs with no model"


def test_the_geometry_check_matches_the_real_configs():
    """`sliding_layers` is what decides this, and kv_geometry already handles
    the trap: phi declares a 262144 window against a 131072 max position, which
    is not a window at all."""
    from config import settings
    from services.model_sizing import kv_geometry, load_config
    gemma = kv_geometry(load_config(settings.main_model) or {}) or {}
    phi = kv_geometry(load_config(settings.fast_model) or {}) or {}
    assert gemma.get("sliding_layers", 0) > 0
    assert phi.get("sliding_layers", 0) == 0
