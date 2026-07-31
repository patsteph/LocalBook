"""L2 infographic HTML skeletons — one per archetype, styled ONLY by the
design-system classes in `design_system.py`.

These mirror the three reference L2 targets (2026-07-28):
  - pipeline_compare  (07.33.01)  two-column "runtime vs compile-time"
  - facts_table       (07.33.34)  messy chunks vs clean cited table
  - three_stage       (07.33.48)  Compile -> Index -> Serve

The LLM never emits coordinates or CSS (HARD RULE §2.1/§2.3). It only fills
`{{UPPER_SNAKE}}` text slots (via the shared `visual_slotfill._apply_slot_fill`
mechanism). `{{ICON_*}}` slots are replaced by the builder with inline Lucide
SVG chosen from an allowlist — never free markup from the model. Structural
arrows are baked in below.
"""
from __future__ import annotations

from services.infographic.icons import allowed_icon_names

# The allowlist offered to the model for `{{ICON_*}}` slots (mirrors slotfill).
_ICON_LIST = ", ".join(allowed_icon_names())

# Baked-in structural glyphs (not model-controlled).
_ARROW_R = (
    '<span class="ib-arrow"><svg viewBox="0 0 24 20" aria-hidden="true">'
    '<path d="M3 10h16"/><path d="m14 4 6 6-6 6"/></svg></span>'
)
_ARROW_R_ACCENT = (
    '<span class="ib-arrow ib-arrow--accent"><svg viewBox="0 0 24 20" aria-hidden="true">'
    '<path d="M3 10h16"/><path d="m14 4 6 6-6 6"/></svg></span>'
)
_STAGE_ARROW = (
    '<div class="ib-stage-arrow"><svg viewBox="0 0 24 20" aria-hidden="true">'
    '<path d="M3 10h16"/><path d="m14 4 6 6-6 6"/></svg></div>'
)
_DOC_CHIP = (
    '<span class="ib-doc-chip"><svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
    '<path d="M14 2v4a2 2 0 0 0 2 2h4"/></svg>[{n}]</span>'
)

# Curved coral "repeats-every-query" loop arrow (structural; not model-controlled).
_LOOP_ARROW = (
    '<div class="ib-loop-arrow"><svg viewBox="0 0 42 26" aria-hidden="true">'
    '<path d="M6 5a15 15 0 0 1 30 4c0 6-5 10-11 11"/>'
    '<path d="m28 17-3 6 7 1"/></svg></div>'
)
# One-to-three branching coral arrow (structural) under the compile-once card.
_BRANCH = (
    '<div class="ib-branch"><svg viewBox="0 0 200 38" aria-hidden="true">'
    '<path d="M100 2v9"/>'
    '<path d="M100 11H26a8 8 0 0 0-8 8v7"/>'
    '<path d="M100 11h74a8 8 0 0 1 8 8v7"/>'
    '<path d="M100 11v15"/>'
    '<path d="m13 26 5 8 5-8"/>'
    '<path d="m95 26 5 8 5-8"/>'
    '<path d="m177 26 5 8 5-8"/></svg></div>'
)
# Static embedding-matrix heatmap (structural) layered under the Index glyph.
_HEATMAP = (
    '<div class="ib-heatmap">'
    '<i class="h2"></i><i></i><i class="h3"></i><i class="h4"></i><i></i><i class="h2"></i>'
    '<i></i><i class="h3"></i><i class="h4"></i><i class="h2"></i><i class="h3"></i><i></i>'
    '<i class="h3"></i><i class="h4"></i><i></i><i class="h2"></i><i class="h4"></i><i class="h3"></i>'
    '</div>'
)


# ── Archetype 1 — pipeline_compare (07.33.01) ──────────────────────────
_PIPELINE_COMPARE = """
<div class="ib">
  <div class="ib-compare">
    <!-- LEFT: runtime retrieval, repeated every query -->
    <div class="ib-col">
      <div class="ib-section-label">{{LEFT_TITLE}}</div>
      <div class="ib-looped">
        __LEFT_PIPE__
        __LOOP_ARROW__
        __LEFT_PIPE__
        __LOOP_ARROW__
        __LEFT_PIPE__
      </div>
      <div class="ib-loop"><span>{{LEFT_LOOP}}</span></div>
    </div>

    <div class="ib-vrule"></div>

    <!-- RIGHT: compile once, serve N times -->
    <div class="ib-col">
      <div class="ib-section-label">{{RIGHT_TITLE}}</div>
      <div class="ib-card ib-bracketed">
        <div class="ib-section-label">{{SECTION_LABEL}}</div>
        <div class="ib-flowrow">
          <div class="ib-node">{{ICON_R1}}<div class="ib-node-label">{{RNODE_1}}</div></div>
          __ARROW_R__
          <div class="ib-node">{{ICON_R2}}<div class="ib-node-label">{{RNODE_2}}</div></div>
          __ARROW_R__
          <div class="ib-node"><span class="ib-icon ib-icon--accent">__GEM__</span><div class="ib-node-label">{{RNODE_3}}</div></div>
        </div>
      </div>
      <div class="ib-fan-label"><b>{{FAN_LABEL}}</b></div>
      __BRANCH__
      <div class="ib-fan">
        <div class="ib-card ib-bracketed ib-center" style="flex-direction:column;gap:8px;padding:16px 10px">{{ICON_SERVE}}<div class="ib-node-label">{{SERVE_LABEL}}</div></div>
        <div class="ib-card ib-bracketed ib-center" style="flex-direction:column;gap:8px;padding:16px 10px">{{ICON_SERVE}}<div class="ib-node-label">{{SERVE_LABEL}}</div></div>
        <div class="ib-card ib-bracketed ib-center" style="flex-direction:column;gap:8px;padding:16px 10px">{{ICON_SERVE}}<div class="ib-node-label">{{SERVE_LABEL}}</div></div>
      </div>
    </div>
  </div>
</div>
"""

_LEFT_PIPE = """<div class="ib-card ib-bracketed">
        <div class="ib-flowrow">
          <div class="ib-node">{{ICON_L1}}<div class="ib-node-label">{{LNODE_1}}</div></div>
          __ARROW_R__
          <div class="ib-node">{{ICON_L2}}<div class="ib-node-label">{{LNODE_2}}</div></div>
          __ARROW_R__
          <div class="ib-node">{{ICON_L3}}<div class="ib-node-label">{{LNODE_3}}</div></div>
        </div>
      </div>"""

_GEM_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h12l4 6-10 13L2 9Z"/>'
    '<path d="M11 3 8 9l4 13 4-13-3-6"/><path d="M2 9h20"/></svg>'
)


# ── Archetype 2 — facts_table (07.33.34) ───────────────────────────────
_FACTS_TABLE = """
<div class="ib">
  <div class="ib-compare">
    <!-- LEFT: messy raw chunks -->
    <div class="ib-col">
      <div class="ib-section-label">{{LEFT_TITLE}}</div>
      <div class="ib-bracketed" style="padding:6px">
        <div class="ib-messy">
          <div class="ib-chunk ib-chunk--1">{{CHUNK_NOISE_1}}</div>
          <div class="ib-chunk ib-chunk--2">{{CHUNK_NOISE_2}}</div>
          <div class="ib-chunk ib-chunk--3">{{CHUNK_NOISE_3}}</div>
          <div class="ib-chunk ib-chunk--4">{{CHUNK_NOISE_4}}</div>
          <div class="ib-chunk ib-chunk--front">{{CHUNK_FRONT}}</div>
        </div>
      </div>
    </div>

    <div class="ib-vrule"></div>

    <!-- RIGHT: clean, cited facts table -->
    <div class="ib-col">
      <div class="ib-section-label">{{RIGHT_TITLE}}</div>
      <div class="ib-card ib-glow ib-bracketed">
        <table class="ib-table">
          <caption>{{TABLE_TITLE}}</caption>
          <tbody>
            <tr><td>{{ROW_1_LABEL}}</td><td>{{ROW_1_VALUE}}<sup class="ib-cite">{{CITE_1}}</sup></td></tr>
            <tr><td>{{ROW_2_LABEL}}</td><td>{{ROW_2_VALUE}}<sup class="ib-cite">{{CITE_2}}</sup></td></tr>
            <tr><td>{{ROW_3_LABEL}}</td><td>{{ROW_3_VALUE}}<sup class="ib-cite">{{CITE_3}}</sup></td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>
"""


# ── Archetype 3 — three_stage (07.33.48) ───────────────────────────────
_THREE_STAGE = """
<div class="ib">
  <div class="ib-row" style="justify-content:space-around;margin-bottom:14px">
    <div class="ib-section-label" style="margin:0">{{PHASE_LEFT}}</div>
    <div class="ib-section-label" style="margin:0">{{PHASE_RIGHT}}</div>
  </div>
  <div class="ib-stages">
    <!-- Compile -->
    <div class="ib-stage ib-bracketed">
      <h3 class="ib-stage-title">{{STAGE_1_TITLE}}</h3>
      <div class="ib-stage-body">
        <span class="ib-icon" style="width:56px;height:56px">__CYCLE__</span>
        <div class="ib-stage-note">{{STAGE_1_NOTE}}</div>
        <div class="ib-code"><span class="ib-comment">{{CODE_COMMENT}}</span>
{{CODE_BODY}}</div>
      </div>
    </div>
    __STAGE_ARROW__
    <!-- Index -->
    <div class="ib-stage ib-bracketed">
      <h3 class="ib-stage-title">{{STAGE_2_TITLE}}</h3>
      <div class="ib-stage-body">
        <span class="ib-icon" style="width:52px;height:52px">__NETWORK__</span>
        __HEATMAP__
        <div class="ib-stage-note">{{STAGE_2_NOTE}}</div>
      </div>
    </div>
    <div class="ib-row"><div class="ib-phase-divider"></div>__STAGE_ARROW__</div>
    <!-- Serve -->
    <div class="ib-stage ib-bracketed">
      <h3 class="ib-stage-title">{{STAGE_3_TITLE}}</h3>
      <div class="ib-stage-body">
        <span class="ib-icon ib-icon--accent" style="width:52px;height:52px">__ARROW_DOWN__</span>
        <div class="ib-json">{{JSON_OUTPUT}}</div>
        <div class="ib-row" style="gap:14px;margin-top:6px">__CHIP1____CHIP2____CHIP3__</div>
      </div>
    </div>
  </div>
  <div class="ib-subtitle" style="margin-top:16px">{{FOOTER}}</div>
</div>
"""

# ── Archetype 4 — stat_grid (KPI tiles) ────────────────────────────────
# A 2x2 grid of stat tiles: big number + label + a real citation superscript.
# Reuses ONLY design-system classes (ib-card / ib-bracketed / ib-center /
# ib-icon / ib-cite / ib-node-label) + inline layout; the CSS custom properties
# (--ib-ink / --ib-accent …) cascade from the `.ib` root, so inline styles that
# reference them stay on-palette without any new class.
def _repeat(tmpl: str, count: int) -> str:
    """Expand a `#N#`-tokened block for indices 1..count. Uses `.replace()` (not
    %-formatting) so literal CSS percents like `max-width:100%` pass through."""
    return "\n    ".join(tmpl.replace("#N#", str(i)) for i in range(1, count + 1))


_STAT_TILE = """<div class="ib-card ib-bracketed ib-center" style="flex-direction:column;gap:6px;padding:20px 14px">
        <span class="ib-icon ib-icon--accent">{{ICON_S#N#}}</span>
        <div style="font-size:32px;font-weight:800;color:var(--ib-ink);line-height:1.05;text-align:center">{{STAT_#N#_VALUE}}<sup class="ib-cite">{{CITE_#N#}}</sup></div>
        <div class="ib-node-label" style="max-width:150px">{{STAT_#N#_LABEL}}</div>
      </div>"""

_STAT_GRID = (
    '\n<div class="ib">\n'
    '  <div class="ib-section-label">{{GRID_TITLE}}</div>\n'
    '  <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;max-width:100%">\n'
    "    " + _repeat(_STAT_TILE, 4) + "\n"
    '  </div>\n'
    '  <div class="ib-subtitle" style="margin-top:16px">{{FOOTER}}</div>\n'
    "</div>\n"
)


# ── Archetype 5 — timeline (vertical chronology) ───────────────────────
# Each event is [date + dashed coral spine] | [bracketed card]. The spine is the
# existing `ib-phase-divider` (a dashed coral rule) so the column of events reads
# as one continuous timeline. Citation superscripts bind to real sources.
_TIMELINE_EVENT = """<div class="ib-row" style="align-items:stretch;gap:14px;margin-bottom:12px">
      <div class="ib-center" style="flex:0 0 76px;flex-direction:column;gap:8px">
        <span class="ib-icon ib-icon--accent ib-icon--sm">{{ICON_E#N#}}</span>
        <div style="font-weight:800;color:var(--ib-accent);font-size:13px;text-align:center;line-height:1.2">{{EVENT_#N#_DATE}}</div>
        <div class="ib-phase-divider" style="flex:1"></div>
      </div>
      <div class="ib-card ib-bracketed" style="flex:1;min-width:0">
        <div style="font-weight:700;color:var(--ib-ink)">{{EVENT_#N#_TITLE}}<sup class="ib-cite">{{CITE_#N#}}</sup></div>
        <div class="ib-note" style="margin-top:6px">{{EVENT_#N#_NOTE}}</div>
      </div>
    </div>"""

_TIMELINE = (
    '\n<div class="ib">\n'
    '  <div class="ib-section-label">{{TIMELINE_TITLE}}</div>\n'
    '  <div style="display:flex;flex-direction:column">\n'
    "    " + _repeat(_TIMELINE_EVENT, 4) + "\n"
    '  </div>\n'
    "</div>\n"
)


# ── Archetype 6 — tree_hierarchy (root -> 3 children) ──────────────────
# A glowing root card branches (via the baked-in coral `_BRANCH` glyph) into a
# 3-up `ib-fan` of child cards. Reuses ib-card / ib-glow / ib-fan / ib-icon.
_TREE_CHILD = """<div class="ib-card ib-bracketed ib-center" style="flex-direction:column;gap:8px;padding:16px 10px">
        <span class="ib-icon">{{ICON_C#N#}}</span>
        <div class="ib-node-label" style="font-weight:700;color:var(--ib-ink)">{{CHILD_#N#_LABEL}}</div>
        <div class="ib-node-label">{{CHILD_#N#_NOTE}}</div>
      </div>"""

_TREE_HIERARCHY = (
    '\n<div class="ib">\n'
    '  <div class="ib-section-label">{{TREE_TITLE}}</div>\n'
    '  <div class="ib-center" style="flex-direction:column;gap:0">\n'
    '    <div class="ib-card ib-glow ib-bracketed ib-center" style="flex-direction:column;gap:8px;padding:16px 24px;min-width:200px">\n'
    '      <span class="ib-icon ib-icon--accent">{{ICON_ROOT}}</span>\n'
    '      <div style="font-weight:800;font-size:17px;color:var(--ib-ink)">{{ROOT_LABEL}}</div>\n'
    '    </div>\n'
    "    __BRANCH__\n"
    '    <div class="ib-fan">\n'
    "      " + _repeat(_TREE_CHILD, 3) + "\n"
    '    </div>\n'
    '  </div>\n'
    "</div>\n"
)


_CYCLE_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
    '<path d="M21 3v5h-5"/>'
    '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>'
    '<path d="M8 16H3v5"/></svg>'
)
_NETWORK_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<rect x="16" y="16" width="6" height="6" rx="1"/>'
    '<rect x="2" y="16" width="6" height="6" rx="1"/>'
    '<rect x="9" y="2" width="6" height="6" rx="1"/>'
    '<path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/>'
    '<path d="M12 12V8"/></svg>'
)
_ARROW_DOWN_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14"/>'
    '<path d="m19 12-7 7-7-7"/></svg>'
)


def _expand_structural(html: str) -> str:
    """Substitute the non-model structural glyph markers."""
    return (
        html.replace("__LEFT_PIPE__", _LEFT_PIPE)
        .replace("__LOOP_ARROW__", _LOOP_ARROW)
        .replace("__BRANCH__", _BRANCH)
        .replace("__HEATMAP__", _HEATMAP)
        .replace("__ARROW_R__", _ARROW_R)
        .replace("__GEM__", _GEM_SVG)
        .replace("__STAGE_ARROW__", _STAGE_ARROW)
        .replace("__CYCLE__", _CYCLE_SVG)
        .replace("__NETWORK__", _NETWORK_SVG)
        .replace("__ARROW_DOWN__", _ARROW_DOWN_SVG)
        .replace("__CHIP1__", _DOC_CHIP.format(n=1))
        .replace("__CHIP2__", _DOC_CHIP.format(n=2))
        .replace("__CHIP3__", _DOC_CHIP.format(n=3))
    )


_SKELETONS = {
    "pipeline_compare": _PIPELINE_COMPARE,
    "facts_table": _FACTS_TABLE,
    "three_stage": _THREE_STAGE,
    "stat_grid": _STAT_GRID,
    "timeline": _TIMELINE,
    "tree_hierarchy": _TREE_HIERARCHY,
}

ARCHETYPES = tuple(_SKELETONS.keys())


def get_skeleton(archetype: str) -> str | None:
    """Return the structural-expanded skeleton (icon + text slots remain)."""
    raw = _SKELETONS.get(archetype)
    if raw is None:
        return None
    return _expand_structural(raw)


# ── Slot contract for the archetypes authored in THIS file ─────────────
# The original three archetypes' slot prompts/validation live in
# `services/infographic/slotfill.py`. The archetypes added here co-locate their
# slot contract with their skeleton (structure + contract in one place). The
# builder resolves an archetype's contract from HERE first, falling back to
# slotfill for the originals (`builder._sys/_key_slots/_icon_slots`).

# archetype -> LLM system prompt enumerating the EXACT text/icon slots to fill.
_L2_SYSTEMS_EXT: dict[str, str] = {
    "stat_grid": (
        "You are filling text slots in a KPI 'stat grid' infographic: four tiles, each a big "
        "headline NUMBER with a short label. Return JSON with these exact keys:\n\n"
        "{\n"
        '  "GRID_TITLE": "max 5 words naming the metric set (e.g. \'Key figures\')",\n'
        '  "STAT_1_VALUE": "max 3 words, a headline number (e.g. \'$1.2B\', \'98%\', \'3.4M\')",\n'
        '  "STAT_1_LABEL": "max 4 words, what the number measures",\n'
        '  "STAT_2_VALUE": "max 3 words, second headline number",\n'
        '  "STAT_2_LABEL": "max 4 words label",\n'
        '  "STAT_3_VALUE": "max 3 words, third headline number",\n'
        '  "STAT_3_LABEL": "max 4 words label",\n'
        '  "STAT_4_VALUE": "max 3 words, fourth headline number",\n'
        '  "STAT_4_LABEL": "max 4 words label",\n'
        '  "FOOTER": "max 14 words, a one-line takeaway or as-of note",\n'
        f'  "ICON_S1": "one of: {_ICON_LIST}",\n'
        '  "ICON_S2": "one icon name from that list",\n'
        '  "ICON_S3": "one icon name from that list",\n'
        '  "ICON_S4": "one icon name from that list"\n'
        "}\n\n"
        "Each VALUE must be a concise figure, not a sentence. Pick an icon that fits each stat."
    ),
    "timeline": (
        "You are filling text slots in a vertical TIMELINE infographic: four events in "
        "chronological order, each with a date, a short title, and a one-line note. Return JSON:\n\n"
        "{\n"
        '  "TIMELINE_TITLE": "max 5 words naming the timeline",\n'
        '  "EVENT_1_DATE": "max 2 words, the earliest date (e.g. a year)",\n'
        '  "EVENT_1_TITLE": "max 4 words, what happened first",\n'
        '  "EVENT_1_NOTE": "max 10 words of detail",\n'
        '  "EVENT_2_DATE": "max 2 words date",\n'
        '  "EVENT_2_TITLE": "max 4 words title",\n'
        '  "EVENT_2_NOTE": "max 10 words of detail",\n'
        '  "EVENT_3_DATE": "max 2 words date",\n'
        '  "EVENT_3_TITLE": "max 4 words title",\n'
        '  "EVENT_3_NOTE": "max 10 words of detail",\n'
        '  "EVENT_4_DATE": "max 2 words, the latest date",\n'
        '  "EVENT_4_TITLE": "max 4 words title",\n'
        '  "EVENT_4_NOTE": "max 10 words of detail",\n'
        f'  "ICON_E1": "one of: {_ICON_LIST}",\n'
        '  "ICON_E2": "one icon name from that list",\n'
        '  "ICON_E3": "one icon name from that list",\n'
        '  "ICON_E4": "one icon name from that list"\n'
        "}\n\n"
        "Order events oldest -> newest. Keep titles short noun phrases."
    ),
    "tree_hierarchy": (
        "You are filling text slots in a HIERARCHY infographic: one root concept that branches "
        "into three children. Return JSON:\n\n"
        "{\n"
        '  "TREE_TITLE": "max 5 words naming the hierarchy",\n'
        '  "ROOT_LABEL": "max 4 words, the parent / root concept",\n'
        '  "CHILD_1_LABEL": "max 3 words, first child",\n'
        '  "CHILD_1_NOTE": "max 6 words describing it",\n'
        '  "CHILD_2_LABEL": "max 3 words, second child",\n'
        '  "CHILD_2_NOTE": "max 6 words describing it",\n'
        '  "CHILD_3_LABEL": "max 3 words, third child",\n'
        '  "CHILD_3_NOTE": "max 6 words describing it",\n'
        f'  "ICON_ROOT": "one of: {_ICON_LIST}",\n'
        '  "ICON_C1": "one icon name from that list",\n'
        '  "ICON_C2": "one icon name from that list",\n'
        '  "ICON_C3": "one icon name from that list"\n'
        "}\n\n"
        "The three children must be sibling sub-parts OF the root. Keep labels to short phrases."
    ),
}

# archetype -> (must-have text slots, minimum filled) — see builder._check_slots
_L2_KEY_SLOTS_EXT: dict[str, tuple[list[str], int]] = {
    "stat_grid": (
        ["GRID_TITLE", "STAT_1_VALUE", "STAT_1_LABEL", "STAT_2_VALUE", "STAT_2_LABEL",
         "STAT_3_VALUE", "STAT_3_LABEL", "STAT_4_VALUE", "STAT_4_LABEL"],
        6,
    ),
    "timeline": (
        ["TIMELINE_TITLE", "EVENT_1_DATE", "EVENT_1_TITLE", "EVENT_2_DATE", "EVENT_2_TITLE",
         "EVENT_3_DATE", "EVENT_3_TITLE", "EVENT_4_DATE", "EVENT_4_TITLE"],
        6,
    ),
    "tree_hierarchy": (
        ["TREE_TITLE", "ROOT_LABEL", "CHILD_1_LABEL", "CHILD_2_LABEL", "CHILD_3_LABEL"],
        4,
    ),
}

# archetype -> list of (icon_slot_key, label_slot_key_for_fallback)
_ICON_SLOTS_EXT: dict[str, list[tuple[str, str]]] = {
    "stat_grid": [
        ("ICON_S1", "STAT_1_LABEL"), ("ICON_S2", "STAT_2_LABEL"),
        ("ICON_S3", "STAT_3_LABEL"), ("ICON_S4", "STAT_4_LABEL"),
    ],
    "timeline": [
        ("ICON_E1", "EVENT_1_TITLE"), ("ICON_E2", "EVENT_2_TITLE"),
        ("ICON_E3", "EVENT_3_TITLE"), ("ICON_E4", "EVENT_4_TITLE"),
    ],
    "tree_hierarchy": [
        ("ICON_ROOT", "ROOT_LABEL"), ("ICON_C1", "CHILD_1_LABEL"),
        ("ICON_C2", "CHILD_2_LABEL"), ("ICON_C3", "CHILD_3_LABEL"),
    ],
}


def l2_system_ext(archetype: str) -> str | None:
    """Slot-fill system prompt for an archetype authored here (else None)."""
    return _L2_SYSTEMS_EXT.get(archetype)


def l2_key_slots_ext(archetype: str):
    return _L2_KEY_SLOTS_EXT.get(archetype)


def l2_icon_slots_ext(archetype: str):
    return _ICON_SLOTS_EXT.get(archetype)
