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


from config import settings
from services.llm_runtime import llm_runtime
from services.visual_capability import VisualCapability, get_capability

logger = logging.getLogger(__name__)

DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 768
DEFAULT_STEPS = 4  # Klein is a "schnell" class model — few steps suffice


# Aspect-ratio → (draft_dims, hero_dims). All values divisible by 8 (Klein
# constraint). Hero dims push resolution where Klein's coherence holds up;
# beyond these the wall-clock cost grows faster than the quality.
_ASPECT_DIMS: dict[str, dict[str, tuple[int, int]]] = {
    "16:9": {"draft": (1024, 576), "hero": (1280, 720)},
    "4:3":  {"draft": (1024, 768), "hero": (1280, 960)},
    "1:1":  {"draft": (1024, 1024), "hero": (1024, 1024)},
    "9:16": {"draft": (576, 1024), "hero": (720, 1280)},
}
_TIER_STEPS = {"draft": 4, "hero": 8}

# Default negative prompt — kept short on purpose. Klein is most sensitive
# to "no text artifacts" (prevents garbled caption text) and "no
# watermarks". Longer negative prompts diminish over schnell-class speed.
DEFAULT_NEGATIVE_PROMPT = (
    "no text artifacts, no watermarks, no UI mockups, no random people, "
    "no extra limbs, no broken typography, no border decorations"
)


def resolve_dimensions(
    aspect_ratio: Optional[str],
    quality_tier: Optional[str],
) -> tuple[int, int, int]:
    """Map (aspect, tier) → (width, height, steps).

    Falls back to (DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_STEPS) on any
    unknown / missing input — preserves legacy behavior for callers that
    don't pass the new params.
    """
    aspect = aspect_ratio if aspect_ratio in _ASPECT_DIMS else None
    tier = quality_tier if quality_tier in _TIER_STEPS else None
    if not aspect or not tier:
        return DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_STEPS
    w, h = _ASPECT_DIMS[aspect][tier]
    return w, h, _TIER_STEPS[tier]


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
        # Wave 9.3b — mflux (FLUX.2 Klein on MLX) resident model, lazy-loaded once.
        self._mflux_model = None
        self._mflux_lock = asyncio.Lock()

    async def generate(
        self,
        prompt: str,
        capability: Optional[VisualCapability] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        steps: Optional[int] = None,
        unload_after: bool = True,
        aspect_ratio: Optional[str] = None,
        quality_tier: Optional[str] = None,
        negative_prompt: Optional[str] = None,
    ) -> DiffusionResult:
        """Generate a PNG from a prompt.

        Args:
            prompt: text prompt for image generation
            capability: optional snapshot (saves a detection call)
            width/height/steps: explicit pixel dims + step count. If
                omitted AND aspect_ratio+quality_tier provided, dims are
                resolved from the (aspect, tier) table. If everything is
                omitted, falls back to the legacy 1024×768 @ 4 steps.
            aspect_ratio: "16:9" | "4:3" | "1:1" | "9:16" — resolves
                width/height via resolve_dimensions().
            quality_tier: "draft" | "hero" — resolves step count via
                resolve_dimensions().
            negative_prompt: passed to Klein as "negative_prompt" in the
                payload. None = no negative prompt sent.
            unload_after: if True, set keep_alive=0 so Klein unloads after
                this call. Use True on swap-mode machines (default safe).
                On concurrent-mode machines you can leave it loaded.
        """
        cap = capability or await get_capability()

        # Wave 9.3b — MLX image engine (Klein/FLUX via mflux). When image_engine=mlx, generate
        # in-process via mflux instead of Ollama x/flux2-klein — same dims/steps, no negative
        # prompt (FLUX.2 is CFG-distilled). Bypasses the Ollama klein_model check. Dual-engine
        # fallback to Ollama on error.
        # In-process via mflux (FLUX.2 Klein). The Ollama `x/flux2-klein` path that used to
        # follow this — plus its pre-warm — is gone with the transport; there is nothing to
        # fall back to, so an mflux failure IS the failure.
        if not cap.klein_model:
            return DiffusionResult(
                success=False,
                error=f"Klein not downloaded ({settings.mlx_image_model}) — get it from LLM Studio.",
            )
        rw, rh, rs = resolve_dimensions(aspect_ratio, quality_tier)
        return await self._generate_mflux(
            prompt,
            width=width if width is not None else rw,
            height=height if height is not None else rh,
            steps=steps if steps is not None else rs,
        )

    async def _load_mflux(self):
        """Load (cache) the mflux FLUX.2 Klein model. Loads once; runs off-loop."""
        if self._mflux_model is not None:
            return self._mflux_model
        async with self._mflux_lock:
            if self._mflux_model is not None:
                return self._mflux_model

            def _load():
                from mflux.models.common.config import ModelConfig  # lazy
                from mflux.models.flux2.variants import Flux2Klein
                cfg = ModelConfig.from_name(model_name="flux2-klein-4b")
                # model_path = the pre-quantized 4-bit mflux weights (HF repo);
                # quantize=4 matches. NOTE: confirm these constructor args on the
                # first real generate (the ~4.3 GB download is the build DoD).
                return Flux2Klein(
                    model_config=cfg, quantize=4,
                    model_path=settings.mlx_image_model,
                    lora_paths=None, lora_scales=None)

            self._mflux_model = await asyncio.to_thread(_load)
            logger.info(f"[visual_diffusion] mflux Klein loaded ({settings.mlx_image_model})")
            return self._mflux_model

    async def _generate_mflux(self, prompt: str, *, width: int, height: int, steps: int) -> DiffusionResult:
        """Generate a PNG via mflux FLUX.2 Klein (MLX, in-process). → DiffusionResult."""
        t0 = time.time()
        try:
            model = await self._load_mflux()
        except Exception as e:
            return DiffusionResult(success=False, model=settings.mlx_image_model,
                                   error=f"mflux Klein load failed: {e}")

        def _gen() -> bytes:
            import io
            image = model.generate_image(
                seed=42, prompt=prompt, width=width, height=height,
                guidance=1.0, num_inference_steps=steps,
                scheduler="flow_match_euler_discrete")
            pil = getattr(image, "image", image)   # GeneratedImage.image is a PIL Image
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            return buf.getvalue()

        try:
            logger.info(f"[visual_diffusion] mflux generate {width}x{height} steps={steps}")
            png = await asyncio.to_thread(_gen)
        except Exception as e:
            return DiffusionResult(success=False, model=settings.mlx_image_model,
                                   elapsed_ms=int((time.time() - t0) * 1000),
                                   error=f"mflux Klein generate failed: {e}")
        return DiffusionResult(
            success=True, png_bytes=png, width=width, height=height,
            elapsed_ms=int((time.time() - t0) * 1000),
            model=settings.mlx_image_model, prompt_used=prompt)

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


# ──────────────────────────────────────────────────────────────────────
# Art-director prompt-writer chain
# ──────────────────────────────────────────────────────────────────────
# A stronger alternative to PROMPT_WRITER_SYSTEM above. Used by the
# full-bleed Klein path. The key differences:
#   • Front-loads art direction (style, palette, lighting, mood) because
#     Klein's text encoder truncates the tail at ~512 tokens
#   • Drops any in-image label requests from the user (Klein cannot spell
#     technical strings; we render typography via SVG overlay instead)
#   • Targets 50-150 words / ~200 tokens — well under the truncation point
#   • Concrete visual language only — no "beautiful", no "amazing"

KLEIN_BRIEF_SYSTEM = """You are an art director writing a single prompt for FLUX.2 [klein], a text-to-image diffusion model. Your job: convert the user's request into a concise, vivid prompt that produces a striking, professional image.

ASSEMBLE THE PROMPT IN THIS EXACT ORDER (Klein parses front-to-back; the tail gets truncated at the text encoder's limit):
  1. STYLE + MEDIUM — e.g. "A cinematic isometric illustration", "A flat editorial vector graphic", "A photographic 3/4 hero shot"
  2. PALETTE — concrete named colors: "warm walnut and brushed silver tones with electric teal, amber, and magenta accents"
  3. LIGHTING + MOOD — "soft directional golden-hour light, calm didactic atmosphere"
  4. SUBJECT — what's actually in the image, described visually (the literal scene/object)
  5. COMPOSITION — layout, perspective, focal point — "centered composition with a faceted crystalline database as the focal point"

HARD RULES:
- Output ONE single prompt, 50–150 words (≈200 tokens MAX). Brevity is the point — every word fights for encoder budget.
- DROP all of: label text the user wants in the image (LanceDB, Embedder, Chunker, etc.), label instructions, "annotated", "labeled", "callouts", "captions", "with the text X". Klein cannot spell technical strings — we render typography on top via SVG. Including label requests here only wastes tokens and produces garbage.
- DROP filler adjectives ("beautiful", "amazing", "nice", "perfect").
- KEEP every aesthetic / lighting / palette / mood / style cue the user provided. These are precious.
- KEEP the literal visual subject described as a scene (a Mac Mini on a walnut desk, a glowing crystalline gem, a cascade of floating documents).
- USE concrete visual language: "soft directional light from upper-left" beats "good lighting", "muted walnut brown" beats "warm tone".

Return ONLY the prompt text. No JSON, no quotes around the whole prompt, no "Prompt:" prefix, no explanations.

EXAMPLES:

User asks: "A simple SVG diagram of five stages, calm palette, sans-serif labels"
→ "A clean editorial vector illustration in a calm three-color palette: soft cornflower blue, warm coral orange, and light slate grey on a bone-white field. Crisp flat shapes, no gradients or shadows, generous whitespace. Five connected stages flowing left to right as distinct iconic forms — a stack of pages, a faceted prism, a hexagonal vessel, a magnifier paired with a glowing core, and a chat bubble. Subtle directional flow lines connect them, with one loop returning. Centered horizontal composition with breathing room on all sides."

User asks: A 200-word cinematic Mac Mini scene
→ "A cinematic isometric cutaway illustration at golden hour, in warm walnut and brushed aluminum tones with electric teal, amber, and magenta highlights. Studio Ghibli warmth, soft volumetric light, shallow depth of field. A sleek aluminum Mac Mini sits on a walnut desk; its chassis is transparent glass revealing a luminous multi-layered interior — floating documents cascading into a glowing splitter, a faceted crystalline gem at center twinkling like a galaxy, and a rotating polyhedron of light at right. Fiber-optic ribbons of cyan, amber, and magenta flow between layers. Hyper-detailed technical-illustration meets editorial elegance, quiet competence."
"""


async def write_klein_brief(
    user_prompt: str,
    title: str,
    capability: Optional[VisualCapability] = None,
) -> Optional[str]:
    """Compress a user's request into a Klein-optimal art-direction prompt.

    Used by the full-bleed Klein path in two cases:
      1. The classifier said the prompt is NOT passthrough-ready (sparse,
         structurally dominant, lacks rich art direction)
      2. The user's passthrough-ready prompt exceeds Klein's text-encoder
         budget (~2000 chars / ~512 tokens), in which case the cinematic
         tail would be silently truncated. Compressing preserves the
         user's art direction by re-ordering it to the front.

    Returns a ~50-150 word string assembled in Klein-optimal order, or
    None on Gemma failure (caller falls through to the structural path).
    """
    cap = capability or await get_capability()
    model = cap.gemma_model or settings.ollama_model

    user_msg = (
        f"USER REQUEST:\n{user_prompt}\n\n"
        f"TITLE FOR THE VISUAL (for context only — do NOT include this "
        f"text in the image, the SVG overlay handles titles): {title}\n\n"
        f"Write the Klein prompt now. Front-load art direction. Drop any "
        f"label/caption/annotation text requests."
    )
    from services.llm_runtime import PRIORITY_FOREGROUND
    result = await llm_runtime.generate(
        prompt=user_msg,
        system=KLEIN_BRIEF_SYSTEM,
        model=model,
        temperature=0.4,
        num_predict=2500,
        timeout=180.0,
        voice_modifier=False,
        priority=PRIORITY_FOREGROUND,  # user is waiting on this image
    )
    raw = (result.get("response") or "").strip()
    if not raw:
        return None
    cleaned = re.sub(r'^["\']+|["\']+$', "", raw).strip()
    cleaned = re.sub(r'^(?:klein\s+)?prompt:\s*', "", cleaned, flags=re.IGNORECASE)
    return cleaned or None


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
    from services.llm_runtime import PRIORITY_FOREGROUND
    result = await llm_runtime.generate(
        prompt=user,
        system=PROMPT_WRITER_SYSTEM,
        model=model,
        temperature=0.4,
        num_predict=2000,
        timeout=180.0,
        voice_modifier=False,
        priority=PRIORITY_FOREGROUND,  # user is waiting on this image
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
    """Evict `model` from memory immediately.

    Used by the hybrid composer to pre-emptively free Gemma before calling Klein on swap-mode
    machines (16-31 GB). This matters MORE under MLX than it did under Ollama: Ollama would
    eventually evict on its own keep_alive TTL, whereas MLX holds weights until something
    explicitly unloads them. Gemma (4.8 GB) plus Klein (4.3 GB) resident together is most of
    a 16 GB machine.

    Was a `keep_alive: 0` nudge over HTTP; now an in-process unload. Still best-effort —
    never let a memory optimisation fail a visual.
    """
    try:
        from services.mlx_engine import mlx_engine, mlx_model_for_role
        target = mlx_model_for_role(model) or model
        freed = await mlx_engine.unload(target)
        logger.info(f"[visual_diffusion] explicit unload {target}: "
                    f"{'freed' if freed else 'not resident'}")
        return bool(freed)
    except Exception as e:
        logger.warning(f"[visual_diffusion] unload nudge for {model} failed: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────
# Module-level singleton
# ──────────────────────────────────────────────────────────────────────
klein_diffusion = KleinDiffusionService()
