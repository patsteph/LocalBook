"""Visual System v2 — slot-fill prompt library + helpers.

Per-idiom JSON-schema prompts the model uses to fill in skeleton
placeholders. Each prompt enumerates exact placeholder keys + verbosity
ceilings so the model produces predictable, slide-ready content.

Also exposes two small utilities used by SkeletonGenerator:
  _apply_slot_fill   — substitute {{KEY}} placeholders with values
  _has_unfilled_slots — sanity check after substitution

Extracted from visual_freeform.py in the v2 consolidation pass (file
size discipline — visual_freeform.py was over the 800-line budget).
"""
from __future__ import annotations


# Per-idiom slot-fill system prompts. Each one tells Olmo exactly which
# placeholder slots to populate and the verbosity ceiling per slot.
_OLMO_SLOTFILL_SYSTEMS = {
    "linear_process": """You are filling slots in a pre-built infographic showing 5 stages of a process. Each stage has a short LABEL and one BODY block that wraps to ~3 lines (max 12 words). Return JSON with these exact keys:

{
  "TITLE": "max 8 words, concrete",
  "SUBTITLE": "max 12 words",
  "SECTION_LABEL": "max 5 words, names the process",
  "STAGE_1_LABEL": "max 4 words",
  "STAGE_1_LINE_1": "max 12 words (will wrap to 2-3 lines)",
  "STAGE_2_LABEL": "max 4 words",
  "STAGE_2_LINE_1": "max 12 words",
  "STAGE_3_LABEL": "max 4 words",
  "STAGE_3_LINE_1": "max 12 words",
  "STAGE_4_LABEL": "max 4 words",
  "STAGE_4_LINE_1": "max 12 words",
  "STAGE_5_LABEL": "max 4 words",
  "STAGE_5_LINE_1": "max 12 words"
}

Hard limits — text that overflows looks broken in the rendered slide. Concrete short labels (e.g., "Lead Capture") beat vague long ones ("The lead capture and qualification stage"). The body lines wrap automatically — write naturally, but keep under 12 words.""",

    "comparison_matrix": """You are filling slots in a comparison matrix with 3 options × 5 attributes. Return JSON:

{
  "TITLE": "max 8 words",
  "SUBTITLE": "max 12 words",
  "OPTION_1": "max 3 words, the first option name",
  "OPTION_2": "max 3 words",
  "OPTION_3": "max 3 words",
  "ATTRIBUTE_1": "max 4 words, the first attribute name",
  "ATTRIBUTE_2": "max 4 words",
  "ATTRIBUTE_3": "max 4 words",
  "ATTRIBUTE_4": "max 4 words",
  "ATTRIBUTE_5": "max 4 words",
  "CELL_1_1": "max 5 words, value of OPTION_1 on ATTRIBUTE_1",
  "CELL_1_2": "max 5 words, value of OPTION_2 on ATTRIBUTE_1",
  "CELL_1_3": "max 5 words, value of OPTION_3 on ATTRIBUTE_1",
  "CELL_2_1": "max 5 words", "CELL_2_2": "max 5 words", "CELL_2_3": "max 5 words",
  "CELL_3_1": "max 5 words", "CELL_3_2": "max 5 words", "CELL_3_3": "max 5 words",
  "CELL_4_1": "max 5 words", "CELL_4_2": "max 5 words", "CELL_4_3": "max 5 words",
  "CELL_5_1": "max 5 words", "CELL_5_2": "max 5 words", "CELL_5_3": "max 5 words"
}

Cells are values, not full sentences. ("Sub-second" not "Latency is sub-second under load").""",

    "swimlane": """You are filling slots in a swimlane diagram with 3 actors and 4 steps per actor. Return JSON:

{
  "TITLE": "max 8 words",
  "SUBTITLE": "max 12 words",
  "LANE_1_LABEL": "max 3 words, the first actor",
  "LANE_2_LABEL": "max 3 words",
  "LANE_3_LABEL": "max 3 words",
  "LANE_1_STEP_1": "max 4 words", "LANE_1_STEP_1_DETAIL": "max 4 words",
  "LANE_1_STEP_2": "max 4 words", "LANE_1_STEP_2_DETAIL": "max 4 words",
  "LANE_1_STEP_3": "max 4 words", "LANE_1_STEP_3_DETAIL": "max 4 words",
  "LANE_1_STEP_4": "max 4 words", "LANE_1_STEP_4_DETAIL": "max 4 words",
  "LANE_2_STEP_1": "max 4 words", "LANE_2_STEP_1_DETAIL": "max 4 words",
  "LANE_2_STEP_2": "max 4 words", "LANE_2_STEP_2_DETAIL": "max 4 words",
  "LANE_2_STEP_3": "max 4 words", "LANE_2_STEP_3_DETAIL": "max 4 words",
  "LANE_2_STEP_4": "max 4 words", "LANE_2_STEP_4_DETAIL": "max 4 words",
  "LANE_3_STEP_1": "max 4 words", "LANE_3_STEP_1_DETAIL": "max 4 words",
  "LANE_3_STEP_2": "max 4 words", "LANE_3_STEP_2_DETAIL": "max 4 words",
  "LANE_3_STEP_3": "max 4 words", "LANE_3_STEP_3_DETAIL": "max 4 words",
  "LANE_3_STEP_4": "max 4 words", "LANE_3_STEP_4_DETAIL": "max 4 words"
}""",

    "layered_architecture": """You are filling slots in a 4-layer architecture diagram with 4 components per layer. Return JSON:

{
  "TITLE": "max 8 words",
  "SUBTITLE": "max 12 words",
  "LAYER_1_LABEL": "max 3 words, the top tier (e.g., 'Client Layer', 'Presentation')",
  "LAYER_2_LABEL": "max 3 words",
  "LAYER_3_LABEL": "max 3 words",
  "LAYER_4_LABEL": "max 3 words, the bottom tier",
  "LAYER_1_COMPONENT_1": "max 4 words", "LAYER_1_COMPONENT_1_ROLE": "max 5 words",
  "LAYER_1_COMPONENT_2": "max 4 words", "LAYER_1_COMPONENT_2_ROLE": "max 5 words",
  "LAYER_1_COMPONENT_3": "max 4 words", "LAYER_1_COMPONENT_3_ROLE": "max 5 words",
  "LAYER_1_COMPONENT_4": "max 4 words", "LAYER_1_COMPONENT_4_ROLE": "max 5 words",
  "LAYER_2_COMPONENT_1": "max 4 words", "LAYER_2_COMPONENT_1_ROLE": "max 5 words",
  "LAYER_2_COMPONENT_2": "max 4 words", "LAYER_2_COMPONENT_2_ROLE": "max 5 words",
  "LAYER_2_COMPONENT_3": "max 4 words", "LAYER_2_COMPONENT_3_ROLE": "max 5 words",
  "LAYER_2_COMPONENT_4": "max 4 words", "LAYER_2_COMPONENT_4_ROLE": "max 5 words",
  "LAYER_3_COMPONENT_1": "max 4 words", "LAYER_3_COMPONENT_1_ROLE": "max 5 words",
  "LAYER_3_COMPONENT_2": "max 4 words", "LAYER_3_COMPONENT_2_ROLE": "max 5 words",
  "LAYER_3_COMPONENT_3": "max 4 words", "LAYER_3_COMPONENT_3_ROLE": "max 5 words",
  "LAYER_3_COMPONENT_4": "max 4 words", "LAYER_3_COMPONENT_4_ROLE": "max 5 words",
  "LAYER_4_COMPONENT_1": "max 4 words", "LAYER_4_COMPONENT_1_ROLE": "max 5 words",
  "LAYER_4_COMPONENT_2": "max 4 words", "LAYER_4_COMPONENT_2_ROLE": "max 5 words",
  "LAYER_4_COMPONENT_3": "max 4 words", "LAYER_4_COMPONENT_3_ROLE": "max 5 words",
  "LAYER_4_COMPONENT_4": "max 4 words", "LAYER_4_COMPONENT_4_ROLE": "max 5 words"
}""",

    "concept_map": """You are filling slots in a hub-and-spoke concept map with 1 central hub and 6 surrounding concepts. Return JSON:

{
  "TITLE": "max 8 words",
  "SUBTITLE": "max 12 words",
  "HUB_LABEL": "max 4 words, the central concept",
  "SPOKE_1_LABEL": "max 4 words", "SPOKE_1_DETAIL": "max 5 words",
  "SPOKE_2_LABEL": "max 4 words", "SPOKE_2_DETAIL": "max 5 words",
  "SPOKE_3_LABEL": "max 4 words", "SPOKE_3_DETAIL": "max 5 words",
  "SPOKE_4_LABEL": "max 4 words", "SPOKE_4_DETAIL": "max 5 words",
  "SPOKE_5_LABEL": "max 4 words", "SPOKE_5_DETAIL": "max 5 words",
  "SPOKE_6_LABEL": "max 4 words", "SPOKE_6_DETAIL": "max 5 words"
}""",

    "microservices_mesh": """You are filling slots in a microservices mesh diagram with 1 central bus and 4 services (each with its own database). Return JSON:

{
  "TITLE": "max 8 words", "SUBTITLE": "max 12 words",
  "BUS_LABEL": "max 4 words (e.g., 'API Gateway', 'Event Bus')",
  "SERVICE_1_NAME": "max 3 words", "SERVICE_1_ROLE": "max 5 words",
  "DB_1": "max 3 words", "DB_1_TYPE": "max 3 words (e.g., 'PostgreSQL', 'Redis')",
  "SERVICE_2_NAME": "max 3 words", "SERVICE_2_ROLE": "max 5 words",
  "DB_2": "max 3 words", "DB_2_TYPE": "max 3 words",
  "SERVICE_3_NAME": "max 3 words", "SERVICE_3_ROLE": "max 5 words",
  "DB_3": "max 3 words", "DB_3_TYPE": "max 3 words",
  "SERVICE_4_NAME": "max 3 words", "SERVICE_4_ROLE": "max 5 words",
  "DB_4": "max 3 words", "DB_4_TYPE": "max 3 words"
}""",

    "request_flow": """You are filling slots in a request flow diagram with 5 components in sequence + 4 step labels. Return JSON:

{
  "TITLE": "max 8 words", "SUBTITLE": "max 12 words",
  "COMPONENT_1": "max 3 words", "COMPONENT_1_NOTE": "max 5 words",
  "COMPONENT_2": "max 3 words", "COMPONENT_2_NOTE": "max 5 words",
  "COMPONENT_3": "max 3 words", "COMPONENT_3_NOTE": "max 5 words",
  "COMPONENT_4": "max 3 words", "COMPONENT_4_NOTE": "max 5 words",
  "COMPONENT_5": "max 3 words", "COMPONENT_5_NOTE": "max 5 words",
  "STEP_1_LABEL": "max 4 words, describes step 1->2",
  "STEP_2_LABEL": "max 4 words, describes step 2->3",
  "STEP_3_LABEL": "max 4 words, describes step 3->4",
  "STEP_4_LABEL": "max 4 words, describes step 4->5"
}""",

    "journey_map": """You are filling slots in a customer journey map with 5 stages, each with a metric ABOVE and an owner BELOW. Return JSON:

{
  "TITLE": "max 8 words", "SUBTITLE": "max 12 words",
  "STAGE_1": "max 3 words", "STAGE_1_ACT_1": "max 4 words", "STAGE_1_ACT_2": "max 4 words", "STAGE_1_ACT_3": "max 4 words",
  "STAGE_1_METRIC": "max 4 chars (e.g., '37%', '4.2x', '12d')", "STAGE_1_METRIC_LABEL": "max 3 words", "STAGE_1_OWNER": "max 3 words",
  "STAGE_2": "max 3 words", "STAGE_2_ACT_1": "max 4 words", "STAGE_2_ACT_2": "max 4 words", "STAGE_2_ACT_3": "max 4 words",
  "STAGE_2_METRIC": "max 4 chars", "STAGE_2_METRIC_LABEL": "max 3 words", "STAGE_2_OWNER": "max 3 words",
  "STAGE_3": "max 3 words", "STAGE_3_ACT_1": "max 4 words", "STAGE_3_ACT_2": "max 4 words", "STAGE_3_ACT_3": "max 4 words",
  "STAGE_3_METRIC": "max 4 chars", "STAGE_3_METRIC_LABEL": "max 3 words", "STAGE_3_OWNER": "max 3 words",
  "STAGE_4": "max 3 words", "STAGE_4_ACT_1": "max 4 words", "STAGE_4_ACT_2": "max 4 words", "STAGE_4_ACT_3": "max 4 words",
  "STAGE_4_METRIC": "max 4 chars", "STAGE_4_METRIC_LABEL": "max 3 words", "STAGE_4_OWNER": "max 3 words",
  "STAGE_5": "max 3 words", "STAGE_5_ACT_1": "max 4 words", "STAGE_5_ACT_2": "max 4 words", "STAGE_5_ACT_3": "max 4 words",
  "STAGE_5_METRIC": "max 4 chars", "STAGE_5_METRIC_LABEL": "max 3 words", "STAGE_5_OWNER": "max 3 words"
}""",

    "decision_tree": """You are filling slots in a decision tree with 1 root question, 2 main branches (yes/no), and 2 sub-options per branch. Return JSON:

{
  "TITLE": "max 8 words", "SUBTITLE": "max 12 words",
  "ROOT_QUESTION": "max 6 words, must be answerable yes/no",
  "YES_LABEL": "max 2 words (typically 'Yes' or e.g., 'Approved')",
  "YES_ACTION": "max 4 words, what happens on yes",
  "YES_DETAIL": "max 5 words, how/why",
  "YES_NEXT_1": "max 3 words, follow-up option A",
  "YES_NEXT_2": "max 3 words, follow-up option B",
  "NO_LABEL": "max 2 words (typically 'No' or e.g., 'Rejected')",
  "NO_ACTION": "max 4 words",
  "NO_DETAIL": "max 5 words",
  "NO_NEXT_1": "max 3 words",
  "NO_NEXT_2": "max 3 words"
}""",

    "timeline": """You are filling slots in a horizontal timeline with 6 events. Each event has a date, title, and one body block that wraps to ~4 lines. Return JSON:

{
  "TITLE": "max 8 words", "SUBTITLE": "max 12 words",
  "DATE_1": "max 8 chars (e.g., '2024 Q1', 'Mar 2025')", "EVENT_1_TITLE": "max 4 words", "EVENT_1_LINE_1": "max 15 words (wraps to 3-4 lines)",
  "DATE_2": "max 8 chars", "EVENT_2_TITLE": "max 4 words", "EVENT_2_LINE_1": "max 15 words",
  "DATE_3": "max 8 chars", "EVENT_3_TITLE": "max 4 words", "EVENT_3_LINE_1": "max 15 words",
  "DATE_4": "max 8 chars", "EVENT_4_TITLE": "max 4 words", "EVENT_4_LINE_1": "max 15 words",
  "DATE_5": "max 8 chars", "EVENT_5_TITLE": "max 4 words", "EVENT_5_LINE_1": "max 15 words",
  "DATE_6": "max 8 chars", "EVENT_6_TITLE": "max 4 words", "EVENT_6_LINE_1": "max 15 words"
}""",

    "before_after": """You are filling slots in a before/after transformation diagram with 4 items per column. Return JSON:

{
  "TITLE": "max 8 words", "SUBTITLE": "max 12 words",
  "BEFORE_LABEL": "max 3 words (e.g., 'Legacy System')",
  "AFTER_LABEL": "max 3 words (e.g., 'New Platform')",
  "TRANSITION_LABEL": "max 4 words (the verb: 'Migrate to', 'Replaced by')",
  "BEFORE_ITEM_1": "max 4 words", "BEFORE_DETAIL_1": "max 5 words",
  "BEFORE_ITEM_2": "max 4 words", "BEFORE_DETAIL_2": "max 5 words",
  "BEFORE_ITEM_3": "max 4 words", "BEFORE_DETAIL_3": "max 5 words",
  "BEFORE_ITEM_4": "max 4 words", "BEFORE_DETAIL_4": "max 5 words",
  "AFTER_ITEM_1": "max 4 words", "AFTER_DETAIL_1": "max 5 words",
  "AFTER_ITEM_2": "max 4 words", "AFTER_DETAIL_2": "max 5 words",
  "AFTER_ITEM_3": "max 4 words", "AFTER_DETAIL_3": "max 5 words",
  "AFTER_ITEM_4": "max 4 words", "AFTER_DETAIL_4": "max 5 words"
}""",

    "pros_cons": """You are filling slots in a pros vs cons diagram with 4 items each side. Return JSON:

{
  "TITLE": "max 8 words", "SUBTITLE": "max 12 words",
  "PRO_1_TITLE": "max 4 words", "PRO_1_DETAIL": "max 5 words",
  "PRO_2_TITLE": "max 4 words", "PRO_2_DETAIL": "max 5 words",
  "PRO_3_TITLE": "max 4 words", "PRO_3_DETAIL": "max 5 words",
  "PRO_4_TITLE": "max 4 words", "PRO_4_DETAIL": "max 5 words",
  "CON_1_TITLE": "max 4 words", "CON_1_DETAIL": "max 5 words",
  "CON_2_TITLE": "max 4 words", "CON_2_DETAIL": "max 5 words",
  "CON_3_TITLE": "max 4 words", "CON_3_DETAIL": "max 5 words",
  "CON_4_TITLE": "max 4 words", "CON_4_DETAIL": "max 5 words"
}""",

    "quadrant_2x2": """You are filling slots in a 2x2 quadrant matrix with two axes and 4 plotted items (one per quadrant). Return JSON:

{
  "TITLE": "max 8 words", "SUBTITLE": "max 12 words",
  "X_AXIS_LOW": "max 3 words (e.g., 'Low Effort')",
  "X_AXIS_HIGH": "max 3 words (e.g., 'High Effort')",
  "Y_AXIS_LOW": "max 3 words (e.g., 'Low Impact')",
  "Y_AXIS_HIGH": "max 3 words (e.g., 'High Impact')",
  "QUADRANT_TL_LABEL": "max 3 words (top-left quadrant name, e.g., 'Quick Wins')",
  "QUADRANT_TR_LABEL": "max 3 words (top-right)",
  "QUADRANT_BL_LABEL": "max 3 words (bottom-left)",
  "QUADRANT_BR_LABEL": "max 3 words (bottom-right)",
  "ITEM_1": "max 3 words (item in TL quadrant)",
  "ITEM_2": "max 3 words (item in TR)",
  "ITEM_3": "max 3 words (item in BL)",
  "ITEM_4": "max 3 words (item in BR)"
}""",

    "tree_hierarchy": """You are filling slots in a top-down tree with 1 root, 3 children, and 2 leaves per child. Return JSON:

{
  "TITLE": "max 8 words", "SUBTITLE": "max 12 words",
  "ROOT_LABEL": "max 4 words, the top-level concept",
  "CHILD_1_LABEL": "max 3 words", "CHILD_1_LEAF_1": "max 3 words", "CHILD_1_LEAF_2": "max 3 words",
  "CHILD_2_LABEL": "max 3 words", "CHILD_2_LEAF_1": "max 3 words", "CHILD_2_LEAF_2": "max 3 words",
  "CHILD_3_LABEL": "max 3 words", "CHILD_3_LEAF_1": "max 3 words", "CHILD_3_LEAF_2": "max 3 words"
}""",

    "stat_callouts": """You are filling slots in a 3x2 grid of statistic callouts (6 large numbers). Return JSON:

{
  "TITLE": "max 8 words", "SUBTITLE": "max 12 words",
  "STAT_1_VALUE": "max 5 chars (e.g., '94%', '3.2M', '$42K')",
  "STAT_1_LABEL": "max 4 words", "STAT_1_NOTE": "max 5 words",
  "STAT_2_VALUE": "max 5 chars", "STAT_2_LABEL": "max 4 words", "STAT_2_NOTE": "max 5 words",
  "STAT_3_VALUE": "max 5 chars", "STAT_3_LABEL": "max 4 words", "STAT_3_NOTE": "max 5 words",
  "STAT_4_VALUE": "max 5 chars", "STAT_4_LABEL": "max 4 words", "STAT_4_NOTE": "max 5 words",
  "STAT_5_VALUE": "max 5 chars", "STAT_5_LABEL": "max 4 words", "STAT_5_NOTE": "max 5 words",
  "STAT_6_VALUE": "max 5 chars", "STAT_6_LABEL": "max 4 words", "STAT_6_NOTE": "max 5 words"
}""",

    "cqrs_pattern": """You are filling slots in a CQRS architecture diagram with 3 lanes (WRITE PATH on top, EVENT STORE in middle, READ PATH on bottom), 4 components per lane. Return JSON:

{
  "TITLE": "max 8 words", "SUBTITLE": "max 12 words",
  "WRITE_LANE_LABEL": "max 3 words (e.g., 'Write Path')",
  "WRITE_DESCRIPTION": "max 8 words",
  "WRITE_COMPONENT_1": "max 4 words", "WRITE_COMPONENT_1_ROLE": "max 5 words",
  "WRITE_COMPONENT_2": "max 4 words", "WRITE_COMPONENT_2_ROLE": "max 5 words",
  "WRITE_COMPONENT_3": "max 4 words", "WRITE_COMPONENT_3_ROLE": "max 5 words",
  "WRITE_COMPONENT_4": "max 4 words", "WRITE_COMPONENT_4_ROLE": "max 5 words",
  "EVENT_LANE_LABEL": "max 3 words (e.g., 'Event Store', 'Kafka Backbone')",
  "EVENT_DESCRIPTION": "max 8 words",
  "EVENT_COMPONENT_1": "max 4 words", "EVENT_COMPONENT_1_ROLE": "max 5 words",
  "EVENT_COMPONENT_2": "max 4 words", "EVENT_COMPONENT_2_ROLE": "max 5 words",
  "EVENT_COMPONENT_3": "max 4 words", "EVENT_COMPONENT_3_ROLE": "max 5 words",
  "EVENT_COMPONENT_4": "max 4 words", "EVENT_COMPONENT_4_ROLE": "max 5 words",
  "READ_LANE_LABEL": "max 3 words (e.g., 'Read Path', 'Query Path')",
  "READ_DESCRIPTION": "max 8 words",
  "READ_COMPONENT_1": "max 4 words", "READ_COMPONENT_1_ROLE": "max 5 words",
  "READ_COMPONENT_2": "max 4 words", "READ_COMPONENT_2_ROLE": "max 5 words",
  "READ_COMPONENT_3": "max 4 words", "READ_COMPONENT_3_ROLE": "max 5 words",
  "READ_COMPONENT_4": "max 4 words", "READ_COMPONENT_4_ROLE": "max 5 words"
}""",

    "value_proposition": """You are filling slots in a value-proposition hero visual: central icon + tagline + 3 benefit cards. Return JSON:

{
  "TITLE": "max 8 words", "SUBTITLE": "max 12 words",
  "ICON_GLYPH": "1-2 characters (an emoji or a single uppercase letter that symbolizes the subject — e.g., '↗', 'λ', 'AI')",
  "HERO_TAGLINE": "max 8 words, the central value statement",
  "HERO_SUPPORTING": "max 14 words supporting line",
  "BENEFIT_1_GLYPH": "1-2 chars",
  "BENEFIT_1_TITLE": "max 4 words", "BENEFIT_1_TAGLINE": "max 6 words",
  "BENEFIT_1_LINE_1": "max 18 words (wraps to 3-4 lines)",
  "BENEFIT_2_GLYPH": "1-2 chars",
  "BENEFIT_2_TITLE": "max 4 words", "BENEFIT_2_TAGLINE": "max 6 words",
  "BENEFIT_2_LINE_1": "max 18 words",
  "BENEFIT_3_GLYPH": "1-2 chars",
  "BENEFIT_3_TITLE": "max 4 words", "BENEFIT_3_TAGLINE": "max 6 words",
  "BENEFIT_3_LINE_1": "max 18 words"
}""",

    "hero_with_callouts": """You are filling slots in a hero infographic with one main image area (an AI image model will fill the raster automatically — do NOT describe the image) and 3 callout cards on the right. Return JSON:

{
  "TITLE": "max 8 words, concrete",
  "SUBTITLE": "max 12 words, audience-framing",
  "CALLOUT_1_TITLE": "max 4 words",
  "CALLOUT_1_LINE_1": "max 15 words (wraps to 3 lines)",
  "CALLOUT_2_TITLE": "max 4 words",
  "CALLOUT_2_LINE_1": "max 15 words",
  "CALLOUT_3_TITLE": "max 4 words",
  "CALLOUT_3_LINE_1": "max 15 words"
}

The HERO_IMAGE_B64 slot is reserved — leave it out of your output; the composer fills it post-hoc with a Klein-generated PNG.""",
}


def _olmo_slotfill_system(idiom_id: str) -> str:
    return _OLMO_SLOTFILL_SYSTEMS.get(idiom_id, _OLMO_SLOTFILL_SYSTEMS["linear_process"])


# ──────────────────────────────────────────────────────────────────────
# Per-idiom KEY slot validation
# ──────────────────────────────────────────────────────────────────────
# When >40% empty isn't sensitive enough (e.g., model returns generic
# headers but blanks every data row), fall back to a "must have at least N
# of these specific key slots filled" check per idiom. If a key slot set
# is mostly blank, the visual is unusable regardless of overall ratio.

# Idiom → (list of must-have-content slot keys, minimum filled count)
_KEY_SLOTS_BY_IDIOM: dict[str, tuple[list[str], int]] = {
    "linear_process": (
        [f"STAGE_{i}_LABEL" for i in range(1, 6)], 4
    ),
    "comparison_matrix": (
        [f"OPTION_{i}" for i in range(1, 4)] + [f"ATTRIBUTE_{i}" for i in range(1, 6)], 5
    ),
    "swimlane": (
        [f"LANE_{i}_LABEL" for i in range(1, 4)], 2
    ),
    "layered_architecture": (
        [f"LAYER_{i}_LABEL" for i in range(1, 5)], 3
    ),
    "concept_map": (
        ["HUB_LABEL"] + [f"SPOKE_{i}_LABEL" for i in range(1, 7)], 4
    ),
    "hero_with_callouts": (
        [f"CALLOUT_{i}_TITLE" for i in range(1, 4)], 2
    ),
    "microservices_mesh": (
        ["BUS_LABEL"] + [f"SERVICE_{i}_NAME" for i in range(1, 5)], 3
    ),
    "request_flow": (
        [f"COMPONENT_{i}" for i in range(1, 6)], 4
    ),
    "journey_map": (
        [f"STAGE_{i}" for i in range(1, 6)], 4
    ),
    "decision_tree": (
        ["ROOT_QUESTION", "YES_ACTION", "NO_ACTION"], 2
    ),
    "timeline": (
        [f"EVENT_{i}_TITLE" for i in range(1, 7)] + [f"DATE_{i}" for i in range(1, 7)], 6
    ),
    "before_after": (
        ["BEFORE_LABEL", "AFTER_LABEL"]
        + [f"BEFORE_ITEM_{i}" for i in range(1, 5)]
        + [f"AFTER_ITEM_{i}" for i in range(1, 5)],
        6,
    ),
    "pros_cons": (
        [f"PRO_{i}_TITLE" for i in range(1, 5)] + [f"CON_{i}_TITLE" for i in range(1, 5)], 4
    ),
    "quadrant_2x2": (
        ["X_AXIS_LOW", "X_AXIS_HIGH", "Y_AXIS_LOW", "Y_AXIS_HIGH"]
        + [f"ITEM_{i}" for i in range(1, 5)],
        5,
    ),
    "tree_hierarchy": (
        ["ROOT_LABEL"] + [f"CHILD_{i}_LABEL" for i in range(1, 4)], 3
    ),
    "stat_callouts": (
        [f"STAT_{i}_VALUE" for i in range(1, 7)] + [f"STAT_{i}_LABEL" for i in range(1, 7)], 6
    ),
    "cqrs_pattern": (
        ["WRITE_LANE_LABEL", "EVENT_LANE_LABEL", "READ_LANE_LABEL"]
        + [f"{lane}_COMPONENT_{i}"
           for lane in ("WRITE", "EVENT", "READ") for i in range(1, 5)],
        6,
    ),
    "value_proposition": (
        ["HERO_TAGLINE"] + [f"BENEFIT_{i}_TITLE" for i in range(1, 4)], 3
    ),
}


def validate_key_slots(idiom_id: str, slots: dict) -> tuple[bool, str]:
    """Return (passed, reason). When passed=False, the visual should fall
    back to the template path even if the overall empty-ratio looked OK."""
    spec = _KEY_SLOTS_BY_IDIOM.get(idiom_id)
    if not spec:
        return (True, "")  # No key-slot spec for this idiom; trust the ratio
    key_slots, min_filled = spec
    filled = sum(
        1 for k in key_slots
        if isinstance(slots.get(k), str) and slots[k].strip()
    )
    if filled < min_filled:
        return (
            False,
            f"key-slot validation: only {filled}/{len(key_slots)} required "
            f"slots for {idiom_id} were populated (need at least {min_filled})",
        )
    return (True, "")


def _apply_slot_fill(skeleton: str, slots: dict) -> str:
    """Replace {{KEY}} placeholders in the skeleton with values from slots.
    Missing slots are left as-is (visible as {{KEY}}) so we can detect failures.
    """
    out = skeleton
    for key, value in slots.items():
        if not isinstance(value, str):
            continue
        # XML-escape minimal — & < > only, since values go inside <text>
        v = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        out = out.replace("{{" + key + "}}", v)
    return out


def _has_unfilled_slots(svg: str) -> bool:
    """True if any {{KEY}} placeholders remain in the SVG."""
    return "{{" in svg and "}}" in svg
