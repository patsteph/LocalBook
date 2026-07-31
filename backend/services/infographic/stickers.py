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

XML VALIDITY (HARD RULE §2.5 + the `test_l3_scene_and_every_sticker_are_valid_xml`
regression): every stroked+filled path passes its fill THROUGH the `_sw(n, fill=…)`
helper. NEVER put a separate `fill="…"` attribute on a path that also carries
`{_sw()}` — the helper already emits a `fill`, so a second one is a DUPLICATE
attribute that makes the fragment invalid XML (WKWebView then renders a broken
image box). Solid dots/marks that use NO stroke helper may carry their own single
`fill="…"`; `fill-opacity` is a distinct attribute and is always safe to add.
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
    # original set
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
    # tech / AI / RAG-explainer expansion
    "database": BLUE,
    "cloud": STEEL,
    "brain": VIOLET,
    "chip": BLUE,
    "server": STEEL,
    "network": TEAL,
    "key": AMBER,
    "lock": STEEL,
    "search": BLUE,
    "funnel": VIOLET,
    "layers": BLUE,
    "vector": CORAL,
    "arrow": INK_SOFT,
    "lightbulb": AMBER,
    "chat": GREEN,
    "terminal": INK_SOFT,
    "sync": TEAL,
    "warning": AMBER,
    "star": AMBER,
    "globe": BLUE,
    # general-explainer expansion (2.2.0)
    "shield": STEEL,
    "rocket": CORAL,
    "magnet": CORAL,
    "scale": INK_SOFT,
    "target": CORAL,
    "flag": GREEN,
    "mappin": CORAL,
    "calendar": BLUE,
    "clock": STEEL,
    "mail": BLUE,
    "bell": AMBER,
    "tag": GREEN,
    "bookmark": CORAL,
    "graph_line": GREEN,
    "pie": BLUE,
    "table": STEEL,
    "code": INK_SOFT,
    "git_branch": VIOLET,
    "eye": BLUE,
    "compass": TEAL,
    "flask": TEAL,
    "atom": VIOLET,
}


def _sw(n: float = 2.6, fill: str = "none") -> str:
    """Shared stroke attributes for a drawn path. `fill` rides THROUGH here so a
    filled shape never also carries a second (duplicate) `fill` attribute."""
    return (
        f'stroke="{INK}" stroke-width="{n}" fill="{fill}" '
        'stroke-linecap="round" stroke-linejoin="round"'
    )


def _cw(color: str, n: float = 2.6) -> str:
    """Colored-stroke variant (e.g. a green check). Emits its own single
    `stroke`/`fill` so it is NEVER combined with `_sw()` on the same element."""
    return (
        f'stroke="{color}" stroke-width="{n}" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"'
    )


# ── the original sticker set ───────────────────────────────────────────
def _document() -> str:
    return (
        f'<path d="M28 12 h32 l14 14 v62 h-46 z" {_sw(fill=PAPER)}/>'
        f'<path d="M60 12 v14 h14" {_sw()}/>'
        f'<path d="M36 44 h30 M36 54 h30 M36 64 h22" {_sw(2.2)}/>'
    )


def _puzzle() -> str:
    return (
        f'<path d="M30 34 h14 a7 7 0 1 1 14 0 h12 v12 a7 7 0 1 1 0 14 v14 h-14 '
        f'a7 7 0 1 0 -14 0 h-12 v-12 a7 7 0 1 0 0 -14 z" '
        f'fill-opacity="0.30" {_sw(fill=VIOLET)}/>'
    )


def _hook() -> str:
    return (
        f'<path d="M50 14 v20 a16 16 0 1 1 -16 16" {_sw(3.2)}/>'
        f'<circle cx="50" cy="14" r="4" fill="{INK}"/>'
    )


def _robot() -> str:
    return (
        f'<rect x="24" y="30" width="52" height="44" rx="12" {_sw(fill=PAPER)}/>'
        f'<path d="M50 18 v12" {_sw()}/><circle cx="50" cy="15" r="4" fill="{STEEL}"/>'
        f'<circle cx="40" cy="50" r="6" fill="{STEEL}"/>'
        f'<circle cx="60" cy="50" r="6" fill="{STEEL}"/>'
        f'<path d="M40 64 q10 6 20 0" {_sw(2.2)}/>'
    )


def _plug() -> str:
    return (
        f'<path d="M38 22 v16 M62 22 v16" {_sw(3)}/>'
        f'<rect x="30" y="38" width="40" height="26" rx="8" '
        f'fill-opacity="0.30" {_sw(fill=TEAL)}/>'
        f'<path d="M50 64 v14 a10 10 0 0 0 10 10 h6" {_sw()}/>'
    )


def _package() -> str:
    return (
        f'<rect x="24" y="34" width="52" height="48" rx="4" '
        f'fill-opacity="0.30" {_sw(fill=AMBER)}/>'
        f'<path d="M50 34 v48 M24 52 h52" {_sw()}/>'
        # bow
        f'<path d="M50 34 q-14 -14 -20 -2 q-2 8 20 2 q22 6 20 -2 q-6 -12 -20 2 z" '
        f'fill-opacity="0.35" {_sw(2.2, fill=BLUE)}/>'
    )


def _laptop() -> str:
    return (
        f'<rect x="26" y="26" width="48" height="34" rx="4" {_sw(fill=PAPER)}/>'
        f'<path d="M18 72 h64 l-6 -12 h-52 z" {_sw(fill=PAPER)}/>'
        # smiley screen
        f'<circle cx="42" cy="40" r="2.4" fill="{INK}"/>'
        f'<circle cx="58" cy="40" r="2.4" fill="{INK}"/>'
        f'<path d="M42 48 q8 6 16 0" {_sw(2)}/>'
    )


def _monitor() -> str:
    return (
        f'<rect x="20" y="24" width="46" height="34" rx="4" {_sw(fill=PAPER)}/>'
        f'<path d="M43 58 v10 M32 78 h22" {_sw()}/>'
        f'<rect x="70" y="30" width="12" height="40" rx="3" {_sw(fill=PAPER)}/>'
        f'<circle cx="36" cy="38" r="2.2" fill="{INK}"/>'
        f'<circle cx="50" cy="38" r="2.2" fill="{INK}"/>'
        f'<path d="M36 46 q7 5 14 0" {_sw(2)}/>'
    )


def _conveyor() -> str:
    # wide belt with rollers, feeding into a machine block on the right
    return (
        f'<rect x="10" y="52" width="60" height="18" rx="9" {_sw(fill=PAPER)}/>'
        f'<circle cx="22" cy="61" r="5" {_sw(2.2, fill=PAPER)}/>'
        f'<circle cx="40" cy="61" r="5" {_sw(2.2, fill=PAPER)}/>'
        f'<circle cx="58" cy="61" r="5" {_sw(2.2, fill=PAPER)}/>'
        f'<path d="M24 84 l6 -14 M46 84 l6 -14" {_sw(2.2)}/>'
        # machine
        f'<rect x="70" y="26" width="24" height="44" rx="4" '
        f'fill-opacity="0.28" {_sw(fill=BLUE)}/>'
        f'<circle cx="82" cy="20" r="5" fill-opacity="0.7" {_sw(2, fill=AMBER)}/>'
        f'<path d="M75 40 h14 M75 48 h14" {_sw(2)}/>'
    )


def _machine() -> str:
    return (
        f'<rect x="28" y="30" width="44" height="50" rx="6" '
        f'fill-opacity="0.28" {_sw(fill=BLUE)}/>'
        f'<circle cx="50" cy="22" r="6" fill-opacity="0.7" {_sw(2.2, fill=AMBER)}/>'
        f'<path d="M50 16 v-4 M42 18 l-3 -3 M58 18 l3 -3" {_sw(2)}/>'
        f'<path d="M36 46 h28 M36 56 h28" {_sw(2.2)}/>'
        f'<circle cx="62" cy="70" r="3" fill="{CORAL}" fill-opacity="0.7"/>'
    )


def _note() -> str:
    return (
        f'<path d="M24 20 h52 v46 l-12 12 h-40 z" '
        f'fill-opacity="0.22" {_sw(fill=AMBER)}/>'
        f'<path d="M64 78 v-12 h12" {_sw(2.2)}/>'
        # three check rows
        f'<path d="M32 34 l4 4 l7 -8" {_cw(GREEN)}/>'
        f'<path d="M48 36 h22" {_sw(2)}/>'
        f'<path d="M32 48 l4 4 l7 -8" {_cw(GREEN)}/>'
        f'<path d="M48 50 h22" {_sw(2)}/>'
        f'<path d="M32 62 l4 4 l7 -8" {_cw(GREEN)}/>'
        f'<path d="M48 64 h18" {_sw(2)}/>'
    )


def _gear() -> str:
    return (
        f'<circle cx="50" cy="50" r="18" {_sw(fill=PAPER)}/>'
        f'<circle cx="50" cy="50" r="7" {_sw(2.2)}/>'
        f'<path d="M50 24 v8 M50 68 v8 M24 50 h8 M68 50 h8 '
        f'M32 32 l6 6 M62 62 l6 6 M68 32 l-6 6 M38 62 l-6 6" {_sw(2.4)}/>'
    )


def _box() -> str:
    return (
        f'<path d="M50 18 l28 14 v34 l-28 14 l-28 -14 v-34 z" '
        f'fill-opacity="0.26" {_sw(fill=AMBER)}/>'
        f'<path d="M22 32 l28 14 l28 -14 M50 46 v34" {_sw()}/>'
    )


# ── tech / AI / RAG-explainer expansion ────────────────────────────────
def _database() -> str:
    return (
        f'<ellipse cx="50" cy="28" rx="24" ry="9" '
        f'fill-opacity="0.30" {_sw(fill=BLUE)}/>'
        f'<path d="M26 28 v44 q0 9 24 9 q24 0 24 -9 v-44" {_sw()}/>'
        f'<path d="M26 50 q0 9 24 9 q24 0 24 -9" {_sw(2.2)}/>'
    )


def _cloud() -> str:
    return (
        f'<path d="M32 70 a15 15 0 0 1 -2 -30 a19 19 0 0 1 36 -4 '
        f'a13 13 0 0 1 4 34 z" fill-opacity="0.26" {_sw(fill=STEEL)}/>'
    )


def _brain() -> str:
    return (
        f'<path d="M48 24 a13 13 0 0 0 -14 12 a12 12 0 0 0 -3 22 '
        f'a13 13 0 0 0 17 12 z" fill-opacity="0.24" {_sw(fill=VIOLET)}/>'
        f'<path d="M52 24 a13 13 0 0 1 14 12 a12 12 0 0 1 3 22 '
        f'a13 13 0 0 1 -17 12 z" fill-opacity="0.24" {_sw(fill=VIOLET)}/>'
        f'<path d="M50 26 v50 M40 42 q6 4 0 8 M60 42 q-6 4 0 8" {_sw(2)}/>'
    )


def _chip() -> str:
    return (
        f'<rect x="30" y="30" width="40" height="40" rx="8" '
        f'fill-opacity="0.26" {_sw(fill=BLUE)}/>'
        f'<rect x="42" y="42" width="16" height="16" rx="3" {_sw(2.2)}/>'
        f'<path d="M40 24 v8 M50 24 v8 M60 24 v8 M40 68 v8 M50 68 v8 M60 68 v8 '
        f'M24 40 h8 M24 50 h8 M24 60 h8 M68 40 h8 M68 50 h8 M68 60 h8" {_sw(2.2)}/>'
    )


def _server() -> str:
    return (
        f'<rect x="28" y="26" width="44" height="20" rx="5" '
        f'fill-opacity="0.26" {_sw(fill=STEEL)}/>'
        f'<rect x="28" y="54" width="44" height="20" rx="5" '
        f'fill-opacity="0.26" {_sw(fill=STEEL)}/>'
        f'<path d="M48 36 h16 M48 64 h16" {_sw(2)}/>'
        f'<circle cx="38" cy="36" r="2.6" fill="{GREEN}"/>'
        f'<circle cx="38" cy="64" r="2.6" fill="{GREEN}"/>'
    )


def _network() -> str:
    return (
        f'<path d="M43 44 l-13 -12 M58 45 l13 -13 M51 60 l1 14" {_sw(2.2)}/>'
        f'<circle cx="50" cy="50" r="10" fill-opacity="0.30" {_sw(fill=TEAL)}/>'
        f'<circle cx="26" cy="28" r="7" fill-opacity="0.30" {_sw(fill=TEAL)}/>'
        f'<circle cx="74" cy="30" r="7" fill-opacity="0.30" {_sw(fill=TEAL)}/>'
        f'<circle cx="52" cy="80" r="7" fill-opacity="0.30" {_sw(fill=TEAL)}/>'
    )


def _key() -> str:
    return (
        f'<circle cx="36" cy="42" r="14" fill-opacity="0.26" {_sw(fill=AMBER)}/>'
        f'<circle cx="36" cy="42" r="5" {_sw(2.2)}/>'
        f'<path d="M47 51 l22 22 M63 67 l6 -6 M55 59 l6 -6" {_sw(3)}/>'
    )


def _lock() -> str:
    return (
        f'<rect x="30" y="46" width="40" height="34" rx="7" '
        f'fill-opacity="0.28" {_sw(fill=STEEL)}/>'
        f'<path d="M38 46 v-8 a12 12 0 0 1 24 0 v8" {_sw(3)}/>'
        f'<path d="M50 60 v8" {_sw(2.6)}/>'
        f'<circle cx="50" cy="59" r="4" fill="{INK}"/>'
    )


def _search() -> str:
    return (
        f'<circle cx="44" cy="44" r="18" fill-opacity="0.22" {_sw(fill=BLUE)}/>'
        f'<path d="M58 58 l18 18" {_sw(4)}/>'
    )


def _funnel() -> str:
    return (
        f'<path d="M26 26 h48 l-16 22 v24 l-16 8 v-32 z" '
        f'fill-opacity="0.26" {_sw(fill=VIOLET)}/>'
    )


def _layers() -> str:
    return (
        f'<path d="M50 24 l26 13 l-26 13 l-26 -13 z" '
        f'fill-opacity="0.28" {_sw(fill=BLUE)}/>'
        f'<path d="M24 50 l26 13 l26 -13" {_sw(2.4)}/>'
        f'<path d="M24 62 l26 13 l26 -13" {_sw(2.4)}/>'
    )


def _vector() -> str:
    # embedding "dot matrix" — solid dots (no stroke helper -> single fill each)
    dots = []
    grid = [
        (0.85, "coral"), (0.35, "ink"), (0.62, "coral"),
        (0.30, "ink"), (0.80, "coral"), (0.45, "ink"),
        (0.55, "coral"), (0.75, "coral"), (0.30, "ink"),
    ]
    for i, (op, kind) in enumerate(grid):
        r, c = divmod(i, 3)
        cx = 34 + c * 16
        cy = 34 + r * 16
        col = CORAL if kind == "coral" else INK
        dots.append(f'<circle cx="{cx}" cy="{cy}" r="5" fill="{col}" fill-opacity="{op}"/>')
    return "".join(dots)


def _arrow() -> str:
    return (
        f'<path d="M22 50 h44" {_sw(5)}/>'
        f'<path d="M56 36 l18 14 l-18 14" {_sw(5)}/>'
    )


def _lightbulb() -> str:
    return (
        f'<path d="M50 20 a20 20 0 0 1 12 36 q-3 3 -3 8 h-18 q0 -5 -3 -8 '
        f'a20 20 0 0 1 12 -36 z" fill-opacity="0.26" {_sw(fill=AMBER)}/>'
        f'<path d="M42 72 h16 M44 80 h12" {_sw(2.6)}/>'
    )


def _chat() -> str:
    return (
        f'<path d="M24 30 h52 a8 8 0 0 1 8 8 v22 a8 8 0 0 1 -8 8 h-30 '
        f'l-14 12 v-12 a8 8 0 0 1 -8 -8 v-22 a8 8 0 0 1 8 -8 z" '
        f'fill-opacity="0.24" {_sw(fill=GREEN)}/>'
        f'<path d="M38 46 h24 M38 54 h16" {_sw(2.2)}/>'
    )


def _terminal() -> str:
    return (
        f'<rect x="22" y="26" width="56" height="46" rx="6" '
        f'fill-opacity="0.24" {_sw(fill=INK_SOFT)}/>'
        f'<path d="M32 40 l8 7 l-8 7 M48 54 h16" {_cw(GREEN, 2.8)}/>'
    )


def _sync() -> str:
    return (
        f'<path d="M28 44 a22 22 0 0 1 40 -8" {_sw(3.2)}/>'
        f'<path d="M72 56 a22 22 0 0 1 -40 8" {_sw(3.2)}/>'
        f'<path d="M68 24 v14 h-14" {_sw(2.8)}/>'
        f'<path d="M32 76 v-14 h14" {_sw(2.8)}/>'
    )


def _warning() -> str:
    return (
        f'<path d="M50 24 l30 52 h-60 z" fill-opacity="0.26" {_sw(fill=AMBER)}/>'
        f'<path d="M50 44 v16" {_sw(3.2)}/>'
        f'<circle cx="50" cy="68" r="2.6" fill="{INK}"/>'
    )


def _star() -> str:
    return (
        f'<path d="M50 22 l8 20 l22 2 l-17 14 l6 22 l-19 -12 l-19 12 l6 -22 '
        f'l-17 -14 l22 -2 z" fill-opacity="0.30" {_sw(fill=AMBER)}/>'
    )


def _globe() -> str:
    return (
        f'<circle cx="50" cy="50" r="26" fill-opacity="0.20" {_sw(fill=BLUE)}/>'
        f'<path d="M24 50 h52 M50 24 v52" {_sw(2.2)}/>'
        f'<path d="M50 24 a26 16 0 0 1 0 52 a26 16 0 0 1 0 -52" {_sw(2.2)}/>'
    )


# ── general-explainer expansion (2.2.0) ────────────────────────────────
def _shield() -> str:
    return (
        f'<path d="M50 18 l26 9 v20 q0 26 -26 35 q-26 -9 -26 -35 v-20 z" '
        f'fill-opacity="0.24" {_sw(fill=STEEL)}/>'
        f'<path d="M40 50 l7 8 l14 -16" {_cw(GREEN, 3)}/>'
    )


def _rocket() -> str:
    return (
        f'<path d="M50 16 q15 11 15 34 l-4 16 h-22 l-4 -16 q0 -23 15 -34 z" '
        f'fill-opacity="0.26" {_sw(fill=CORAL)}/>'
        f'<circle cx="50" cy="40" r="6" {_sw(2.2, fill=PAPER)}/>'
        f'<path d="M35 60 l-9 12 l13 -4 M65 60 l9 12 l-13 -4" {_sw(2.4)}/>'
        f'<path d="M46 78 q4 8 8 0" {_cw(AMBER, 3)}/>'
    )


def _magnet() -> str:
    return (
        f'<path d="M34 26 v22 a16 16 0 0 0 32 0 v-22" {_sw(6)}/>'
        f'<path d="M28 26 h12" {_cw(CORAL, 6)}/>'
        f'<path d="M60 26 h12" {_cw(BLUE, 6)}/>'
    )


def _scale() -> str:
    return (
        f'<path d="M50 22 v46 M38 72 h24" {_sw(3)}/>'
        f'<path d="M26 32 h48" {_sw(3)}/>'
        f'<circle cx="50" cy="22" r="3.4" fill="{INK}"/>'
        # hangers
        f'<path d="M26 32 l-6 16 M26 32 l6 16 M74 32 l-6 16 M74 32 l6 16" {_sw(2)}/>'
        # pans
        f'<path d="M14 48 a12 6 0 0 0 24 0 z" fill-opacity="0.24" {_sw(2.2, fill=AMBER)}/>'
        f'<path d="M62 48 a12 6 0 0 0 24 0 z" fill-opacity="0.24" {_sw(2.2, fill=AMBER)}/>'
    )


def _target() -> str:
    return (
        f'<circle cx="50" cy="50" r="26" fill-opacity="0.18" {_sw(fill=CORAL)}/>'
        f'<circle cx="50" cy="50" r="16" {_sw(2.4)}/>'
        f'<circle cx="50" cy="50" r="6" {_sw(2.2)}/>'
        f'<circle cx="50" cy="50" r="2.4" fill="{CORAL}"/>'
    )


def _flag() -> str:
    return (
        f'<path d="M32 18 v64" {_sw(3.2)}/>'
        f'<path d="M32 22 h36 q-9 9 0 18 h-36 z" '
        f'fill-opacity="0.30" {_sw(fill=GREEN)}/>'
    )


def _mappin() -> str:
    return (
        f'<path d="M50 20 a18 18 0 0 1 18 18 q0 16 -18 38 q-18 -22 -18 -38 '
        f'a18 18 0 0 1 18 -18 z" fill-opacity="0.26" {_sw(fill=CORAL)}/>'
        f'<circle cx="50" cy="38" r="7" {_sw(2.2, fill=PAPER)}/>'
    )


def _calendar() -> str:
    return (
        f'<rect x="24" y="26" width="52" height="48" rx="6" {_sw(fill=PAPER)}/>'
        f'<path d="M24 40 h52" {_sw(2.4)}/>'
        f'<path d="M36 20 v10 M64 20 v10" {_sw(3)}/>'
        f'<rect x="34" y="48" width="8" height="8" rx="1.5" fill="{BLUE}" fill-opacity="0.55"/>'
        f'<rect x="58" y="48" width="8" height="8" rx="1.5" fill="{BLUE}" fill-opacity="0.30"/>'
        f'<rect x="34" y="60" width="8" height="8" rx="1.5" fill="{BLUE}" fill-opacity="0.30"/>'
        f'<rect x="58" y="60" width="8" height="8" rx="1.5" fill="{BLUE}" fill-opacity="0.55"/>'
    )


def _clock() -> str:
    return (
        f'<circle cx="50" cy="50" r="26" {_sw(fill=PAPER)}/>'
        f'<path d="M50 50 v-15 M50 50 l12 7" {_sw(3)}/>'
        f'<circle cx="50" cy="50" r="2.6" fill="{INK}"/>'
        f'<path d="M50 26 v4 M50 70 v4 M26 50 h4 M70 50 h4" {_sw(2.2)}/>'
    )


def _mail() -> str:
    return (
        f'<rect x="22" y="30" width="56" height="40" rx="6" {_sw(fill=PAPER)}/>'
        f'<path d="M24 34 l26 20 l26 -20" {_sw(2.6)}/>'
    )


def _bell() -> str:
    return (
        f'<path d="M34 64 q2 -7 4 -13 a12 14 0 0 1 24 0 q2 6 4 13 z" '
        f'fill-opacity="0.28" {_sw(fill=AMBER)}/>'
        f'<path d="M50 22 v6" {_sw(2.6)}/>'
        f'<circle cx="50" cy="20" r="3.2" fill="{INK}"/>'
        f'<path d="M44 68 a6 6 0 0 0 12 0" {_sw(2.4)}/>'
    )


def _tag() -> str:
    return (
        f'<path d="M24 46 l22 -22 a4 4 0 0 1 3 -1 h20 a4 4 0 0 1 4 4 v20 '
        f'a4 4 0 0 1 -1 3 l-22 22 a4 4 0 0 0 -3 1 l-24 -24 a4 4 0 0 1 1 -3 z" '
        f'fill-opacity="0.28" {_sw(fill=GREEN)}/>'
        f'<circle cx="62" cy="38" r="4.4" {_sw(2.2, fill=PAPER)}/>'
    )


def _bookmark() -> str:
    return (
        f'<path d="M34 20 h32 v60 l-16 -13 l-16 13 z" '
        f'fill-opacity="0.28" {_sw(fill=CORAL)}/>'
    )


def _graph_line() -> str:
    return (
        f'<path d="M26 22 v56 h52" {_sw(3)}/>'
        f'<path d="M32 68 l14 -16 l10 8 l20 -26" {_cw(GREEN, 3.2)}/>'
        f'<circle cx="46" cy="52" r="3" fill="{GREEN}"/>'
        f'<circle cx="56" cy="60" r="3" fill="{GREEN}"/>'
        f'<circle cx="76" cy="34" r="3" fill="{GREEN}"/>'
    )


def _pie() -> str:
    return (
        f'<circle cx="50" cy="50" r="26" fill-opacity="0.20" {_sw(fill=BLUE)}/>'
        f'<path d="M50 50 v-26 a26 26 0 0 1 22 13 z" '
        f'fill-opacity="0.45" {_sw(2.2, fill=AMBER)}/>'
        f'<path d="M50 50 l22 -13 M50 50 v-26" {_sw(2.2)}/>'
    )


def _table() -> str:
    return (
        f'<rect x="22" y="28" width="56" height="44" rx="5" {_sw(fill=PAPER)}/>'
        f'<path d="M22 42 h56" {_sw(2.4)}/>'
        f'<path d="M22 57 h56 M40 42 v30 M60 42 v30" {_sw(2)}/>'
        f'<path d="M22 42 h56 v-9 a5 5 0 0 0 -5 -5 h-46 a5 5 0 0 0 -5 5 z" '
        f'fill-opacity="0.30" {_sw(2, fill=STEEL)}/>'
    )


def _code() -> str:
    return (
        f'<path d="M40 34 l-16 16 l16 16" {_sw(4)}/>'
        f'<path d="M60 34 l16 16 l-16 16" {_sw(4)}/>'
        f'<path d="M55 28 l-10 44" {_cw(CORAL, 3)}/>'
    )


def _git_branch() -> str:
    return (
        f'<path d="M34 36 v28" {_sw(3)}/>'
        f'<path d="M66 36 v4 a16 16 0 0 1 -16 16 h-16" {_sw(3)}/>'
        f'<circle cx="34" cy="30" r="6" fill-opacity="0.35" {_sw(2.2, fill=VIOLET)}/>'
        f'<circle cx="34" cy="70" r="6" fill-opacity="0.35" {_sw(2.2, fill=VIOLET)}/>'
        f'<circle cx="66" cy="30" r="6" fill-opacity="0.35" {_sw(2.2, fill=VIOLET)}/>'
    )


def _eye() -> str:
    return (
        f'<path d="M22 50 q28 -26 56 0 q-28 26 -56 0 z" {_sw(fill=PAPER)}/>'
        f'<circle cx="50" cy="50" r="9" fill-opacity="0.40" {_sw(2.2, fill=BLUE)}/>'
        f'<circle cx="50" cy="50" r="3" fill="{INK}"/>'
    )


def _compass() -> str:
    return (
        f'<circle cx="50" cy="50" r="26" fill-opacity="0.18" {_sw(fill=TEAL)}/>'
        f'<path d="M50 30 l7 20 l-7 20 l-7 -20 z" '
        f'fill-opacity="0.45" {_sw(2.2, fill=CORAL)}/>'
        f'<circle cx="50" cy="50" r="2.6" fill="{INK}"/>'
    )


def _flask() -> str:
    return (
        f'<path d="M42 22 h16 v16 l14 32 a6 6 0 0 1 -6 8 h-32 a6 6 0 0 1 -6 -8 '
        f'l14 -32 v-16 z" fill-opacity="0.22" {_sw(fill=TEAL)}/>'
        f'<path d="M40 22 h20" {_sw(2.6)}/>'
        f'<path d="M35 58 h30" {_sw(2.2)}/>'
        f'<circle cx="46" cy="66" r="2.4" fill="{TEAL}"/>'
        f'<circle cx="56" cy="70" r="2" fill="{TEAL}"/>'
    )


def _atom() -> str:
    return (
        f'<circle cx="50" cy="50" r="4" fill="{VIOLET}"/>'
        f'<ellipse cx="50" cy="50" rx="28" ry="11" {_sw(2.2)}/>'
        f'<ellipse cx="50" cy="50" rx="28" ry="11" transform="rotate(60 50 50)" {_sw(2.2)}/>'
        f'<ellipse cx="50" cy="50" rx="28" ry="11" transform="rotate(-60 50 50)" {_sw(2.2)}/>'
    )


_STICKERS = {
    # original set
    "document": _document, "puzzle": _puzzle, "hook": _hook, "robot": _robot,
    "plug": _plug, "package": _package, "laptop": _laptop, "monitor": _monitor,
    "conveyor": _conveyor, "machine": _machine, "note": _note, "gear": _gear,
    "box": _box,
    # tech / AI / RAG-explainer expansion
    "database": _database, "cloud": _cloud, "brain": _brain, "chip": _chip,
    "server": _server, "network": _network, "key": _key, "lock": _lock,
    "search": _search, "funnel": _funnel, "layers": _layers, "vector": _vector,
    "arrow": _arrow, "lightbulb": _lightbulb, "chat": _chat, "terminal": _terminal,
    "sync": _sync, "warning": _warning, "star": _star, "globe": _globe,
    # general-explainer expansion (2.2.0)
    "shield": _shield, "rocket": _rocket, "magnet": _magnet, "scale": _scale,
    "target": _target, "flag": _flag, "mappin": _mappin, "calendar": _calendar,
    "clock": _clock, "mail": _mail, "bell": _bell, "tag": _tag,
    "bookmark": _bookmark, "graph_line": _graph_line, "pie": _pie, "table": _table,
    "code": _code, "git_branch": _git_branch, "eye": _eye, "compass": _compass,
    "flask": _flask, "atom": _atom,
}

# Fail-open glyph — a plain sketched box.
_FALLBACK = f'<rect x="28" y="28" width="44" height="44" rx="6" {_sw(fill=PAPER)}/>'


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
