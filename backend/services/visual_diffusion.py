"""Visual System v2 — FLUX.2-klein text-to-image via Ollama.

Klein (Black Forest Labs FLUX.2 [klein]) is uniquely well-suited for
infographic hero imagery because, unlike most diffusion models, it
renders readable text correctly. License: Apache 2.0 (commercial OK).

Used in the hybrid composer mode: when a PERSUADE-class idiom or an
explicit hero request lands, this module generates the raster fill while
the Gemma-composed SVG provides structure and labels around it.

API (per docs/api.md as of 2026-05-24):
  POST /api/generate
  body: {"model": "x/flux2-klein", "prompt": "...", "width": N, "height": N, "steps": N}
  response: {..., "image": "<base64 PNG>", "done": true}

Lazy load: Klein is ~5.7 GB. Pre-warm on first call, idle-unload after
generation (set keep_alive: 0 on last call) when in swap mode.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from config import settings
from services.ollama_service import ollama_service
from services.visual_capability import VisualCapability, get_capability

logger = logging.getLogger(__name__)

DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 768
DEFAULT_STEPS = 4  # Klein is a "schnell" class model — few steps suffice

KLEIN_TIMEOUT = 240.0  # Generous; first cold load can take 60-90s
KLEIN_PREWARM_TIMEOUT = 600.0


# ──────────────────────────────────────────────────────────────────────
# Klein wrapper
# ──────────────────────────────────────────────────────────────────────
@dataclass
class DiffusionResult:
    """Result of a Klein text-to-image generation."""
    success: bool
    png_bytes: Optional[bytes] = None
    width: int = 0
    height: int = 0
    elapsed_ms: int = 0
    model: Optional[str] = None
    prompt_used: Optional[str] = None
    error: Optional[str] = None


class KleinDiffusionService:
    """Text-to-image via Klein (FLUX.2 [klein]) running in Ollama."""

    def __init__(self):
        self._warmed = False
        self._warm_lock = asyncio.Lock()

    async def generate(
        self,
        prompt: str,
        capability: Optional[VisualCapability] = None,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        steps: int = DEFAULT_STEPS,
        unload_after: bool = True,
    ) -> DiffusionResult:
        """Generate a PNG from a prompt.

        Args:
            prompt: text prompt for image generation
            capability: optional snapshot (saves a detection call)
            width/height: image dimensions in px
            steps: diffusion steps
            unload_after: if True, set keep_alive=0 so Klein unloads after
                this call. Use True on swap-mode machines (default safe).
                On concurrent-mode machines you can leave it loaded.
        """
        cap = capability or await get_capability()
        if not cap.klein_model:
            return DiffusionResult(
                success=False,
                error="Klein model not installed (need x/flux2-klein in ollama list)",
            )

        model = cap.klein_model
        t0 = time.time()

        # Pre-warm on first ever call (idempotent within the singleton)
        async with self._warm_lock:
            if not self._warmed:
                warm_ok = await self._prewarm(model)
                self._warmed = warm_ok

        payload = {
            "model": model,
            "prompt": prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "stream": False,
            "keep_alive": 0 if unload_after else "5m",
        }

        url = f"{settings.ollama_base_url}/api/generate"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=KLEIN_TIMEOUT)) as client:
                logger.info(
                    f"[visual_diffusion] generate model={model} "
                    f"{width}x{height} steps={steps} unload_after={unload_after}"
                )
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            return DiffusionResult(
                success=False,
                model=model,
                elapsed_ms=int((time.time() - t0) * 1000),
                error="Klein generation timed out",
            )
        except httpx.HTTPStatusError as e:
            return DiffusionResult(
                success=False,
                model=model,
                elapsed_ms=int((time.time() - t0) * 1000),
                error=f"Klein HTTP {e.response.status_code}: {e.response.text[:200]}",
            )
        except Exception as e:
            return DiffusionResult(
                success=False,
                model=model,
                elapsed_ms=int((time.time() - t0) * 1000),
                error=f"Klein call failed: {e}",
            )

        b64 = data.get("image") or ""
        if not b64:
            return DiffusionResult(
                success=False,
                model=model,
                elapsed_ms=int((time.time() - t0) * 1000),
                error=(f"Klein returned no image (keys={list(data.keys())}); "
                       f"check API contract"),
            )

        try:
            png = base64.b64decode(b64)
        except Exception as e:
            return DiffusionResult(
                success=False,
                model=model,
                elapsed_ms=int((time.time() - t0) * 1000),
                error=f"Klein image base64 decode failed: {e}",
            )

        return DiffusionResult(
            success=True,
            png_bytes=png,
            width=width,
            height=height,
            elapsed_ms=int((time.time() - t0) * 1000),
            model=model,
            prompt_used=prompt,
        )

    async def _prewarm(self, model: str) -> bool:
        """Tiny first request to load Klein into VRAM. Best-effort."""
        logger.info(f"[visual_diffusion] pre-warming {model}")
        t0 = time.time()
        try:
            url = f"{settings.ollama_base_url}/api/generate"
            payload = {
                "model": model,
                "prompt": "a single dot",
                "width": 256,
                "height": 256,
                "steps": 1,
                "stream": False,
                "keep_alive": "30s",
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=KLEIN_PREWARM_TIMEOUT)) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
            logger.info(
                f"[visual_diffusion] {model} warm ({time.time() - t0:.1f}s)"
            )
            return True
        except Exception as e:
            logger.warning(f"[visual_diffusion] pre-warm failed: {e}")
            return False


# ──────────────────────────────────────────────────────────────────────
# Gemma-as-prompt-writer chain
# ──────────────────────────────────────────────────────────────────────
# Klein's output quality depends heavily on prompt phrasing. Gemma writes
# the prompt because it (a) knows the visual intent, (b) understands what
# styles work, (c) can emphasize Klein's text-rendering strength.

PROMPT_WRITER_SYSTEM = """You are writing image-generation prompts for FLUX.2 [klein], a diffusion model that excels at rendering readable text in images. You receive the visual intent and the title that should appear in the hero image. Write a single prompt that produces a polished, business-presentation-quality hero image.

PROMPT RULES:
- 1-3 sentences, no longer
- Lead with the subject ("A clean isometric illustration of...", "A minimalist editorial graphic showing...", etc.)
- Specify the style: flat vector, isometric, editorial illustration, infographic, low-poly, or photographic — match to the subject
- Specify the color palette in concrete terms ("indigo and slate", "soft pastels", "blue and white with orange accent")
- If the title text should appear in the image, include it in quotes: "with the text 'Cloud Architecture' rendered cleanly"
- Avoid: generic adjectives ("beautiful", "amazing"), people unless explicitly requested, copyrighted brand imagery, complex scenes
- Optimize for: clean composition, legible text, professional aesthetic, single focal point

Return ONLY the prompt text — no JSON, no explanations, no quotes around the whole prompt."""


async def write_klein_prompt(
    intent: str,
    title: str,
    style_hint: str = "minimalist editorial",
    capability: Optional[VisualCapability] = None,
) -> Optional[str]:
    """Use Gemma (or whichever main is configured) to write a Klein prompt."""
    cap = capability or await get_capability()
    # Prefer Gemma when available; fall back to configured main
    model = cap.gemma_model or settings.ollama_model

    user = (
        f"Visual intent: {intent}\n"
        f"Hero title (to appear in the image): {title}\n"
        f"Style hint: {style_hint}\n\n"
        f"Write the Klein prompt now."
    )
    # Gemma 4 burns tokens on internal channel/thinking output before the
    # final answer; with num_predict too low (<1500) the visible response
    # ends up empty because the model never reached the final-message stage.
    result = await ollama_service.generate(
        prompt=user,
        system=PROMPT_WRITER_SYSTEM,
        model=model,
        temperature=0.4,
        num_predict=2000,
        timeout=180.0,
        voice_modifier=False,
    )
    raw = (result.get("response") or "").strip()
    if not raw:
        return None
    # Strip leading/trailing quotes if the model wrapped output
    cleaned = re.sub(r'^["\']|["\']$', "", raw).strip()
    return cleaned or None


# ──────────────────────────────────────────────────────────────────────
# Explicit unload utility — for RAM-safe swap orchestration
# ──────────────────────────────────────────────────────────────────────
async def force_unload(model: str) -> bool:
    """Tell Ollama to evict `model` from memory immediately.

    Used by the hybrid composer to pre-emptively free Gemma RAM before
    calling Klein on swap-mode machines (16-31 GB total RAM). Without
    this, Gemma stays loaded for ~5 min, and Klein loading on top can
    push us into OS swap or trigger OOM.

    Ollama's /api/generate with empty prompt + keep_alive=0 is the
    documented way to just unload a model.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={"model": model, "keep_alive": 0, "prompt": ""},
            )
            response.raise_for_status()
            logger.info(f"[visual_diffusion] explicit unload {model} OK")
            return True
    except Exception as e:
        # Non-fatal — Ollama may evict on memory pressure anyway
        logger.warning(f"[visual_diffusion] unload nudge for {model} failed: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────
# Module-level singleton
# ──────────────────────────────────────────────────────────────────────
klein_diffusion = KleinDiffusionService()
