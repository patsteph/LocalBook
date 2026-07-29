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
from services.infographic.skeletons import ARCHETYPES, get_skeleton
from services.infographic import slotfill as sf

logger = logging.getLogger(__name__)

_SLOTFILL_TIMEOUT = 120.0
_SLOTFILL_NUM_PREDICT = 2000
_ALLOWED_ICONS = set(allowed_icon_names())


# ── helpers ────────────────────────────────────────────────────────────
def _new_id() -> str:
    return f"ig_{uuid.uuid4().hex[:10]}"


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def _run_slotfill(system: str, content: str, model: str) -> Optional[dict]:
    """One JSON slot-fill call. Fails open to None (caller degrades)."""
    try:
        result = await ollama_service.generate(
            prompt=(
                f"SOURCE CONTENT:\n{content}\n\n"
                "Fill in every slot from the schema based on the source content. "
                "Return JSON only."
            ),
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
    spec = sf.l2_key_slots(archetype)
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
    for icon_key, label_key in sf.l2_icon_slots(archetype):
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
def pick_archetype(content: str) -> str:
    low = (content or "").lower()
    if any(w in low for w in ("compile", "index", "stage", "pipeline stage", "offline", "request-time")):
        return "three_stage"
    if any(w in low for w in ("table", "facts", "figures", "revenue", "metric", "$", "filing", "quarter")):
        return "facts_table"
    return "pipeline_compare"


# ── L2 ─────────────────────────────────────────────────────────────────
async def build_l2(
    content: str,
    archetype: Optional[str] = None,
    *,
    model: Optional[str] = None,
    max_content_chars: int = 8000,
    sources: Optional[list] = None,
) -> Optional[dict]:
    archetype = archetype if archetype in ARCHETYPES else pick_archetype(content)
    skeleton = get_skeleton(archetype)
    if not skeleton:
        return None
    model = model or settings.ollama_model
    trimmed = (content or "")[:max_content_chars]
    prov = _normalize_sources(sources)

    slots = await _run_slotfill(sf.l2_system(archetype), trimmed, model)
    if not slots:
        return None
    ok, reason = _check_slots(archetype, slots)
    if not ok:
        logger.info(f"[infographic] L2 {archetype} slot check failed: {reason}")
        return None

    skeleton = _apply_icons(skeleton, archetype, slots)

    citations = []
    if archetype == "facts_table":
        # Bind each row's citation superscript to a REAL source number so
        # every number carries a source ID (HARD RULE §2.6). No provenance ->
        # empty superscripts rather than dangling [1][2][3].
        slots.update(_cite_slots_for_rows(prov, n_rows=3))
        citations = [
            {"id": i, "label": slots.get(f"ROW_{i}_LABEL", f"Fact {i}"),
             "cite": slots.get(f"CITE_{i}", "")}
            for i in range(1, 4)
        ]

    body = _finalize_body(skeleton, slots)

    title = (
        slots.get("TABLE_TITLE")
        or slots.get("SECTION_LABEL")
        or slots.get("STAGE_1_TITLE")
        or "Infographic"
    )

    payload = {
        "lane": "L2",
        "archetype": archetype,
        "body_html": body,
        "citations": citations,
        "sources": prov,
    }
    art = json_artifact(
        id=_new_id(), kind="infographic", payload=payload,
        title=str(title)[:80],
        metadata={"lane": "L2", "archetype": archetype},
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
) -> Optional[dict]:
    from services.structured_llm import structured_llm

    trimmed = (content or "")[:max_content_chars]
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

    # Annotation text (coordinate-free; anchors computed by the renderer).
    ann = await _run_slotfill(sf.L1_ANNOTATION_SYSTEM, trimmed, model or settings.ollama_model)
    ann = ann or {}
    annotations = {
        "callout_text": (ann.get("CALLOUT_TEXT") or "").strip(),
        "callout_subtext": (ann.get("CALLOUT_SUBTEXT") or "").strip(),
        "primary_label": (ann.get("PRIMARY_LABEL") or (series[0].get("label") if series else "")).strip(),
        "baseline_label": (ann.get("BASELINE_LABEL") or (series[1].get("label") if len(series) > 1 else "")).strip(),
        "midpoint_note": (ann.get("MIDPOINT_NOTE") or "").strip(),
    }

    payload = {
        "lane": "L1",
        "archetype": "annotated_chart",
        "chart": chart,
        "annotations": annotations,
        "sources": _normalize_sources(sources),
    }
    art = json_artifact(
        id=_new_id(), kind="infographic", payload=payload,
        title=str(chart.get("title") or "Annotated chart")[:80],
        metadata={"lane": "L1", "archetype": "annotated_chart"},
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
) -> dict:
    """Build an infographic Artifact for the given lane, applying the §3.5
    degradation ladder. Always returns a dict (never None, never raises).

    `sources` is the real provenance behind every citation — the caller
    (visual.py) resolves it from `context_builder` so each superscript maps
    to an actual notebook source (HARD RULE §2.6). It rides through every
    rung of the degradation ladder unchanged."""
    content = content or ""
    lane = (lane or "L2").upper()

    if lane == "L1":
        try:
            out = await build_l1(content, model=model, sources=sources)
            if out:
                return out
        except Exception as e:
            logger.warning(f"[infographic] L1 build error: {e}")
        # L1 chart invalid -> try an L2 facts table -> prose
        try:
            out = await build_l2(content, "facts_table", model=model, sources=sources)
            if out:
                out.setdefault("metadata", {})["degraded_from"] = "L1"
                return out
        except Exception as e:
            logger.warning(f"[infographic] L1->L2 fallback error: {e}")
        return _prose_fallback(content, "chart + table generation failed", "L1", sources)

    # Default lane = L2 (the volume lane).
    try:
        out = await build_l2(content, archetype, model=model, sources=sources)
        if out:
            return out
    except Exception as e:
        logger.warning(f"[infographic] L2 build error: {e}")
    # L2 failed -> try one alternate archetype before prose.
    alt = "pipeline_compare" if (archetype or pick_archetype(content)) != "pipeline_compare" else "facts_table"
    try:
        out = await build_l2(content, alt, model=model, sources=sources)
        if out:
            out.setdefault("metadata", {})["degraded_from"] = "L2"
            return out
    except Exception as e:
        logger.warning(f"[infographic] L2 alt-archetype error: {e}")
    return _prose_fallback(content, "slot-fill unusable for all archetypes", "L2", sources)
