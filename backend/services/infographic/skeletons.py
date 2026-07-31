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
# Left-pointing feedback arrow (structural; not model-controlled) — closes the
# stepped_cards loop line ("needs_revision sends feedback back to the agent <-").
_ARROW_L = (
    '<svg viewBox="0 0 24 16" aria-hidden="true">'
    '<path d="M21 8H4"/><path d="m10 2-6 6 6 6"/></svg>'
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


# ── Archetype 7 — compare_code (07.55.27) ──────────────────────────────
# Family-B "deck" look: two code columns, each a year/label pill + title +
# code block + prose, split by a "vs", closed by a shared takeaway callout.
# Reuses ib-code / ib-note + the new ib-pill / ib-vs / ib-hairline / ib-takeaway.
_COMPARE_CODE = """
<div class="ib">
  <div class="ib-head" style="text-align:left">
    <div class="ib-title">{{HEADLINE}}</div>
    <div class="ib-subtitle">{{SUBHEAD}}</div>
  </div>
  <div style="display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);gap:20px;align-items:center;max-width:100%">
    <div class="ib-card ib-bracketed" style="align-self:stretch;display:flex;flex-direction:column;gap:12px;min-width:0">
      <div class="ib-row" style="gap:10px">
        <span class="ib-pill ib-pill--muted">{{LEFT_PILL}}</span>
        <div style="font-weight:800;font-size:16px;color:var(--ib-ink)">{{LEFT_TITLE}}</div>
      </div>
      <div class="ib-code"><span class="ib-comment">{{LEFT_CODE_COMMENT}}</span>
{{LEFT_CODE_BODY}}</div>
      <div class="ib-hairline"></div>
      <div class="ib-note">{{LEFT_PROSE}}</div>
    </div>
    <div class="ib-vs">vs</div>
    <div class="ib-card ib-bracketed" style="align-self:stretch;display:flex;flex-direction:column;gap:12px;min-width:0">
      <div class="ib-row" style="gap:10px">
        <span class="ib-pill">{{RIGHT_PILL}}</span>
        <div style="font-weight:800;font-size:16px;color:var(--ib-ink)">{{RIGHT_TITLE}}</div>
      </div>
      <div class="ib-code"><span class="ib-comment">{{RIGHT_CODE_COMMENT}}</span>
{{RIGHT_CODE_BODY}}</div>
      <div class="ib-hairline"></div>
      <div class="ib-note">{{RIGHT_PROSE}}</div>
    </div>
  </div>
  <div class="ib-takeaway">{{TAKEAWAY}}</div>
</div>
"""


# ── Archetype 8 — stepped_cards (07.55.10) ─────────────────────────────
# Three independent titled step cards (icon-in-rounded-square + STEP n baked),
# a band of state pills, a feedback-loop note, and a takeaway. three_stage's
# "deck" sibling. Reuses ib-card + the new ib-icon-square / ib-step-num /
# ib-badge-band / ib-loopline / ib-takeaway.
_STEP_CARD = """<div class="ib-card ib-bracketed" style="display:flex;flex-direction:column;gap:10px;min-width:0">
        <span class="ib-icon-square">{{ICON_STEP#N#}}</span>
        <div class="ib-step-num">STEP #N#</div>
        <div style="font-weight:800;font-size:16px;color:var(--ib-ink)">{{STEP_#N#_TITLE}}</div>
        <div class="ib-note">{{STEP_#N#_BODY}}</div>
      </div>"""

_STATE_PILL = '<span class="ib-pill ib-pill--muted">{{STATE_#N#}}</span>'

_STEPPED_CARDS = (
    '\n<div class="ib">\n'
    '  <div class="ib-head" style="text-align:left">\n'
    '    <div class="ib-title">{{HEADLINE}}</div>\n'
    '    <div class="ib-subtitle">{{SUBHEAD}}</div>\n'
    '  </div>\n'
    '  <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;max-width:100%">\n'
    "    " + _repeat(_STEP_CARD, 3) + "\n"
    '  </div>\n'
    '  <div class="ib-badge-band">\n'
    "    " + _repeat(_STATE_PILL, 5) + "\n"
    '  </div>\n'
    '  <div class="ib-loopline">{{LOOP_NOTE}}<span class="ib-loopline-rule"></span>__ARROW_L__</div>\n'
    '  <div class="ib-takeaway">{{TAKEAWAY}}</div>\n'
    "</div>\n"
)


# ── Archetype 9 — tier_ladder (07.57.39, flat L2) ──────────────────────
# Intro column (eyebrow pill + headline + subhead + 2x2 stat chips) beside a
# ladder of four status-badged tiers anchored by a home/base node. Reuses
# ib-cite + the new ib-pill / ib-chip / ib-tier / ib-tier-meta / ib-anchor.
_TIER_CHIP = ('<div class="ib-chip">'
              '<div class="ib-chip-val">{{CHIP_#N#_VALUE}}</div>'
              '<div class="ib-chip-lab">{{CHIP_#N#_LABEL}}<sup class="ib-cite">{{CITE_#N#}}</sup></div>'
              '</div>')

_TIER_ROW = """<div class="ib-tier">
        <div class="ib-tier-rung">#N#</div>
        <div style="min-width:0;flex:1">
          <div class="ib-tier-meta">{{TIER_#N#_META}}</div>
          <div style="font-weight:800;font-size:15px;color:var(--ib-ink)">{{TIER_#N#_LABEL}}</div>
        </div>
        <span class="ib-pill">{{TIER_#N#_BADGE}}</span>
      </div>"""

_TIER_LADDER = (
    '\n<div class="ib">\n'
    '  <div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:26px;align-items:start;max-width:100%">\n'
    '    <div style="display:flex;flex-direction:column;gap:12px;min-width:0">\n'
    '      <div><span class="ib-pill">{{EYEBROW}}</span></div>\n'
    '      <div class="ib-title" style="text-align:left;font-size:26px;margin:0">{{HEADLINE}}</div>\n'
    '      <div class="ib-subtitle" style="text-align:left">{{SUBHEAD}}</div>\n'
    '      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:4px">\n'
    "        " + _repeat(_TIER_CHIP, 4) + "\n"
    '      </div>\n'
    '    </div>\n'
    '    <div class="ib-tier-list">\n'
    "      " + _repeat(_TIER_ROW, 4) + "\n"
    '      <div class="ib-anchor ib-center">{{ICON_ANCHOR}}<div class="ib-node-label" style="max-width:none;color:var(--ib-ink)">{{ANCHOR_LABEL}}</div></div>\n'
    '    </div>\n'
    '  </div>\n'
    "</div>\n"
)


# ── Archetype 10 — layer_stack (07.53.29, flat 2D) ─────────────────────
# Flat 2D vertical stack of five labeled bands, each with a numbered badge and a
# leader-line callout to a right-hand note (the 3D isometric look is L4-only).
# Reuses the new ib-layer-stack / ib-layer / ib-layer-num / ib-layer-band /
# ib-leader / ib-layer-note.
_LAYER_ROW = """<div class="ib-layer">
      <span class="ib-layer-num">#N#</span>
      <div class="ib-layer-band">{{LAYER_#N#_LABEL}}</div>
      <div class="ib-leader"></div>
      <div class="ib-layer-note">{{LAYER_#N#_NOTE}}</div>
    </div>"""

_LAYER_STACK = (
    '\n<div class="ib">\n'
    '  <div class="ib-head" style="text-align:left">\n'
    '    <div class="ib-title">{{STACK_TITLE}}</div>\n'
    '    <div class="ib-subtitle">{{STACK_SUBHEAD}}</div>\n'
    '  </div>\n'
    '  <div class="ib-layer-stack">\n'
    "    " + _repeat(_LAYER_ROW, 5) + "\n"
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
        .replace("__ARROW_L__", _ARROW_L)
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
    "compare_code": _COMPARE_CODE,
    "stepped_cards": _STEPPED_CARDS,
    "tier_ladder": _TIER_LADDER,
    "layer_stack": _LAYER_STACK,
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
    "compare_code": (
        "You are filling text slots in a TWO-COLUMN CODE COMPARISON infographic: the LEFT column "
        "is the old / do-it-yourself way, the RIGHT column is the new / managed way. Each column "
        "has a short label pill, a title, a code snippet, and a one-line prose note; a shared "
        "takeaway closes it. Return JSON:\n\n"
        "{\n"
        '  "HEADLINE": "max 6 words, the punchy contrast headline",\n'
        '  "SUBHEAD": "max 18 words, one sentence of context",\n'
        '  "LEFT_PILL": "max 2 words, the left label (e.g. a year or \'DIY\')",\n'
        '  "LEFT_TITLE": "max 4 words naming the left approach",\n'
        '  "LEFT_CODE_COMMENT": "max 8 words, a code comment for the left snippet",\n'
        '  "LEFT_CODE_BODY": "max 30 words of short pseudo-code (newlines ok)",\n'
        '  "LEFT_PROSE": "max 28 words describing the left trade-off",\n'
        '  "RIGHT_PILL": "max 2 words, the right label (e.g. a year or \'Managed\')",\n'
        '  "RIGHT_TITLE": "max 4 words naming the right approach",\n'
        '  "RIGHT_CODE_COMMENT": "max 8 words, a code comment for the right snippet",\n'
        '  "RIGHT_CODE_BODY": "max 30 words of short pseudo-code (newlines ok)",\n'
        '  "RIGHT_PROSE": "max 28 words describing the right trade-off",\n'
        '  "TAKEAWAY": "max 20 words, the one-line punchline shared by both"\n'
        "}\n\n"
        "Keep the two code snippets short — they render in small monospace panels."
    ),
    "stepped_cards": (
        "You are filling text slots in a THREE-STEP CARD infographic: three independent titled "
        "step cards, a band of outcome-state badges, a feedback-loop note, and a takeaway. "
        "Return JSON:\n\n"
        "{\n"
        '  "HEADLINE": "max 7 words naming the process",\n'
        '  "SUBHEAD": "max 18 words of one-sentence context",\n'
        '  "STEP_1_TITLE": "max 3 words, first step name",\n'
        '  "STEP_1_BODY": "max 14 words describing step 1",\n'
        '  "STEP_2_TITLE": "max 3 words, second step name",\n'
        '  "STEP_2_BODY": "max 14 words describing step 2",\n'
        '  "STEP_3_TITLE": "max 3 words, third step name",\n'
        '  "STEP_3_BODY": "max 14 words describing step 3",\n'
        '  "STATE_1": "max 3 words, a possible outcome state",\n'
        '  "STATE_2": "max 3 words, another state",\n'
        '  "STATE_3": "max 3 words, another state",\n'
        '  "STATE_4": "max 3 words, another state",\n'
        '  "STATE_5": "max 3 words, another state",\n'
        '  "LOOP_NOTE": "max 10 words, what triggers a loop back to a step",\n'
        '  "TAKEAWAY": "max 20 words, the key insight",\n'
        f'  "ICON_STEP1": "one of: {_ICON_LIST}",\n'
        '  "ICON_STEP2": "one icon name from that list",\n'
        '  "ICON_STEP3": "one icon name from that list"\n'
        "}\n\n"
        "Steps are sequential; the states are the discrete outcomes of the loop."
    ),
    "tier_ladder": (
        "You are filling text slots in a CAPABILITY TIER LADDER infographic: an intro column "
        "(eyebrow pill, headline, subhead, four stat chips) beside a ladder of four tiers, each "
        "with a small meta label, a name, and a one-word status badge, anchored by a home/base "
        "node. Return JSON:\n\n"
        "{\n"
        '  "EYEBROW": "max 4 words, a small eyebrow label",\n'
        '  "HEADLINE": "max 5 words, the punchy headline",\n'
        '  "SUBHEAD": "max 16 words of one-sentence context",\n'
        '  "CHIP_1_VALUE": "a short stat/number (e.g. 5-30x, 42%, 1.2M, 4 steps)",\n'
        '  "CHIP_1_LABEL": "max 3 words naming what the stat measures",\n'
        '  "CHIP_2_VALUE": "a short stat/number",\n'
        '  "CHIP_2_LABEL": "max 3 words",\n'
        '  "CHIP_3_VALUE": "a short stat/number",\n'
        '  "CHIP_3_LABEL": "max 3 words",\n'
        '  "CHIP_4_VALUE": "a short stat/number",\n'
        '  "CHIP_4_LABEL": "max 3 words",\n'
        '  "TIER_1_META": "max 3 words, the top tier qualifier (e.g. a size)",\n'
        '  "TIER_1_LABEL": "max 4 words, the top tier name",\n'
        '  "TIER_1_BADGE": "one word status (e.g. RENT / RUNS / TRAINS)",\n'
        '  "TIER_2_META": "max 3 words qualifier",\n'
        '  "TIER_2_LABEL": "max 4 words tier name",\n'
        '  "TIER_2_BADGE": "one word status",\n'
        '  "TIER_3_META": "max 3 words qualifier",\n'
        '  "TIER_3_LABEL": "max 4 words tier name",\n'
        '  "TIER_3_BADGE": "one word status",\n'
        '  "TIER_4_META": "max 3 words qualifier",\n'
        '  "TIER_4_LABEL": "max 4 words tier name",\n'
        '  "TIER_4_BADGE": "one word status",\n'
        '  "ANCHOR_LABEL": "max 3 words naming the base / home node",\n'
        f'  "ICON_ANCHOR": "one of: {_ICON_LIST}"\n'
        "}\n\n"
        "Order tiers most-demanding at the top down to the anchor. Badges are single words."
    ),
    "layer_stack": (
        "You are filling text slots in a LAYER STACK infographic: a vertical stack of five "
        "labeled layers, each with a short right-hand note. Return JSON:\n\n"
        "{\n"
        '  "STACK_TITLE": "max 5 words naming the stack",\n'
        '  "STACK_SUBHEAD": "max 16 words of one-sentence context",\n'
        '  "LAYER_1_LABEL": "max 3 words, the top layer",\n'
        '  "LAYER_1_NOTE": "max 8 words describing it",\n'
        '  "LAYER_2_LABEL": "max 3 words, second layer",\n'
        '  "LAYER_2_NOTE": "max 8 words describing it",\n'
        '  "LAYER_3_LABEL": "max 3 words, third layer",\n'
        '  "LAYER_3_NOTE": "max 8 words describing it",\n'
        '  "LAYER_4_LABEL": "max 3 words, fourth layer",\n'
        '  "LAYER_4_NOTE": "max 8 words describing it",\n'
        '  "LAYER_5_LABEL": "max 3 words, the bottom layer",\n'
        '  "LAYER_5_NOTE": "max 8 words describing it"\n'
        "}\n\n"
        "Order layers top-to-bottom as they stack. Keep labels to short noun phrases."
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
    "compare_code": (
        ["HEADLINE", "LEFT_TITLE", "RIGHT_TITLE", "LEFT_CODE_BODY", "RIGHT_CODE_BODY",
         "LEFT_PROSE", "RIGHT_PROSE", "TAKEAWAY"],
        5,
    ),
    "stepped_cards": (
        ["HEADLINE", "STEP_1_TITLE", "STEP_2_TITLE", "STEP_3_TITLE",
         "STEP_1_BODY", "STEP_2_BODY", "STEP_3_BODY"],
        4,
    ),
    "tier_ladder": (
        ["HEADLINE", "TIER_1_LABEL", "TIER_2_LABEL", "TIER_3_LABEL", "TIER_4_LABEL",
         "CHIP_1_LABEL"],
        4,
    ),
    "layer_stack": (
        ["STACK_TITLE", "LAYER_1_LABEL", "LAYER_2_LABEL", "LAYER_3_LABEL",
         "LAYER_4_LABEL", "LAYER_5_LABEL"],
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
    "stepped_cards": [
        ("ICON_STEP1", "STEP_1_TITLE"), ("ICON_STEP2", "STEP_2_TITLE"),
        ("ICON_STEP3", "STEP_3_TITLE"),
    ],
    "tier_ladder": [
        ("ICON_ANCHOR", "ANCHOR_LABEL"),
    ],
}


def l2_system_ext(archetype: str) -> str | None:
    """Slot-fill system prompt for an archetype authored here (else None)."""
    return _L2_SYSTEMS_EXT.get(archetype)


def l2_key_slots_ext(archetype: str):
    return _L2_KEY_SLOTS_EXT.get(archetype)


def l2_icon_slots_ext(archetype: str):
    return _ICON_SLOTS_EXT.get(archetype)
