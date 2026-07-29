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
}

ARCHETYPES = tuple(_SKELETONS.keys())


def get_skeleton(archetype: str) -> str | None:
    """Return the structural-expanded skeleton (icon + text slots remain)."""
    raw = _SKELETONS.get(archetype)
    if raw is None:
        return None
    return _expand_structural(raw)
