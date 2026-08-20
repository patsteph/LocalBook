"""Visual System v2 — runtime capability detection.

Decides which generation path the visual composer should use based on:
  • Which MLX models are downloaded (Gemma 4, Flux2-Klein, vision)
  • System RAM (concurrent vs swap mode for model loading)
  • Currently-configured main/fast/vision model names

Two deployment setups (see READFIRST/VISUAL_V2_PLAN.md):
  • non-Gemma setups → template path (Tier D; Setup-A olmo trio removed S1/B1 2026-07-03)
  • Setup B — Gemma trio (gemma4 + phi4-mini + flux2-klein optional) → freeform path

Cached for 60s — model availability changes rarely; system RAM never.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


from config import settings

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Model-name detection rules. Match by prefix so :tag suffix doesn't matter.
# ──────────────────────────────────────────────────────────────────────

# RAM thresholds (bytes). Total system RAM, not available.
RAM_CONCURRENT_THRESHOLD = 32 * 1024**3  # 32 GB+ → can co-load Gemma + Klein
RAM_SWAP_THRESHOLD = 18 * 1024**3        # 18-31 GB → swap mode for Setup B
# <18 GB → swap-strict; Klein gets skipped on allocation failure

CACHE_TTL_SEC = 60.0


class Setup(str, Enum):
    SETUP_A = "setup_a"   # Olmo trio
    SETUP_B = "setup_b"   # Gemma trio
    UNKNOWN = "unknown"   # Neither main model recognized


class ConcurrencyMode(str, Enum):
    CONCURRENT = "concurrent"     # 32 GB+ — both Gemma + Klein can stay loaded
    SWAP = "swap"                 # 18-31 GB — explicit unload between Gemma/Klein
    SWAP_STRICT = "swap_strict"   # <18 GB — same as swap + warn user


@dataclass
class VisualCapability:
    """Snapshot of what the current install can do for visual generation."""
    setup: Setup
    concurrency_mode: ConcurrencyMode
    total_ram_gb: float

    # Installed (weights present in the HF cache)
    has_gemma: bool = False
    has_klein: bool = False
    has_vision_model: bool = False
    installed_models: List[str] = field(default_factory=list)

    # Resolved canonical names (highest-priority match for each family)
    gemma_model: Optional[str] = None
    klein_model: Optional[str] = None
    vision_model: Optional[str] = None

    # Derived flags for the composer
    can_freeform_gemma: bool = False     # Setup B primary path
    can_critic_gemma_vision: bool = False  # Gemma as vision critic
    can_critic_separate_vision: bool = False  # Granite/etc as critic
    can_diffusion_klein: bool = False    # Hero raster images
    template_fallback_available: bool = True  # Always true (Tier D floor)

    @property
    def warn_user(self) -> bool:
        return self.concurrency_mode == ConcurrencyMode.SWAP_STRICT and self.has_klein

    def summary(self) -> str:
        bits = [f"setup={self.setup.value}", f"ram={self.total_ram_gb:.0f}GB",
                f"mode={self.concurrency_mode.value}"]
        if self.can_freeform_gemma:
            bits.append("gemma-freeform")
        if self.can_diffusion_klein:
            bits.append("klein")
        crit = "gemma" if self.can_critic_gemma_vision else (
            "vision-model" if self.can_critic_separate_vision else "none")
        bits.append(f"critic={crit}")
        return " ".join(bits)


# ──────────────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────────────
_cached: Optional[VisualCapability] = None
_cached_at: float = 0.0
_lock = asyncio.Lock()


async def get_capability(force_refresh: bool = False) -> VisualCapability:
    """Return current visual capability snapshot (cached 60s)."""
    global _cached, _cached_at
    if not force_refresh and _cached and (time.time() - _cached_at) < CACHE_TTL_SEC:
        return _cached
    async with _lock:
        if not force_refresh and _cached and (time.time() - _cached_at) < CACHE_TTL_SEC:
            return _cached
        cap = await _detect()
        # Robustness: a degraded 'unknown' detection means the Ollama /api/tags
        # probe failed (typically a timeout while Ollama is saturated under
        # concurrent load) — NOT that Klein/Gemma vanished. Never let that
        # overwrite a known-good snapshot, or the visual silently drops to the
        # non-Klein legacy path and produces an unusable result. Keep last-good
        # and refresh the TTL so we re-probe on the next call.
        if cap.setup == Setup.UNKNOWN and _cached is not None and _cached.setup != Setup.UNKNOWN:
            logger.warning(
                "[visual_capability] probe degraded (Ollama busy/unreachable); "
                f"keeping last-good capability: {_cached.summary()}"
            )
            _cached_at = time.time()
            return _cached
        _cached = cap
        _cached_at = time.time()
        logger.info(f"[visual_capability] detected: {cap.summary()}")
        return cap


def invalidate_cache():
    """Force next get_capability() to re-detect."""
    global _cached, _cached_at
    _cached = None
    _cached_at = 0.0


async def _detect() -> VisualCapability:
    # Presence is a FILESYSTEM question now — the MLX checkpoints either have weights in the
    # HF cache or they do not. This used to enumerate `ollama list` over /api/tags, which
    # returned an empty list once Ollama was gone and silently degraded every visual to the
    # template path.
    from services.model_presence import is_present

    total_ram = _total_ram_bytes()
    _roles = {
        "gemma": settings.mlx_main_model,
        "klein": settings.mlx_image_model,
        "vision": settings.mlx_vision_model,
    }
    _have = {k: (bool(v) and is_present(v)) for k, v in _roles.items()}
    installed = [v for k, v in _roles.items() if _have[k]]
    gemma = _roles["gemma"] if _have["gemma"] else None
    klein = _roles["klein"] if _have["klein"] else None
    vision = _roles["vision"] if _have["vision"] else None

    # Determine setup. Prefer Setup B if Gemma is the configured main model
    # OR if Gemma is installed and configured vision_model also points at Gemma.
    # S1/B1 (2026-07-03): Setup-A (olmo trio) detection removed — non-gemma
    # setups take the always-available template path (Tier D). SETUP_A stays in
    # the enum only as a legacy label the frontend types; it is never produced.
    # UNKNOWN semantics preserved (the degraded-probe last-good guard relies on it).
    setup = Setup.SETUP_B if gemma else Setup.UNKNOWN

    # Concurrency mode based on total RAM
    if total_ram >= RAM_CONCURRENT_THRESHOLD:
        mode = ConcurrencyMode.CONCURRENT
    elif total_ram >= RAM_SWAP_THRESHOLD:
        mode = ConcurrencyMode.SWAP
    else:
        mode = ConcurrencyMode.SWAP_STRICT

    cap = VisualCapability(
        setup=setup,
        concurrency_mode=mode,
        total_ram_gb=total_ram / (1024**3),
        has_gemma=gemma is not None,
        has_klein=klein is not None,
        has_vision_model=vision is not None,
        installed_models=installed,
        gemma_model=gemma,
        klein_model=klein,
        vision_model=vision,
    )

    # Derived capability flags
    cap.can_freeform_gemma = setup == Setup.SETUP_B and gemma is not None
    cap.can_critic_gemma_vision = gemma is not None  # Gemma is multimodal
    cap.can_critic_separate_vision = vision is not None
    # Klein availability is independent of which main model is configured.
    # A user on Setup A with Klein installed can still use hybrid mode for
    # PERSUADE-class visuals — the composer decides per-request.
    cap.can_diffusion_klein = klein is not None

    return cap




def _total_ram_bytes() -> int:
    """Total physical RAM in bytes. Falls back to 16 GB assumption."""
    try:
        import psutil
        return psutil.virtual_memory().total
    except Exception:
        logger.warning("[visual_capability] psutil unavailable; assuming 16 GB")
        return 16 * 1024**3
