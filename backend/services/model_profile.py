"""Which model setup this Mac can afford: standard or compact (LB-1, 2026-10-03).

Standard keeps a separate fast model resident beside the main one. Compact lets the
fast role share the main model — one resident instead of two — which on today's
defaults saves ~3.4 GB: the fast model's weights AND its context cache (the fast
model has no sliding window, so its KV costs ~8× the main model's per token).

Decided from the reserve-aware GPU budget and the models ACTUALLY configured on this
Mac — model-agnostic, nothing here names a model. Measured (model_sizing, 2026-10-03):
standard needs ~10.5 GB, compact ~7.1 GB → an 18 GB Mac (budget ~10.8) runs compact,
24 GB (~15.3) runs standard. Per Mac, never synced: the answer depends on the hardware.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

MAIN_CTX = 16384
FAST_CTX = 8192
HEADROOM = 1.10          # standard must fit with 10% to spare, or the Mac goes compact
_configured_fast: str = ""


def _need(model_id: str, ctx: int) -> float:
    from services.model_sizing import exact_weight_gb, kv_cache_gb, load_config
    w = exact_weight_gb(model_id) or 0.0
    cfg = load_config(model_id) if w else None
    kv = (kv_cache_gb(cfg, ctx) if cfg else None) or 0.0
    return w * 1.2 + kv


def decide() -> Dict[str, Any]:
    from config import settings
    from services.model_sizing import budget_gb, exact_weight_gb

    override = (getattr(settings, "model_profile", "auto") or "auto").strip().lower()
    fast = _configured_fast or settings.fast_model
    main, embed = settings.main_model, settings.embedding_model
    embed_gb = exact_weight_gb(embed) or 0.0
    compact_need = _need(main, MAIN_CTX) + embed_gb
    standard_need = compact_need + (_need(fast, FAST_CTX) if fast and fast != main else 0.0)
    budget = budget_gb()
    if override in ("standard", "compact"):
        profile, reason = override, "chosen in LLM Studio"
    elif budget <= 0 or standard_need <= 0:
        profile, reason = "standard", "memory budget unknown"
    elif budget < standard_need * HEADROOM:
        profile, reason = "compact", (f"budget {budget:.1f} GB < {standard_need * HEADROOM:.1f} GB "
                                      f"needed for separate main and fast models")
    else:
        profile, reason = "standard", f"budget {budget:.1f} GB fits both ({standard_need:.1f} GB)"
    return {"profile": profile, "setting": override, "reason": reason, "budget_gb": budget,
            "standard_gb": round(standard_need, 2), "compact_gb": round(compact_need, 2),
            "fast_model": fast, "main_model": main}


def apply_at_startup() -> Dict[str, Any]:
    """Run once after the saved model choices are restored. Never raises."""
    global _configured_fast
    from config import settings
    try:
        _configured_fast = settings.fast_model
        d = decide()
        if d["profile"] == "compact" and settings.fast_model != settings.main_model:
            settings.fast_model = settings.main_model
            print(f"[SafeStart] Compact setup: the fast role shares the main model ({d['reason']})")
        else:
            print(f"[SafeStart] Model setup: {d['profile']} ({d['reason']})")
        return d
    except Exception as exc:
        logger.warning(f"[model-profile] not applied: {exc}")
        return {"profile": "standard", "reason": f"error: {exc}"}
