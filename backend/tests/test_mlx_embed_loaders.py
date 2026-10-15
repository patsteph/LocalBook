"""Loader rules: never float16 for a model that overflows in it; pooling per profile; no NaN out."""

import pytest

mx = pytest.importorskip("mlx.core")

from services import mlx_embed_loaders as L
from services.embedding_profile import EmbeddingProfile

GEMMA = EmbeddingProfile(model_id="g", loader="mlx_vlm", pooling="model", min_dtype="bfloat16")
ARCTIC = EmbeddingProfile(model_id="a", loader="mlx_embeddings", pooling="cls")


def test_a_float16_checkpoint_is_refused_before_loading(monkeypatch):
    monkeypatch.setattr(L, "_config", lambda m: {"text_config": {"dtype": "float16"}})
    with pytest.raises(L.EmbedPrecisionError, match="float16"):
        L.check_precision("g", GEMMA)
    L.check_precision("a", ARCTIC)            # no min_dtype → not this rule's business


def test_bf16_passes_and_loaded_float16_weights_are_caught(monkeypatch):
    monkeypatch.setattr(L, "_config", lambda m: {"dtype": "bfloat16"})
    L.check_precision("g", GEMMA)

    class _M:
        def parameters(self):
            return {"w": mx.zeros((2, 2), dtype=mx.float16)}
    assert L._loaded_float16(_M()) is True


class _Res:
    def __init__(self, lhs, pooled):
        self.last_hidden_state, self.text_embeds = lhs, pooled


class _Tok:
    def batch_encode_plus(self, chunk, **kw):
        return {"input_ids": mx.zeros((len(chunk), 3), dtype=mx.int32)}

    def __call__(self, chunk, **kw):
        import numpy as np
        return {"input_ids": np.zeros((len(chunk), 3), dtype=np.int32)}


def _model(pooled_value):
    lhs = mx.array([[[3.0, 4.0], [9.0, 9.0], [9.0, 9.0]]])          # token 0 = CLS = (3, 4)
    pooled = mx.array([pooled_value])

    def call(*a, **k):
        return _Res(lhs, pooled)
    return call


def test_pooling_follows_the_profile():
    cls_rows = L.forward(_model([0.0, 2.0]), _Tok(), ["x"], 8, ARCTIC)
    assert cls_rows == [pytest.approx([0.6, 0.8])]                    # token 0, normalised
    own_rows = L.forward(_model([0.0, 2.0]), _Tok(), ["x"], 8, GEMMA)
    assert own_rows == [pytest.approx([0.0, 1.0])]                    # the model's own output


def test_a_zero_or_nan_vector_is_never_returned():
    with pytest.raises(RuntimeError, match="NaN or zero"):
        L.forward(_model([0.0, 0.0]), _Tok(), ["x"], 8, GEMMA)
