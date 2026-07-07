"""RAM-fit estimation for local models (Locker rebuild — build A).

Answers "will this quantized model fit / how much context can it take on THIS
Mac's unified memory," so the Locker can slot + warn accurately instead of the
old disk_size×1.2 guess. Grounded in the deep-research RAM guidance (2026-07-07):

    weight_GB ≈ param_count_B × bytes_per_weight(quant)
    keep model weights under ~60% of total unified memory
    KV cache grows linearly with context (≈4–8 GB for a 14B @ 32K)

Pure functions, no I/O — safe to call from slotting + swap analysis.
"""
from __future__ import annotations

import re
from typing import Optional

# Approximate BYTES per weight by quantization level (research 2026-07-07:
# Q4_K_M ≈ 4.9 bits ≈ 0.61 B/wt … F16 = 2 B/wt). Keys are matched case-insensitively
# with a longest-prefix fallback so unseen sub-variants (Q4_K_S, IQ4_XS) still resolve.
_QUANT_BYTES = {
    "Q2_K": 0.33,
    "Q3_K": 0.43,
    "Q3_K_S": 0.43, "Q3_K_M": 0.48, "Q3_K_L": 0.52,
    "Q4_0": 0.56, "Q4_1": 0.63,
    "Q4_K": 0.61, "Q4_K_S": 0.58, "Q4_K_M": 0.61,
    "Q5_0": 0.69, "Q5_1": 0.75,
    "Q5_K": 0.71, "Q5_K_S": 0.69, "Q5_K_M": 0.71,
    "Q6_K": 0.83,
    "Q8_0": 1.06, "Q8_K": 1.09,
    "IQ2": 0.28, "IQ3": 0.42, "IQ4": 0.55,
    "F16": 2.0, "FP16": 2.0, "BF16": 2.0,
    "F32": 4.0, "FP32": 4.0,
}

_DEFAULT_BYTES_PER_WEIGHT = 1.0  # conservative when quant is unknown

# Keep weights under this fraction of total unified memory (leaves room for macOS,
# the app, KV cache, embeddings, and the runtime).
WEIGHT_BUDGET_FRACTION = 0.60


def bytes_per_weight(quant: Optional[str]) -> float:
    """Bytes-per-weight for an Ollama/GGUF quantization label. Case-insensitive;
    falls back to the longest matching known prefix, then a safe default."""
    if not quant:
        return _DEFAULT_BYTES_PER_WEIGHT
    q = quant.strip().upper().replace("-", "_")
    if q in _QUANT_BYTES:
        return _QUANT_BYTES[q]
    # Longest-prefix match (e.g. "Q4_K_M_SOMETHING" → "Q4_K_M" → "Q4_K" → "Q4").
    for key in sorted(_QUANT_BYTES, key=len, reverse=True):
        if q.startswith(key):
            return _QUANT_BYTES[key]
    return _DEFAULT_BYTES_PER_WEIGHT


def parse_param_count_b(param_size: Optional[str]) -> float:
    """Parse an Ollama `details.parameter_size` string into BILLIONS of params.
    '8.0B'→8.0, '566.70M'→0.567, '3.8B'→3.8, '1500K'→0.0015, '7'→7.0."""
    if not param_size:
        return 0.0
    m = re.match(r"\s*([\d.]+)\s*([BMK]?)", str(param_size).strip().upper())
    if not m:
        return 0.0
    try:
        val = float(m.group(1))
    except ValueError:
        return 0.0
    unit = m.group(2)
    if unit == "M":
        return val / 1_000.0
    if unit == "K":
        return val / 1_000_000.0
    return val  # 'B' or bare number → already billions


def estimate_weight_gb(param_count_b: float, quant: Optional[str]) -> float:
    """Weights-only memory footprint in GB. B params × bytes/weight = GB."""
    if param_count_b <= 0:
        return 0.0
    return round(param_count_b * bytes_per_weight(quant), 2)


def estimate_kv_cache_gb(param_count_b: float, context_tokens: int) -> float:
    """Rough KV-cache footprint in GB — scales ~linearly with context and model
    size. Calibrated to the research anchor (~4–8 GB for a 14B @ 32K ≈ midpoint
    6 GB): ~6 GB / (14B × 32k) per (B·token). Deliberately approximate; used only
    for headroom warnings, not hard gating."""
    if param_count_b <= 0 or context_tokens <= 0:
        return 0.0
    per_b_per_ktok = 6.0 / (14.0 * 32.0)  # GB per (billion-param · 1k-token)
    return round(param_count_b * (context_tokens / 1000.0) * per_b_per_ktok, 2)


def ram_fit(
    param_count_b: float,
    quant: Optional[str],
    total_ram_gb: float,
    context_tokens: int = 0,
) -> dict:
    """Assess whether a model's weights fit this machine's unified memory.

    Returns a JSON-serialisable dict the Locker/UI can surface directly:
      weight_gb, kv_gb, budget_gb (60% of RAM), fits (bool), headroom_gb,
      recommendation ("ok" | "tight" | "over").
    """
    weight = estimate_weight_gb(param_count_b, quant)
    kv = estimate_kv_cache_gb(param_count_b, context_tokens) if context_tokens else 0.0
    budget = round(WEIGHT_BUDGET_FRACTION * float(total_ram_gb or 0), 2)
    total_needed = round(weight + kv, 2)
    headroom = round(budget - total_needed, 2)
    if budget <= 0 or weight <= 0:
        rec = "unknown"
        fits = True  # don't block on missing data
    elif total_needed <= budget * 0.8:
        rec, fits = "ok", True
    elif total_needed <= budget:
        rec, fits = "tight", True
    else:
        rec, fits = "over", False
    return {
        "weight_gb": weight,
        "kv_gb": kv,
        "budget_gb": budget,
        "total_needed_gb": total_needed,
        "headroom_gb": headroom,
        "fits": fits,
        "recommendation": rec,
    }
