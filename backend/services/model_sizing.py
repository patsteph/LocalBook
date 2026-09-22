"""How much memory a model needs, and whether it fits this Mac.

Three quantities, and only the first two are ever large:

  weights      — every parameter that must be resident. For a QUANTIZED MLX
                 checkpoint this is NOT parameters × dtype width: MLX packs
                 weights into U32 words and HuggingFace reports the logical
                 parameter count against that dtype, so the naive product
                 overstates a 4-bit model by ~7×. The real cost is
                 `bits/8 + 2×2/group_size` bytes per parameter — weights plus a
                 scale and a bias per group. Verified to 0.0% against the file
                 sizes of six checkpoints (`tests/test_model_sizing_math.py`).

                 For a Mixture-of-Experts model this is EVERY expert. "30B with
                 3B active" describes the compute, not the footprint: under
                 stock mlx-lm all experts are resident. Expert streaming exists
                 in third-party forks, not in the engine LocalBook runs.

  KV cache     — 2 (K and V) × layers × kv_heads × head_dim × tokens × bytes.
                 Must use kv_heads, not attention heads: Qwen3-30B-A3B has 32
                 attention heads and 4 KV heads, an eightfold difference.
                 Sliding-window layers are capped at their window. Judged at
                 the DEPLOYED context, never the native one.

  activations  — scratch during a forward pass. Small, and covered by the ~1.2×
                 allowance HF accelerate and EleutherAI both use.

The budget is Apple's own `max_recommended_working_set_size` minus a NAMED
reserve for what LocalBook keeps resident anyway. It is deliberately not a
fraction of a fraction: stacking margins is how a 48 GB Mac came to be budgeted
26.6 GB against the 35.5 GB Apple says is addressable.
"""
import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

GB = 1024 ** 3

# Fraction of the GPU's addressable working set we will actually commit to model weights + KV.
# Not a fraction of total RAM: on this 16 GB M4 the working set is 11.84 GiB, so "60 % of RAM"
# (9.6) and "75 % of working set" (8.9) are different numbers, and only the latter tracks what
# the GPU can address on any given machine.
WORKING_SET_FRACTION = 0.75

_CACHE: Dict[str, Any] = {}


# ── hardware ────────────────────────────────────────────────────────────────────
def working_set_gb() -> float:
    """What the GPU can actually address, in GiB. Apple's own number, per-device."""
    hit = _CACHE.get("working_set")
    if hit:
        return hit
    val = 0.0
    try:
        import mlx.core as mx
        val = float(mx.device_info()["max_recommended_working_set_size"]) / GB
    except Exception:
        try:
            import subprocess
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            # Apple's recommended working set is ~74 % of RAM on Apple Silicon; approximate it
            # rather than pretending all of RAM is addressable.
            val = (float(out.stdout.strip()) / GB) * 0.74
        except Exception:
            val = 0.0
    _CACHE["working_set"] = val
    return val


# What LocalBook itself keeps resident regardless of the chat model: the
# embedding model (~1.1 GB for arctic-embed-l) plus app and framework overhead.
# Stated as a number rather than folded into a fraction — a reserve you can name
# is one you can argue with, and "× 0.75" was neither.
RESIDENT_RESERVE_GB = 2.5


def budget_gb(fraction: Optional[float] = None) -> float:
    """How much memory a chat model may have on this machine.

    Apple's `max_recommended_working_set_size` is ALREADY the safe ceiling for
    GPU-addressable memory — roughly 74% of RAM. Taking a further 25% off it, as
    this did, applies caution twice and made the answer needlessly pessimistic:
    a 48 GB Mac was budgeted 26.6 GB when Apple says 35.5 GB is addressable.

    So: working set minus a NAMED reserve for what we keep resident anyway.

    `fraction` is accepted for callers that want the old proportional behaviour.
    """
    ws = working_set_gb()
    if ws <= 0:
        return 0.0
    if fraction is not None:
        return round(ws * fraction, 2)
    return round(max(ws - RESIDENT_RESERVE_GB, ws * 0.5), 2)


# ── model files ─────────────────────────────────────────────────────────────────
def _snapshot_dir(model_id: str) -> Optional[str]:
    """The local snapshot for a cached HF model, without touching the network."""
    try:
        from huggingface_hub import try_to_load_from_cache
        for probe in ("config.json", "model.safetensors.index.json"):
            p = try_to_load_from_cache(model_id, probe)
            if isinstance(p, str) and os.path.isfile(p):
                return os.path.dirname(p)
    except Exception:
        pass

    # Probing for known filenames only finds layouts we already know about. A
    # GGUF repo ships neither config.json nor a safetensors index — just .gguf
    # files — so it was invisible, and an 8.6 GB download looked like nothing at
    # all. Fall back to the cache's own directory layout, which is the same for
    # every repo whatever it contains.
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        folder = os.path.join(HF_HUB_CACHE,
                              "models--" + model_id.replace("/", "--"), "snapshots")
        if os.path.isdir(folder):
            snaps = [os.path.join(folder, d) for d in os.listdir(folder)]
            snaps = [d for d in snaps if os.path.isdir(d)]
            if snaps:
                # Newest wins when a repo has several revisions cached.
                return max(snaps, key=os.path.getmtime)
    except Exception:
        pass
    return None


def load_config(model_id: str) -> Optional[Dict[str, Any]]:
    key = f"cfg::{model_id}"
    if key in _CACHE:
        return _CACHE[key]
    cfg = None
    d = _snapshot_dir(model_id)
    if d:
        try:
            with open(os.path.join(d, "config.json")) as fh:
                cfg = json.load(fh)
        except Exception:
            cfg = None
    _CACHE[key] = cfg
    return cfg


# Weight file formats we count. safetensors is the norm; mlx-whisper ships weights.npz;
# `.bin` covers older torch checkpoints. `.gguf` is deliberately absent — nothing loads GGUF
# since the llama-server sidecar was removed.
# `.gguf` added 2026-09-21: a companion tool (Meeting Notes) installs llama.cpp
# and downloads GGUF weights into the SAME HuggingFace cache LocalBook uses. An
# 8.6 GB repo we cannot size is invisible to presence, readiness and the model
# browser — the exact false negative that once hid a fully-downloaded Klein.
_WEIGHT_SUFFIXES = (".safetensors", ".npz", ".bin", ".gguf")


def exact_weight_gb(model_id: str) -> Optional[float]:
    """EXACT weight bytes on disk — no quantization guessing.

    Three layouts, tried in order:
      1. `model.safetensors.index.json` at the snapshot root (`metadata.total_size`) — what
         every sharded LLM checkpoint ships.
      2. Loose `*.safetensors` at the root — single-shard checkpoints have no index.
      3. **Nested components.** Diffusion models (FLUX/Klein) are not one checkpoint but
         several: `transformer/`, `text_encoder/`, `vae/`, each with its OWN index. Nothing
         lives at the root, so layouts 1 and 2 both find zero bytes and report the model
         absent — which made `is_present()` return False for a fully-downloaded 4.3 GB Klein
         and hid it from the model browser entirely.

    Returns None only when nothing is on disk.
    """
    key = f"w::{model_id}"
    if key in _CACHE:
        return _CACHE[key]
    val: Optional[float] = None
    d = _snapshot_dir(model_id)
    if d:
        idx = os.path.join(d, "model.safetensors.index.json")
        try:
            if os.path.isfile(idx):
                with open(idx) as fh:
                    meta = json.load(fh).get("metadata") or {}
                total = meta.get("total_size")
                if isinstance(total, (int, float)) and total > 0:
                    val = round(float(total) / GB, 3)
        except Exception:
            val = None
        if val is None:
            # Walk the whole snapshot: covers loose root shards AND nested components.
            # Follows symlinks because the HF cache stores real bytes in ../../blobs and
            # links them into the snapshot — os.path.getsize on the link reports the target,
            # but the walk must not skip them.
            #
            # Multiple weight formats on purpose: safetensors is the norm, but mlx-whisper
            # ships a single `weights.npz` and older checkpoints use `.bin`. Recognising only
            # safetensors reports those models as absent, which is the same false negative
            # that hid Klein.
            try:
                tot = 0
                for root, _dirs, files in os.walk(d, followlinks=True):
                    for f in files:
                        if f.endswith(_WEIGHT_SUFFIXES):
                            try:
                                tot += os.path.getsize(os.path.join(root, f))
                            except OSError:
                                pass          # a broken link mid-download — count nothing
                if tot > 0:
                    val = round(tot / GB, 3)
            except Exception:
                val = None
    _CACHE[key] = val
    return val


# ── KV geometry ─────────────────────────────────────────────────────────────────
def kv_geometry(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract what actually determines KV size. Handles the text_config nesting VLMs use."""
    if not cfg:
        return None
    t = cfg.get("text_config") or cfg
    layers = t.get("num_hidden_layers") or cfg.get("num_hidden_layers")
    kv_heads = t.get("num_key_value_heads") or cfg.get("num_key_value_heads")
    if not layers or not kv_heads:
        return None
    head_dim = t.get("head_dim") or cfg.get("head_dim")
    if not head_dim:
        hidden = t.get("hidden_size") or cfg.get("hidden_size")
        heads = t.get("num_attention_heads") or cfg.get("num_attention_heads")
        head_dim = int(hidden / heads) if hidden and heads else None
    if not head_dim:
        return None

    # Which layers grow with context, and which are pinned to a sliding window.
    layer_types = t.get("layer_types") or cfg.get("layer_types")
    window = t.get("sliding_window") or cfg.get("sliding_window")
    max_pos = t.get("max_position_embeddings") or cfg.get("max_position_embeddings") or 0
    if isinstance(layer_types, list) and layer_types:
        sliding = sum(1 for x in layer_types if "sliding" in str(x))
        full = len(layer_types) - sliding
    elif window and max_pos and int(window) < int(max_pos):
        sliding, full = int(layers), 0     # every layer slides
    else:
        sliding, full = 0, int(layers)     # no effective sliding window
    # A window >= the max context is not a window at all (phi declares 262144 vs 131072).
    if window and max_pos and int(window) >= int(max_pos):
        sliding, full = 0, int(layers)
        window = None
    return {
        "layers": int(layers), "kv_heads": int(kv_heads), "head_dim": int(head_dim),
        "full_layers": int(full), "sliding_layers": int(sliding),
        "sliding_window": int(window) if window else None,
        "max_position_embeddings": int(max_pos) if max_pos else None,
    }


def kv_cache_gb(cfg: Dict[str, Any], context_tokens: int, bytes_per_elem: int = 2) -> Optional[float]:
    """KV cache in GiB at a given context, from real geometry.

    per layer per token = 2 (K and V) × kv_heads × head_dim × bytes_per_elem
    Sliding layers are capped at their window; only full-attention layers scale with context.
    """
    g = kv_geometry(cfg)
    if not g or context_tokens <= 0:
        return None
    per_layer_token = 2 * g["kv_heads"] * g["head_dim"] * bytes_per_elem
    full_tokens = context_tokens * g["full_layers"]
    win = g["sliding_window"] or context_tokens
    slide_tokens = min(context_tokens, win) * g["sliding_layers"]
    return round(per_layer_token * (full_tokens + slide_tokens) / GB, 3)


# ── the fit verdict ─────────────────────────────────────────────────────────────
def fit(model_id: str, context_tokens: int = 0, *, activation_factor: float = 1.2) -> Dict[str, Any]:
    """Will this model fit, at this context, on this machine?

    `activation_factor` covers activations/scratch on top of weights (HF accelerate and
    EleutherAI both use ~1.2). Applied to weights only — KV is counted exactly.
    """
    cfg = load_config(model_id)
    weight = exact_weight_gb(model_id)
    kv = kv_cache_gb(cfg, context_tokens) if (cfg and context_tokens) else None
    bud = budget_gb()

    out: Dict[str, Any] = {
        "model_id": model_id,
        "weight_gb": weight,
        "kv_gb": kv,
        "context_tokens": int(context_tokens or 0),
        "budget_gb": bud,
        "working_set_gb": round(working_set_gb(), 2),
        "geometry": kv_geometry(cfg) if cfg else None,
        "exact": weight is not None,
    }
    if weight is None:
        # Not on disk. Say so rather than inventing a number — the old code's silent 0.0
        # estimate is exactly how a guardrail becomes decorative.
        out.update({"fits": None, "recommendation": "unknown",
                    "reason": "model not in the local HF cache — size unknown until downloaded"})
        return out

    need = round(weight * activation_factor + (kv or 0.0), 3)
    out["total_needed_gb"] = need
    out["headroom_gb"] = round(bud - need, 3)
    if bud <= 0:
        out.update({"fits": None, "recommendation": "unknown", "reason": "no working-set reading"})
    elif need <= bud * 0.9:
        # 0.9, matching model_catalog.fit_for. KV is already counted EXACTLY
        # here and activations are already covered by activation_factor, so a
        # further 20% would be a third margin on the same number.
        out.update({"fits": True, "recommendation": "ok"})
    elif need <= bud:
        out.update({"fits": True, "recommendation": "tight"})
    else:
        out.update({"fits": False, "recommendation": "over"})
    return out


def reset_cache() -> None:
    _CACHE.clear()
