"""Infographic builder — turns notebook content into a `json:infographic`
Artifact for the L1 (annotated chart) and L2 (structured diagram) lanes.

Design rules honored here:
  - The LLM only fills text/icon SLOTS; it never emits coordinates or CSS
    (HARD RULE §2.1/§2.3). Layout is the hand-authored skeleton + stylesheet.
  - Every lane FAILS OPEN via the degradation ladder (§3.5): a bad slot-fill
    or chart never throws and never returns nothing — it degrades to a
    simpler-but-legible artifact, ultimately prose.
  - Reuses the shipped slot-fill primitives (`_apply_slot_fill`,
    `_has_unfilled_slots`) and the chart stack (`structured_llm.generate_chart`).

Public entry: `build_infographic(content, lane, archetype=None, ...)`.
Returns a plain dict (Artifact.model_dump) ready to hand to the frontend
`json:infographic` renderer and the export pipeline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from typing import Any, Optional

from config import settings
from services.ollama_service import ollama_service
from services.artifact_spec import json_artifact
from services.visual_slotfill import _apply_slot_fill, _has_unfilled_slots
from utils.json_repair import robust_json_parse

from services.infographic.icons import (
    allowed_icon_names,
    icon_for_label,
    render_icon,
)
from services.infographic.skeletons import (
    ARCHETYPES,
    get_skeleton,
    l2_system_ext,
    l2_key_slots_ext,
    l2_icon_slots_ext,
)
from services.infographic import slotfill as sf
from services.infographic.scene import parse_graph, compose_scene
from services.infographic.accents import pick_accent, accent_spot_html, accent_markup

logger = logging.getLogger(__name__)

_SLOTFILL_TIMEOUT = 120.0
_SLOTFILL_NUM_PREDICT = 2000
_ALLOWED_ICONS = set(allowed_icon_names())


# ── helpers ────────────────────────────────────────────────────────────
def _new_id() -> str:
    return f"ig_{uuid.uuid4().hex[:10]}"


# Slot-contract resolution: the archetypes authored in `skeletons.py` carry their
# own contract there; the original three live in `slotfill.py`. Prefer the
# co-located (skeletons) contract, fall back to slotfill.
def _sys(archetype: str) -> str:
    return l2_system_ext(archetype) or sf.l2_system(archetype)


def _key_slots(archetype: str):
    return l2_key_slots_ext(archetype) or sf.l2_key_slots(archetype)


def _icon_slots(archetype: str) -> list[tuple[str, str]]:
    return l2_icon_slots_ext(archetype) or sf.l2_icon_slots(archetype)


# Archetypes whose rows carry citation superscripts -> (row count, label-key fmt).
_CITE_SPECS: dict[str, tuple[int, str]] = {
    "facts_table": (3, "ROW_{i}_LABEL"),
    "stat_grid": (4, "STAT_{i}_LABEL"),
    "timeline": (4, "EVENT_{i}_TITLE"),
    "tier_ladder": (4, "CHIP_{i}_LABEL"),
}


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def _run_slotfill(system: str, content: str, model: str, topic: str = "") -> Optional[dict]:
    """One JSON slot-fill call. Fails open to None (caller degrades).

    `topic` is the user's REQUEST. When present it leads the prompt as the authoritative
    subject so the infographic is ABOUT what was asked — the retrieved `content` is
    supporting material for specifics, not the subject. Without this, slot-fill answered
    "something about this notebook" and drifted off the request (field diag 2026-08-03)."""
    req = (topic or "").strip()
    if req:
        prompt = (
            f"USER REQUEST: {req}\n\n"
            "SUPPORTING MATERIAL (from the user's notebook — use it for concrete facts, names, "
            "and numbers, but the infographic must be ABOUT the request above, not a summary of "
            f"this material):\n{content}\n\n"
            "Fill every slot to directly answer the USER REQUEST, using its own subject and "
            "terms. Return JSON only."
        )
    else:
        prompt = (
            f"SOURCE CONTENT:\n{content}\n\n"
            "Fill in every slot from the schema based on the source content. Return JSON only."
        )
    try:
        result = await ollama_service.generate(
            prompt=prompt,
            system=system,
            model=model,
            temperature=0.2,
            num_predict=_SLOTFILL_NUM_PREDICT,
            timeout=_SLOTFILL_TIMEOUT,
            format="json",
            voice_modifier=False,
        )
    except Exception as e:  # never propagate — degrade instead
        logger.warning(f"[infographic] slot-fill call failed: {e}")
        return None
    raw = (result or {}).get("response", "")
    if not raw:
        return None
    parsed = robust_json_parse(raw, expect="object", fallback=None, label="Infographic")
    return parsed if isinstance(parsed, dict) else None


def _check_slots(archetype: str, slots: dict) -> tuple[bool, str]:
    """Empty-ratio + per-archetype key-slot gate (mirrors visual_freeform)."""
    text_slots = {
        k: v for k, v in slots.items()
        if not k.startswith("ICON_") and isinstance(v, str)
    }
    if not text_slots:
        return False, "no text slots returned"
    empty = sum(1 for v in text_slots.values() if not v.strip())
    if empty / len(text_slots) > 0.4:
        return False, f"{empty}/{len(text_slots)} slots blank — input too vague"
    spec = _key_slots(archetype)
    if spec:
        keys, need = spec
        filled = sum(1 for k in keys if isinstance(slots.get(k), str) and slots[k].strip())
        if filled < need:
            return False, f"only {filled}/{len(keys)} key slots for {archetype} (need {need})"
    return True, ""


def _apply_icons(skeleton: str, archetype: str, slots: dict) -> str:
    """Replace {{ICON_*}} with inline SVG chosen from the allowlist (fail-open
    to a keyword-derived icon). Pops icon keys out of `slots` so the text
    slot-fill pass doesn't stringify them."""
    for icon_key, label_key in _icon_slots(archetype):
        name = str(slots.pop(icon_key, "") or "").strip().lower()
        if name not in _ALLOWED_ICONS:
            name = icon_for_label(str(slots.get(label_key, "")))
        skeleton = skeleton.replace("{{" + icon_key + "}}", render_icon(name))
    return skeleton


def _finalize_body(skeleton: str, slots: dict) -> str:
    body = _apply_slot_fill(skeleton, slots)
    if _has_unfilled_slots(body):
        body = re.sub(r"\{\{[A-Z0-9_]+\}\}", "", body)
    return body


def _normalize_sources(sources: Optional[list]) -> list[dict]:
    """Coerce the caller's provenance list into clean `{n, source_id, title}`
    rows (1-based, in order). Never raises; drops malformed entries. This is
    the real source identity behind every citation number (HARD RULE §2.6)."""
    out: list[dict] = []
    if not isinstance(sources, list):
        return out
    for i, s in enumerate(sources, start=1):
        if not isinstance(s, dict):
            continue
        title = (s.get("title") or s.get("filename") or "").strip()
        if not title:
            continue
        out.append({
            "n": int(s.get("n") or i),
            "source_id": s.get("source_id"),
            "title": title,
        })
    # Re-number sequentially so the legend + superscripts always agree.
    for idx, row in enumerate(out, start=1):
        row["n"] = idx
    return out


def _cite_slots_for_rows(sources: list[dict], n_rows: int = 3) -> dict:
    """Map each facts-table row to a REAL source number (round-robin over the
    available sources). Empty when there is no provenance — a bare row rather
    than a number that points at nothing."""
    slots: dict[str, str] = {}
    if not sources:
        for i in range(1, n_rows + 1):
            slots[f"CITE_{i}"] = ""
        return slots
    for i in range(1, n_rows + 1):
        src = sources[(i - 1) % len(sources)]
        slots[f"CITE_{i}"] = str(src["n"])
    return slots


def _prose_fallback(content: str, reason: str, lane: str, sources: Optional[list] = None) -> dict:
    """Ultimate fail-open: legible prose card (§3.5 'any -> L0 prose')."""
    snippet = _escape(content.strip()[:1200]) or "No content available."
    body = (
        '<div class="ib"><div class="ib-card ib-bracketed">'
        '<div class="ib-section-label">Summary</div>'
        f'<div class="ib-fallback">{snippet}</div></div></div>'
    )
    payload = {
        "lane": lane,
        "archetype": "prose",
        "body_html": body,
        "sources": _normalize_sources(sources),
        "degraded": True,
        "degrade_reason": reason,
    }
    art = json_artifact(
        id=_new_id(), kind="infographic", payload=payload,
        title="Summary", tagline=None,
        metadata={"lane": lane, "degraded": True, "reason": reason},
    )
    return art.model_dump()


# ── archetype heuristic (deterministic; router may override) ───────────
# Archetype intent vocabulary, ordered most-specific first. Matched against the user's
# REQUEST first (authoritative); only if the request is silent do we fall back to
# HIGH-PRECISION multi-word phrases in the retrieved content. Single common words
# ("tier", "stage", "metric") are request-only — an incidental one in 8k chars of noisy
# retrieval used to collapse every infographic to tier_ladder (field diag 2026-08-03).
_ARCHETYPE_INTENTS: list[tuple[str, tuple[str, ...]]] = [
    ("compare_code", ("compare the code", "code side by side", "two implementations",
                      "sdk call", "api call", "code snippet")),
    ("compare_columns", ("versus", " vs ", " vs.", "compare ", "comparison", "pros and cons",
                         "pros & cons", "pros/cons", "difference between", "differences between",
                         "trade-off", "tradeoff", "advantages and disadvantages",
                         "which is better", "head to head", "head-to-head")),
    ("layer_stack", ("layer stack", "stacked layer", "layered", "layers of", "the layers",
                     "from the bottom up", "bottom-up", "slabs", "strata", "exploded")),
    ("stepped_cards", ("step by step", "step-by-step", "steps to", "sequential", "walkthrough",
                       "the workflow", "the pipeline", "the process", "stages of", " steps",
                       "stepped card", "step card", "feedback loop", "revises until")),
    ("tier_ladder", ("tier", "ladder", "rung", "maturity", "levels of", "skill level",
                     "what runs", "runs at home", "status badge")),
    ("timeline", ("timeline", "chronolog", "milestone", "history of", "evolution", "roadmap",
                  "over time", " era ")),
    ("tree_hierarchy", ("hierarchy", "taxonomy", "tree", "breakdown", "categories", "category",
                        "org chart", "sub-component")),
    ("three_stage", ("three stage", "three-stage", "3 stage", "offline", "request-time",
                     "compile once", "serve many")),
    ("stat_grid", ("kpi", "at a glance", "key metrics", "dashboard", "headline number",
                   "by the numbers")),
    ("facts_table", ("facts table", "figures", "revenue", "filing", "quarter", "financials")),
    # pipeline_compare is the SPECIFIC retrieval-loop-vs-compile-once shape — no longer the
    # generic default; it needs an explicit signal now that compare_columns exists.
    ("pipeline_compare", ("retrieval vs", "cache vs recompute", "runtime vs precompute",
                          "recomputed every")),
]


def _match_intent(text: str, multiword_only: bool = False) -> Optional[str]:
    low = f" {(text or '').lower()} "
    for arche, phrases in _ARCHETYPE_INTENTS:
        for p in phrases:
            if multiword_only and " " not in p.strip():
                continue
            if p in low:
                return arche
    return None


def pick_archetype(content: str, topic: str = "") -> str:
    """Choose an L2 archetype from the user's REQUEST first (authoritative), then—only if the
    request names no shape—from HIGH-PRECISION multi-word phrases in the retrieved content,
    else a neutral default. Request-first so a noisy 8k-char retrieval can't hijack the layout
    (field diag 2026-08-03: incidental 'tier'/'stage' collapsed everything to tier_ladder)."""
    return _match_intent(topic) or _match_intent(content, multiword_only=True) or "stepped_cards"


# On-brand restyle presets seeded per generation to break the "every L2 looks
# the same" rut (field feedback 2026-07-31). The restyle machinery already
# exists (restyle.py); nothing seeded it, so every L2 shipped coral/paper/cozy.
# Coral (the brand) is weighted highest; the rest are the design-system's OWN
# restyle palette, so variety stays on-brand. Deterministic by content → the
# same source reproduces, different sources diverge. Fully user-overrideable via
# the tombstone restyle controls (this only sets the STARTING look).
_STYLE_PRESETS: list[dict] = [
    {},                                                     # coral / paper / cozy
    {},                                                     # (coral weighted ~2×)
    {"accent": "coral", "tone": "light", "scale": "roomy"},
    {"accent": "blue"},
    {"accent": "emerald"},
    {"accent": "violet", "tone": "light"},
    {"accent": "amber"},
    {"accent": "slate", "tone": "light", "scale": "compact"},
    {"tone": "cream"},                                      # Family-B "deck" look
    {"accent": "coral", "tone": "cream"},
]


def _seed_style(content: str, topic: str = "") -> dict:
    """Pick an on-brand restyle preset deterministically from the content, so a
    notebook's infographics don't all look identical. Never raises."""
    key = f"{topic}\n{(content or '')[:400]}".encode("utf-8", "ignore")
    idx = int(hashlib.sha1(key).hexdigest(), 16) % len(_STYLE_PRESETS)
    return dict(_STYLE_PRESETS[idx])


# ── L4-accent (decorative glow-crystal spot; gated + off by default) ───
def _accent_enabled(accent_art: bool) -> bool:
    """The L4-accent is OPT-IN (design-corpus lever 6: "gated + off by default").
    A caller can request it per-generation, or a global `settings.infographic_
    accent_art` flag can turn it on everywhere — default OFF, so the shipping
    path is byte-identical until someone opts in."""
    return bool(accent_art) or bool(getattr(settings, "infographic_accent_art", False))


def _inject_accent_l2(body: str, variant: str) -> str:
    """Slot the accent spot into the FIRST child of the `.ib` root so it lands in
    the (empty) corner behind all content. No-op for an unknown variant."""
    spot = accent_spot_html(variant, "tr")
    if not spot:
        return body
    return body.replace('<div class="ib">', '<div class="ib">' + spot, 1)


# ── L1 data-anchors (numeric/data anchoring; Hard Rule §2.1 + §2.6) ────
def _l1_anchors(chart: dict) -> dict:
    """Compute coordinate-free DATA anchors (0..1 fractions of the plot area)
    for the L1 annotation chrome from the REAL chart data — so the callout points
    at the primary series' endpoint and the labels sit on their series, instead
    of floating at hardcoded percentages. The model never emits these (§2.1); the
    renderer maps the fractions onto the plot rect. Never raises.

    Also returns a data-derived `ratio` (primary endpoint ÷ baseline endpoint) so
    the callout can be numerically grounded even when the LLM annotation is thin.
    """
    try:
        data = chart.get("data") or []
        series = chart.get("series") or []
        keys = [s.get("key") for s in series if isinstance(s, dict) and s.get("key")]
        if not data or not keys:
            return {}
        vals: list[float] = []
        for row in data:
            for k in keys:
                v = row.get(k)
                if isinstance(v, (int, float)):
                    vals.append(float(v))
        if not vals:
            return {}
        ymin, ymax = min(vals), max(vals)
        span = (ymax - ymin) or 1.0
        n = len(data)

        def yfrac(v: float) -> float:
            return round(max(0.0, min(1.0, 1.0 - (v - ymin) / span)), 4)

        def xfrac(i: int) -> float:
            return round(i / (n - 1), 4) if n > 1 else 1.0

        def last_val(key: str):
            for row in reversed(data):
                v = row.get(key)
                if isinstance(v, (int, float)):
                    return float(v)
            return None

        prim_key = keys[0]
        base_key = keys[1] if len(keys) > 1 else None
        prim_last = last_val(prim_key)
        base_last = last_val(base_key) if base_key else None

        anchors: dict = {}
        if prim_last is not None:
            # callout points at the primary series endpoint
            anchors["callout"] = {"x": xfrac(n - 1), "y": yfrac(prim_last)}
            # primary label rides ~two-thirds along the growing series
            midi = max(0, min(n - 1, int(round((n - 1) * 0.66))))
            mid_prim = None
            row = data[midi]
            if isinstance(row.get(prim_key), (int, float)):
                mid_prim = float(row[prim_key])
            anchors["primary"] = {"x": xfrac(midi),
                                  "y": yfrac(mid_prim if mid_prim is not None else prim_last)}
        if base_key and base_last is not None:
            anchors["baseline"] = {"x": xfrac(n - 1), "y": yfrac(base_last)}
        # midpoint note sits under the early part of the primary curve
        anchors["midpoint"] = {"x": xfrac(max(0, min(n - 1, int(round((n - 1) * 0.30))))),
                               "y": 0.72}

        ratio = None
        if prim_last and base_last and base_last != 0:
            ratio = round(prim_last / base_last, 1)
        return {"anchors": anchors, "ratio": ratio}
    except Exception as e:  # never take down the L1 build for an anchor calc
        logger.debug(f"[infographic] L1 anchor calc failed: {e}")
        return {}


# ── L2 ─────────────────────────────────────────────────────────────────
async def build_l2(
    content: str,
    archetype: Optional[str] = None,
    *,
    model: Optional[str] = None,
    max_content_chars: int = 8000,
    sources: Optional[list] = None,
    topic: str = "",
    accent_art: bool = False,
) -> Optional[dict]:
    archetype = archetype if archetype in ARCHETYPES else pick_archetype(content, topic)
    skeleton = get_skeleton(archetype)
    if not skeleton:
        return None
    model = model or settings.ollama_model
    trimmed = (content or "")[:max_content_chars]
    prov = _normalize_sources(sources)

    slots = await _run_slotfill(_sys(archetype), trimmed, model, topic=topic)
    if not slots:
        return None
    ok, reason = _check_slots(archetype, slots)
    if not ok:
        logger.info(f"[infographic] L2 {archetype} slot check failed: {reason}")
        return None

    skeleton = _apply_icons(skeleton, archetype, slots)

    citations = []
    cite_spec = _CITE_SPECS.get(archetype)
    if cite_spec:
        # Bind each row's citation superscript to a REAL source number so
        # every number carries a source ID (HARD RULE §2.6). No provenance ->
        # empty superscripts rather than dangling [1][2][3].
        n_rows, label_fmt = cite_spec
        slots.update(_cite_slots_for_rows(prov, n_rows=n_rows))
        citations = [
            {"id": i, "label": slots.get(label_fmt.format(i=i), f"Fact {i}"),
             "cite": slots.get(f"CITE_{i}", "")}
            for i in range(1, n_rows + 1)
        ]

    body = _finalize_body(skeleton, slots)

    # Optional decorative L4-accent (off by default). Baked into the stored body
    # so it renders identically in-app + on export; `currentColor` + the
    # `.ib-accent-spot` class recolor it with the seeded/user restyle.
    accent_variant = ""
    if _accent_enabled(accent_art):
        accent_variant = pick_accent(f"{archetype}\n{content[:200]}")
        body = _inject_accent_l2(body, accent_variant)

    title = (
        slots.get("TABLE_TITLE")
        or slots.get("SECTION_LABEL")
        or slots.get("STAGE_1_TITLE")
        or slots.get("GRID_TITLE")
        or slots.get("TIMELINE_TITLE")
        or slots.get("TREE_TITLE")
        or slots.get("HEADLINE")
        or slots.get("STACK_TITLE")
        or "Infographic"
    )

    payload = {
        "lane": "L2",
        "archetype": archetype,
        "body_html": body,
        "citations": citations,
        "sources": prov,
        # Seed an on-brand starting style so a notebook's L2s vary (anti-rut).
        "style": _seed_style(content, title),
        "accent": accent_variant,
    }
    art = json_artifact(
        id=_new_id(), kind="infographic", payload=payload,
        title=str(title)[:80],
        metadata={"lane": "L2", "archetype": archetype},
    )
    return art.model_dump()


# ── L3 (hand-drawn scene from hand-picked stickers) ────────────────────
async def build_l3(
    content: str,
    *,
    model: Optional[str] = None,
    max_content_chars: int = 8000,
    sources: Optional[list] = None,
    title: Optional[str] = None,
    topic: str = "",
    accent_art: bool = False,
) -> Optional[dict]:
    """L3 SCENE lane (plan §4 proof): the LLM emits a coordinate-free node/edge
    GRAPH; `scene.py` computes the layout and composes a hand-drawn SVG from the
    curated sticker set (`stickers.py`). No coordinates from the model (HARD RULE
    §2.1), no generation (that is the gated Phase 5).

    FAILS OPEN to None so the caller degrades L3 -> L2 -> prose (§3.5): an empty
    LLM response, an unparseable graph, or a graph with no renderable node all
    return None. Never raises."""
    model = model or settings.ollama_model
    trimmed = (content or "")[:max_content_chars]

    graph_raw = await _run_slotfill(sf.l3_scene_system(), trimmed, model, topic=topic or title or "")
    graph = parse_graph(graph_raw)
    if not graph:
        logger.info("[infographic] L3 graph unusable — degrading to L2")
        return None

    accent_variant = ""
    if _accent_enabled(accent_art):
        accent_variant = pick_accent(f"scene\n{content[:200]}")
    try:
        svg = compose_scene(graph, accent=accent_variant or None)
    except Exception as e:  # composition must never take down the request
        logger.warning(f"[infographic] L3 scene composition failed: {e}")
        return None
    if not svg or "<svg" not in svg:
        return None

    scene_title = (title or "").strip() or graph.get("title") or "Scene"
    payload = {
        "lane": "L3",
        "archetype": "scene",
        # The SVG is the artifact SOURCE (HARD RULE §2.4): stored, not rasterized.
        # The frontend renders it inline; export rasterizes it via Playwright.
        "scene_svg": svg,
        "scene_graph": graph,   # kept for provenance / future re-layout
        "sources": _normalize_sources(sources),
        "accent": accent_variant,
    }
    art = json_artifact(
        id=_new_id(), kind="infographic", payload=payload,
        title=str(scene_title)[:80],
        metadata={"lane": "L3", "archetype": "scene",
                  "node_count": len(graph.get("nodes", []))},
    )
    return art.model_dump()


# ── L1 ─────────────────────────────────────────────────────────────────
_ACCENT = "#e0503a"
_BASELINE = "#1b1a18"


async def build_l1(
    content: str,
    *,
    model: Optional[str] = None,
    max_content_chars: int = 8000,
    sources: Optional[list] = None,
    topic: str = "",
    accent_art: bool = False,
) -> Optional[dict]:
    from services.structured_llm import structured_llm

    trimmed = (content or "")[:max_content_chars]
    prov = _normalize_sources(sources)
    try:
        chart = await structured_llm.generate_chart(
            content_summary=trimmed,
            intent="annotated trend chart contrasting a growing series vs a flat baseline",
            chart_type_hint="line",
        )
    except Exception as e:
        logger.warning(f"[infographic] L1 generate_chart failed: {e}")
        chart = None
    if not chart or not isinstance(chart, dict) or not chart.get("series"):
        return None  # caller degrades to prose/table

    # Recolor: primary series -> coral, second -> baseline ink.
    series = chart.get("series") or []
    if series:
        series[0]["color"] = _ACCENT
    if len(series) > 1:
        series[1]["color"] = _BASELINE
    chart["show_legend"] = False
    chart["series"] = series

    # Annotation text (coordinate-free; anchors computed from data, not the model).
    ann = await _run_slotfill(sf.L1_ANNOTATION_SYSTEM, trimmed, model or settings.ollama_model, topic=topic)
    ann = ann or {}

    # DATA ANCHORS: compute the annotation positions (+ a numeric ratio) from the
    # REAL chart data so the callout points at the primary series' endpoint and
    # every label sits on its series (§2.1 — the model never emits coordinates).
    anchor_info = _l1_anchors(chart)
    anchors = anchor_info.get("anchors") or {}
    ratio = anchor_info.get("ratio")

    callout_text = (ann.get("CALLOUT_TEXT") or "").strip()
    if not callout_text and ratio and ratio >= 1.5:
        # Ground the callout in the data even when the LLM annotation is thin.
        callout_text = f"{ratio:g}× higher"

    annotations = {
        "callout_text": callout_text,
        "callout_subtext": (ann.get("CALLOUT_SUBTEXT") or "").strip(),
        "primary_label": (ann.get("PRIMARY_LABEL") or (series[0].get("label") if series else "")).strip(),
        "baseline_label": (ann.get("BASELINE_LABEL") or (series[1].get("label") if len(series) > 1 else "")).strip(),
        "midpoint_note": (ann.get("MIDPOINT_NOTE") or "").strip(),
        # Data-driven anchor fractions (0..1 of the plot rect) — renderer maps them.
        "anchors": anchors,
        # Bind the callout NUMBER to a real source id (Hard Rule §2.6 — every
        # number carries a source; empty rather than dangling when no provenance).
        "callout_cite": str(prov[0]["n"]) if prov else "",
    }

    accent_variant = pick_accent(f"L1\n{content[:200]}") if _accent_enabled(accent_art) else ""
    # L1 has no body_html/scene_svg to bake the accent into, so ship the accent's
    # inner SVG (currentColor) in the payload for the renderer to overlay — reuses
    # the SAME accents.py art as L2/L3 (no separate frontend copy).
    accent_svg = accent_markup(accent_variant, "currentColor") if accent_variant else ""

    payload = {
        "lane": "L1",
        "archetype": "annotated_chart",
        "chart": chart,
        "annotations": annotations,
        "sources": prov,
        "accent": accent_variant,
        "accent_svg": accent_svg,
    }
    art = json_artifact(
        id=_new_id(), kind="infographic", payload=payload,
        title=str(chart.get("title") or "Annotated chart")[:80],
        metadata={"lane": "L1", "archetype": "annotated_chart"},
    )
    return art.model_dump()


async def _poster_title(content: str, model: str) -> str:
    """A short, poster-style title (2–4 words) for the L4 overlay.

    The raw L4 request is usually a long instruction ("make an evocative cover
    poster for the current state of agentic AI — atmospheric …"). Baking that
    whole string over the art reads as noise and drags the image down (field
    feedback 2026-07-31). Compress it to a clean title. Fails open to "" — a
    missing overlay is better than a noisy one; the evocative image stands alone.
    """
    text = (content or "").strip()
    if not text:
        return ""
    try:
        r = await ollama_service.generate(
            prompt=(f"Source request: {text[:500]}\n\n"
                    "Write a punchy 2-4 word cover title in Title Case. "
                    "Title only — no quotes, no trailing punctuation, no explanation."),
            system="You write short, evocative poster/cover titles.",
            model=model, temperature=0.4, num_predict=16, timeout=20.0,
        )
        line = ((r or {}).get("response") or "").strip().splitlines()[0] if (r or {}).get("response") else ""
    except Exception as e:
        logger.debug(f"[infographic] L4 poster-title failed: {e}")
        return ""
    line = line.strip().strip('"\'' + "“”").rstrip(".!?—-:; ")
    # Reject a non-title (too long / the model echoed the instruction).
    if not line or len(line.split()) > 6 or len(line) > 48:
        return ""
    return line


# ── L4 (decorative / hero — Klein-rendered) ────────────────────────────
async def build_l4(
    content: str,
    *,
    title: Optional[str] = None,
    model: Optional[str] = None,
    max_content_chars: int = 4000,
    sources: Optional[list] = None,
    aspect_ratio: str = "16:9",
    quality_tier: str = "draft",
) -> Optional[dict]:
    """L4 decorative lane — a TEXTLESS Klein raster + optional DOM/SVG title
    overlay, wrapped as a `json:infographic` Artifact.

    Klein is opt-in + heavy, so the engine is imported + probed lazily and
    the whole path FAILS OPEN to None (the caller degrades down the ladder).

    HARD RULES honored:
      - §2.2 (textless raster): the Klein brief drops all label text and a
        text-suppressing negative prompt is sent; the title rides as a
        DOM/SVG overlay in the payload (`title_overlay`), never baked into
        the pixels.
      - §2.5 (fail open): unavailable/errored Klein → None, never throws.

    Returns the Artifact dict on success, or None so the caller can degrade
    (L4 → L2 → prose)."""
    # Lazy imports — Klein pulls httpx/mflux; never hard-require at module load.
    try:
        from services.visual_diffusion import (
            klein_diffusion,
            write_klein_brief,
            DEFAULT_NEGATIVE_PROMPT,
        )
        from services.visual_capability import get_capability
    except Exception as e:
        logger.warning(f"[infographic] L4 imports unavailable: {e}")
        return None

    overlay_title = (title or "").strip()
    trimmed = (content or "")[:max_content_chars].strip()
    seed = overlay_title or trimmed
    if not seed:
        return None

    # Probe engine availability up front so we degrade cleanly instead of
    # burning a Gemma brief call we can't use. mflux (MLX) needs no Ollama
    # klein_model; the Ollama path does.
    try:
        cap = await get_capability()
    except Exception as e:
        logger.warning(f"[infographic] L4 capability probe failed: {e}")
        return None
    engine_mlx = getattr(settings, "image_engine", "ollama") == "mlx"
    if not engine_mlx and not getattr(cap, "klein_model", None):
        logger.info("[infographic] L4 skipped — Klein not installed; degrading")
        return None

    # Gemma compresses the request into a Klein-optimal, TEXTLESS art brief
    # (drops every label/caption request — §2.2). Fail-open to the raw seed.
    try:
        brief = await write_klein_brief(seed, overlay_title or "Illustration", capability=cap)
    except Exception as e:
        logger.warning(f"[infographic] L4 brief failed: {e}")
        brief = None
    prompt = brief or seed

    # Belt-and-braces text suppression: even with a clean brief, tell Klein
    # explicitly to render no glyphs. Typography is the SVG/DOM overlay's job.
    negative = (
        f"{DEFAULT_NEGATIVE_PROMPT}, no text in image, no labels, no captions, "
        "no readable words, no typography in image, no signs, no logos, "
        "no character text, no written language"
    )

    try:
        result = await klein_diffusion.generate(
            prompt=prompt,
            capability=cap,
            aspect_ratio=aspect_ratio,
            quality_tier=quality_tier,
            negative_prompt=negative,
            unload_after=True,
        )
    except Exception as e:
        logger.warning(f"[infographic] L4 Klein generate raised: {e}")
        return None
    if not result or not result.success or not result.png_bytes:
        logger.info(
            f"[infographic] L4 Klein produced no image: {getattr(result, 'error', None)}"
        )
        return None

    import base64
    b64 = base64.b64encode(result.png_bytes).decode("ascii")

    # Overlay title = a SHORT poster title, NOT the raw request (which is a long
    # instruction). Generated on the fast model; fails open to no overlay so a
    # noisy label never drags the image down. The raw request still seeded the
    # (better) art brief above.
    short_title = await _poster_title(overlay_title or trimmed, settings.ollama_fast_model)

    payload = {
        "lane": "L4",
        "archetype": "decorative",
        "image": f"data:image/png;base64,{b64}",
        "width": result.width,
        "height": result.height,
        # Title text is a DOM/SVG overlay layer — NOT baked into the raster.
        "title_overlay": short_title,
        "prompt_used": result.prompt_used,
        "sources": _normalize_sources(sources),
    }
    art = json_artifact(
        id=_new_id(), kind="infographic", payload=payload,
        title=(short_title or "Decorative image")[:80],
        metadata={"lane": "L4", "archetype": "decorative", "model": result.model},
    )
    return art.model_dump()


# ── top-level with the degradation ladder ──────────────────────────────
async def build_infographic(
    content: str,
    lane: str,
    *,
    archetype: Optional[str] = None,
    model: Optional[str] = None,
    sources: Optional[list] = None,
    title: Optional[str] = None,
    accent_art: bool = False,
) -> dict:
    """Build an infographic Artifact for the given lane, applying the §3.5
    degradation ladder. Always returns a dict (never None, never raises).

    `sources` is the real provenance behind every citation — the caller
    (visual.py) resolves it from `context_builder` so each superscript maps
    to an actual notebook source (HARD RULE §2.6). It rides through every
    rung of the degradation ladder unchanged.

    `title` seeds the L4 decorative overlay (the user's request text); it is
    ignored by the L1/L2 lanes."""
    content = content or ""
    lane = (lane or "L2").upper()

    if lane == "L4":
        try:
            out = await build_l4(content, title=title, model=model, sources=sources)  # L4 IS the decorative lane; accent n/a
            if out:
                return out
        except Exception as e:
            logger.warning(f"[infographic] L4 build error: {e}")
        # Klein unavailable/failed -> down the ladder to L2 -> prose.
        try:
            out = await build_l2(content, archetype, model=model, sources=sources, topic=title or "", accent_art=accent_art)
            if out:
                out.setdefault("metadata", {})["degraded_from"] = "L4"
                return out
        except Exception as e:
            logger.warning(f"[infographic] L4->L2 fallback error: {e}")
        return _prose_fallback(content, "decorative image unavailable", "L4", sources)

    if lane == "L3":
        try:
            out = await build_l3(content, model=model, sources=sources, title=title, topic=title or "", accent_art=accent_art)
            if out:
                return out
        except Exception as e:
            logger.warning(f"[infographic] L3 build error: {e}")
        # Scene unavailable/failed -> down the ladder to L2 -> prose (§3.5).
        try:
            out = await build_l2(content, archetype, model=model, sources=sources, topic=title or "", accent_art=accent_art)
            if out:
                out.setdefault("metadata", {})["degraded_from"] = "L3"
                return out
        except Exception as e:
            logger.warning(f"[infographic] L3->L2 fallback error: {e}")
        return _prose_fallback(content, "scene composition unavailable", "L3", sources)

    if lane == "L1":
        try:
            out = await build_l1(content, model=model, sources=sources, topic=title or "", accent_art=accent_art)
            if out:
                return out
        except Exception as e:
            logger.warning(f"[infographic] L1 build error: {e}")
        # L1 chart invalid -> try an L2 facts table -> prose
        try:
            out = await build_l2(content, "facts_table", model=model, sources=sources, topic=title or "", accent_art=accent_art)
            if out:
                out.setdefault("metadata", {})["degraded_from"] = "L1"
                return out
        except Exception as e:
            logger.warning(f"[infographic] L1->L2 fallback error: {e}")
        return _prose_fallback(content, "chart + table generation failed", "L1", sources)

    # Default lane = L2 (the volume lane).
    try:
        out = await build_l2(content, archetype, model=model, sources=sources, topic=title or "", accent_art=accent_art)
        if out:
            return out
    except Exception as e:
        logger.warning(f"[infographic] L2 build error: {e}")
    # L2 failed -> try one alternate archetype before prose.
    alt = "pipeline_compare" if (archetype or pick_archetype(content, title or "")) != "pipeline_compare" else "facts_table"
    try:
        out = await build_l2(content, alt, model=model, sources=sources, topic=title or "", accent_art=accent_art)
        if out:
            out.setdefault("metadata", {})["degraded_from"] = "L2"
            return out
    except Exception as e:
        logger.warning(f"[infographic] L2 alt-archetype error: {e}")
    return _prose_fallback(content, "slot-fill unusable for all archetypes", "L2", sources)
