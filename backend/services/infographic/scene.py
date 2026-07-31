"""L3 "Scene" composition — graph -> deterministic layout -> hand-drawn SVG.

Pipeline (plan §4 + HARD RULE §2.1 "the LLM never emits coordinates"):

  1. The LLM emits a coordinate-free GRAPH: ordered `groups` (the phases of the
     scene) + `nodes` (each mapped to a hand-picked sticker + a group) + optional
     `edges` (phase flow). No x/y anywhere. (Parsed/validated by `parse_graph`.)
  2. `layout_scene` computes positions with a LEAN left-to-right flow layout
     (one column per group; heroes stacked, small nodes in a 2-col grid). No
     ELK / dagre dependency — a 4B model cannot do spatial arithmetic and we do
     not need a graph-layout library for a phased flow.
  3. `compose_scene` renders the placed scene as ONE self-contained SVG: the
     curated stickers from `stickers.py`, highlighter-pill phase labels, dashed
     connector arrows, and a `feTurbulence`/`feDisplacementMap` "roughen" filter
     that gives the whole thing its hand-drawn wobble — ZERO runtime deps.

The SVG is the artifact SOURCE (HARD RULE §2.4: stored as source, not raster);
the frontend renders it inline and the export pipeline rasterizes it through the
shared Playwright singleton. Everything fails open (§2.5): a malformed graph
returns `None` from `parse_graph`, and the composer never raises.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from services.infographic.stickers import (
    render_sticker, sticker_tint, INK, INK_SOFT, AMBER,
)

# ── highlighter pill palette (marker tints, semi-transparent fill) ─────
_PILL_COLORS: dict[str, str] = {
    "blue": "#3b6fd4", "green": "#3f9d5a", "teal": "#37a9a0",
    "violet": "#8a6ad4", "amber": "#e0a63a", "coral": "#e0503a",
    "slate": "#5b7aa8",
}
_PILL_ORDER = ["blue", "green", "amber", "violet", "teal", "coral", "slate"]

# ── layout constants (px, in the SVG user space) ───────────────────────
_PAD_X = 44
_PAD_TOP = 96          # room for the phase pills
_PAD_BOTTOM = 44
_COL_GAP = 96          # room for the connector arrow between columns
_HEADER_Y = 34         # pill baseline band
_SMALL = 84            # small sticker edge
_SMALL_CELL_W = 116
_SMALL_CELL_H = 126
_HERO = 172            # hero sticker edge
_HERO_CELL_W = 208
_HERO_CELL_H = 216
_LABEL_FONT = 15
_HERO_LABEL_FONT = 17
_PILL_FONT = 20
_PILL_MAX = 24         # phase-label char cap (longer truncates) so pills stay sane

_FONT_STACK = (
    "'Chalkboard SE','Comic Sans MS','Comic Sans','Segoe Print',"
    "'Bradley Hand','Marker Felt',cursive"
)


def _esc(t: str) -> str:
    return (
        (t or "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ── 1. graph parsing / validation (fail-open) ──────────────────────────
def parse_graph(raw: Any) -> Optional[dict]:
    """Coerce an LLM graph dict into a clean `{title, groups[], nodes[], edges[]}`.

    Never raises. Returns None when there is nothing renderable (no group with a
    node) so the caller degrades down the ladder (L3 -> L2 -> prose)."""
    if not isinstance(raw, dict):
        return None

    groups_in = raw.get("groups")
    nodes_in = raw.get("nodes")
    if not isinstance(nodes_in, list) or not nodes_in:
        return None

    # Groups: keep declaration order; assign a rotating pill color when absent.
    groups: list[dict] = []
    seen: dict[str, dict] = {}
    if isinstance(groups_in, list):
        for i, g in enumerate(groups_in):
            if not isinstance(g, dict):
                continue
            gid = str(g.get("id") or g.get("label") or f"g{i+1}").strip()
            if not gid or gid in seen:
                continue
            color = str(g.get("color") or "").strip().lower()
            if color not in _PILL_COLORS:
                color = _PILL_ORDER[len(groups) % len(_PILL_ORDER)]
            row = {"id": gid, "label": str(g.get("label") or "").strip(), "color": color}
            groups.append(row)
            seen[gid] = row

    # Nodes: bind to a group (default group when the model omits it).
    nodes: list[dict] = []
    for i, n in enumerate(nodes_in):
        if not isinstance(n, dict):
            continue
        label = str(n.get("label") or "").strip()
        sticker = str(n.get("sticker") or n.get("icon") or "box").strip().lower()
        gid = str(n.get("group") or n.get("group_id") or "").strip()
        size = str(n.get("size") or "").strip().lower()
        if size not in ("hero", "small"):
            size = ""
        if gid not in seen:
            # unknown/absent group -> attach to a synthetic default column
            if "__default__" not in seen:
                row = {"id": "__default__", "label": "", "color": _PILL_ORDER[0]}
                groups.append(row)
                seen["__default__"] = row
            gid = "__default__"
        nodes.append({"id": str(n.get("id") or f"n{i+1}"), "label": label,
                      "sticker": sticker, "group": gid, "size": size})

    if not groups or not nodes:
        return None

    # Drop groups that ended up empty (keeps columns tight).
    used = {n["group"] for n in nodes}
    groups = [g for g in groups if g["id"] in used]
    if not groups:
        return None

    edges = []
    for e in (raw.get("edges") or []):
        if isinstance(e, dict) and e.get("from") and e.get("to"):
            edges.append({"from": str(e["from"]), "to": str(e["to"])})

    return {
        "title": str(raw.get("title") or "").strip(),
        "groups": groups,
        "nodes": nodes,
        "edges": edges,
    }


# ── 2. deterministic layout ────────────────────────────────────────────
def layout_scene(graph: dict) -> dict:
    """Compute positions for every sticker + pill. Pure arithmetic, no model.

    Left-to-right: one column per group. Within a column, `hero` nodes stack
    (one per row); the rest fill a 2-wide grid. Columns are vertically centered
    against the tallest column so the flow reads as a single band."""
    groups = graph["groups"]
    by_group: dict[str, list[dict]] = {g["id"]: [] for g in groups}
    for n in graph["nodes"]:
        by_group[n["group"]].append(n)

    columns: list[dict] = []
    for g in groups:
        members = by_group[g["id"]]
        # size inference: a lone node in its group is a hero unless told otherwise
        heroes = [n for n in members if n["size"] == "hero"
                  or (n["size"] == "" and len(members) == 1)]
        smalls = [n for n in members if n not in heroes]

        small_rows = math.ceil(len(smalls) / 2) if smalls else 0
        small_cols = min(len(smalls), 2) if smalls else 0
        # The phase pill sits above the column — make the column AT LEAST as wide as
        # its pill so pills never overlap their neighbours or bleed off-canvas (field
        # bug: 5 long phase labels collided). Absurdly long labels truncate.
        pill_label = g["label"] if len(g["label"]) <= _PILL_MAX else g["label"][:_PILL_MAX - 1].rstrip() + "…"
        pill_w = max(70.0, len(pill_label) * _PILL_FONT * 0.62 + 34)
        width = max(
            _HERO_CELL_W if heroes else 0,
            small_cols * _SMALL_CELL_W,
            pill_w,
        ) or _SMALL_CELL_W
        height = len(heroes) * _HERO_CELL_H + small_rows * _SMALL_CELL_H
        columns.append({
            "group": g, "heroes": heroes, "smalls": smalls,
            "width": width, "height": height, "pill_label": pill_label,
            "small_rows": small_rows, "small_cols": small_cols,
        })

    total_w = _PAD_X * 2 + sum(c["width"] for c in columns) + _COL_GAP * (len(columns) - 1)
    band_h = max((c["height"] for c in columns), default=_SMALL_CELL_H)
    total_h = _PAD_TOP + band_h + _PAD_BOTTOM

    placements: list[dict] = []
    pills: list[dict] = []
    col_centers: list[float] = []

    x = _PAD_X
    for c in columns:
        cx = x + c["width"] / 2
        col_centers.append(cx)
        # vertically center this column's content within the band
        y = _PAD_TOP + (band_h - c["height"]) / 2

        pills.append({
            "cx": cx, "label": c["pill_label"], "color": c["group"]["color"],
        })

        for hero in c["heroes"]:
            placements.append({
                "x": cx - _HERO / 2, "y": y, "size": _HERO,
                "sticker": hero["sticker"], "label": hero["label"],
                "font": _HERO_LABEL_FONT, "cell_w": c["width"],
            })
            y += _HERO_CELL_H

        # small grid (2 cols), rows centered under the column
        grid_w = c["small_cols"] * _SMALL_CELL_W
        gx0 = cx - grid_w / 2
        for idx, node in enumerate(c["smalls"]):
            r, cc = divmod(idx, 2)
            # last row with a single item -> center it
            row_items = 2 if (r + 1) * 2 <= len(c["smalls"]) else len(c["smalls"]) - r * 2
            row_w = row_items * _SMALL_CELL_W
            rx0 = cx - row_w / 2
            cell_x = rx0 + (idx - r * 2) * _SMALL_CELL_W
            placements.append({
                "x": cell_x + (_SMALL_CELL_W - _SMALL) / 2,
                "y": y + r * _SMALL_CELL_H,
                "size": _SMALL, "sticker": node["sticker"], "label": node["label"],
                "font": _LABEL_FONT, "cell_w": _SMALL_CELL_W,
            })
        x += c["width"] + _COL_GAP

    return {
        "width": round(total_w), "height": round(total_h),
        "band_top": _PAD_TOP, "band_h": band_h,
        "placements": placements, "pills": pills, "col_centers": col_centers,
    }


# ── 3. SVG composition ─────────────────────────────────────────────────
def _wrap_label(text: str, max_chars: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    lines = lines[:2]  # never more than 2 lines
    if lines:  # drop a dangling comma/semicolon left by the 2-line cap
        lines[-1] = lines[-1].rstrip(",;")
    return lines


def _pill(cx: float, y: float, label: str, color: str) -> tuple[str, str]:
    """Return (roughened rect markup, crisp text markup) for a highlighter pill."""
    if not label:
        return "", ""
    hexc = _PILL_COLORS.get(color, _PILL_COLORS["slate"])
    w = max(70.0, len(label) * _PILL_FONT * 0.62 + 34)
    h = 38
    x = cx - w / 2
    rect = (
        f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h}" rx="15" '
        f'fill="{hexc}" fill-opacity="0.28" stroke="{hexc}" stroke-width="2.4" '
        f'stroke-opacity="0.85"/>'
    )
    text = (
        f'<text x="{cx:.0f}" y="{y + h/2 + 1:.0f}" text-anchor="middle" '
        f'dominant-baseline="middle" font-family="{_FONT_STACK}" '
        f'font-size="{_PILL_FONT}" font-weight="700" fill="{INK}">{_esc(label)}</text>'
    )
    return rect, text


def _dashed_arrow(x1: float, y1: float, x2: float, y2: float) -> str:
    midx = (x1 + x2) / 2
    cy = min(y1, y2) - 26
    d = f"M{x1:.0f} {y1:.0f} Q{midx:.0f} {cy:.0f} {x2:.0f} {y2:.0f}"
    # arrowhead
    ang = math.atan2(y2 - cy, x2 - midx)
    ah = 10
    a1x, a1y = x2 - ah * math.cos(ang - 0.5), y2 - ah * math.sin(ang - 0.5)
    a2x, a2y = x2 - ah * math.cos(ang + 0.5), y2 - ah * math.sin(ang + 0.5)
    return (
        f'<path d="{d}" fill="none" stroke="{INK_SOFT}" stroke-width="2.6" '
        f'stroke-dasharray="2 8" stroke-linecap="round"/>'
        f'<path d="M{a1x:.0f} {a1y:.0f} L{x2:.0f} {y2:.0f} L{a2x:.0f} {a2y:.0f}" '
        f'fill="none" stroke="{INK_SOFT}" stroke-width="2.6" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )


def _sparkles(cx: float, cy: float, r: float) -> str:
    """A radiating amber sparkle burst behind a hero sticker."""
    rays = []
    for i in range(12):
        a = i * math.pi / 6
        x1, y1 = cx + r * 0.72 * math.cos(a), cy + r * 0.72 * math.sin(a)
        x2, y2 = cx + r * math.cos(a), cy + r * math.sin(a)
        rays.append(
            f'<path d="M{x1:.0f} {y1:.0f} L{x2:.0f} {y2:.0f}" stroke="{AMBER}" '
            f'stroke-width="3" stroke-linecap="round" stroke-opacity="0.7"/>'
        )
    return "".join(rays)


def compose_scene(graph: dict, *, seed: int = 7) -> str:
    """Render a parsed+laid-out graph to one self-contained SVG string.

    `seed` varies the roughen turbulence so re-composing yields a slightly
    different (but stable-per-seed) wobble. Never raises."""
    lay = layout_scene(graph)
    W, H = lay["width"], lay["height"]

    rough_layer: list[str] = []   # drawn objects (get the hand-drawn wobble)
    text_layer: list[str] = []    # labels (stay crisp — displacement ruins text)

    # phase pills
    for p in lay["pills"]:
        rect, text = _pill(p["cx"], _HEADER_Y, p["label"], p["color"])
        if rect:
            rough_layer.append(rect)
            text_layer.append(text)

    # connector arrows between consecutive columns (default phase flow)
    cy = lay["band_top"] + lay["band_h"] / 2
    centers = lay["col_centers"]
    for i in range(len(centers) - 1):
        x1 = centers[i] + 40
        x2 = centers[i + 1] - 40
        rough_layer.append(_dashed_arrow(x1, cy, x2, cy))

    # stickers + their labels
    for pl in lay["placements"]:
        scale = pl["size"] / 100.0
        # hero package/box gets a radiating amber "sparkle burst" (the target's
        # celebratory glow around the finished artifact).
        if pl["size"] >= _HERO and pl["sticker"] in ("package", "box", "gift"):
            rough_layer.append(_sparkles(pl["x"] + pl["size"] / 2,
                                         pl["y"] + pl["size"] / 2, pl["size"] * 0.62))
        rough_layer.append(
            f'<g transform="translate({pl["x"]:.1f} {pl["y"]:.1f}) scale({scale:.3f})">'
            f'{render_sticker(pl["sticker"])}</g>'
        )
        tint = sticker_tint(pl["sticker"])
        max_chars = max(8, int(pl["cell_w"] / (pl["font"] * 0.56)))
        lines = _wrap_label(pl["label"], max_chars)
        ly = pl["y"] + pl["size"] + pl["font"] + 4
        cxp = pl["x"] + pl["size"] / 2
        for li, line in enumerate(lines):
            text_layer.append(
                f'<text x="{cxp:.0f}" y="{ly + li * (pl["font"] + 3):.0f}" '
                f'text-anchor="middle" font-family="{_FONT_STACK}" '
                f'font-size="{pl["font"]}" font-weight="600" fill="{tint}">'
                f'{_esc(line)}</text>'
            )

    defs = (
        '<defs>'
        f'<filter id="rough" x="-6%" y="-6%" width="112%" height="112%">'
        # Lower baseFrequency = longer, more organic wave; larger displacement
        # scale pushes the hand-drawn wobble (drawn objects only — the text_layer
        # is composed separately + unfiltered, so labels stay crisp).
        f'<feTurbulence type="fractalNoise" baseFrequency="0.013" numOctaves="2" '
        f'seed="{seed}" result="n"/>'
        f'<feDisplacementMap in="SourceGraphic" in2="n" scale="5.0" '
        f'xChannelSelector="R" yChannelSelector="G"/>'
        '</filter></defs>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" font-family="{_FONT_STACK}">'
        f'{defs}'
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="#fdfdfb"/>'
        f'<g filter="url(#rough)">{"".join(rough_layer)}</g>'
        f'<g>{"".join(text_layer)}</g>'
        f'</svg>'
    )
