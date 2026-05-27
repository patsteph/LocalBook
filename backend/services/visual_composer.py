"""Visual System v2 — central composer / orchestrator.

Single entry point for all visual generation. Replaces direct calls to
VisualGenerator from the API layer. Decides which path to use based on
runtime capability detection (see visual_capability.py), runs the
critic-driven retry loop where applicable, and returns a unified
ComposedVisual result.

Generation path matrix:

  Setup B + Gemma         → freeform Gemma SVG + Gemma critic (+ Klein for hero)
  Setup B + Gemma (no AI) → freeform Gemma SVG + Gemma critic
  Setup A + Olmo + vision → freeform Olmo SVG (scaffolded) + vision critic
                            with template-path fallback on validation failure
  Setup A only            → existing 42-template path (Tier D, unchanged)

Phase 1 ship: this file routes everything to the legacy template path
(Tier D) while the freeform + critic modules are still being built. Each
new module slots in via a single conditional branch below.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional

from services.visual_capability import (
    Setup,
    VisualCapability,
    get_capability,
)
from services.visual_critic import CritiqueResult, visual_critic
from services.visual_diffusion import (
    DEFAULT_NEGATIVE_PROMPT,
    force_unload,
    klein_diffusion,
    resolve_dimensions,
    write_klein_brief,
    write_klein_prompt,
)
from services.visual_freeform import (
    FreeformResult,
    gemma_skeleton,    # Setup B primary path
    olmo_freeform,     # Backward-compat alias = olmo_skeleton
    olmo_skeleton,     # Setup A primary path
)
from services.visual_generator import GeneratedVisual, VisualGenerator
from services.visual_intent import IllustrationIntent, classify_intent
from services.svg_renderer import render_svg_to_png
from services.visual_skeletons import HERO_IDIOMS

logger = logging.getLogger(__name__)


# Klein's text encoder (T5-XXL or similar) truncates beyond ~512 tokens.
# At ~4 chars/token that's ~2000 chars; we set the safe passthrough limit
# below that so the user's full prompt fits without losing the cinematic
# tail. Prompts over this go through the art director compressor.
KLEIN_PROMPT_PASSTHROUGH_LIMIT = 1500


# ──────────────────────────────────────────────────────────────────────
# Result types — unified across Mermaid (template path) and SVG (freeform)
# ──────────────────────────────────────────────────────────────────────
class OutputFormat(str, Enum):
    SVG = "svg"          # Native SVG markup (freeform path)
    MERMAID = "mermaid"  # Mermaid code (template path → rendered via mermaid_renderer)


class GenerationPath(str, Enum):
    GEMMA_FREEFORM = "gemma_freeform"        # Setup B primary
    OLMO_FREEFORM = "olmo_freeform"          # Setup A primary
    TEMPLATE = "template"                     # Tier D fallback (today's path)


@dataclass
class CriticScore:
    """5-axis vision critic scoring + actionable critique."""
    legibility: float = 0.0
    hierarchy: float = 0.0
    balance: float = 0.0
    color_harmony: float = 0.0
    message_clarity: float = 0.0
    overall: float = 0.0
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    @property
    def passed_threshold(self, threshold: float = 0.70) -> bool:
        return self.overall >= threshold


@dataclass
class ComposedVisual:
    """Unified result type. Either svg_markup OR mermaid_code is populated."""
    success: bool

    # Routing metadata
    path: GenerationPath
    setup: Setup
    output_format: OutputFormat

    # Content (one of these will be populated)
    svg_markup: Optional[str] = None
    mermaid_code: Optional[str] = None

    # Common metadata
    title: str = ""
    subtitle: str = ""   # rendered as a separate line in the frontend overlay
    description: str = ""
    key_points: List[str] = field(default_factory=list)
    alternatives: List[Dict[str, str]] = field(default_factory=list)

    # Tier-A/B only
    critic_score: Optional[CriticScore] = None
    retry_count: int = 0   # 0 if no critic retry; 1 if a second pass ran

    # Telemetry
    generation_ms: int = 0
    error: Optional[str] = None

    # Provenance (for debugging + scoreboards)
    model_used: Optional[str] = None
    template_id: Optional[str] = None
    template_name: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────
# Composer
# ──────────────────────────────────────────────────────────────────────
class VisualComposer:
    """Routes generation to the appropriate path based on capability."""

    def __init__(self):
        # critic_threshold + max_retries removed in the 2026-05-26 consolidation
        # — the critic-driven retry loop was found too noisy to be useful.
        self._template_generator: Optional[VisualGenerator] = None

    def _get_template_generator(self) -> VisualGenerator:
        """Lazy init the legacy template generator (Tier D path)."""
        if self._template_generator is None:
            self._template_generator = VisualGenerator()
        return self._template_generator

    async def compose(
        self,
        content: str,
        template_id: Optional[str] = None,
        force_path: Optional[GenerationPath] = None,
        force_idiom: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> ComposedVisual:
        """Generate a visual using the best available path.

        Args:
            content: Source text to visualize (prompt + enriched notebook
                sources, used for actual generation)
            template_id: Optional specific legacy template (forces Tier D path)
            force_path: Override capability detection (testing only)
            force_idiom: Skip the v2 two-stage picker and use this skeleton
                idiom directly. Used by the 'Swap idiom' UI to let users
                override picker mistakes.
            topic: The raw user prompt BEFORE enrichment. Used by the
                illustration-intent classifier so notebook source content
                doesn't pollute the classification (a 4-6 KB enriched blob
                dominated by RAG-article text routinely fools the binary
                illustration-vs-diagram judgment). Falls back to `content`
                when None for backward compatibility.
        """
        t0 = time.time()
        capability = await get_capability()

        # Stash forced idiom on the instance so _compose_*_freeform can pick
        # it up. Cleared per-call.
        self._forced_idiom = force_idiom if force_idiom and not template_id else None

        # Stash topic on the instance for the classifier. Falls back to
        # parsing it out of the enriched content when caller didn't pass it.
        self._topic = topic if topic and topic.strip() else _extract_topic(content)

        path = force_path or self._select_path(capability, template_id)
        logger.info(
            f"[visual_composer] compose path={path.value} "
            f"setup={capability.setup.value} content_chars={len(content)} "
            f"topic_chars={len(self._topic)} force_idiom={force_idiom or '-'}"
        )

        try:
            if path == GenerationPath.GEMMA_FREEFORM:
                result = await self._compose_gemma_freeform(content, capability)
            elif path == GenerationPath.OLMO_FREEFORM:
                result = await self._compose_olmo_freeform(content, capability)
            else:
                result = await self._compose_template(content, template_id, capability)

            result.generation_ms = int((time.time() - t0) * 1000)
            return result
        except Exception as e:
            logger.exception(f"[visual_composer] generation failed on path={path.value}")
            # Last-resort fallback: template path always works (Tier D floor)
            if path != GenerationPath.TEMPLATE:
                logger.info("[visual_composer] falling back to template path")
                result = await self._compose_template(content, template_id, capability)
                result.generation_ms = int((time.time() - t0) * 1000)
                result.error = f"primary path failed: {e}"
                return result
            return ComposedVisual(
                success=False,
                path=path,
                setup=capability.setup,
                output_format=OutputFormat.MERMAID,
                error=str(e),
                generation_ms=int((time.time() - t0) * 1000),
            )

    async def compose_stream(
        self,
        content: str,
        template_id: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream generation events to the UI. Yields dicts shaped as:
            {"type": "tier", "setup": "...", "path": "...", "concurrency": "..."}
            {"type": "progress", "stage": "...", "pct": 0-100}
            {"type": "critic", "score": {...}}
            {"type": "result", "visual": ComposedVisual-as-dict}
            {"type": "error", "message": "..."}

        Phase 1 stub: emits tier event then runs non-streaming compose() and emits result.
        Production version (Phase 1 final) streams the per-pass output from freeform.
        """
        capability = await get_capability()
        path = self._select_path(capability, template_id)
        yield {
            "type": "tier",
            "setup": capability.setup.value,
            "path": path.value,
            "concurrency": capability.concurrency_mode.value,
        }
        try:
            visual = await self.compose(content, template_id=template_id)
            if visual.critic_score:
                yield {"type": "critic", "score": _critic_to_dict(visual.critic_score)}
            yield {"type": "result", "visual": _visual_to_dict(visual)}
        except Exception as e:
            yield {"type": "error", "message": str(e)}

    # ────────────────────────────────────────────────────────────────
    # Path selection
    # ────────────────────────────────────────────────────────────────
    def _select_path(
        self,
        capability: VisualCapability,
        template_id: Optional[str],
    ) -> GenerationPath:
        """Decide which generation path to use.

        Explicit template_id always forces the template path — caller is
        asking for a specific visual idiom from the legacy catalog.
        """
        if template_id:
            return GenerationPath.TEMPLATE

        if capability.can_freeform_gemma:
            return GenerationPath.GEMMA_FREEFORM
        if capability.can_freeform_olmo:
            return GenerationPath.OLMO_FREEFORM

        return GenerationPath.TEMPLATE

    # ────────────────────────────────────────────────────────────────
    # Path implementations
    # ────────────────────────────────────────────────────────────────
    async def _compose_template(
        self,
        content: str,
        template_id: Optional[str],
        capability: VisualCapability,
    ) -> ComposedVisual:
        """Tier D — existing 42-template Mermaid path. Unchanged behavior."""
        generator = self._get_template_generator()
        legacy: GeneratedVisual = await generator.generate(content, template_id=template_id)
        return ComposedVisual(
            success=legacy.success,
            path=GenerationPath.TEMPLATE,
            setup=capability.setup,
            output_format=OutputFormat.MERMAID,
            mermaid_code=legacy.mermaid_code or None,
            title=legacy.title,
            description=legacy.description,
            key_points=legacy.key_points,
            alternatives=legacy.alternatives,
            template_id=legacy.template_id,
            template_name=legacy.template_name,
            error=legacy.error,
        )

    async def _compose_gemma_freeform(
        self,
        content: str,
        capability: VisualCapability,
    ) -> ComposedVisual:
        """Setup B primary path — UNIFIED v2 (2026-05-25):

        Picker-driven routing (no keyword heuristics):

        1. gemma_skeleton runs the two-stage picker → idiom
        2. If picker chose hero_with_callouts → invoke Klein for the raster slot
        3. Otherwise → standard skeleton output (vector only)
        4. Skeleton failure → gemma_freeform legacy (rare fallback)
        5. Freeform failure → template path (Tier D safety net)

        Benchmark validated: skeleton path beats freeform by ~0.05 critic
        AND runs ~4× faster (42s vs 170s). Picker handles hero choice
        natively — no more false hybrid triggers on abstract value-prop
        content.

        v2.1 addition (2026-05-26): an illustration-intent pre-classifier
        runs BEFORE the structural picker. When the user is asking for a
        cinematic/illustrated image (cues like "cinematic", "isometric",
        "render", art-direction vocabulary) AND Klein is installed, the
        composer routes to _compose_klein_full_bleed — a pure raster hero
        with no SVG callouts. Mirrors how Gemini / Claude / ChatGPT route
        image asks separately from structural ones. Failure here is
        non-fatal: falls through to the structural skeleton path.
        """
        # NEW: illustration-intent pre-classifier (Klein-first path)
        forced = getattr(self, "_forced_idiom", None)
        if capability.can_diffusion_klein and not forced:
            # Classify on the user's TOPIC only, not the enriched content.
            # Notebook-source enrichment dominates the blob and routinely
            # fools the binary illustration-vs-diagram judgment (e.g., a
            # purely structural "simple SVG diagram" prompt gets misjudged
            # as illustration because the source blob mentions palettes
            # and aesthetic words from RAG articles).
            topic_for_classifier = getattr(self, "_topic", None) or content
            intent = await classify_intent(topic_for_classifier, capability.gemma_model)
            if intent.routes_to_klein():
                logger.info(
                    f"[visual_composer] intent routes to Klein full-bleed "
                    f"(conf={intent.confidence:.2f}, reason={intent.reason!r})"
                )
                klein_result = await self._compose_klein_full_bleed(
                    content, intent, capability, topic=topic_for_classifier,
                )
                if klein_result and klein_result.svg_markup:
                    return klein_result
                logger.warning(
                    "[visual_composer] Klein full-bleed path failed; "
                    "falling through to structural skeleton path"
                )

        # Path 1: gemma_skeleton (primary — runs the two-stage picker)
        logger.info("[visual_composer] Setup B path: gemma_skeleton (two-stage picker)")
        scaffolded = await gemma_skeleton.generate(
            content, capability, force_idiom=forced,
        )

        if scaffolded.success and scaffolded.svg_markup and _is_valid_svg(scaffolded.svg_markup):
            # If picker chose hero_with_callouts AND Klein available → upgrade
            # to hybrid (replace the {{HERO_IMAGE_B64}} placeholder with a real
            # Klein-generated image). Otherwise the vector skeleton is final.
            final_svg = scaffolded.svg_markup
            used_hybrid = False
            if (
                scaffolded.idiom_id == "hero_with_callouts"
                and capability.can_diffusion_klein
                and "{{HERO_IMAGE_B64}}" in scaffolded.svg_markup
            ):
                # Note: gemma_skeleton fills the {{HERO_IMAGE_B64}} placeholder
                # with empty string when it strips remaining placeholders. So
                # this branch only fires when the slot-fill happened to leave
                # the placeholder intact — defensive only.
                used_hybrid = True

            # Alternative: if picker chose hero_with_callouts but the placeholder
            # was already stripped, run Klein post-hoc and re-insert. This is
            # the actual path users hit.
            if (
                scaffolded.idiom_id == "hero_with_callouts"
                and capability.can_diffusion_klein
            ):
                klein_svg = await self._inject_klein_hero(
                    scaffolded.svg_markup,
                    title=scaffolded.title,
                    intent=scaffolded.subtitle or scaffolded.description,
                    capability=capability,
                )
                if klein_svg:
                    final_svg = klein_svg
                    used_hybrid = True

            critic_result = await self._run_critic(
                final_svg,
                title=scaffolded.title,
                intent=scaffolded.subtitle or scaffolded.description,
                capability=capability,
            )
            return ComposedVisual(
                success=True,
                path=GenerationPath.GEMMA_FREEFORM,
                setup=capability.setup,
                output_format=OutputFormat.SVG,
                svg_markup=final_svg,
                title=scaffolded.title,
                description=(
                    f"{scaffolded.description} + Klein hero"
                    if used_hybrid else scaffolded.description
                ),
                key_points=[],
                alternatives=[],
                critic_score=_critic_result_to_score(critic_result) if critic_result else None,
                retry_count=0,  # Skeleton path doesn't retry — quality is structural
                model_used=(
                    f"{scaffolded.model_used}+{capability.klein_model}"
                    if used_hybrid else scaffolded.model_used
                ),
                template_id=scaffolded.idiom_id,
                template_name=scaffolded.idiom_id,
            )

        # Skeleton failed → straight to template path (Tier D floor).
        # The legacy gemma_freeform fallback was removed in the 2026-05-26
        # consolidation: benchmarks showed it loses to skeleton on every
        # prompt AND is 3-4× slower. When skeletons fail (rare), the
        # template path is the right safety net.
        logger.warning(
            f"[visual_composer] gemma_skeleton failed ({scaffolded.error}); "
            f"falling through to template path"
        )
        result = await self._compose_template(content, None, capability)
        result.error = f"skeleton failed: {scaffolded.error}; used template fallback"
        return result

    async def _compose_klein_full_bleed(
        self,
        content: str,
        intent: IllustrationIntent,
        capability: VisualCapability,
        topic: Optional[str] = None,
    ) -> Optional[ComposedVisual]:
        """Klein-first path: pure raster hero, no SVG callouts.

        Triggered by the intent classifier when the user is asking for a
        cinematic / illustrated image rather than a structural diagram.
        Bypasses the two-stage idiom picker entirely.

        Flow:
          1. Resolve Klein params from intent (aspect, tier → w/h/steps)
          2. Build Klein prompt — passthrough (use user content directly)
             when the prompt is already art-directed, otherwise have
             Gemma write a tuned prompt via write_klein_prompt
          3. Pre-unload Gemma on swap-mode machines
          4. Generate the Klein image with negative prompt
          5. Wrap the PNG in a minimal full-bleed SVG
          6. Run the critic (best-effort)

        Returns None on any failure so the caller can fall through to the
        existing skeleton path — never raises.
        """
        t0 = time.time()
        title = intent.title or "Hero Visual"
        subtitle = intent.subtitle or ""
        # Use the user's topic for prompt building — passing the enriched
        # blob (topic + 3KB of notebook articles) to Klein would be both
        # over-budget and off-target. Fall back to content for legacy callers
        # that didn't thread topic through.
        prompt_source = (topic or content).strip()

        # Step 1: dimensions + steps from (aspect, tier)
        width, height, steps = resolve_dimensions(intent.aspect_ratio, intent.quality_tier)

        # Step 2: build the Klein prompt.
        #
        # Three branches:
        #   A) passthrough + short  → use user prompt verbatim (preserves
        #      every comma of their art direction)
        #   B) passthrough + long   → art director compression. Klein's T5
        #      text encoder caps at ~512 tokens (≈2000 chars); a passthrough
        #      prompt over the limit gets its CINEMATIC TAIL silently
        #      truncated. The compressor re-orders to front-load art
        #      direction so nothing important is lost.
        #   C) not passthrough      → art director pass (replaces the old
        #      one-shot write_klein_prompt). Produces a 50-150 word prompt
        #      assembled in Klein-optimal order (style → palette → lighting
        #      → subject → composition).
        if intent.passthrough_recommended and len(prompt_source) <= KLEIN_PROMPT_PASSTHROUGH_LIMIT:
            klein_prompt = prompt_source
            logger.info(
                f"[visual_composer] full-bleed passthrough (verbatim, "
                f"{len(klein_prompt)} chars within encoder budget)"
            )
        elif intent.passthrough_recommended:
            logger.info(
                f"[visual_composer] full-bleed passthrough requested but prompt is "
                f"{len(prompt_source)} chars (>{KLEIN_PROMPT_PASSTHROUGH_LIMIT}); "
                f"compressing via art director pass to preserve art-direction tail"
            )
            klein_prompt = await write_klein_brief(
                user_prompt=prompt_source, title=title, capability=capability,
            )
            if not klein_prompt:
                logger.warning("[visual_composer] full-bleed: compression failed")
                return None
            logger.info(
                f"[visual_composer] full-bleed compressed prompt ({len(klein_prompt)} chars): "
                f"{klein_prompt[:140]}..."
            )
        else:
            logger.info("[visual_composer] full-bleed: running art director pass")
            klein_prompt = await write_klein_brief(
                user_prompt=prompt_source, title=title, capability=capability,
            )
            if not klein_prompt:
                logger.warning("[visual_composer] full-bleed: art director pass failed")
                return None
            logger.info(
                f"[visual_composer] full-bleed art-directed prompt ({len(klein_prompt)} chars): "
                f"{klein_prompt[:140]}..."
            )

        # Step 2b: always suppress in-image text. The editorial typography
        # overlay added by _build_full_bleed_svg below provides the title
        # and subtitle as crisp SVG type — Klein should never compete with
        # that by attempting to render labels itself. Klein has no
        # character-level knowledge of programmatic strings ("LanceDB",
        # "Embedder", "Chunker"), so any text it generates for technical
        # content is illegible by design.
        negative_prompt = (
            f"{DEFAULT_NEGATIVE_PROMPT}, "
            f"no text in image, no labels, no captions, "
            f"no readable words, no typography in image, "
            f"no signs, no logos, no character text, "
            f"no written language"
        )

        # Step 3: pre-unload Gemma on swap-mode machines so Klein has room
        swap_mode = capability.concurrency_mode.value in ("swap", "swap_strict")
        if swap_mode and capability.gemma_model:
            logger.info("[visual_composer] full-bleed: pre-Klein Gemma unload (swap mode)")
            await force_unload(capability.gemma_model)

        # Step 4: Klein generation
        diffusion = await klein_diffusion.generate(
            prompt=klein_prompt,
            capability=capability,
            aspect_ratio=intent.aspect_ratio,
            quality_tier=intent.quality_tier,
            negative_prompt=negative_prompt,
            unload_after=swap_mode,
        )
        if not diffusion.success or not diffusion.png_bytes:
            logger.warning(
                f"[visual_composer] full-bleed: Klein generation failed: {diffusion.error}"
            )
            return None

        # Step 5: wrap in minimal full-bleed SVG (image only). Title and
        # subtitle stay in ComposedVisual metadata; the frontend renders
        # them as a user-controllable overlay layer on top — toggleable,
        # repositionable, editable — so users aren't stuck with a fixed
        # overlay that Klein had no awareness of when composing the scene.
        svg = _build_full_bleed_svg(
            png_bytes=diffusion.png_bytes,
            width=diffusion.width or width,
            height=diffusion.height or height,
        )

        # Step 6: critic (best-effort — None on failure is fine).
        # Pass the user's ACTUAL prompt (not the enriched content blob, not
        # truncated to 300 chars) so the critic can apply the prompt-fidelity
        # hard-penalty check from CRITIC_SYSTEM. Cap at 1500 chars to keep
        # the critic's context bounded.
        critic_intent = prompt_source[:1500] if prompt_source else content[:300]
        critic_result = await self._run_critic(
            svg, title=title, intent=critic_intent, capability=capability,
        )

        return ComposedVisual(
            success=True,
            path=GenerationPath.GEMMA_FREEFORM,
            setup=capability.setup,
            output_format=OutputFormat.SVG,
            svg_markup=svg,
            title=title,
            subtitle=subtitle,
            description=(
                f"Klein full-bleed hero"
                + (f" — {intent.style_hint}" if intent.style_hint else "")
                + (f" ({intent.aspect_ratio}, {intent.quality_tier})")
            ),
            key_points=[],
            alternatives=[],
            critic_score=_critic_result_to_score(critic_result) if critic_result else None,
            retry_count=0,
            model_used=f"{capability.gemma_model}+{capability.klein_model}",
            template_id="full_bleed_hero",
            template_name="full_bleed_hero",
            generation_ms=int((time.time() - t0) * 1000),
        )

    async def _inject_klein_hero(
        self,
        svg_with_image_tag: str,
        title: str,
        intent: str,
        capability: VisualCapability,
    ) -> Optional[str]:
        """Generate a Klein hero image and inject it into the <image href>
        of an SVG that was produced by the hero_with_callouts skeleton.

        Returns the modified SVG with the Klein PNG embedded as base64, or
        None on any failure (caller keeps the vector-only version).
        """
        import base64
        import re as _re
        from services.visual_skeletons import get_skeleton

        logger.info("[visual_composer] picker chose hero_with_callouts; injecting Klein image")

        # Write the Klein prompt via Gemma
        klein_prompt = await write_klein_prompt(
            intent=intent or title,
            title=title,
            style_hint="minimalist editorial illustration, indigo and slate palette, clean composition",
            capability=capability,
        )
        if not klein_prompt:
            logger.warning("[visual_composer] Klein prompt-writer failed; keeping vector-only hero")
            return None
        logger.info(f"[visual_composer] Klein prompt: {klein_prompt[:120]}...")

        # RAM safety: explicit Gemma unload before Klein in swap mode
        swap_mode = capability.concurrency_mode.value in ("swap", "swap_strict")
        if swap_mode and capability.gemma_model:
            logger.info("[visual_composer] hybrid: pre-Klein Gemma unload (swap mode)")
            await force_unload(capability.gemma_model)

        # Generate the Klein image
        diffusion = await klein_diffusion.generate(
            prompt=klein_prompt,
            capability=capability,
            width=1024,
            height=768,
            steps=4,
            unload_after=swap_mode,
        )
        if not diffusion.success or not diffusion.png_bytes:
            logger.warning(
                f"[visual_composer] Klein generation failed: {diffusion.error}; "
                f"keeping vector-only hero"
            )
            return None

        # Inject base64 PNG into the SVG's <image href> for the hero region.
        # The skeleton's image element looks like:
        #   <image x=... y=... width=... height=... href="data:image/png;base64,..." />
        # The href attribute was set to a placeholder during slot-fill; we
        # replace its content (which is now empty after the strip) with the
        # real Klein PNG.
        b64 = base64.b64encode(diffusion.png_bytes).decode("ascii")
        # Find the <image href="data:image/png;base64,..."> element and replace
        new_svg, n = _re.subn(
            r'(<image[^>]*?href="data:image/png;base64,)([^"]*)("[^>]*/>)',
            lambda m: f'{m.group(1)}{b64}{m.group(3)}',
            svg_with_image_tag,
            count=1,
        )
        if n == 0:
            logger.warning("[visual_composer] no <image> tag matched; keeping vector-only hero")
            return None
        return new_svg

    async def _run_critic(
        self,
        svg_markup: str,
        title: str,
        intent: str,
        capability: VisualCapability,
    ) -> Optional[CritiqueResult]:
        """Render the SVG and run the critic. Returns None on render failure."""
        png = await render_svg_to_png(svg_markup)
        if not png:
            logger.warning("[visual_composer] critic skipped: PNG render failed")
            return None
        try:
            return await visual_critic.critique(
                png_bytes=png,
                visual_title=title,
                visual_intent=intent,
                capability=capability,
            )
        except Exception as e:
            logger.exception(f"[visual_composer] critic call raised: {e}")
            return None

    async def _compose_olmo_freeform(
        self,
        content: str,
        capability: VisualCapability,
    ) -> ComposedVisual:
        """Setup A primary path: skeleton-based scaffolding via Olmo.

        Strategy:
          1. Olmo picks the best of 5 skeletons + writes title/subtitle (JSON)
          2. Olmo fills in slot map (JSON)
          3. Apply slot map to skeleton SVG
          4. Validate output (must be parseable, must have <svg>...</svg>)
          5. If valid, run granite-vision critic with looser threshold (~0.55)
          6. If invalid OR critic score < 0.40 floor → silent fallback to today's
             template path (Tier D), never crash, never show broken output
        """
        freeform = await olmo_freeform.generate(
            content, capability, force_idiom=getattr(self, "_forced_idiom", None),
        )

        if not freeform.success or not freeform.svg_markup or not _is_valid_svg(freeform.svg_markup):
            logger.info(
                f"[visual_composer] olmo_freeform invalid output → template fallback "
                f"(error={freeform.error})"
            )
            result = await self._compose_template(content, None, capability)
            result.error = f"olmo freeform failed: {freeform.error}; used template fallback"
            return result

        # Critic: granite vision with looser threshold (Olmo path produces
        # serviceable-not-stunning output; threshold of 0.40 is "is it usable")
        critic_result = await self._run_critic(
            freeform.svg_markup,
            title=freeform.title,
            intent=freeform.subtitle or freeform.description,
            capability=capability,
        )

        # Setup A floor: critic-floor failure also triggers template fallback
        OLMO_PATH_FLOOR = 0.40
        if (
            critic_result
            and critic_result.success
            and critic_result.overall < OLMO_PATH_FLOOR
        ):
            logger.info(
                f"[visual_composer] olmo output scored {critic_result.overall:.2f} "
                f"< floor {OLMO_PATH_FLOOR}; falling back to template path"
            )
            result = await self._compose_template(content, None, capability)
            result.error = (
                f"olmo output scored {critic_result.overall:.2f} below floor; "
                f"used template fallback"
            )
            return result

        return ComposedVisual(
            success=True,
            path=GenerationPath.OLMO_FREEFORM,
            setup=capability.setup,
            output_format=OutputFormat.SVG,
            svg_markup=freeform.svg_markup,
            title=freeform.title,
            description=freeform.description,
            key_points=[],
            alternatives=[],
            critic_score=_critic_result_to_score(critic_result) if critic_result else None,
            retry_count=0,  # Olmo path does not retry — fallback to template instead
            model_used=freeform.model_used,
            template_id=freeform.idiom_id,
            template_name=freeform.idiom_id,
        )


# ──────────────────────────────────────────────────────────────────────
# Validation + intent detection
# ──────────────────────────────────────────────────────────────────────
def _is_valid_svg(markup: Optional[str]) -> bool:
    """Cheap structural check that the SVG is renderable."""
    if not markup or len(markup) < 200:
        return False
    lower = markup.lower()
    return "<svg" in lower and "</svg>" in lower


_SOURCE_CONTENT_MARKER = "\n\nSource content:\n"


def _extract_topic(content: str) -> str:
    """Pull just the user's topic out of an enriched content blob.

    API endpoints build content as `{topic}\\n\\nSource content:\\n{...}`.
    Used as a fallback when callers don't thread topic through explicitly —
    splits on the marker and returns everything before it. If the marker
    isn't present, returns content unchanged.
    """
    if not content:
        return ""
    idx = content.find(_SOURCE_CONTENT_MARKER)
    if idx == -1:
        return content.strip()
    return content[:idx].strip()


def _build_full_bleed_svg(png_bytes: bytes, width: int, height: int) -> str:
    """Wrap a Klein PNG in a minimal image-only SVG.

    Title overlay is rendered by the FRONTEND as a user-controllable
    layer (toggleable, repositionable, editable). See
    src/components/shared/VisualHeroOverlay.tsx — keeping the overlay
    out of the SVG means users aren't stuck with a fixed placement that
    Klein had no awareness of when composing the scene.
    """
    import base64
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
        f'<image x="0" y="0" width="{width}" height="{height}" '
        f'href="data:image/png;base64,{b64}" '
        f'preserveAspectRatio="xMidYMid meet" />'
        f'</svg>'
    )


# ──────────────────────────────────────────────────────────────────────
# Adapters
# ──────────────────────────────────────────────────────────────────────
def _critic_result_to_score(c: CritiqueResult) -> CriticScore:
    """Adapt the critic service's result type to the composer's score type."""
    return CriticScore(
        legibility=c.legibility,
        hierarchy=c.hierarchy,
        balance=c.balance,
        color_harmony=c.color_harmony,
        message_clarity=c.message_clarity,
        overall=c.overall,
        strengths=list(c.strengths or []),
        weaknesses=list(c.weaknesses or []),
        suggestions=list(c.suggestions or []),
    )


# ──────────────────────────────────────────────────────────────────────
# Serialization helpers (for SSE / JSON responses)
# ──────────────────────────────────────────────────────────────────────
def _critic_to_dict(s: CriticScore) -> Dict[str, Any]:
    return {
        "legibility": s.legibility,
        "hierarchy": s.hierarchy,
        "balance": s.balance,
        "color_harmony": s.color_harmony,
        "message_clarity": s.message_clarity,
        "overall": s.overall,
        "strengths": s.strengths,
        "weaknesses": s.weaknesses,
        "suggestions": s.suggestions,
    }


def _visual_to_dict(v: ComposedVisual) -> Dict[str, Any]:
    return {
        "success": v.success,
        "path": v.path.value,
        "setup": v.setup.value,
        "output_format": v.output_format.value,
        "svg_markup": v.svg_markup,
        "mermaid_code": v.mermaid_code,
        "title": v.title,
        "subtitle": v.subtitle,
        "description": v.description,
        "key_points": v.key_points,
        "alternatives": v.alternatives,
        "critic_score": _critic_to_dict(v.critic_score) if v.critic_score else None,
        "retry_count": v.retry_count,
        "generation_ms": v.generation_ms,
        "error": v.error,
        "model_used": v.model_used,
        "template_id": v.template_id,
        "template_name": v.template_name,
    }


# ──────────────────────────────────────────────────────────────────────
# Module-level singleton (matches the pattern of other services)
# ──────────────────────────────────────────────────────────────────────
visual_composer = VisualComposer()
