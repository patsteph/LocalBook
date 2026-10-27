"""Load and run an MLX embedding model the way ITS profile says (embedding_profile).

Two families today:

  * `mlx_embeddings` + CLS pooling — arctic-embed-l-v2.0. Token 0 of the last
    hidden state, which matches the stored vectors exactly (cosine 1.0000). The
    library's `text_embeds` is mean-pooled and drifts ~0.84 from them, so it is
    deliberately NOT used here.
  * `mlx_vlm` + the model's own output — EmbeddingGemma 2. Mean pooling through
    its 512→768 dense head IS `text_embeds`; token 0 would be wrong vectors that
    do not look wrong.

Precision: a profile with `min_dtype` refuses a float16 checkpoint before it is
loaded, and re-checks the loaded weights. EmbeddingGemma's activations overflow
float16 ("NaN or silently degraded embeddings"); nothing here ever casts.
"""

from __future__ import annotations

import json
import logging
from typing import Any, List, Tuple

logger = logging.getLogger(__name__)


class EmbedPrecisionError(RuntimeError):
    """The checkpoint is stored in a precision this model must never run in."""


def _config(model_id: str) -> dict:
    try:
        from huggingface_hub import try_to_load_from_cache
        p = try_to_load_from_cache(model_id, "config.json")
        if isinstance(p, str):
            with open(p) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def stored_dtype(model_id: str) -> str:
    """The dtype the checkpoint declares ("" when it does not say)."""
    cfg = _config(model_id)
    for c in (cfg, cfg.get("text_config") or {}):
        for key in ("dtype", "torch_dtype"):
            v = c.get(key)
            if isinstance(v, str) and v:
                return v.replace("torch.", "")
    return ""


def check_precision(model_id: str, profile) -> None:
    """Refuse float16 for a model that must not run in it — before any weights load."""
    if not getattr(profile, "min_dtype", ""):
        return
    if stored_dtype(model_id) == "float16":
        raise EmbedPrecisionError(
            f"{model_id} is stored in float16; this model overflows in float16 and must run in "
            f"bfloat16 or float32. Use its bf16 build.")


def _loaded_float16(model) -> bool:
    try:
        import mlx.core as mx
        from mlx.utils import tree_flatten
        return any(v.dtype == mx.float16 for _, v in tree_flatten(model.parameters()))
    except Exception:
        return False


def load(model_id: str, profile) -> Tuple[Any, Any]:
    """(model, tokenizer) for the profile's loader."""
    check_precision(model_id, profile)
    if profile.loader == "mlx_vlm":
        from mlx_vlm.embedding_loader import load_embedding_model
        from mlx_vlm.utils import get_model_path
        from transformers import AutoTokenizer
        path = get_model_path(model_id)
        model = load_embedding_model(path)
        tokenizer = AutoTokenizer.from_pretrained(str(path))
    else:
        from mlx_embeddings import load as _eload
        model, tokenizer = _eload(model_id)
    if getattr(profile, "min_dtype", "") and _loaded_float16(model):
        raise EmbedPrecisionError(f"{model_id} loaded with float16 weights; refusing to run it in float16.")
    return model, tokenizer


def forward(model, tokenizer, chunk: List[str], max_length: int, profile):
    """One batch → L2-normalised rows (list of float lists). Raises on NaN/zero vectors — a bad
    vector is permanent index corruption, so it must never be returned."""
    import mlx.core as mx
    if profile.loader == "mlx_vlm":
        enc = tokenizer(chunk, padding=True, truncation=True, max_length=max_length, return_tensors="np")
        res = model(**{k: mx.array(v) for k, v in enc.items()})
    else:
        enc = tokenizer.batch_encode_plus(chunk, return_tensors="mlx", padding=True,
                                          truncation=True, max_length=max_length)
        res = model(enc["input_ids"], attention_mask=enc.get("attention_mask"))
    if profile.pooling == "model":
        embs = getattr(res, "text_embeds", None)
        if embs is None:
            raise RuntimeError(f"{profile.model_id} has no pooled text_embeds output")
    else:
        lhs = getattr(res, "last_hidden_state", None)
        if lhs is None:
            lhs = res[0]
        embs = lhs[:, 0, :]                       # CLS
    embs = embs / mx.linalg.norm(embs, axis=-1, keepdims=True)
    mx.eval(embs)
    rows = [[float(v) for v in row] for row in embs.tolist()]
    # A zero vector normalises to NaN, so one check covers both. (float16 overflow shows up
    # here first, as NaN.)
    if any(v != v for row in rows for v in row):
        raise RuntimeError(f"{profile.model_id} produced NaN or zero vectors — refusing to return them")
    return rows
