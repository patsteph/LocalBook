"""Infographic restyle tokens — the "one stylesheet, restyle is free" rule
(plan §2.4) made operational.

A restyle is expressed purely as a small set of CSS-variable overrides layered
AFTER the hand-authored `INFOGRAPHIC_L2_CSS`. Because the design tokens live in
`.ib { --ib-* }`, a later `.ib { --ib-accent: … }` rule wins (equal specificity,
later source order) and cascades through every element that reads the variable —
so an accent / tone / density switch recolors the whole artifact with NO
regeneration and NO change to the byte-identical base stylesheet.

MIRROR: this module is mirrored by the frontend at
`src/components/artifact/renderers/infographicRestyle.ts` (`buildRestyleCss`).
The Shadow-DOM renderer injects the TS copy on screen; the export pipeline
(`artifact_renderer._build_infographic_page`) injects THIS copy so a saved
PNG/PDF/HTML matches what the user restyled on screen. Keep the two token maps
in sync (same as the design_system.py <-> infographicDesignSystem.ts pair).

`restyle_css(style)` never raises — an unknown/empty style yields "" (the base
stylesheet stands alone, i.e. the coral/paper default).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Accent palettes — override the coral trio (accent / soft fill / glow).
# "coral" is the default and intentionally emits NO override.
_ACCENTS: Dict[str, Dict[str, str]] = {
    "coral": {},
    "blue": {
        "--ib-accent": "#2563eb",
        "--ib-accent-soft": "rgba(37,99,235,.16)",
        "--ib-glow": "rgba(37,99,235,.42)",
    },
    "emerald": {
        "--ib-accent": "#0f9d70",
        "--ib-accent-soft": "rgba(15,157,112,.16)",
        "--ib-glow": "rgba(15,157,112,.42)",
    },
    "violet": {
        "--ib-accent": "#7c3aed",
        "--ib-accent-soft": "rgba(124,58,237,.16)",
        "--ib-glow": "rgba(124,58,237,.42)",
    },
    "amber": {
        "--ib-accent": "#d97706",
        "--ib-accent-soft": "rgba(217,119,6,.16)",
        "--ib-glow": "rgba(217,119,6,.42)",
    },
    "slate": {
        "--ib-accent": "#475569",
        "--ib-accent-soft": "rgba(71,85,105,.16)",
        "--ib-glow": "rgba(71,85,105,.42)",
    },
}

# Tone — override the paper/ink surface tokens. "paper" is the default (no
# override). "light" brightens; "dark" is a full ink inversion.
_TONES: Dict[str, Dict[str, str]] = {
    "paper": {},
    "light": {
        "--ib-bg": "#ffffff",
        "--ib-card-b": "#f5f3ef",
        "--ib-line": "#e6e4df",
    },
    "dark": {
        "--ib-ink": "#f2f0ec",
        "--ib-ink-soft": "#cbc8c2",
        "--ib-muted": "#938f88",
        "--ib-bg": "#1b1a18",
        "--ib-card-a": "#26241f",
        "--ib-card-b": "#201e1b",
        "--ib-line": "#3a3833",
        "--ib-bracket": "rgba(216,213,205,.5)",
    },
}

# Density/scale — a zoom on the .ib root. Deterministic, works in both the
# WebView Shadow DOM and the Playwright export page. "cozy" is the default.
_SCALES: Dict[str, str] = {
    "cozy": "",
    "compact": "0.9",
    "roomy": "1.12",
}

ACCENT_IDS = tuple(_ACCENTS.keys())
TONE_IDS = tuple(_TONES.keys())
SCALE_IDS = tuple(_SCALES.keys())


def restyle_css(style: Optional[Dict[str, Any]]) -> str:
    """Build the CSS-variable override block for a restyle. Returns "" for an
    empty/absent/default style. Fail-open: bad input degrades to "" so the base
    stylesheet (coral/paper) stands unchanged."""
    if not isinstance(style, dict):
        return ""

    root: Dict[str, str] = {}

    accent = _ACCENTS.get(str(style.get("accent") or "coral"))
    if accent:
        root.update(accent)

    tone_id = str(style.get("tone") or "paper")
    tone = _TONES.get(tone_id)
    if tone:
        root.update(tone)

    scale = _SCALES.get(str(style.get("scale") or "cozy"), "")
    if scale:
        root["zoom"] = scale

    blocks: list[str] = []
    if root:
        decls = "".join(f"{k}:{v};" for k, v in root.items())
        blocks.append(f".ib{{{decls}}}")

    # Dark tone: the base dotted background is a dark dot on a light field; swap
    # it for a light dot so the texture survives the inversion.
    if tone_id == "dark":
        blocks.append(
            ".ib{background-image:radial-gradient("
            "rgba(220,215,205,.10) 1px, transparent 1.5px);}"
        )

    # Glow off — flatten the hero glow to the neutral card border.
    if style.get("glow") is False:
        blocks.append(".ib-glow{box-shadow:none;border-color:var(--ib-line);}")

    return "".join(blocks)
