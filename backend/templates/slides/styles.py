"""Visual style definitions for video slide rendering.

Each style is a dict of CSS template variables that get substituted into base.html.
Styles are purely cosmetic — they don't change layout, only colors, fonts, and textures.
"""

from typing import Dict


VISUAL_STYLES: Dict[str, Dict[str, str]] = {
    "classic": {
        "font_family": "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif",
        "bg_color": "#FFFFFF",
        "text_color": "#1a1a2e",
        "heading_color": "#16213e",
        "accent_color": "#4361ee",
        "card_bg": "rgba(67, 97, 238, 0.06)",
        "style_overrides": "",
    },
    "dark": {
        "font_family": "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif",
        "bg_color": "#0f0f1a",
        "text_color": "#e0e0e8",
        "heading_color": "#ffffff",
        "accent_color": "#818cf8",
        "card_bg": "rgba(129, 140, 248, 0.08)",
        "style_overrides": "",
    },
    "whiteboard": {
        "font_family": "'Caveat', 'Comic Neue', 'Segoe Print', cursive, sans-serif",
        "bg_color": "#faf8f5",
        "text_color": "#2d2d2d",
        "heading_color": "#1a1a1a",
        "accent_color": "#e74c3c",
        "card_bg": "rgba(231, 76, 60, 0.06)",
        "style_overrides": """
            body { background-image: 
                linear-gradient(rgba(0,0,0,0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(0,0,0,0.03) 1px, transparent 1px);
                background-size: 40px 40px;
            }
            .slide::before { background: #e74c3c; width: 6px; border-radius: 3px; top: 40px; bottom: 40px; }
            .bullet-list .items li::before { border-radius: 2px; transform: rotate(2deg); }
        """,
    },
    "midnight": {
        "font_family": "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif",
        "bg_color": "#0a0a1a",
        "text_color": "#c8cad0",
        "heading_color": "#f0f0f5",
        "accent_color": "#06d6a0",
        "card_bg": "rgba(6, 214, 160, 0.08)",
        "style_overrides": """
            body { background: linear-gradient(135deg, #0a0a1a 0%, #1a1a3e 100%); }
        """,
    },
    "warm": {
        "font_family": "'Georgia', 'Cambria', 'Times New Roman', serif",
        "bg_color": "#fdf6ee",
        "text_color": "#3d2c1e",
        "heading_color": "#2c1810",
        "accent_color": "#d4763c",
        "card_bg": "rgba(212, 118, 60, 0.07)",
        "style_overrides": "",
    },
    "ocean": {
        "font_family": "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif",
        "bg_color": "#f0f7ff",
        "text_color": "#1e3a5f",
        "heading_color": "#0d2137",
        "accent_color": "#0077b6",
        "card_bg": "rgba(0, 119, 182, 0.06)",
        "style_overrides": """
            body { background: linear-gradient(180deg, #f0f7ff 0%, #e0efff 100%); }
        """,
    },
}

# Default style
DEFAULT_STYLE = "classic"


def get_style(style_name: str) -> Dict[str, str]:
    """Get a visual style by name, falling back to classic."""
    return VISUAL_STYLES.get(style_name, VISUAL_STYLES[DEFAULT_STYLE])


def list_styles() -> list:
    """List available style names."""
    return list(VISUAL_STYLES.keys())


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB tuple to hex string."""
    return f"#{r:02x}{g:02x}{b:02x}"


def _hex_to_rgb(hex_str: str):
    """Convert hex string to (r, g, b) tuple."""
    hex_str = hex_str.lstrip('#')
    return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))


def _luminance(r: int, g: int, b: int) -> float:
    """Relative luminance (0=black, 1=white)."""
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def extract_style_from_pptx(pptx_path: str) -> Dict[str, str]:
    """Extract a video style from a PPTX template's theme colors.

    Reads the slide master's color scheme and font theme to build
    a CSS variable dict matching our slide template format.
    """
    from pathlib import Path
    try:
        from pptx import Presentation
        from pptx.util import Pt
        from pptx.dml.color import RGBColor
    except ImportError:
        return VISUAL_STYLES[DEFAULT_STYLE]

    try:
        prs = Presentation(pptx_path)
    except Exception:
        return VISUAL_STYLES[DEFAULT_STYLE]

    # -- Extract theme colors from slide master --
    bg_color = "#FFFFFF"
    text_color = "#1a1a2e"
    heading_color = "#16213e"
    accent_color = "#4361ee"
    font_family = "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif"

    try:
        theme = prs.slide_masters[0].slide_layouts[0].slide_master.element
        # Try to find theme XML
        from lxml import etree
        ns = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}

        # Color scheme
        clr_scheme = theme.find('.//a:clrScheme', ns)
        if clr_scheme is not None:
            # Background: dk1 or lt1 depending on which is lighter
            for tag, attr in [('a:lt1', 'bg'), ('a:dk1', 'text'), ('a:dk2', 'heading'), ('a:accent1', 'accent')]:
                el = clr_scheme.find(tag, ns)
                if el is not None:
                    srgb = el.find('a:srgbClr', ns)
                    sys_clr = el.find('a:sysClr', ns)
                    hex_val = None
                    if srgb is not None:
                        hex_val = srgb.get('val')
                    elif sys_clr is not None:
                        hex_val = sys_clr.get('lastClr')
                    if hex_val and len(hex_val) == 6:
                        if attr == 'bg':
                            bg_color = f"#{hex_val}"
                        elif attr == 'text':
                            text_color = f"#{hex_val}"
                        elif attr == 'heading':
                            heading_color = f"#{hex_val}"
                        elif attr == 'accent':
                            accent_color = f"#{hex_val}"

        # Font theme
        font_scheme = theme.find('.//a:fontScheme', ns)
        if font_scheme is not None:
            major = font_scheme.find('a:majorFont/a:latin', ns)
            if major is not None:
                typeface = major.get('typeface')
                if typeface and typeface != '':
                    font_family = f"'{typeface}', system-ui, -apple-system, sans-serif"

    except Exception:
        pass

    # If background is dark, ensure text colors are light
    bg_rgb = _hex_to_rgb(bg_color)
    bg_lum = _luminance(*bg_rgb)
    if bg_lum < 0.4:
        # Dark background — ensure text/heading are light
        txt_rgb = _hex_to_rgb(text_color)
        if _luminance(*txt_rgb) < 0.5:
            text_color = "#e0e0e8"
        hdg_rgb = _hex_to_rgb(heading_color)
        if _luminance(*hdg_rgb) < 0.5:
            heading_color = "#ffffff"

    # Build card_bg from accent with transparency
    acc_rgb = _hex_to_rgb(accent_color)
    card_bg = f"rgba({acc_rgb[0]}, {acc_rgb[1]}, {acc_rgb[2]}, 0.07)"

    return {
        "font_family": font_family,
        "bg_color": bg_color,
        "text_color": text_color,
        "heading_color": heading_color,
        "accent_color": accent_color,
        "card_bg": card_bg,
        "style_overrides": "",
    }
