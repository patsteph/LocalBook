"""Video Slide Renderer — Playwright renders HTML slides to PNG images.

Takes a Storyboard and a visual style, renders each scene as a 1920x1080 PNG
using a headless Chromium browser via Playwright. Each PNG becomes a frame
in the final video.

This module is completely independent — it does NOT modify any existing services.
"""

import asyncio
import html
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# SLIDE CONTENT GENERATORS — produce inner HTML for each visual_type
# =============================================================================

def _esc(text: str) -> str:
    """HTML-escape text for safe rendering."""
    return html.escape(str(text)) if text else ""


def _render_title_slide(content: Dict) -> str:
    title = _esc(content.get("title", "Video Overview"))
    subtitle = _esc(content.get("subtitle", ""))
    subtitle_html = f'<div class="subtitle">{subtitle}</div>' if subtitle else ""
    return f"""
        <div class="title">{title}</div>
        <div class="accent-line"></div>
        {subtitle_html}
    """


def _render_stat_callout(content: Dict) -> str:
    number = _esc(content.get("number", "?"))
    label = _esc(content.get("label", ""))
    context = _esc(content.get("context", ""))
    context_html = f'<div class="context">{context}</div>' if context else ""
    return f"""
        <div class="number">{number}</div>
        <div class="label">{label}</div>
        {context_html}
    """


def _render_bullet_list(content: Dict) -> str:
    title = _esc(content.get("title", ""))
    items = content.get("items", [])
    title_html = f'<div class="section-title">{title}</div>' if title else ""
    items_html = "\n".join(f'<li>{_esc(item)}</li>' for item in items[:6])
    return f"""
        {title_html}
        <ul class="items">
            {items_html}
        </ul>
    """


def _render_quote(content: Dict) -> str:
    quote = _esc(content.get("quote", ""))
    attribution = _esc(content.get("attribution", ""))
    attr_html = f'<div class="attribution">— {attribution}</div>' if attribution else ""
    return f"""
        <div class="quote-mark">"</div>
        <div class="quote-text">{quote}</div>
        {attr_html}
    """


def _render_key_point(content: Dict) -> str:
    heading = _esc(content.get("heading", ""))
    body = _esc(content.get("body", ""))
    return f"""
        <div class="heading">{heading}</div>
        <div class="body">{body}</div>
    """


def _render_comparison(content: Dict) -> str:
    title = _esc(content.get("title", "Comparison"))
    left_label = _esc(content.get("left_label", "A"))
    right_label = _esc(content.get("right_label", "B"))
    left_points = content.get("left_points", [])
    right_points = content.get("right_points", [])
    left_html = "\n".join(f"<li>{_esc(p)}</li>" for p in left_points[:5])
    right_html = "\n".join(f"<li>{_esc(p)}</li>" for p in right_points[:5])
    return f"""
        <div class="comp-title">{title}</div>
        <div class="columns">
            <div class="column">
                <div class="column-label">{left_label}</div>
                <ul>{left_html}</ul>
            </div>
            <div class="column">
                <div class="column-label">{right_label}</div>
                <ul>{right_html}</ul>
            </div>
        </div>
    """


def _render_timeline_point(content: Dict) -> str:
    step_num = content.get("step_number", 1)
    total = content.get("total_steps", 1)
    label = _esc(content.get("label", ""))
    desc = _esc(content.get("description", ""))
    progress_pct = int((step_num / max(total, 1)) * 100)
    return f"""
        <div class="step-indicator">Step {step_num} of {total}</div>
        <div class="step-label">{label}</div>
        <div class="step-description">{desc}</div>
        <div class="progress-bar">
            <div class="progress-fill" style="width: {progress_pct}%"></div>
        </div>
    """


def _render_diagram_placeholder(content: Dict) -> str:
    concept = _esc(content.get("concept", ""))
    caption = _esc(content.get("caption", ""))
    return f"""
        <div class="placeholder-box">
            <div class="placeholder-icon">◇</div>
        </div>
        <div class="caption">{caption or concept}</div>
    """


def _render_composed_svg(content: Dict) -> str:
    """Cross-medium visuals: inline a (pre-sanitized) composed SVG into the slide,
    scaled to fit. Falls back to the diagram placeholder if the SVG is missing."""
    svg = content.get("svg_markup") or ""
    if not svg.strip():
        return _render_diagram_placeholder(content)
    caption = _esc(content.get("caption", "") or content.get("concept", ""))
    caption_html = f'<div class="caption">{caption}</div>' if caption else ""
    return f"""
        <div class="composed-visual" style="width:100%;height:82%;display:flex;align-items:center;justify-content:center;overflow:hidden;">
            {svg}
        </div>
        {caption_html}
    """


def _render_closing(content: Dict) -> str:
    title = _esc(content.get("title", "Key Takeaways"))
    items = content.get("items", [])
    items_html = "\n".join(f"<li>{_esc(item)}</li>" for item in items[:5])
    return f"""
        <div class="closing-title">{title}</div>
        <ul class="takeaways">
            {items_html}
        </ul>
    """


# Map visual_type → (CSS class, render function)
RENDERERS = {
    "title_slide": ("title-slide", _render_title_slide),
    "stat_callout": ("stat-callout", _render_stat_callout),
    "bullet_list": ("bullet-list", _render_bullet_list),
    "quote": ("quote-slide", _render_quote),
    "key_point": ("key-point", _render_key_point),
    "comparison": ("comparison", _render_comparison),
    "timeline_point": ("timeline-point", _render_timeline_point),
    "diagram_placeholder": ("diagram-placeholder", _render_diagram_placeholder),
    "composed_diagram": ("composed-diagram", _render_composed_svg),
    "closing": ("closing", _render_closing),
}


# Cross-medium video visuals (opt-in, default OFF). Only NON-hero idioms are used so
# visual_composer never routes to Klein diffusion (the ~2 min/scene blow-up); each
# compose is time-boxed and falls back to the original text card on any failure.
_VISUAL_ROLE_IDIOMS = {
    "contrast": "comparison_matrix",
    "evidence": "concept_map",
    "turn": "linear_process",
    "synthesis": "concept_map",
    # hook / stakes / payoff → keep the text card (openings/closings read better as text)
}


def _video_visual_cap() -> int:
    import os
    try:
        return max(0, int(os.getenv("LOCALBOOK_VIDEO_VISUAL_CAP", "3")))
    except ValueError:
        return 3


def _video_visual_timeout() -> float:
    import os
    try:
        return max(10.0, float(os.getenv("LOCALBOOK_VIDEO_VISUAL_TIMEOUT", "90")))
    except ValueError:
        return 90.0


# =============================================================================
# SLIDE RENDERER
# =============================================================================

class VideoSlideRenderer:
    """Renders storyboard scenes to PNG slides using Playwright."""

    def __init__(self):
        self._template_dir = Path(__file__).parent.parent / "templates" / "slides"
        self._base_html: Optional[str] = None

    def _load_base_template(self) -> str:
        """Load the base HTML template."""
        if self._base_html is None:
            template_path = self._template_dir / "base.html"
            self._base_html = template_path.read_text(encoding="utf-8")
        return self._base_html

    def _resolve_pptx_style(self, template_id: str) -> Dict[str, str]:
        """Look up a custom PPTX template by ID and extract its visual style."""
        from templates.slides.styles import extract_style_from_pptx, get_style
        try:
            from config import settings
            import json
            meta_path = settings.data_dir / "pptx_templates" / "_meta.json"
            if meta_path.exists():
                with open(meta_path) as f:
                    meta = json.load(f)
                for tpl in meta:
                    if tpl.get("id") == template_id:
                        tpl_path = settings.data_dir / "pptx_templates" / tpl["filename"]
                        if tpl_path.exists():
                            return extract_style_from_pptx(str(tpl_path))
        except Exception as e:
            logger.warning(f"Failed to resolve PPTX template style '{template_id}': {e}")
        return get_style("classic")

    def _build_slide_html(self, visual_type: str, content: Dict, style: Dict[str, str]) -> str:
        """Build complete HTML for a single slide."""
        base = self._load_base_template()

        # Get renderer for this visual type
        css_class, render_fn = RENDERERS.get(visual_type, ("key-point", _render_key_point))
        slide_content = render_fn(content)

        # Substitute template variables
        result = base
        result = result.replace("{{slide_class}}", css_class)
        result = result.replace("{{slide_content}}", slide_content)

        # Substitute style variables
        for key, value in style.items():
            result = result.replace("{{" + key + "}}", value)

        return result

    async def _maybe_compose_visual(self, scene, topic: str) -> Optional[str]:
        """Cross-medium visuals: for an eligible scene role, compose ONE real diagram
        via visual_composer, forcing a non-hero idiom so Klein diffusion is never
        reached. Time-boxed; returns sanitized SVG markup or None (→ keep text card)."""
        role = getattr(scene, "role", "") or ""
        idiom = _VISUAL_ROLE_IDIOMS.get(role)
        if not idiom:
            return None
        narration = getattr(scene, "narration", "") or ""
        if len(narration.strip()) < 40:
            return None
        try:
            from services.visual_composer import visual_composer
            # force_idiom=<non-hero> is what guarantees no Klein: it bypasses the
            # illustration classifier AND avoids the only Klein idiom (hero_with_callouts).
            # We let the composer pick the best available freeform path (don't force one,
            # so it stays valid whichever main model is installed).
            result = await asyncio.wait_for(
                visual_composer.compose(
                    content=narration,
                    topic=topic or None,
                    force_idiom=idiom,
                ),
                timeout=_video_visual_timeout(),
            )
            if result and getattr(result, "success", False) and getattr(result, "svg_markup", None):
                from services.svg_sanitizer import sanitize_svg
                return sanitize_svg(result.svg_markup)
        except asyncio.TimeoutError:
            logger.warning(f"[SlideRenderer] visual compose timed out for role={role}; using text card")
        except Exception as e:
            logger.warning(f"[SlideRenderer] visual compose failed (role={role}), using text card: {e}")
        return None

    async def render_slides(
        self,
        scenes: list,
        style_name: str = "classic",
        output_dir: Optional[Path] = None,
        include_visuals: bool = False,
        topic: str = "",
    ) -> List[Path]:
        """Render all scenes to PNG files.

        Args:
            scenes: List of Scene objects (from Storyboard)
            style_name: Visual style name (classic, dark, whiteboard, etc.)
            output_dir: Directory to save PNGs (created if needed)
            include_visuals: opt-in — compose real diagrams for eligible scenes
                (default OFF; also enabled by LOCALBOOK_VIDEO_VISUALS=1)
            topic: the video topic (passed to visual_composer for context)

        Returns:
            List of paths to rendered PNG files, in scene order
        """
        import os
        from templates.slides.styles import get_style, extract_style_from_pptx

        visuals_on = include_visuals or os.getenv("LOCALBOOK_VIDEO_VISUALS") == "1"
        visual_cap = _video_visual_cap()
        composed_count = 0

        # Resolve style — built-in or custom PPTX template
        if style_name.startswith("tpl:"):
            template_id = style_name[4:]
            style = self._resolve_pptx_style(template_id)
        else:
            style = get_style(style_name)

        if output_dir is None:
            from config import settings
            output_dir = settings.data_dir / "video" / "slides_temp"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Launch Playwright browser
        png_paths = []

        try:
            from services.playwright_utils import ensure_playwright_browsers_path
            from playwright.async_api import async_playwright

            ensure_playwright_browsers_path()

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1920, "height": 1080})

                for scene in scenes:
                    visual = scene.visual if hasattr(scene, 'visual') else scene.get("visual", {})

                    if hasattr(visual, 'visual_type'):
                        v_type = visual.visual_type
                        v_content = visual.content
                    else:
                        v_type = visual.get("visual_type", "key_point")
                        v_content = visual.get("content", {})

                    scene_id = scene.scene_id if hasattr(scene, 'scene_id') else scene.get("scene_id", 0)

                    # Cross-medium visuals (opt-in): for eligible scene roles, replace the
                    # text card with a real composed diagram. Capped per video + time-boxed;
                    # any failure keeps the original text card (fallback-safe).
                    if visuals_on and composed_count < visual_cap and hasattr(scene, 'visual'):
                        svg = await self._maybe_compose_visual(scene, topic)
                        if svg:
                            composed_count += 1
                            v_type = "composed_diagram"
                            v_content = {"svg_markup": svg, "caption": v_content.get("caption", "") if isinstance(v_content, dict) else ""}
                            logger.info(f"[SlideRenderer] scene {scene_id}: composed diagram ({composed_count}/{visual_cap})")

                    # Build HTML for this slide
                    slide_html = self._build_slide_html(v_type, v_content, style)

                    # Render to page
                    await page.set_content(slide_html, wait_until="load")
                    # Brief wait for font loading / CSS paint
                    await page.wait_for_timeout(200)

                    # Screenshot
                    png_path = output_dir / f"slide_{scene_id:04d}.png"
                    await page.screenshot(path=str(png_path), type="png")
                    png_paths.append(png_path)

                    logger.info(f"[SlideRenderer] Rendered scene {scene_id}: {v_type} → {png_path.name}")

                await browser.close()

        except Exception as e:
            logger.error(f"[SlideRenderer] Playwright rendering failed: {e}")
            raise RuntimeError(f"Slide rendering failed: {e}")

        logger.info(f"[SlideRenderer] Rendered {len(png_paths)} slides with style '{style_name}'")
        return png_paths


# Singleton
video_slide_renderer = VideoSlideRenderer()
