"""Hand-authored sketch stickers for the L3 "Scene" lane (plan §4, Phase-4 proof).

The L3 lane composes a hand-drawn whiteboard scene from a curated, reusable set
of DRAWN OBJECTS — no diffusion, no generation (that is the gated Phase 5). Each
sticker here is an ORIGINAL inline `<svg>` fragment authored for this project in
a chunky, rounded, marker-drawn style; the sketch "wobble" comes from an SVG
`feTurbulence`/`feDisplacementMap` roughen filter applied at compose time
(`scene.py`), so the look needs ZERO runtime dependency (no rough.js, no
Excalidraw, no icon package).

LICENSING: every path below is hand-authored for LocalBook (original work,
same license as the repo). A few silhouettes are visually inspired by the Lucide
line-art family (ISC, already a dep) but the path data is redrawn here — nothing
is copied verbatim and no third-party asset ships. There is nothing to attribute.

Coordinate space: each sticker draws inside a 100x100 box (origin top-left). The
composer wraps the returned inner markup in a `<g transform="translate/scale">`
so a sticker can be placed and sized anywhere. `render_sticker(name)` fails open
to a neutral box glyph so an unknown name from the graph never breaks the render
(HARD RULE §2.5).
"""
from __future__ import annotations

# ── palette (marker tints; the ink is near-black) ──────────────────────
INK = "#2b2a28"
INK_SOFT = "#4a4844"
BLUE = "#3b6fd4"
GREEN = "#3f9d5a"
TEAL = "#37a9a0"
VIOLET = "#8a6ad4"
AMBER = "#e0a63a"
CORAL = "#e0503a"
PAPER = "#fbfaf7"
STEEL = "#5b7aa8"

# name -> tint used for the node label under the sticker (matches the target's
# colored captions). Falls open to INK_SOFT.
STICKER_TINT: dict[str, str] = {
    "document": BLUE,
    "puzzle": VIOLET,
    "hook": GREEN,
    "robot": STEEL,
    "plug": TEAL,
    "package": AMBER,
    "laptop": INK_SOFT,
    "monitor": INK_SOFT,
    "conveyor": STEEL,
    "machine": BLUE,
    "note": AMBER,
    "gear": INK_SOFT,
    "box": AMBER,
}


def _sw(n: float = 2.6) -> str:
    return f'stroke="{INK}" stroke-width="{n}" fill="none" stroke-linecap="round" stroke-linejoin="round"'


# ── the sticker set (12 reusable objects) ──────────────────────────────
def _document() -> str:
    return (
        f'<path d="M28 12 h32 l14 14 v62 h-46 z" fill="{PAPER}" {_sw()}/>'
        f'<path d="M60 12 v14 h14" {_sw()}/>'
        f'<path d="M36 44 h30 M36 54 h30 M36 64 h22" {_sw(2.2)}/>'
    )


def _puzzle() -> str:
    return (
        f'<path d="M30 34 h14 a7 7 0 1 1 14 0 h12 v12 a7 7 0 1 1 0 14 v14 h-14 '
        f'a7 7 0 1 0 -14 0 h-12 v-12 a7 7 0 1 0 0 -14 z" '
        f'fill="{VIOLET}" fill-opacity="0.30" {_sw()}/>'
    )


def _hook() -> str:
    return (
        f'<path d="M50 14 v20 a16 16 0 1 1 -16 16" {_sw(3.2)}/>'
        f'<circle cx="50" cy="14" r="4" fill="{INK}"/>'
    )


def _robot() -> str:
    return (
        f'<rect x="24" y="30" width="52" height="44" rx="12" '
        f'fill="{PAPER}" {_sw()}/>'
        f'<path d="M50 18 v12" {_sw()}/><circle cx="50" cy="15" r="4" fill="{STEEL}"/>'
        f'<circle cx="40" cy="50" r="6" fill="{STEEL}"/>'
        f'<circle cx="60" cy="50" r="6" fill="{STEEL}"/>'
        f'<path d="M40 64 q10 6 20 0" {_sw(2.2)}/>'
    )


def _plug() -> str:
    return (
        f'<path d="M38 22 v16 M62 22 v16" {_sw(3)}/>'
        f'<rect x="30" y="38" width="40" height="26" rx="8" '
        f'fill="{TEAL}" fill-opacity="0.30" {_sw()}/>'
        f'<path d="M50 64 v14 a10 10 0 0 0 10 10 h6" {_sw()}/>'
    )


def _package() -> str:
    return (
        f'<rect x="24" y="34" width="52" height="48" rx="4" '
        f'fill="{AMBER}" fill-opacity="0.30" {_sw()}/>'
        f'<path d="M50 34 v48 M24 52 h52" {_sw()}/>'
        # bow
        f'<path d="M50 34 q-14 -14 -20 -2 q-2 8 20 2 q22 6 20 -2 q-6 -12 -20 2 z" '
        f'fill="{BLUE}" fill-opacity="0.35" {_sw(2.2)}/>'
    )


def _laptop() -> str:
    return (
        f'<rect x="26" y="26" width="48" height="34" rx="4" fill="{PAPER}" {_sw()}/>'
        f'<path d="M18 72 h64 l-6 -12 h-52 z" fill="{PAPER}" {_sw()}/>'
        # smiley screen
        f'<circle cx="42" cy="40" r="2.4" fill="{INK}"/>'
        f'<circle cx="58" cy="40" r="2.4" fill="{INK}"/>'
        f'<path d="M42 48 q8 6 16 0" {_sw(2)}/>'
    )


def _monitor() -> str:
    return (
        f'<rect x="20" y="24" width="46" height="34" rx="4" fill="{PAPER}" {_sw()}/>'
        f'<path d="M43 58 v10 M32 78 h22" {_sw()}/>'
        f'<rect x="70" y="30" width="12" height="40" rx="3" fill="{PAPER}" {_sw()}/>'
        f'<circle cx="36" cy="38" r="2.2" fill="{INK}"/>'
        f'<circle cx="50" cy="38" r="2.2" fill="{INK}"/>'
        f'<path d="M36 46 q7 5 14 0" {_sw(2)}/>'
    )


def _conveyor() -> str:
    # wide belt with rollers, feeding into a machine block on the right
    return (
        f'<rect x="10" y="52" width="60" height="18" rx="9" fill="{PAPER}" {_sw()}/>'
        f'<circle cx="22" cy="61" r="5" fill="{PAPER}" {_sw(2.2)}/>'
        f'<circle cx="40" cy="61" r="5" fill="{PAPER}" {_sw(2.2)}/>'
        f'<circle cx="58" cy="61" r="5" fill="{PAPER}" {_sw(2.2)}/>'
        f'<path d="M24 84 l6 -14 M46 84 l6 -14" {_sw(2.2)}/>'
        # machine
        f'<rect x="70" y="26" width="24" height="44" rx="4" '
        f'fill="{BLUE}" fill-opacity="0.28" {_sw()}/>'
        f'<circle cx="82" cy="20" r="5" fill="{AMBER}" fill-opacity="0.7" {_sw(2)}/>'
        f'<path d="M75 40 h14 M75 48 h14" {_sw(2)}/>'
    )


def _machine() -> str:
    return (
        f'<rect x="28" y="30" width="44" height="50" rx="6" '
        f'fill="{BLUE}" fill-opacity="0.28" {_sw()}/>'
        f'<circle cx="50" cy="22" r="6" fill="{AMBER}" fill-opacity="0.7" {_sw(2.2)}/>'
        f'<path d="M50 16 v-4 M42 18 l-3 -3 M58 18 l3 -3" {_sw(2)}/>'
        f'<path d="M36 46 h28 M36 56 h28" {_sw(2.2)}/>'
        f'<circle cx="62" cy="70" r="3" fill="{CORAL}" fill-opacity="0.7"/>'
    )


def _note() -> str:
    return (
        f'<path d="M24 20 h52 v46 l-12 12 h-40 z" fill="{AMBER}" '
        f'fill-opacity="0.22" {_sw()}/>'
        f'<path d="M64 78 v-12 h12" {_sw(2.2)}/>'
        # three check rows
        f'<path d="M32 34 l4 4 l7 -8" stroke="{GREEN}" stroke-width="2.6" fill="none" '
        f'stroke-linecap="round" stroke-linejoin="round"/><path d="M48 36 h22" {_sw(2)}/>'
        f'<path d="M32 48 l4 4 l7 -8" stroke="{GREEN}" stroke-width="2.6" fill="none" '
        f'stroke-linecap="round" stroke-linejoin="round"/><path d="M48 50 h22" {_sw(2)}/>'
        f'<path d="M32 62 l4 4 l7 -8" stroke="{GREEN}" stroke-width="2.6" fill="none" '
        f'stroke-linecap="round" stroke-linejoin="round"/><path d="M48 64 h18" {_sw(2)}/>'
    )


def _gear() -> str:
    return (
        f'<circle cx="50" cy="50" r="18" fill="{PAPER}" {_sw()}/>'
        f'<circle cx="50" cy="50" r="7" fill="none" {_sw(2.2)}/>'
        f'<path d="M50 24 v8 M50 68 v8 M24 50 h8 M68 50 h8 '
        f'M32 32 l6 6 M62 62 l6 6 M68 32 l-6 6 M38 62 l-6 6" {_sw(2.4)}/>'
    )


def _box() -> str:
    return (
        f'<path d="M50 18 l28 14 v34 l-28 14 l-28 -14 v-34 z" '
        f'fill="{AMBER}" fill-opacity="0.26" {_sw()}/>'
        f'<path d="M22 32 l28 14 l28 -14 M50 46 v34" {_sw()}/>'
    )


_STICKERS = {
    "document": _document, "puzzle": _puzzle, "hook": _hook, "robot": _robot,
    "plug": _plug, "package": _package, "laptop": _laptop, "monitor": _monitor,
    "conveyor": _conveyor, "machine": _machine, "note": _note, "gear": _gear,
    "box": _box,
}

# Fail-open glyph — a plain sketched box.
_FALLBACK = (
    f'<rect x="28" y="28" width="44" height="44" rx="6" fill="{PAPER}" {_sw()}/>'
)


def sticker_names() -> list[str]:
    """The allowlist a graph-generation prompt should offer the model."""
    return sorted(_STICKERS.keys())


def sticker_tint(name: str) -> str:
    return STICKER_TINT.get((name or "").strip().lower(), INK_SOFT)


def render_sticker(name: str) -> str:
    """Return the inner SVG for a sticker (drawn in a 0..100 box). Unknown
    name -> neutral box (never empty, never raises)."""
    fn = _STICKERS.get((name or "").strip().lower())
    return fn() if fn else _FALLBACK
