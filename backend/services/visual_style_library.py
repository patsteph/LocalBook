"""Visual System v2 — style library.

Single source of truth for visual aesthetics. v1 ships Stripe-clean only.
Additional styles (NYT-editorial, McKinsey-precise, Tufte-dense) slot in
during Phase 4 by adding new VisualStyle instances.

Exposes both:
  • Python tokens (constants accessible from generation code)
  • An LLM-injectable spec block (text rules dropped into system prompts)

The spec block is what makes Gemma reliably produce on-style output — it
gives the model concrete numbers, colors, and rules rather than vague
"make it look clean" instructions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class StyleName(str, Enum):
    STRIPE_CLEAN = "stripe_clean"
    # Future Phase 4 additions:
    # NYT_EDITORIAL = "nyt_editorial"
    # MCKINSEY_PRECISE = "mckinsey_precise"
    # TUFTE_DENSE = "tufte_dense"


@dataclass(frozen=True)
class ColorTokens:
    """Named colors used throughout the visual."""
    bg: str               # Page background
    bg_alt: str           # Card / section background
    border: str           # Subtle borders, dividers
    text_strong: str      # Headings, key labels
    text_body: str        # Standard body text
    text_muted: str       # Captions, helper text
    primary: str          # Hero color, key accents
    primary_dark: str     # Gradient end-stop, hover states
    success: str          # Positive / pass states
    warning: str          # Caution states
    error: str            # Failure / negative states
    accent_tint: str      # Soft fills for tinted regions


@dataclass(frozen=True)
class TypographyScale:
    """Text size scale, all sizes in pixels at 1600x900 viewBox."""
    font_family: str      # CSS-style family string
    title: int            # Slide title
    subtitle: int         # Slide subtitle / caption to title
    section: int          # Section / group headers
    component: int        # Component/card titles
    body: int             # Standard label text
    caption: int          # Helper labels, metadata

    title_weight: int = 600
    subtitle_weight: int = 400
    section_weight: int = 600
    component_weight: int = 600
    body_weight: int = 400
    caption_weight: int = 400


@dataclass(frozen=True)
class SpacingScale:
    """Spacing tokens in pixels at 1600x900 viewBox."""
    edge_padding: int     # Min distance from viewBox edge
    group_gap: int        # Between major visual groups
    component_gap: int    # Between sibling components
    text_gap: int         # Between adjacent text lines
    corner_radius_lg: int  # Large cards
    corner_radius_md: int  # Standard cards
    corner_radius_sm: int  # Small elements


@dataclass(frozen=True)
class ShadowSpec:
    """Drop-shadow filter spec."""
    dx: int
    dy: int
    blur: int
    color: str  # rgba(...)


@dataclass
class VisualStyle:
    """A complete style spec with everything a generator needs."""
    name: StyleName
    label: str            # Human-readable name shown in UI
    description: str      # One-line description of the aesthetic

    colors: ColorTokens
    typography: TypographyScale
    spacing: SpacingScale
    shadow: ShadowSpec

    # SVG-specific snippets the generator embeds verbatim
    defs_snippet: str = ""  # <defs> markers, gradients, filters
    arrow_marker_id: str = "arrowhead"
    drop_shadow_filter_id: str = "softShadow"


# ──────────────────────────────────────────────────────────────────────
# Stripe-clean v1 — the only style we ship for Phase 1
# ──────────────────────────────────────────────────────────────────────
STRIPE_COLORS = ColorTokens(
    bg="#FFFFFF",
    bg_alt="#F6F9FC",
    border="#E3E8EE",
    text_strong="#0A2540",
    text_body="#425466",
    text_muted="#8898AA",
    primary="#635BFF",
    primary_dark="#4A43D1",
    success="#00C896",
    warning="#F5A623",
    error="#D32F2F",
    accent_tint="#EEF0FF",
)

STRIPE_TYPOGRAPHY = TypographyScale(
    font_family="'Inter', system-ui, -apple-system, sans-serif",
    title=44,
    subtitle=20,
    section=22,
    component=18,
    body=14,
    caption=12,
)

STRIPE_SPACING = SpacingScale(
    edge_padding=60,
    group_gap=40,
    component_gap=24,
    text_gap=8,
    corner_radius_lg=12,
    corner_radius_md=8,
    corner_radius_sm=4,
)

STRIPE_SHADOW = ShadowSpec(
    dx=0,
    dy=2,
    blur=6,
    color="rgba(10, 37, 64, 0.08)",
)


def _build_stripe_defs() -> str:
    """SVG <defs> block: arrow marker, drop shadow filter, primary gradient."""
    c = STRIPE_COLORS
    s = STRIPE_SHADOW
    return f"""<defs>
  <marker id="arrowhead" markerWidth="10" markerHeight="10" refX="10" refY="5" orient="auto">
    <polygon points="0 0, 10 5, 0 10" fill="{c.text_body}" />
  </marker>
  <marker id="arrowhead_primary" markerWidth="10" markerHeight="10" refX="10" refY="5" orient="auto">
    <polygon points="0 0, 10 5, 0 10" fill="{c.primary}" />
  </marker>
  <filter id="softShadow" x="-10%" y="-10%" width="120%" height="120%">
    <feDropShadow dx="{s.dx}" dy="{s.dy}" stdDeviation="{s.blur // 2}" flood-color="{c.text_strong}" flood-opacity="0.08" />
  </filter>
  <linearGradient id="primaryGradient" x1="0%" y1="0%" x2="0%" y2="100%">
    <stop offset="0%" stop-color="{c.primary}" stop-opacity="1" />
    <stop offset="100%" stop-color="{c.primary_dark}" stop-opacity="1" />
  </linearGradient>
  <linearGradient id="headerGradient" x1="0%" y1="0%" x2="100%" y2="0%">
    <stop offset="0%" stop-color="{c.bg_alt}" stop-opacity="1" />
    <stop offset="100%" stop-color="#FFFFFF" stop-opacity="1" />
  </linearGradient>
</defs>"""


STRIPE_CLEAN = VisualStyle(
    name=StyleName.STRIPE_CLEAN,
    label="Stripe Clean",
    description=(
        "Modern tech aesthetic with indigo accents, generous whitespace, "
        "soft drop shadows, and Inter typography. Strong fit for SaaS, "
        "API/architecture diagrams, and customer-facing business visuals."
    ),
    colors=STRIPE_COLORS,
    typography=STRIPE_TYPOGRAPHY,
    spacing=STRIPE_SPACING,
    shadow=STRIPE_SHADOW,
    defs_snippet=_build_stripe_defs(),
)


# ──────────────────────────────────────────────────────────────────────
# Registry + lookup
# ──────────────────────────────────────────────────────────────────────
_STYLES: Dict[StyleName, VisualStyle] = {
    StyleName.STRIPE_CLEAN: STRIPE_CLEAN,
}


def get_style(name: StyleName = StyleName.STRIPE_CLEAN) -> VisualStyle:
    """Return a registered style, defaulting to Stripe-clean."""
    return _STYLES.get(name, STRIPE_CLEAN)


def list_styles() -> List[Dict[str, str]]:
    """Return style metadata for the UI picker."""
    return [
        {"name": s.name.value, "label": s.label, "description": s.description}
        for s in _STYLES.values()
    ]


# ──────────────────────────────────────────────────────────────────────
# Klein style presets — prompt-tail nudges for the diffusion path
#
# Distinct from VisualStyle (which configures SVG generation). Klein
# styles attach to full-bleed hero prompts via the Edit-panel chip row:
# clicking a chip appends `prompt_tail` to the user's prompt textarea.
# No backend routing changes — the modified prompt flows through the
# existing /visual/v2/compose path.
# ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class KleinStyle:
    """A named visual style preset for Klein full-bleed generation."""
    id: str           # Stable kebab-case identifier
    label: str        # Display label (with a one-glyph prefix)
    prompt_tail: str  # Appended to the prompt textarea after a comma


KLEIN_STYLES: List[KleinStyle] = [
    KleinStyle(
        id="editorial",
        label="📰 Editorial",
        prompt_tail="editorial photography style, considered composition, magazine cover aesthetic, sophisticated color grading, balanced negative space, professional retouching",
    ),
    KleinStyle(
        id="watercolor",
        label="🎨 Watercolor",
        prompt_tail="watercolor painting, soft washes of pigment, hand-painted texture, paper grain visible, gentle bleeds and color blooms, light and airy palette",
    ),
    KleinStyle(
        id="low-poly",
        label="🔷 Low-poly",
        prompt_tail="low-poly 3D illustration, flat geometric facets, smooth gradient fills, minimal detail, three-quarter angle, soft ambient lighting",
    ),
    KleinStyle(
        id="blueprint",
        label="📐 Blueprint",
        prompt_tail="architectural blueprint style, white linework on deep cyan paper, technical drawing conventions, dimension callouts and grid, drafting precision, no realistic shading",
    ),
    KleinStyle(
        id="photographic",
        label="📷 Photographic",
        prompt_tail="professional DSLR photograph, sharp focus, natural lighting, realistic textures and materials, shallow depth of field, color-accurate, hyper-realistic",
    ),
    KleinStyle(
        id="ghibli",
        label="🌸 Ghibli",
        prompt_tail="Studio Ghibli animation style, hand-painted backgrounds, soft pastel palette, warm cinematic lighting, gentle painterly brushwork, nostalgic atmosphere",
    ),
    KleinStyle(
        id="noir",
        label="🎭 Noir",
        prompt_tail="film noir aesthetic, high-contrast black and white, dramatic chiaroscuro, deep shadows, single hard light source, smoky atmosphere, 1940s cinematic mood",
    ),
    KleinStyle(
        id="risograph",
        label="🖨 Risograph",
        prompt_tail="risograph print aesthetic, limited two-color palette, visible halftone dot texture, slight print misregistration, matte paper feel, indie zine vibe",
    ),
]


def list_klein_styles() -> List[Dict[str, str]]:
    """Return Klein style presets for the Edit-panel chip row."""
    return [
        {"id": s.id, "label": s.label, "prompt_tail": s.prompt_tail}
        for s in KLEIN_STYLES
    ]


def get_klein_style(style_id: str) -> Optional[KleinStyle]:
    """Look up a Klein style by id; returns None if unknown."""
    for s in KLEIN_STYLES:
        if s.id == style_id:
            return s
    return None


# ──────────────────────────────────────────────────────────────────────
# LLM prompt builder — the spec block injected into freeform prompts
# ──────────────────────────────────────────────────────────────────────
def build_style_prompt_block(style: VisualStyle) -> str:
    """Build the text block dropped into freeform generation system prompts.

    Gives the LLM concrete numbers and color codes rather than vague
    "make it look clean" guidance. This is the file that defines what
    "Stripe-clean" means to the generator.
    """
    c = style.colors
    t = style.typography
    sp = style.spacing

    return f"""VISUAL STYLE — {style.label}

COLOR PALETTE (use exactly these hex values):
- Background: {c.bg}
- Card / section background: {c.bg_alt}
- Borders / dividers: {c.border} (stroke-width 1)
- Strong text (titles, key labels): {c.text_strong}
- Body text: {c.text_body}
- Muted text (captions): {c.text_muted}
- Primary accent: {c.primary}
- Primary dark (gradient end, emphasis): {c.primary_dark}
- Soft accent tint (highlighted regions): {c.accent_tint}
- Success/positive: {c.success}
- Warning: {c.warning}
- Error/negative: {c.error}

TYPOGRAPHY (always set font-family explicitly):
- font-family: {t.font_family}
- Title: {t.title}px, font-weight={t.title_weight}, fill={c.text_strong}
- Subtitle: {t.subtitle}px, font-weight={t.subtitle_weight}, fill={c.text_body}
- Section header: {t.section}px, font-weight={t.section_weight}, fill={c.text_strong}
- Component title: {t.component}px, font-weight={t.component_weight}, fill={c.text_strong}
- Body label: {t.body}px, font-weight={t.body_weight}, fill={c.text_body}
- Caption: {t.caption}px, font-weight={t.caption_weight}, fill={c.text_muted}
- Never use a font smaller than {t.caption}px — it won't be readable at slide-projection size.

SPACING DISCIPLINE:
- Keep at least {sp.edge_padding}px from every viewBox edge.
- {sp.group_gap}px between major visual groups.
- {sp.component_gap}px between sibling components within a group.
- {sp.text_gap}px between adjacent text lines.
- Corner radius: rx="{sp.corner_radius_lg}" on large cards, rx="{sp.corner_radius_md}" on standard cards, rx="{sp.corner_radius_sm}" on small elements.

SHADOWS, GRADIENTS, MARKERS:
- Include this <defs> block VERBATIM at the start of the SVG (it provides arrow markers, drop-shadow filter, and primary gradient):

{style.defs_snippet}

- Apply soft drop shadow on cards via: filter="url(#{style.drop_shadow_filter_id})"
- Use primary gradient on hero/emphasis bands via: fill="url(#primaryGradient)"
- Arrows MUST use marker-end="url(#{style.arrow_marker_id})" — never draw arrowheads as inline polygons.

COMPOSITION QUALITY GATES:
- Strong typographic hierarchy: title > subtitle > section > component > body. Never collapse adjacent levels.
- Group related elements with proximity AND a subtle bg_alt fill region. Don't rely on proximity alone.
- Cards stay on a consistent grid — equivalent-tier elements get equivalent dimensions.
- Arrows route around unrelated elements, never crossing them.
- If you find yourself adding decorative chrome to fill space, add whitespace instead.

CONCRETE EXAMPLES — copy these patterns:

Title block (use this exact pattern at the top):
  <rect x="60" y="40" width="1480" height="100" rx="12" fill="url(#headerGradient)" />
  <text x="80" y="86" font-family="'Inter', system-ui, sans-serif" font-size="40" font-weight="600" fill="#0A2540">[your concrete title]</text>
  <text x="80" y="118" font-family="'Inter', system-ui, sans-serif" font-size="18" font-weight="400" fill="#425466">[your subtitle]</text>

Component card (use this exact pattern for every component):
  <rect x="[x]" y="[y]" width="200" height="100" rx="8" fill="#FFFFFF" stroke="#E3E8EE" stroke-width="1" filter="url(#softShadow)" />
  <text x="[x+20]" y="[y+40]" font-family="'Inter', system-ui, sans-serif" font-size="18" font-weight="600" fill="#0A2540">Primary Label</text>
  <text x="[x+20]" y="[y+66]" font-family="'Inter', system-ui, sans-serif" font-size="12" font-weight="400" fill="#8898AA">sub-label</text>

Connecting arrow (use this exact pattern between related components):
  <path d="M [x1] [y1] L [x2] [y2]" stroke="#425466" stroke-width="2" fill="none" marker-end="url(#arrowhead)" />
  <text x="[mid_x]" y="[mid_y - 6]" font-family="'Inter', system-ui, sans-serif" font-size="11" fill="#8898AA" text-anchor="middle">arrow label</text>

Section band (use to group related components):
  <rect x="40" y="[y]" width="1520" height="[h]" rx="12" fill="#F6F9FC" />
  <text x="60" y="[y+30]" font-family="'Inter', system-ui, sans-serif" font-size="20" font-weight="600" fill="#0A2540">SECTION NAME</text>

Stat callout (when emphasizing a number):
  <text x="[x]" y="[y]" font-family="'Inter', system-ui, sans-serif" font-size="48" font-weight="600" fill="#635BFF">[BIG NUMBER]</text>
  <text x="[x]" y="[y+28]" font-family="'Inter', system-ui, sans-serif" font-size="12" font-weight="400" fill="#8898AA">[caption]</text>

Reuse these patterns. Don't invent new visual treatments — every visual at this style level looks consistent because every visual uses the same building blocks."""
