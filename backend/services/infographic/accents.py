"""L4-accent — a tasteful, hand-authored decorative "glow crystal" spot that an
L1/L2/L3 infographic can OPTIONALLY carry (design-corpus lever 6 + the reference
33.01 "diffusion glow-crystal (L4 accent)").

Intent shape (from the plan): allow ONE textless decorative spot inside an
otherwise data-driven infographic — gated, off by default, "all numbers stay
DOM/SVG; the accent is decorative only." A diffusion (Klein) hero object is the
richest form, but it is heavy, Apple-Silicon-only and un-verifiable offline, so
the DEFAULT here is a self-contained SVG accent that:

  - renders everywhere the infographic renders (in-app Shadow DOM, the export
    Playwright page, and inline inside an L3 scene SVG) with ZERO new runtime
    dependency,
  - uses `currentColor` so the design-system accent restyle recolors it for free
    (the L2 `.ib-accent-spot` class sets `color:var(--ib-accent)`),
  - uses ONLY plain shapes (circles / paths) — no gradients or filters — so it
    survives the DOMPurify svg profile untouched,
  - is `pointer-events:none` and lives in an empty corner slot at `z-index:-1`
    (behind all content) so it can NEVER collide with a label (the field
    overlap complaint).

A Klein raster can later REPLACE the SVG spot for the same slot without touching
callers — the accent is decorative and coordinate-free either way (Hard Rule
§2.1/§2.2). Everything fails open: an unknown variant yields "".
"""
from __future__ import annotations

import hashlib

# Deterministic accent-variant order (seeded per generation so a notebook's
# accented infographics don't all show the same spot).
ACCENT_VARIANTS = ("crystal", "orb", "bloom", "prism")


def pick_accent(seed: str) -> str:
    """Deterministically choose an accent variant from a seed string."""
    key = (seed or "").encode("utf-8", "ignore")
    idx = int(hashlib.sha1(key).hexdigest(), 16) % len(ACCENT_VARIANTS)
    return ACCENT_VARIANTS[idx]


def _crystal(c: str) -> str:
    return (
        f'<circle cx="50" cy="52" r="40" fill="{c}" fill-opacity="0.05"/>'
        f'<circle cx="50" cy="52" r="28" fill="{c}" fill-opacity="0.07"/>'
        f'<circle cx="50" cy="52" r="18" fill="{c}" fill-opacity="0.10"/>'
        f'<path d="M50 16 L72 42 L58 84 L42 84 L28 42 Z" fill="{c}" fill-opacity="0.30" '
        f'stroke="{c}" stroke-width="2.4" stroke-linejoin="round"/>'
        f'<path d="M28 42 H72 M50 16 V84 M50 16 L40 42 M50 16 L60 42" fill="none" '
        f'stroke="{c}" stroke-width="1.4" stroke-opacity="0.55"/>'
        f'<path d="M80 24 l2.5 7 l7 2.5 l-7 2.5 l-2.5 7 l-2.5 -7 l-7 -2.5 l7 -2.5 z" '
        f'fill="{c}" fill-opacity="0.75"/>'
    )


def _orb(c: str) -> str:
    return (
        f'<circle cx="50" cy="50" r="42" fill="{c}" fill-opacity="0.05"/>'
        f'<circle cx="50" cy="50" r="30" fill="{c}" fill-opacity="0.08"/>'
        f'<circle cx="50" cy="50" r="20" fill="{c}" fill-opacity="0.16" '
        f'stroke="{c}" stroke-width="2" stroke-opacity="0.5"/>'
        f'<circle cx="43" cy="43" r="6" fill="{c}" fill-opacity="0.40"/>'
        f'<path d="M80 28 l2 6 l6 2 l-6 2 l-2 6 l-2 -6 l-6 -2 l6 -2 z" fill="{c}" fill-opacity="0.7"/>'
        f'<circle cx="24" cy="72" r="3" fill="{c}" fill-opacity="0.6"/>'
    )


def _bloom(c: str) -> str:
    petals = "".join(
        f'<ellipse cx="50" cy="30" rx="9" ry="20" fill="{c}" fill-opacity="0.16" '
        f'stroke="{c}" stroke-width="1.6" stroke-opacity="0.4" '
        f'transform="rotate({i * 60} 50 50)"/>'
        for i in range(6)
    )
    return (
        f'<circle cx="50" cy="50" r="42" fill="{c}" fill-opacity="0.05"/>'
        f'{petals}'
        f'<circle cx="50" cy="50" r="9" fill="{c}" fill-opacity="0.5"/>'
    )


def _prism(c: str) -> str:
    return (
        f'<circle cx="50" cy="52" r="40" fill="{c}" fill-opacity="0.05"/>'
        f'<path d="M50 20 L78 74 L22 74 Z" fill="{c}" fill-opacity="0.22" '
        f'stroke="{c}" stroke-width="2.4" stroke-linejoin="round"/>'
        f'<path d="M50 20 V74 M50 20 L36 74 M50 20 L64 74" fill="none" '
        f'stroke="{c}" stroke-width="1.4" stroke-opacity="0.5"/>'
        f'<path d="M62 48 h20 M62 56 h16 M62 64 h12" stroke="{c}" stroke-width="2" '
        f'stroke-opacity="0.55" stroke-linecap="round"/>'
    )


_VARIANTS = {
    "crystal": _crystal,
    "orb": _orb,
    "bloom": _bloom,
    "prism": _prism,
}


def accent_markup(variant: str, color: str = "currentColor") -> str:
    """Inner SVG markup for an accent variant drawn in a 0..100 box. Unknown
    variant -> "" (fail open)."""
    fn = _VARIANTS.get((variant or "").strip().lower())
    return fn(color) if fn else ""


def accent_spot_html(variant: str, corner: str = "tr") -> str:
    """A corner-slotted `.ib-accent-spot` div wrapping the accent SVG, for
    injection into an L2 `body_html`. Uses `currentColor` so the design-system
    accent restyle recolors it. Empty for an unknown variant."""
    inner = accent_markup(variant, "currentColor")
    if not inner:
        return ""
    corner = corner if corner in ("tr", "br", "bl") else "tr"
    return (
        f'<div class="ib-accent-spot ib-accent-spot--{corner}" aria-hidden="true">'
        f'<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">{inner}</svg>'
        f'</div>'
    )
