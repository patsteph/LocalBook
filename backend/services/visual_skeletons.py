"""Visual System v2 — pre-built SVG skeletons for the Olmo path.

Olmo (Setup A main model) is text-only and noticeably worse than Gemma
at composing SVG from scratch. The scaffolded approach: give Olmo a
complete, valid SVG skeleton in Stripe-clean style with placeholder
text, and ask it to fill in the slots with content-appropriate labels.

Skeleton selection by idiom is done in visual_freeform.OlmoFreeformGenerator.
Each skeleton is dimensioned for the 1600x900 slide viewBox and uses the
same <defs> block from visual_style_library so styling stays consistent.
"""
from __future__ import annotations

from typing import Dict, Optional

from services.visual_style_library import STRIPE_CLEAN

# Style tokens — keep skeletons readable by referencing tokens explicitly
_C = STRIPE_CLEAN.colors
_T = STRIPE_CLEAN.typography
_FONT = _T.font_family

# Defs block shared across all skeletons
_DEFS = STRIPE_CLEAN.defs_snippet


# ──────────────────────────────────────────────────────────────────────
# Text-wrap helper — SVG <text> doesn't wrap; <foreignObject> with HTML does
# ──────────────────────────────────────────────────────────────────────
# All skeleton body text uses this so multi-word slot-fill content reflows
# inside the available width instead of overflowing the box. Chromium-based
# renderers (Playwright PNG, app WebView) handle foreignObject natively.
def wrap(
    x: int,
    y: int,
    w: int,
    h: int,
    placeholder: str,
    *,
    font_size: int = 14,
    weight: int = 400,
    color: str = "#425466",
    align: str = "left",            # 'left' | 'center'
    vertical: str = "top",          # 'top' | 'middle'
    line_height: float = 1.3,
) -> str:
    """Emit a <foreignObject> + nested HTML div that auto-wraps {{placeholder}}.

    Uses `-webkit-line-clamp` to truncate cleanly with `…` when slot content
    overflows the bounding box — rather than silently clipping mid-character
    like the prior `overflow:hidden`-only approach did. Slots that fit
    render unchanged; long slot-fill values get gracefully truncated at the
    last full line that fits with an ellipsis marker.

    Args:
        x, y, w, h: bounding box in viewBox coords
        placeholder: slot key (will become {{PLACEHOLDER}} in output)
        font_size: pixels (matched to the typography scale)
        weight: 400/600 (regular/semibold)
        color: hex
        align: 'left' or 'center' (text-align in the inner div)
        vertical: 'top' or 'middle' (vertical alignment via flexbox)
        line_height: CSS line-height multiplier
    """
    text_align = "center" if align == "center" else "left"
    flex_align = "center" if vertical == "middle" else "flex-start"
    # Compute the max number of full lines that fit in `h` at this font size.
    # Floor of (h ÷ (font_size × line_height)). At minimum 1 line — even tiny
    # label boxes need to render *something*.
    line_clamp = max(1, int(h / (font_size * line_height)))
    # CRITICAL: `overflow="hidden"` on the foreignObject ELEMENT (not just the
    # inner div). Without this, text content that wraps beyond the height
    # attribute bleeds past the foreignObject boundary and overlaps neighbors
    # in the SVG. The inner div's overflow:hidden alone doesn't clip the SVG
    # rendering — the foreignObject's own clip-region has to be set.
    return (
        f'<foreignObject x="{x}" y="{y}" width="{w}" height="{h}" overflow="hidden">'
        f'<div xmlns="http://www.w3.org/1999/xhtml" '
        f'style="width:{w}px;height:{h}px;display:flex;flex-direction:column;'
        f'justify-content:{flex_align};font-family:{_FONT};font-size:{font_size}px;'
        f'font-weight:{weight};color:{color};line-height:{line_height};'
        f'text-align:{text_align};word-wrap:break-word;overflow-wrap:break-word;'
        f'overflow:hidden;box-sizing:border-box;">'
        f'<div style="display:-webkit-box;-webkit-box-orient:vertical;'
        f'-webkit-line-clamp:{line_clamp};overflow:hidden;text-overflow:ellipsis;">'
        f'{{{{{placeholder}}}}}'
        f'</div></div></foreignObject>'
    )


# ──────────────────────────────────────────────────────────────────────
# Skeleton 1 — linear_process (5 numbered stages, horizontal flow)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_linear_process() -> str:
    cards = []
    arrows = []
    card_w = 240
    card_h = 140
    gap = 50
    n = 5
    total_w = n * card_w + (n - 1) * gap
    start_x = (1600 - total_w) // 2
    y = 380

    for i in range(n):
        x = start_x + i * (card_w + gap)
        cards.append(f"""
  <rect x="{x}" y="{y}" width="{card_w}" height="{card_h}" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  <circle cx="{x + 28}" cy="{y + 30}" r="16" fill="{_C.primary}" />
  <text x="{x + 28}" y="{y + 36}" font-family="{_FONT}" font-size="14" font-weight="600" fill="{_C.bg}" text-anchor="middle">{i + 1}</text>
  {wrap(x + 52, y + 16, card_w - 60, 32, f"STAGE_{i + 1}_LABEL", font_size=_T.component, weight=600, color=_C.text_strong, vertical="middle")}
  {wrap(x + 16, y + 60, card_w - 32, 70, f"STAGE_{i + 1}_LINE_1", font_size=_T.body, color=_C.text_body)}""")
        if i < n - 1:
            ax1 = x + card_w + 6
            ax2 = x + card_w + gap - 6
            ay = y + card_h // 2
            arrows.append(f"""
  <path d="M {ax1} {ay} L {ax2} {ay}" stroke="{_C.text_body}" stroke-width="2" fill="none" marker-end="url(#arrowhead)" />""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
  <rect x="60" y="60" width="1480" height="100" rx="12" fill="url(#headerGradient)" />
  {wrap(80, 76, 1440, 56, "TITLE", font_size=_T.title, weight=600, color=_C.text_strong, vertical="middle")}
  {wrap(80, 130, 1440, 28, "SUBTITLE", font_size=_T.subtitle, color=_C.text_body)}
  {wrap(80, 300, 1440, 36, "SECTION_LABEL", font_size=_T.section, weight=600, color=_C.text_strong)}
{''.join(cards)}
{''.join(arrows)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 2 — comparison_matrix (3 options × 5 attributes)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_comparison_matrix() -> str:
    options = 3
    attrs = 5
    col_w = 380
    row_h = 90
    label_col_w = 220
    start_x = (1600 - (label_col_w + options * col_w)) // 2
    start_y = 220
    header_h = 80

    cells = []
    # Header row
    for i in range(options):
        x = start_x + label_col_w + i * col_w
        cells.append(f"""
  <rect x="{x}" y="{start_y}" width="{col_w - 8}" height="{header_h}" rx="8" fill="{_C.primary}" />
  {wrap(x + 8, start_y + 10, col_w - 24, header_h - 20, f"OPTION_{i + 1}", font_size=_T.component + 2, weight=600, color=_C.bg, align="center", vertical="middle")}""")

    # Attribute rows
    for r in range(attrs):
        y = start_y + header_h + 16 + r * row_h
        fill = _C.bg_alt if r % 2 == 0 else _C.bg
        cells.append(f"""
  <rect x="{start_x}" y="{y}" width="{label_col_w + options * col_w - 8}" height="{row_h - 8}" rx="6" fill="{fill}" />
  {wrap(start_x + 12, y + 6, label_col_w - 20, row_h - 20, f"ATTRIBUTE_{r + 1}", font_size=_T.body + 2, weight=600, color=_C.text_strong, vertical="middle")}""")
        for i in range(options):
            cx = start_x + label_col_w + i * col_w
            cells.append(f"""
  {wrap(cx + 8, y + 6, col_w - 24, row_h - 20, f"CELL_{r + 1}_{i + 1}", font_size=_T.body, color=_C.text_body, align="center", vertical="middle")}""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(cells)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 3 — swimlane (3 lanes × 4 steps each)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_swimlane() -> str:
    lanes = 3
    steps = 4
    lane_h = 160
    lane_label_w = 200
    step_w = 240
    step_h = 90
    gap = 40
    start_y = 220
    start_x = lane_label_w + 40

    parts = []
    for li in range(lanes):
        y = start_y + li * (lane_h + 10)
        # Lane background
        parts.append(f"""
  <rect x="40" y="{y}" width="1520" height="{lane_h}" rx="8" fill="{_C.bg_alt}" />
  {wrap(56, y + (lane_h - 30) // 2, lane_label_w - 24, 30, f"LANE_{li + 1}_LABEL", font_size=_T.section, weight=600, color=_C.text_strong, vertical="middle")}""")
        # Step cards
        for si in range(steps):
            x = start_x + si * (step_w + gap)
            sy = y + (lane_h - step_h) // 2
            parts.append(f"""
  <rect x="{x}" y="{sy}" width="{step_w}" height="{step_h}" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(x + 12, sy + 12, step_w - 24, 26, f"LANE_{li + 1}_STEP_{si + 1}", font_size=_T.component, weight=600, color=_C.text_strong, align="center", vertical="middle")}
  {wrap(x + 12, sy + 44, step_w - 24, step_h - 52, f"LANE_{li + 1}_STEP_{si + 1}_DETAIL", font_size=_T.body, color=_C.text_body, align="center")}""")
            # Connecting arrow to next step in same lane
            if si < steps - 1:
                ax1 = x + step_w + 4
                ax2 = x + step_w + gap - 4
                ay = sy + step_h // 2
                parts.append(f"""
  <path d="M {ax1} {ay} L {ax2} {ay}" stroke="{_C.text_body}" stroke-width="2" fill="none" marker-end="url(#arrowhead)" />""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 4 — layered_architecture (4 horizontal bands)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_layered_architecture() -> str:
    layers = 4
    layer_h = 140
    gap = 14
    start_y = 200
    cards_per_layer = 4
    card_w = 280
    card_h = 100
    label_col_w = 200

    parts = []
    for li in range(layers):
        y = start_y + li * (layer_h + gap)
        parts.append(f"""
  <rect x="40" y="{y}" width="1520" height="{layer_h}" rx="8" fill="{_C.bg_alt}" />
  {wrap(56, y + (layer_h - 30) // 2, label_col_w - 24, 30, f"LAYER_{li + 1}_LABEL", font_size=_T.section, weight=600, color=_C.text_strong, vertical="middle")}""")
        total_card_w = cards_per_layer * card_w + (cards_per_layer - 1) * 24
        start_x = label_col_w + 60 + (1500 - label_col_w - total_card_w) // 2
        for ci in range(cards_per_layer):
            cx = start_x + ci * (card_w + 24)
            cy = y + (layer_h - card_h) // 2
            parts.append(f"""
  <rect x="{cx}" y="{cy}" width="{card_w}" height="{card_h}" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(cx + 16, cy + 14, card_w - 32, 26, f"LAYER_{li + 1}_COMPONENT_{ci + 1}", font_size=_T.component, weight=600, color=_C.text_strong, vertical="middle")}
  {wrap(cx + 16, cy + 46, card_w - 32, card_h - 56, f"LAYER_{li + 1}_COMPONENT_{ci + 1}_ROLE", font_size=_T.body, color=_C.text_body)}""")
        # Down-arrow from this layer to next
        if li < layers - 1:
            ax = 800
            ay1 = y + layer_h + 1
            ay2 = y + layer_h + gap - 1
            parts.append(f"""
  <path d="M {ax} {ay1} L {ax} {ay2}" stroke="{_C.text_body}" stroke-width="2" fill="none" marker-end="url(#arrowhead)" />""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 5 — concept_map (hub + spokes)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_concept_map() -> str:
    cx_hub, cy_hub = 800, 500
    hub_w, hub_h = 280, 120
    spokes = 6
    spoke_w, spoke_h = 220, 100
    radius = 320
    import math

    parts = [f"""
  <rect x="{cx_hub - hub_w // 2}" y="{cy_hub - hub_h // 2}" width="{hub_w}" height="{hub_h}" rx="12" fill="url(#primaryGradient)" filter="url(#softShadow)" />
  {wrap(cx_hub - hub_w // 2 + 16, cy_hub - hub_h // 2 + 16, hub_w - 32, hub_h - 32, "HUB_LABEL", font_size=_T.component + 4, weight=600, color=_C.bg, align="center", vertical="middle")}"""]

    for i in range(spokes):
        angle = (i * (360 / spokes) - 90) * (3.14159 / 180)
        sx = int(cx_hub + radius * math.cos(angle) - spoke_w // 2)
        sy = int(cy_hub + radius * math.sin(angle) - spoke_h // 2)
        # Connector line from spoke center to hub edge
        sx_center = sx + spoke_w // 2
        sy_center = sy + spoke_h // 2
        parts.append(f"""
  <path d="M {sx_center} {sy_center} L {cx_hub} {cy_hub}" stroke="{_C.border}" stroke-width="2" fill="none" />
  <rect x="{sx}" y="{sy}" width="{spoke_w}" height="{spoke_h}" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(sx + 12, sy + 12, spoke_w - 24, 28, f"SPOKE_{i + 1}_LABEL", font_size=_T.component, weight=600, color=_C.text_strong, align="center", vertical="middle")}
  {wrap(sx + 12, sy + 44, spoke_w - 24, spoke_h - 52, f"SPOKE_{i + 1}_DETAIL", font_size=_T.body, color=_C.text_body, align="center")}""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 6 — hero_with_callouts (Phase 3, Klein-powered)
# ──────────────────────────────────────────────────────────────────────
# A large raster image fills the left ~60% of the slide; 3 callout cards
# on the right summarize the key points. The image slot is rendered by
# Klein via base64-embedded <image>. The {{HERO_IMAGE_B64}} placeholder
# gets replaced with the Klein output post-generation.
def _skeleton_hero_with_callouts() -> str:
    img_x, img_y, img_w, img_h = 60, 200, 960, 640
    cb_x = img_x + img_w + 40
    cb_w = 480
    cb_h = 180
    cb_gap = 20
    parts = []
    for i in range(3):
        y = img_y + i * (cb_h + cb_gap)
        parts.append(f"""
  <rect x="{cb_x}" y="{y}" width="{cb_w}" height="{cb_h}" rx="12" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  <rect x="{cb_x}" y="{y}" width="6" height="{cb_h}" rx="3" fill="{_C.primary}" />
  {wrap(cb_x + 24, y + 16, cb_w - 36, 32, f"CALLOUT_{i + 1}_TITLE", font_size=_T.component + 2, weight=600, color=_C.text_strong)}
  {wrap(cb_x + 24, y + 56, cb_w - 36, cb_h - 70, f"CALLOUT_{i + 1}_LINE_1", font_size=_T.body + 2, color=_C.text_body)}""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
  <!-- Hero image (filled by Klein diffusion) -->
  <rect x="{img_x}" y="{img_y}" width="{img_w}" height="{img_h}" rx="12" fill="{_C.bg_alt}" stroke="{_C.border}" stroke-width="1" />
  <image x="{img_x}" y="{img_y}" width="{img_w}" height="{img_h}" href="data:image/png;base64,{{{{HERO_IMAGE_B64}}}}" preserveAspectRatio="xMidYMid slice" />
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Shared header helper to keep skeleton bodies short
# ──────────────────────────────────────────────────────────────────────
def _header_band() -> str:
    """Standard title + subtitle band used at the top of every skeleton.

    Uses wrap() so titles longer than ~30 chars don't overflow the 1480px band.
    """
    return f"""  <rect x="60" y="60" width="1480" height="100" rx="12" fill="url(#headerGradient)" />
  {wrap(80, 72, 1440, 50, "TITLE", font_size=_T.title, weight=600, color=_C.text_strong, vertical="middle")}
  {wrap(80, 124, 1440, 28, "SUBTITLE", font_size=_T.subtitle, color=_C.text_body)}"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 7 — microservices_mesh (central bus + services + dbs)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_microservices_mesh() -> str:
    bus_x, bus_y, bus_w, bus_h = 200, 440, 1200, 80
    n = 4
    svc_w, svc_h = 240, 100
    gap = (bus_w - n * svc_w) // (n + 1)
    parts = []
    for i in range(n):
        x = bus_x + gap + i * (svc_w + gap)
        # Service card above the bus
        sy = bus_y - 160
        parts.append(f"""
  <rect x="{x}" y="{sy}" width="{svc_w}" height="{svc_h}" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(x + 12, sy + 16, svc_w - 24, 28, f"SERVICE_{i + 1}_NAME", font_size=_T.component, weight=600, color=_C.text_strong, align="center", vertical="middle")}
  {wrap(x + 12, sy + 52, svc_w - 24, svc_h - 60, f"SERVICE_{i + 1}_ROLE", font_size=_T.body, color=_C.text_body, align="center")}
  <path d="M {x + svc_w // 2} {sy + svc_h + 4} L {x + svc_w // 2} {bus_y - 4}" stroke="{_C.primary}" stroke-width="2" fill="none" marker-end="url(#arrowhead_primary)" />""")
        # DB below the bus
        dy = bus_y + bus_h + 80
        parts.append(f"""
  <rect x="{x + 30}" y="{dy}" width="{svc_w - 60}" height="80" rx="8" fill="{_C.bg_alt}" stroke="{_C.border}" stroke-width="1" />
  {wrap(x + 38, dy + 14, svc_w - 76, 26, f"DB_{i + 1}", font_size=_T.component - 2, weight=600, color=_C.text_strong, align="center", vertical="middle")}
  {wrap(x + 38, dy + 44, svc_w - 76, 28, f"DB_{i + 1}_TYPE", font_size=_T.caption + 1, color=_C.text_muted, align="center")}
  <path d="M {x + svc_w // 2} {bus_y + bus_h + 4} L {x + svc_w // 2} {dy - 4}" stroke="{_C.text_body}" stroke-width="2" fill="none" marker-end="url(#arrowhead)" />""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
  <!-- Central bus / gateway -->
  <rect x="{bus_x}" y="{bus_y}" width="{bus_w}" height="{bus_h}" rx="12" fill="url(#primaryGradient)" />
  {wrap(bus_x + 20, bus_y + 18, bus_w - 40, bus_h - 36, "BUS_LABEL", font_size=_T.section, weight=600, color=_C.bg, align="center", vertical="middle")}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 8 — request_flow (numbered horizontal chain)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_request_flow() -> str:
    n = 5
    card_w, card_h = 240, 120
    gap = 60
    total = n * card_w + (n - 1) * gap
    start_x = (1600 - total) // 2
    y = 380
    parts = []
    for i in range(n):
        x = start_x + i * (card_w + gap)
        parts.append(f"""
  <rect x="{x}" y="{y}" width="{card_w}" height="{card_h}" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(x + 12, y + 14, card_w - 24, 30, f"COMPONENT_{i + 1}", font_size=_T.component, weight=600, color=_C.text_strong, align="center", vertical="middle")}
  {wrap(x + 12, y + 50, card_w - 24, card_h - 60, f"COMPONENT_{i + 1}_NOTE", font_size=_T.body, color=_C.text_body, align="center")}""")
        if i < n - 1:
            ax1 = x + card_w + 6
            ax2 = x + card_w + gap - 6
            ay = y + card_h // 2
            mid_x = (ax1 + ax2) // 2
            parts.append(f"""
  <path d="M {ax1} {ay} L {ax2} {ay}" stroke="{_C.primary}" stroke-width="2.5" fill="none" marker-end="url(#arrowhead_primary)" />
  <circle cx="{mid_x}" cy="{ay - 20}" r="14" fill="{_C.primary}" />
  <text x="{mid_x}" y="{ay - 16}" font-family="{_FONT}" font-size="13" font-weight="600" fill="{_C.bg}" text-anchor="middle">{i + 1}</text>
  {wrap(mid_x - 50, ay + 16, 100, 24, f"STEP_{i + 1}_LABEL", font_size=_T.caption, color=_C.text_muted, align="center")}""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 9 — journey_map (stages + metrics above + owners below)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_journey_map() -> str:
    n = 5
    stage_w, stage_h = 260, 140
    gap = 30
    total = n * stage_w + (n - 1) * gap
    start_x = (1600 - total) // 2
    stage_y = 420
    metric_y = stage_y - 130
    owner_y = stage_y + stage_h + 30
    parts = []
    for i in range(n):
        x = start_x + i * (stage_w + gap)
        # Stage card
        parts.append(f"""
  <rect x="{x}" y="{stage_y}" width="{stage_w}" height="{stage_h}" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  <rect x="{x}" y="{stage_y}" width="{stage_w}" height="36" rx="8" fill="{_C.accent_tint}" />
  {wrap(x + 8, stage_y + 6, stage_w - 16, 30, f"STAGE_{i + 1}", font_size=_T.component, weight=600, color=_C.primary_dark, align="center", vertical="middle")}
  {wrap(x + 14, stage_y + 46, stage_w - 28, 30, f"STAGE_{i + 1}_ACT_1", font_size=_T.body, color=_C.text_body)}
  {wrap(x + 14, stage_y + 76, stage_w - 28, 30, f"STAGE_{i + 1}_ACT_2", font_size=_T.body, color=_C.text_body)}
  {wrap(x + 14, stage_y + 106, stage_w - 28, 30, f"STAGE_{i + 1}_ACT_3", font_size=_T.body, color=_C.text_body)}""")
        # Metric above
        parts.append(f"""
  {wrap(x, metric_y - 30, stage_w, 40, f"STAGE_{i + 1}_METRIC", font_size=36, weight=600, color=_C.primary, align="center", vertical="middle")}
  {wrap(x, metric_y + 8, stage_w, 22, f"STAGE_{i + 1}_METRIC_LABEL", font_size=_T.caption, color=_C.text_muted, align="center")}""")
        # Owner below
        parts.append(f"""
  {wrap(x, owner_y - 16, stage_w, 26, f"STAGE_{i + 1}_OWNER", font_size=_T.body + 2, weight=600, color=_C.text_strong, align="center", vertical="middle")}""")
        if i < n - 1:
            ax1 = x + stage_w + 4
            ax2 = x + stage_w + gap - 4
            ay = stage_y + stage_h // 2
            parts.append(f"""
  <path d="M {ax1} {ay} L {ax2} {ay}" stroke="{_C.text_body}" stroke-width="2" fill="none" marker-end="url(#arrowhead)" />""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
  <text x="80" y="270" font-family="{_FONT}" font-size="{_T.caption + 2}" font-weight="600" fill="{_C.text_muted}">METRICS</text>
  <text x="80" y="{owner_y}" font-family="{_FONT}" font-size="{_T.caption + 2}" font-weight="600" fill="{_C.text_muted}">OWNERS</text>
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 10 — decision_tree (top-down conditional)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_decision_tree() -> str:
    parts = [f"""
  <!-- Root decision -->
  <polygon points="800,260 920,330 800,400 680,330" fill="{_C.accent_tint}" stroke="{_C.primary}" stroke-width="2" />
  <text x="800" y="335" font-family="{_FONT}" font-size="{_T.component - 2}" font-weight="600" fill="{_C.text_strong}" text-anchor="middle">{{{{ROOT_QUESTION}}}}</text>"""]
    # Left branch (Yes)
    # Replace the root question text with a wrap call (still inside the diamond)
    # We previously emitted <text x="800" y="335">{{ROOT_QUESTION}}</text>;
    # wrap inside the polygon's bounding box centered around (800, 330).
    parts[0] = f"""
  <!-- Root decision -->
  <polygon points="800,260 920,330 800,400 680,330" fill="{_C.accent_tint}" stroke="{_C.primary}" stroke-width="2" />
  {wrap(710, 290, 180, 80, "ROOT_QUESTION", font_size=_T.component - 2, weight=600, color=_C.text_strong, align="center", vertical="middle")}"""

    parts.append(f"""
  <path d="M 740 380 L 480 470" stroke="{_C.text_body}" stroke-width="2" fill="none" marker-end="url(#arrowhead)" />
  {wrap(540, 388, 120, 24, "YES_LABEL", font_size=_T.body, weight=600, color=_C.success, align="center")}
  <rect x="320" y="470" width="320" height="100" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(336, 480, 288, 32, "YES_ACTION", font_size=_T.component, weight=600, color=_C.text_strong, align="center", vertical="middle")}
  {wrap(336, 518, 288, 44, "YES_DETAIL", font_size=_T.body, color=_C.text_body, align="center")}""")
    # Right branch (No)
    parts.append(f"""
  <path d="M 860 380 L 1120 470" stroke="{_C.text_body}" stroke-width="2" fill="none" marker-end="url(#arrowhead)" />
  {wrap(940, 388, 120, 24, "NO_LABEL", font_size=_T.body, weight=600, color=_C.error, align="center")}
  <rect x="960" y="470" width="320" height="100" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(976, 480, 288, 32, "NO_ACTION", font_size=_T.component, weight=600, color=_C.text_strong, align="center", vertical="middle")}
  {wrap(976, 518, 288, 44, "NO_DETAIL", font_size=_T.body, color=_C.text_body, align="center")}""")
    # Sub-decisions under each
    for side, side_x, label_keys in (
        ("YES", 480, ["YES_NEXT_1", "YES_NEXT_2"]),
        ("NO", 1120, ["NO_NEXT_1", "NO_NEXT_2"]),
    ):
        for j, key in enumerate(label_keys):
            x = side_x - 100 + j * 200
            parts.append(f"""
  <path d="M {side_x} 580 L {x} 660" stroke="{_C.text_muted}" stroke-width="1.5" fill="none" marker-end="url(#arrowhead)" />
  <rect x="{x - 80}" y="660" width="160" height="60" rx="6" fill="{_C.bg_alt}" />
  {wrap(x - 72, 670, 144, 44, key, font_size=_T.body, weight=600, color=_C.text_strong, align="center", vertical="middle")}""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 11 — timeline (horizontal axis, alternating events)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_timeline() -> str:
    axis_y = 500
    n = 6
    spacing = 220
    start_x = (1600 - (n - 1) * spacing) // 2
    parts = [f"""
  <line x1="60" y1="{axis_y}" x2="1540" y2="{axis_y}" stroke="{_C.primary}" stroke-width="3" />"""]
    for i in range(n):
        x = start_x + i * spacing
        above = i % 2 == 0
        ey = axis_y - 180 if above else axis_y + 60
        # Marker on axis
        parts.append(f"""
  <circle cx="{x}" cy="{axis_y}" r="10" fill="{_C.bg}" stroke="{_C.primary}" stroke-width="3" />
  {wrap(x - 80, axis_y + 18, 160, 24, f"DATE_{i + 1}", font_size=_T.body + 2, weight=600, color=_C.primary, align="center")}""")
        # Event card
        parts.append(f"""
  <line x1="{x}" y1="{axis_y - 10 if above else axis_y + 10}" x2="{x}" y2="{ey + (160 if above else 0)}" stroke="{_C.border}" stroke-width="1" stroke-dasharray="4,4" />
  <rect x="{x - 110}" y="{ey}" width="220" height="160" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(x - 102, ey + 10, 204, 36, f"EVENT_{i + 1}_TITLE", font_size=_T.component, weight=600, color=_C.text_strong, align="center", vertical="middle")}
  {wrap(x - 102, ey + 54, 204, 96, f"EVENT_{i + 1}_LINE_1", font_size=_T.body, color=_C.text_body, align="center")}""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 12 — before_after (two-column transformation)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_before_after() -> str:
    col_w = 640
    col_h = 580
    col_y = 220
    left_x = 80
    right_x = 1600 - 80 - col_w
    arrow_y = col_y + col_h // 2
    parts = []
    for side, x, label_prefix, header_key, header_color in (
        ("left", left_x, "BEFORE", "BEFORE_LABEL", _C.text_muted),
        ("right", right_x, "AFTER", "AFTER_LABEL", _C.primary),
    ):
        parts.append(f"""
  <rect x="{x}" y="{col_y}" width="{col_w}" height="{col_h}" rx="12" fill="{_C.bg_alt}" />
  <rect x="{x}" y="{col_y}" width="{col_w}" height="56" rx="12" fill="{header_color}" />
  {wrap(x + 12, col_y + 14, col_w - 24, 32, header_key, font_size=_T.section, weight=600, color=_C.bg, align="center", vertical="middle")}""")
        for i in range(4):
            cy = col_y + 100 + i * 110
            parts.append(f"""
  <rect x="{x + 30}" y="{cy}" width="{col_w - 60}" height="90" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(x + 46, cy + 12, col_w - 92, 30, f"{label_prefix}_ITEM_{i + 1}", font_size=_T.component, weight=600, color=_C.text_strong)}
  {wrap(x + 46, cy + 48, col_w - 92, 36, f"{label_prefix}_DETAIL_{i + 1}", font_size=_T.body, color=_C.text_body)}""")
    # Arrow between columns
    arrow_x1 = left_x + col_w + 12
    arrow_x2 = right_x - 12
    parts.append(f"""
  <path d="M {arrow_x1} {arrow_y} L {arrow_x2} {arrow_y}" stroke="{_C.primary}" stroke-width="4" fill="none" marker-end="url(#arrowhead_primary)" />
  {wrap(arrow_x1, arrow_y - 38, arrow_x2 - arrow_x1, 24, "TRANSITION_LABEL", font_size=_T.body + 2, weight=600, color=_C.primary, align="center")}""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 13 — pros_cons (two-column tradeoff)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_pros_cons() -> str:
    col_w = 640
    col_h = 580
    col_y = 220
    left_x = 80
    right_x = 1600 - 80 - col_w
    parts = []
    for x, label_prefix, header, color in (
        (left_x, "PRO", "PROS", _C.success),
        (right_x, "CON", "CONS", _C.error),
    ):
        # Headers are literal "PROS" / "CONS" labels (not slot placeholders), so
        # they can stay as <text> — they never overflow.
        parts.append(f"""
  <rect x="{x}" y="{col_y}" width="{col_w}" height="{col_h}" rx="12" fill="{_C.bg_alt}" />
  <rect x="{x}" y="{col_y}" width="{col_w}" height="56" rx="12" fill="{color}" />
  <text x="{x + col_w // 2}" y="{col_y + 38}" font-family="{_FONT}" font-size="{_T.section}" font-weight="600" fill="{_C.bg}" text-anchor="middle">{header}</text>""")
        for i in range(4):
            cy = col_y + 100 + i * 110
            parts.append(f"""
  <rect x="{x + 30}" y="{cy}" width="{col_w - 60}" height="90" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(x + 46, cy + 12, col_w - 92, 30, f"{label_prefix}_{i + 1}_TITLE", font_size=_T.component, weight=600, color=_C.text_strong)}
  {wrap(x + 46, cy + 48, col_w - 92, 36, f"{label_prefix}_{i + 1}_DETAIL", font_size=_T.body, color=_C.text_body)}""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 14 — quadrant_2x2 (cross axes with items)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_quadrant_2x2() -> str:
    # Plot area
    px, py, pw, ph = 240, 220, 1120, 600
    cx, cy = px + pw // 2, py + ph // 2
    parts = [f"""
  <rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="8" fill="{_C.bg_alt}" />
  <!-- Axes -->
  <line x1="{cx}" y1="{py}" x2="{cx}" y2="{py + ph}" stroke="{_C.border}" stroke-width="1.5" />
  <line x1="{px}" y1="{cy}" x2="{px + pw}" y2="{cy}" stroke="{_C.border}" stroke-width="1.5" />
  <!-- Axis labels -->
  {wrap(cx - 200, py - 36, 400, 26, "Y_AXIS_HIGH", font_size=_T.body + 2, weight=600, color=_C.text_strong, align="center")}
  {wrap(cx - 200, py + ph + 8, 400, 26, "Y_AXIS_LOW", font_size=_T.body + 2, weight=600, color=_C.text_strong, align="center")}
  {wrap(px - 200, cy - 12, 184, 26, "X_AXIS_LOW", font_size=_T.body + 2, weight=600, color=_C.text_strong, align="center", vertical="middle")}
  {wrap(px + pw + 16, cy - 12, 184, 26, "X_AXIS_HIGH", font_size=_T.body + 2, weight=600, color=_C.text_strong, align="center", vertical="middle")}"""]
    # Quadrant labels (top-left, top-right, bottom-left, bottom-right)
    quad_positions = [
        ("TL", px + 16, py + 16, "left"),
        ("TR", cx + 16, py + 16, "left"),
        ("BL", px + 16, cy + ph // 2 - 32, "left"),
        ("BR", cx + 16, cy + ph // 2 - 32, "left"),
    ]
    for code, qx, qy, _align in quad_positions:
        parts.append(f"""
  {wrap(qx, qy, pw // 2 - 32, 30, f"QUADRANT_{code}_LABEL", font_size=_T.caption + 2, weight=600, color=_C.text_muted, align="left")}""")
    # 4 example items (one per quadrant) with placeholder positions
    item_offsets = [(-220, -180), (220, -180), (-220, 180), (220, 180)]
    for i, (dx, dy) in enumerate(item_offsets, start=1):
        ix, iy = cx + dx, cy + dy
        parts.append(f"""
  <rect x="{ix - 80}" y="{iy - 30}" width="160" height="60" rx="8" fill="{_C.bg}" stroke="{_C.primary}" stroke-width="1.5" filter="url(#softShadow)" />
  {wrap(ix - 72, iy - 22, 144, 44, f"ITEM_{i}", font_size=_T.body + 2, weight=600, color=_C.text_strong, align="center", vertical="middle")}""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 15 — tree_hierarchy (top-down org / taxonomy)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_tree_hierarchy() -> str:
    # 1 root → 3 children → 2 leaves each
    root_x, root_y = 800, 230
    root_w, root_h = 280, 90
    child_y = 410
    leaf_y = 590
    child_w, child_h = 240, 90
    leaf_w, leaf_h = 200, 70
    parts = [f"""
  <rect x="{root_x - root_w // 2}" y="{root_y}" width="{root_w}" height="{root_h}" rx="10" fill="url(#primaryGradient)" filter="url(#softShadow)" />
  {wrap(root_x - root_w // 2 + 16, root_y + 16, root_w - 32, root_h - 32, "ROOT_LABEL", font_size=_T.component + 4, weight=600, color=_C.bg, align="center", vertical="middle")}"""]
    child_xs = [400, 800, 1200]
    for i, cx in enumerate(child_xs, start=1):
        # Line from root to child
        parts.append(f"""
  <path d="M {root_x} {root_y + root_h} L {cx} {child_y}" stroke="{_C.border}" stroke-width="2" fill="none" />
  <rect x="{cx - child_w // 2}" y="{child_y}" width="{child_w}" height="{child_h}" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(cx - child_w // 2 + 12, child_y + 12, child_w - 24, child_h - 24, f"CHILD_{i}_LABEL", font_size=_T.component, weight=600, color=_C.text_strong, align="center", vertical="middle")}""")
        # 2 leaves per child
        for j, dx in enumerate([-110, 110]):
            lx = cx + dx
            parts.append(f"""
  <path d="M {cx} {child_y + child_h} L {lx} {leaf_y}" stroke="{_C.border}" stroke-width="1.5" fill="none" />
  <rect x="{lx - leaf_w // 2}" y="{leaf_y}" width="{leaf_w}" height="{leaf_h}" rx="6" fill="{_C.bg_alt}" />
  {wrap(lx - leaf_w // 2 + 10, leaf_y + 10, leaf_w - 20, leaf_h - 20, f"CHILD_{i}_LEAF_{j + 1}", font_size=_T.body + 2, weight=600, color=_C.text_strong, align="center", vertical="middle")}""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 16 — stat_callouts (grid of large numbers)
# ──────────────────────────────────────────────────────────────────────
def _skeleton_stat_callouts() -> str:
    cols, rows = 3, 2
    cell_w = (1600 - 120 - (cols - 1) * 40) // cols
    cell_h = 240
    start_x = 60
    start_y = 240
    parts = []
    n = 1
    for r in range(rows):
        for c in range(cols):
            x = start_x + c * (cell_w + 40)
            y = start_y + r * (cell_h + 40)
            parts.append(f"""
  <rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" rx="12" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(x + 16, y + 30, cell_w - 32, 100, f"STAT_{n}_VALUE", font_size=84, weight=600, color=_C.primary, align="center", vertical="middle")}
  {wrap(x + 16, y + 138, cell_w - 32, 36, f"STAT_{n}_LABEL", font_size=_T.component + 2, weight=600, color=_C.text_strong, align="center")}
  {wrap(x + 16, y + 178, cell_w - 32, 50, f"STAT_{n}_NOTE", font_size=_T.body, color=_C.text_muted, align="center")}""")
            n += 1

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 17 — cqrs_pattern (write path / event store / read path)
# ──────────────────────────────────────────────────────────────────────
# Specifically shaped for CQRS / event-sourcing / command-query split.
# Three horizontal lanes with the event store as the connecting middle
# layer. Used when content describes separate write and read paths.
def _skeleton_cqrs_pattern() -> str:
    lane_h = 170
    inner_card_h = 100
    start_y = 220
    write_y = start_y                       # top
    event_y = start_y + lane_h + 14         # middle
    read_y = start_y + 2 * (lane_h + 14)    # bottom

    components_per_lane = 4
    card_w = 250
    gap = 30
    lane_label_w = 220
    available = 1520 - lane_label_w - 40
    total_cards = components_per_lane * card_w + (components_per_lane - 1) * gap
    start_x = lane_label_w + 60 + (available - total_cards) // 2

    parts = []

    # Helper to draw one lane of components
    def lane_block(y: int, label_key: str, lane_color: str, prefix: str) -> None:
        parts.append(f"""
  <rect x="40" y="{y}" width="1520" height="{lane_h}" rx="10" fill="{_C.bg_alt}" />
  <rect x="40" y="{y}" width="6" height="{lane_h}" rx="3" fill="{lane_color}" />
  {wrap(56, y + lane_h // 2 - 28, lane_label_w - 24, 28, label_key, font_size=_T.section, weight=600, color=_C.text_strong, vertical="middle")}
  {wrap(56, y + lane_h // 2 + 4, lane_label_w - 24, 26, f"{prefix}_DESCRIPTION", font_size=_T.body, color=_C.text_muted)}""")
        cy = y + (lane_h - inner_card_h) // 2
        for i in range(components_per_lane):
            cx = start_x + i * (card_w + gap)
            parts.append(f"""
  <rect x="{cx}" y="{cy}" width="{card_w}" height="{inner_card_h}" rx="8" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  {wrap(cx + 12, cy + 12, card_w - 24, 30, f"{prefix}_COMPONENT_{i + 1}", font_size=_T.component, weight=600, color=_C.text_strong, align="center", vertical="middle")}
  {wrap(cx + 12, cy + 48, card_w - 24, inner_card_h - 56, f"{prefix}_COMPONENT_{i + 1}_ROLE", font_size=_T.body, color=_C.text_body, align="center")}""")
            # Horizontal arrow to next within lane
            if i < components_per_lane - 1:
                ax1 = cx + card_w + 4
                ax2 = cx + card_w + gap - 4
                ay = cy + inner_card_h // 2
                parts.append(f"""
  <path d="M {ax1} {ay} L {ax2} {ay}" stroke="{lane_color}" stroke-width="2" fill="none" marker-end="url(#arrowhead)" />""")

    lane_block(write_y, "WRITE_LANE_LABEL", _C.primary, "WRITE")
    lane_block(event_y, "EVENT_LANE_LABEL", _C.success, "EVENT")
    lane_block(read_y, "READ_LANE_LABEL", _C.warning, "READ")

    # Cross-lane down-arrows: write → event store, event store → read
    arrow_x = start_x + (components_per_lane * card_w + (components_per_lane - 1) * gap) // 2
    parts.append(f"""
  <path d="M {arrow_x} {write_y + lane_h + 1} L {arrow_x} {event_y - 1}" stroke="{_C.text_body}" stroke-width="2.5" fill="none" marker-end="url(#arrowhead)" />
  <path d="M {arrow_x} {event_y + lane_h + 1} L {arrow_x} {read_y - 1}" stroke="{_C.text_body}" stroke-width="2.5" fill="none" marker-end="url(#arrowhead)" />""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Skeleton 18 — value_proposition (vector hero, no raster needed)
# ──────────────────────────────────────────────────────────────────────
# Used when content is hero/value-prop framing AND Klein isn't available.
# Polished without a raster: central icon + tagline + 3 benefit cards.
def _skeleton_value_proposition() -> str:
    # Central focal section
    fx, fy, fw, fh = 200, 200, 1200, 280
    icon_cx, icon_cy, icon_r = fx + fw // 2, fy + 100, 56

    # 3 benefit cards below
    cb_y = fy + fh + 60
    cb_w = 440
    cb_h = 220
    gap = 40
    total_w = 3 * cb_w + 2 * gap
    cb_start_x = (1600 - total_w) // 2

    parts = [f"""
  <!-- Central focal section -->
  <rect x="{fx}" y="{fy}" width="{fw}" height="{fh}" rx="16" fill="url(#headerGradient)" />
  <circle cx="{icon_cx}" cy="{icon_cy}" r="{icon_r}" fill="url(#primaryGradient)" filter="url(#softShadow)" />
  {wrap(icon_cx - icon_r, icon_cy - 26, icon_r * 2, 56, "ICON_GLYPH", font_size=44, weight=600, color=_C.bg, align="center", vertical="middle")}
  {wrap(fx + 40, fy + 170, fw - 80, 56, "HERO_TAGLINE", font_size=42, weight=600, color=_C.text_strong, align="center", vertical="middle")}
  {wrap(fx + 40, fy + 226, fw - 80, 40, "HERO_SUPPORTING", font_size=20, weight=400, color=_C.text_body, align="center")}"""]

    benefits = [
        ("BENEFIT_1", "Customer / outcome focus", _C.primary),
        ("BENEFIT_2", "Operational efficiency", _C.success),
        ("BENEFIT_3", "Strategic scale", _C.warning),
    ]
    for i, (prefix, _desc, color) in enumerate(benefits):
        x = cb_start_x + i * (cb_w + gap)
        parts.append(f"""
  <rect x="{x}" y="{cb_y}" width="{cb_w}" height="{cb_h}" rx="12" fill="{_C.bg}" stroke="{_C.border}" stroke-width="1" filter="url(#softShadow)" />
  <rect x="{x}" y="{cb_y}" width="{cb_w}" height="6" rx="3" fill="{color}" />
  <circle cx="{x + 50}" cy="{cb_y + 60}" r="22" fill="{color}" fill-opacity="0.15" />
  {wrap(x + 28, cb_y + 48, 44, 30, f"{prefix}_GLYPH", font_size=22, weight=600, color=color, align="center", vertical="middle")}
  {wrap(x + 86, cb_y + 36, cb_w - 100, 30, f"{prefix}_TITLE", font_size=22, weight=600, color=_C.text_strong)}
  {wrap(x + 86, cb_y + 68, cb_w - 100, 24, f"{prefix}_TAGLINE", font_size=14, color=_C.text_muted)}
  {wrap(x + 22, cb_y + 108, cb_w - 44, cb_h - 120, f"{prefix}_LINE_1", font_size=_T.body + 2, color=_C.text_body)}""")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" width="1600" height="900">
{_DEFS}
  <rect x="0" y="0" width="1600" height="900" fill="{_C.bg}" />
{_header_band()}
{''.join(parts)}
</svg>"""


# ──────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────
_SKELETONS: Dict[str, callable] = {
    "linear_process": _skeleton_linear_process,
    "comparison_matrix": _skeleton_comparison_matrix,
    "swimlane": _skeleton_swimlane,
    "layered_architecture": _skeleton_layered_architecture,
    "concept_map": _skeleton_concept_map,
    "hero_with_callouts": _skeleton_hero_with_callouts,
    "microservices_mesh": _skeleton_microservices_mesh,
    "request_flow": _skeleton_request_flow,
    "journey_map": _skeleton_journey_map,
    "decision_tree": _skeleton_decision_tree,
    "timeline": _skeleton_timeline,
    "before_after": _skeleton_before_after,
    "pros_cons": _skeleton_pros_cons,
    "quadrant_2x2": _skeleton_quadrant_2x2,
    "tree_hierarchy": _skeleton_tree_hierarchy,
    "stat_callouts": _skeleton_stat_callouts,
    "cqrs_pattern": _skeleton_cqrs_pattern,
    "value_proposition": _skeleton_value_proposition,
}

# Skeletons that contain a Klein-fillable raster slot
HERO_IDIOMS = {"hero_with_callouts"}

# Idioms supported by the Olmo (scaffolded) path — must match _SKELETONS keys
# Renamed from OLMO_IDIOMS (S1/B1 2026-07-03): this is the FULL idiom catalog,
# used by every skeleton family — the old name was a Setup-A-era artifact.
ALL_IDIOMS = list(_SKELETONS.keys())


def get_skeleton(idiom_id: str) -> Optional[str]:
    """Return a complete, valid SVG skeleton with placeholder slots."""
    builder = _SKELETONS.get(idiom_id)
    if not builder:
        return None
    return builder()


def list_skeleton_idioms() -> list[str]:
    return list(_SKELETONS.keys())
