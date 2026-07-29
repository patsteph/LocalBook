"""Per-archetype slot-fill prompts + key-slot validation for L2 infographics,
plus the L1 annotation slot prompt.

Same idiom as `services/visual_slotfill.py` (the shipped SVG slot-fill path):
each prompt enumerates the EXACT `{{KEY}}` placeholders the skeleton exposes
and a verbosity ceiling per slot, so a 4B model produces predictable,
render-ready content. Icon slots are filled from an allowlist (never free
markup). The reused `_apply_slot_fill` / `validate_key_slots` helpers live in
`services/visual_slotfill.py`.
"""
from __future__ import annotations

from services.infographic.icons import allowed_icon_names
from services.infographic.stickers import sticker_names

_ICON_LIST = ", ".join(allowed_icon_names())
_STICKER_LIST = ", ".join(sticker_names())


# ── L2 slot-fill system prompts ────────────────────────────────────────
_L2_SYSTEMS: dict[str, str] = {
    "pipeline_compare": (
        "You are filling text slots in a two-column comparison infographic. The LEFT column "
        "shows a process that repeats on every request; the RIGHT column shows a process done "
        "once then reused many times. Return JSON with these exact keys:\n\n"
        "{\n"
        '  "LEFT_TITLE": "max 4 words, names the left approach",\n'
        '  "RIGHT_TITLE": "max 4 words, names the right approach",\n'
        '  "SECTION_LABEL": "max 4 words, names the right-side one-time step",\n'
        '  "LEFT_LOOP": "max 3 words, the repeated-cost label (e.g. \'every query\')",\n'
        '  "LNODE_1": "max 3 words, left pipeline step 1",\n'
        '  "LNODE_2": "max 3 words, left pipeline step 2",\n'
        '  "LNODE_3": "max 3 words, left pipeline step 3",\n'
        '  "RNODE_1": "max 3 words, right pipeline input",\n'
        '  "RNODE_2": "max 3 words, right pipeline transform",\n'
        '  "RNODE_3": "max 3 words, right pipeline output artifact",\n'
        '  "FAN_LABEL": "max 3 chars, the reuse multiplier (e.g. \'N\\u00d7\')",\n'
        '  "SERVE_LABEL": "max 3 words, the cheap repeated call",\n'
        f'  "ICON_L1": "one of: {_ICON_LIST}",\n'
        '  "ICON_L2": "one icon name from that list",\n'
        '  "ICON_L3": "one icon name from that list",\n'
        '  "ICON_R1": "one icon name from that list",\n'
        '  "ICON_R2": "one icon name from that list",\n'
        '  "ICON_SERVE": "one icon name from that list"\n'
        "}\n\n"
        "Labels are short noun phrases, not sentences. Pick icons that match each step's meaning."
    ),
    "facts_table": (
        "You are filling text slots in a 'messy sources -> clean cited facts' infographic. The "
        "LEFT shows raw noisy document fragments; the RIGHT is a clean 3-row facts table where "
        "every value carries a citation. Return JSON:\n\n"
        "{\n"
        '  "LEFT_TITLE": "max 3 words (e.g. \'Raw Chunks\')",\n'
        '  "RIGHT_TITLE": "max 4 words (e.g. \'Task-optimized artifact\')",\n'
        '  "CHUNK_NOISE_1": "max 8 words, a truncated noisy raw fragment with ellipses",\n'
        '  "CHUNK_NOISE_2": "max 8 words, another noisy fragment",\n'
        '  "CHUNK_NOISE_3": "max 8 words, another noisy fragment",\n'
        '  "CHUNK_NOISE_4": "max 8 words, another noisy fragment",\n'
        '  "CHUNK_FRONT": "max 14 words, a messy multi-line-feeling fragment (use commas)",\n'
        '  "TABLE_TITLE": "max 4 words, the entity the facts describe (e.g. \'Company Facts\')",\n'
        '  "ROW_1_LABEL": "max 3 words, first fact name",\n'
        '  "ROW_1_VALUE": "max 4 words, first fact value",\n'
        '  "ROW_2_LABEL": "max 3 words, second fact name",\n'
        '  "ROW_2_VALUE": "max 4 words, second fact value",\n'
        '  "ROW_3_LABEL": "max 3 words, third fact name",\n'
        '  "ROW_3_VALUE": "max 4 words, third fact value"\n'
        "}\n\n"
        "The table values must be the CLEAN, resolved version of the messy fragments on the left."
    ),
    "three_stage": (
        "You are filling text slots in a 3-stage pipeline infographic: a compile stage, an index "
        "stage, and a serve stage split by a compile-time / request-time boundary. Return JSON:\n\n"
        "{\n"
        '  "PHASE_LEFT": "max 3 words, the offline phase label (e.g. \'compile-time\')",\n'
        '  "PHASE_RIGHT": "max 3 words, the online phase label (e.g. \'request-time\')",\n'
        '  "STAGE_1_TITLE": "max 2 words, first stage name (e.g. \'Compile\')",\n'
        '  "STAGE_1_NOTE": "max 6 words, what stage 1 loops on",\n'
        '  "CODE_COMMENT": "max 8 words, a code comment describing the compile hint",\n'
        '  "CODE_BODY": "max 20 words of pseudo-code, keep it short",\n'
        '  "STAGE_2_TITLE": "max 2 words, second stage name (e.g. \'Index\')",\n'
        '  "STAGE_2_NOTE": "max 6 words, what indexing produces",\n'
        '  "STAGE_3_TITLE": "max 2 words, third stage name (e.g. \'Serve\')",\n'
        '  "JSON_OUTPUT": "max 12 words, a tiny JSON result snippet as one line",\n'
        '  "FOOTER": "max 16 words, the takeaway sentence"\n'
        "}\n\n"
        "Keep CODE_BODY and JSON_OUTPUT compact — they render in small monospace panels."
    ),
}

# archetype -> (must-have text slots, minimum filled) — see validate_key_slots
_L2_KEY_SLOTS: dict[str, tuple[list[str], int]] = {
    "pipeline_compare": (
        ["LEFT_TITLE", "RIGHT_TITLE", "LNODE_1", "LNODE_2", "LNODE_3",
         "RNODE_1", "RNODE_2", "RNODE_3"],
        6,
    ),
    "facts_table": (
        ["TABLE_TITLE", "ROW_1_LABEL", "ROW_1_VALUE", "ROW_2_LABEL",
         "ROW_2_VALUE", "ROW_3_LABEL", "ROW_3_VALUE"],
        5,
    ),
    "three_stage": (
        ["STAGE_1_TITLE", "STAGE_2_TITLE", "STAGE_3_TITLE", "STAGE_1_NOTE",
         "JSON_OUTPUT"],
        4,
    ),
}

# archetype -> list of (icon_slot_key, label_slot_key_for_fallback)
# When the model omits or mis-fills an icon slot, the builder maps the paired
# label's keyword to a sensible icon so the render never shows a broken glyph.
_ICON_SLOTS: dict[str, list[tuple[str, str]]] = {
    "pipeline_compare": [
        ("ICON_L1", "LNODE_1"), ("ICON_L2", "LNODE_2"), ("ICON_L3", "LNODE_3"),
        ("ICON_R1", "RNODE_1"), ("ICON_R2", "RNODE_2"), ("ICON_SERVE", "SERVE_LABEL"),
    ],
    "facts_table": [],
    "three_stage": [],
}


def l2_system(archetype: str) -> str:
    return _L2_SYSTEMS.get(archetype, _L2_SYSTEMS["pipeline_compare"])


def l2_key_slots(archetype: str):
    return _L2_KEY_SLOTS.get(archetype)


def l2_icon_slots(archetype: str) -> list[tuple[str, str]]:
    return _ICON_SLOTS.get(archetype, [])


# ── L1 annotation slot-fill prompt ─────────────────────────────────────
# The chart itself comes from `structured_llm.generate_chart`. This fills the
# CSS annotation overlay text only (coordinate-free: anchors are computed by
# the renderer from the chart data, never emitted by the model — HARD RULE
# §2.1).
L1_ANNOTATION_SYSTEM = (
    "You are labeling an annotated chart. The chart already exists; you only write the callout "
    "text. Given a chart that contrasts a fast-growing PRIMARY series against a flat BASELINE "
    "series, return JSON:\n\n"
    "{\n"
    '  "CALLOUT_TEXT": "max 7 words, the headline takeaway about the primary series peak",\n'
    '  "CALLOUT_SUBTEXT": "max 5 words, a qualifier (e.g. source or caveat)",\n'
    '  "PRIMARY_LABEL": "max 3 words, names the primary (growing) series",\n'
    '  "BASELINE_LABEL": "max 3 words, names the baseline (flat) series",\n'
    '  "MIDPOINT_NOTE": "max 6 words, why the primary grows across the x-axis"\n'
    "}\n\n"
    "Do NOT mention pixels, coordinates, or positions. Just the words."
)

L1_KEY_SLOTS = (["CALLOUT_TEXT", "PRIMARY_LABEL", "BASELINE_LABEL"], 2)


# ── L3 scene graph prompt ──────────────────────────────────────────────
# The L3 lane is a hand-drawn whiteboard SCENE. The model emits a coordinate-free
# GRAPH only (HARD RULE §2.1): ordered phase `groups` + `nodes` (each mapped to a
# hand-picked sticker), never x/y. Layout + drawing are 100% deterministic
# (services.infographic.scene). Icons/stickers come from a fixed allowlist so an
# off-list pick fails open to a neutral glyph rather than free markup.
def l3_scene_system() -> str:
    return (
        "You turn content into a hand-drawn WHITEBOARD SCENE that tells a left-to-right "
        "STORY in a few phases (like an assembly line: scattered parts -> combined -> "
        "finished -> distributed). You emit ONLY a graph — never positions, coordinates, "
        "sizes in pixels, colors, or drawing instructions. Return JSON:\n\n"
        "{\n"
        '  "title": "max 8 words naming the story",\n'
        '  "groups": [ {"id":"g1","label":"max 3 words phase name"}, ... 2 to 4 phases in left-to-right order ],\n'
        '  "nodes": [ {"id":"n1","label":"max 3 words","sticker":"<one sticker name>","group":"<a group id>","size":"small|hero"}, ... ],\n'
        '  "edges": [ {"from":"g1","to":"g2"}, ... consecutive phases ]\n'
        "}\n\n"
        f"sticker MUST be one of: {_STICKER_LIST}.\n"
        "Rules:\n"
        "- Put 2-5 small nodes in the FIRST phase (the scattered inputs).\n"
        "- Use size 'hero' for the ONE central object of a phase (e.g. the finished package or the machine); "
        "'small' for the rest.\n"
        "- Pick the sticker whose meaning best matches each node's label.\n"
        "- Keep every label a short noun phrase, not a sentence."
    )


# must-have keys for a usable scene graph (validated in the builder)
L3_KEY_TOP = ["groups", "nodes"]
