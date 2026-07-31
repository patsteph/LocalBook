"""Model-free tests for the infographic lane (2.2.0 Phase 2).

None of these call an LLM — they exercise the deterministic surface: skeleton
expansion, icon fail-open, the provenance helpers (HARD RULE §2.6), the
degradation-ladder prose fallback, and the backend<->frontend design-system
CSS mirror (the byte-identical invariant the export pipeline depends on).

Run:  cd backend && python -m pytest tests/test_infographic.py -q
"""
import asyncio
import re
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from services.infographic import icons
from services.infographic.skeletons import ARCHETYPES, get_skeleton
from services.infographic.builder import (
    _normalize_sources,
    _cite_slots_for_rows,
    _prose_fallback,
    _finalize_body,
    _check_slots,
    pick_archetype,
    build_l4,
)

BACKEND = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND.parent


# ── CSS mirror (byte-identical invariant) ──────────────────────────────
def _extract(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.S)
    assert m, "CSS block not found"
    return m.group(1)


def test_css_mirror_byte_identical():
    """backend design_system.py CSS == frontend infographicDesignSystem.ts CSS.
    The export pipeline injects the Python copy; the Shadow-DOM renderer injects
    the TS copy — they MUST be identical or exports diverge from the app."""
    ts_path = REPO_ROOT / "src/components/artifact/renderers/infographicDesignSystem.ts"
    if not ts_path.exists():
        pytest.skip("frontend tree not present (backend-only checkout)")
    py_css = _extract(
        r'INFOGRAPHIC_L2_CSS = r"""(.*?)"""',
        (BACKEND / "services/infographic/design_system.py").read_text(),
    )
    ts_css = _extract(r"export const INFOGRAPHIC_L2_CSS = `(.*)`;", ts_path.read_text())
    assert py_css == ts_css, "design-system CSS mirror has diverged — regenerate the .ts from the .py"


def test_css_has_no_template_literal_breakers():
    """The CSS body is embedded in a JS template literal AND a Python raw
    string; a backtick / ${ / backslash would break one of the two copies."""
    py_css = _extract(
        r'INFOGRAPHIC_L2_CSS = r"""(.*?)"""',
        (BACKEND / "services/infographic/design_system.py").read_text(),
    )
    for bad in ("`", "${", "\\"):
        assert bad not in py_css, f"CSS contains {bad!r}"


# ── Skeleton expansion ─────────────────────────────────────────────────
def test_all_skeletons_expand_without_structural_markers():
    for arch in ARCHETYPES:
        sk = get_skeleton(arch)
        assert sk, f"{arch} produced no skeleton"
        assert "__" not in sk, f"{arch} left an unexpanded structural marker"
        # icon/text slots are still present for the slot-fill pass
        assert "{{" in sk


def test_fidelity_glyphs_present():
    """The Phase-2 curved-arrow + heatmap fidelity glyphs are baked in."""
    pc = get_skeleton("pipeline_compare")
    assert "ib-loop-arrow" in pc and "ib-branch" in pc
    ts = get_skeleton("three_stage")
    assert "ib-heatmap" in ts


# ── Icons fail-open ────────────────────────────────────────────────────
def test_icon_render_fail_open():
    assert "<svg" in icons.render_icon("does-not-exist")   # neutral dot, never empty
    assert "<svg" in icons.render_icon("database")


def test_icon_for_label_keyword_mapping():
    assert icons.icon_for_label("vector store") == "database"
    assert icons.icon_for_label("serve fast") == "rocket"    # first keyword 'serve' wins
    assert icons.icon_for_label("totally unknown phrase") == "sparkles"  # fail-open default


# ── Provenance helpers (HARD RULE §2.6) ────────────────────────────────
def test_normalize_sources_renumbers_and_drops_malformed():
    out = _normalize_sources([
        {"n": 5, "source_id": "a", "title": "Alpha"},
        {"n": 9, "title": ""},          # dropped (no title)
        "not-a-dict",                    # dropped
        {"source_id": "b", "filename": "Beta.pdf"},  # title from filename
    ])
    assert [s["n"] for s in out] == [1, 2]          # resequenced
    assert out[0]["title"] == "Alpha" and out[0]["source_id"] == "a"
    assert out[1]["title"] == "Beta.pdf"


def test_normalize_sources_empty():
    assert _normalize_sources(None) == []
    assert _normalize_sources([]) == []


def test_cite_slots_round_robin():
    prov = _normalize_sources([
        {"title": "One"}, {"title": "Two"},
    ])
    slots = _cite_slots_for_rows(prov, n_rows=3)
    assert slots == {"CITE_1": "1", "CITE_2": "2", "CITE_3": "1"}


def test_cite_slots_empty_when_no_provenance():
    slots = _cite_slots_for_rows([], n_rows=3)
    assert slots == {"CITE_1": "", "CITE_2": "", "CITE_3": ""}


def test_facts_table_superscripts_bind_to_real_sources():
    """Filling the facts_table skeleton with real cite slots yields concrete
    superscript numbers, not the dangling literal 1/2/3 of Phase 1."""
    sk = get_skeleton("facts_table")
    prov = _normalize_sources([{"title": "10-K"}, {"title": "PR"}])
    slots = {
        "ROW_1_VALUE": "$1.2B", "ROW_2_VALUE": "12%", "ROW_3_VALUE": "3.4M",
    }
    slots.update(_cite_slots_for_rows(prov, n_rows=3))
    body = _finalize_body(sk, slots)
    assert '<sup class="ib-cite">1</sup>' in body
    assert '<sup class="ib-cite">2</sup>' in body
    # row 3 round-robins back to source 1
    assert body.count('<sup class="ib-cite">1</sup>') == 2


def test_facts_table_no_dangling_citation_without_sources():
    sk = get_skeleton("facts_table")
    slots = {"ROW_1_VALUE": "x", "ROW_2_VALUE": "y", "ROW_3_VALUE": "z"}
    slots.update(_cite_slots_for_rows([], n_rows=3))
    body = _finalize_body(sk, slots)
    assert '<sup class="ib-cite">1</sup>' not in body
    assert '<sup class="ib-cite"></sup>' in body   # empty, not dangling


# ── Prose fallback carries provenance ──────────────────────────────────
def test_prose_fallback_shape_and_sources():
    art = _prose_fallback("some content", "reason", "L2", [{"title": "Src"}])
    assert art["payload"]["degraded"] is True
    assert art["payload"]["sources"] == [{"n": 1, "source_id": None, "title": "Src"}]
    assert art["type"] == "json:infographic"


# ── L4 lane routing + degradation (Klein decorative lane) ──────────────
def test_normalize_lane_l4_is_buildable():
    """L4 (Klein) and L3 (scene) now both build — neither collapses to L2."""
    from services.intent_classifier import _normalize_lane
    assert _normalize_lane("L4") == "L4"
    assert _normalize_lane("l4") == "L4"
    assert _normalize_lane("L3") == "L3"       # Phase-4: scene lane now builds
    assert _normalize_lane("l3") == "L3"
    assert _normalize_lane("nonsense") == "L2"  # fail-open to the volume lane


def _fake_cap(klein_model):
    return types.SimpleNamespace(
        klein_model=klein_model, gemma_model=None,
        concurrency_mode=types.SimpleNamespace(value="concurrent"),
    )


def test_l4_degrades_when_klein_unavailable(monkeypatch):
    """No Klein model + Ollama engine -> build_l4 returns None (caller degrades
    down the ladder). Fails open, never raises, never calls the LLM."""
    import services.visual_capability as vc
    from config import settings

    async def _cap():
        return _fake_cap(None)

    monkeypatch.setattr(vc, "get_capability", _cap)
    monkeypatch.setattr(settings, "image_engine", "ollama", raising=False)

    out = asyncio.run(build_l4("draw a serene mountain lake", title="Mountain Lake"))
    assert out is None


def test_l4_success_produces_textless_data_uri(monkeypatch):
    """With Klein available, build_l4 returns a json:infographic L4 artifact
    whose payload carries a base64 PNG data URI and the title as an OVERLAY
    (never baked into the raster — HARD RULE §2.2)."""
    import services.visual_capability as vc
    import services.visual_diffusion as vd
    from config import settings

    async def _cap():
        return _fake_cap("x/flux2-klein")

    async def _brief(seed, title, capability=None):
        return "A cinematic mountain lake at golden hour"

    captured = {}

    async def _generate(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["negative_prompt"] = kwargs.get("negative_prompt")
        return vd.DiffusionResult(
            success=True, png_bytes=b"\x89PNG\r\n\x1a\nfake",
            width=1280, height=720, model="x/flux2-klein", prompt_used=prompt,
        )

    # The overlay is a SHORT generated poster title (fast model) — stub it
    # model-free so the test stays LLM-free.
    import services.infographic.builder as _bld

    async def _short(content, model):
        return "Golden Hour"

    monkeypatch.setattr(vc, "get_capability", _cap)
    monkeypatch.setattr(settings, "image_engine", "ollama", raising=False)
    monkeypatch.setattr(vd, "write_klein_brief", _brief)
    monkeypatch.setattr(vd.klein_diffusion, "generate", _generate)
    monkeypatch.setattr(_bld, "_poster_title", _short)

    out = asyncio.run(build_l4("hero image", title="My Title"))
    assert out is not None
    assert out["type"] == "json:infographic"
    p = out["payload"]
    assert p["lane"] == "L4"
    assert p["archetype"] == "decorative"
    assert p["image"].startswith("data:image/png;base64,")
    # Title is a SHORT OVERLAY, not pixels — it rides in the payload, not the
    # prompt (the raw request is never baked into the raster).
    assert p["title_overlay"] == "Golden Hour"
    assert "My Title" not in captured["prompt"]
    # Negative prompt explicitly suppresses in-image text.
    assert "no text in image" in captured["negative_prompt"]


def test_l4_returns_none_on_klein_failure(monkeypatch):
    """A failed Klein generation -> None (caller degrades), never raises."""
    import services.visual_capability as vc
    import services.visual_diffusion as vd
    from config import settings

    async def _cap():
        return _fake_cap("x/flux2-klein")

    async def _brief(seed, title, capability=None):
        return "a prompt"

    async def _generate(prompt, **kwargs):
        return vd.DiffusionResult(success=False, error="klein boom")

    monkeypatch.setattr(vc, "get_capability", _cap)
    monkeypatch.setattr(settings, "image_engine", "ollama", raising=False)
    monkeypatch.setattr(vd, "write_klein_brief", _brief)
    monkeypatch.setattr(vd.klein_diffusion, "generate", _generate)

    assert asyncio.run(build_l4("x", title="T")) is None


# ── L3 scene: graph -> layout -> SVG (model-free) ──────────────────────
from services.infographic import scene as l3scene       # noqa: E402
from services.infographic import stickers as l3stickers  # noqa: E402
from services.infographic.builder import build_l3, build_infographic  # noqa: E402

_PLUGIN_GRAPH = {
    "title": "Scattered to plugin",
    "groups": [
        {"id": "g1", "label": "Scattered", "color": "blue"},
        {"id": "g2", "label": "Package", "color": "green"},
        {"id": "g3", "label": "Install", "color": "violet"},
    ],
    "nodes": [
        {"id": "n1", "label": "CLAUDE.md", "sticker": "document", "group": "g1", "size": "small"},
        {"id": "n2", "label": "Skill", "sticker": "puzzle", "group": "g1", "size": "small"},
        {"id": "n3", "label": "MCP", "sticker": "plug", "group": "g1", "size": "small"},
        {"id": "n4", "label": "plugin.json", "sticker": "package", "group": "g2", "size": "hero"},
        {"id": "n5", "label": "Laptop", "sticker": "laptop", "group": "g3", "size": "small"},
    ],
    "edges": [{"from": "g1", "to": "g2"}, {"from": "g2", "to": "g3"}],
}


def test_l3_parse_graph_fail_open():
    assert l3scene.parse_graph("not a dict") is None
    assert l3scene.parse_graph({"nodes": []}) is None       # no nodes
    assert l3scene.parse_graph({"groups": [{"id": "g"}]}) is None  # no nodes
    g = l3scene.parse_graph(_PLUGIN_GRAPH)
    assert g and len(g["nodes"]) == 5 and len(g["groups"]) == 3


def test_l3_parse_graph_orphan_node_gets_default_group():
    g = l3scene.parse_graph({"nodes": [{"label": "x", "sticker": "gear"}]})
    assert g and len(g["groups"]) == 1        # a synthetic default column
    assert g["nodes"][0]["group"] == g["groups"][0]["id"]


def test_l3_layout_positions_are_ordered_left_to_right():
    g = l3scene.parse_graph(_PLUGIN_GRAPH)
    lay = l3scene.layout_scene(g)
    assert lay["width"] > 0 and lay["height"] > 0
    # one pill per group, columns strictly increasing in x
    assert len(lay["pills"]) == 3
    assert lay["col_centers"] == sorted(lay["col_centers"])
    # every placement sits inside the canvas
    for p in lay["placements"]:
        assert 0 <= p["x"] < lay["width"] and 0 <= p["y"] < lay["height"]


def test_l3_compose_scene_contains_stickers_and_labels():
    g = l3scene.parse_graph(_PLUGIN_GRAPH)
    svg = l3scene.compose_scene(g)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "url(#rough)" in svg                       # roughen filter applied
    assert "feDisplacementMap" in svg
    assert "CLAUDE.md" in svg and "plugin.json" in svg  # node labels present
    assert "Scattered" in svg and "Install" in svg      # phase pill labels
    assert svg.count("stroke-dasharray") >= 2           # 2 connector arrows


def test_l3_phase_pills_do_not_overlap():
    """Field bug: 5 long phase labels made the highlighter pills overlap + bleed
    off-canvas. Each column must now be >= its pill width, so pills never collide."""
    from services.infographic.scene import _PILL_FONT, _PILL_MAX
    labels = ["Scattered documents", "Chunk & embed", "Vector store",
              "Retrieve & rerank", "Grounded answer"]
    g = l3scene.parse_graph({
        "groups": [{"id": f"g{i}", "label": lbl} for i, lbl in enumerate(labels)],
        "nodes": [{"id": f"n{i}", "label": "x", "sticker": "box", "group": f"g{i}"} for i in range(5)],
    })
    lay = l3scene.layout_scene(g)

    def pill_w(label):  # mirror scene._pill's width formula (label already truncated)
        return max(70.0, len(label) * _PILL_FONT * 0.62 + 34)

    pills = sorted(lay["pills"], key=lambda p: p["cx"])
    for a, b in zip(pills, pills[1:]):
        assert a["cx"] + pill_w(a["label"]) / 2 <= b["cx"] - pill_w(b["label"]) / 2, \
            f"pills overlap: {a['label']!r} / {b['label']!r}"
    # first pill doesn't bleed off the left; last doesn't run past the right
    assert pills[0]["cx"] - pill_w(pills[0]["label"]) / 2 >= 0
    assert pills[-1]["cx"] + pill_w(pills[-1]["label"]) / 2 <= lay["width"]
    # over-long labels truncate
    assert all(len(p["label"]) <= _PILL_MAX for p in lay["pills"])


def test_l3_compose_escapes_untrusted_labels():
    g = l3scene.parse_graph({
        "groups": [{"id": "g1", "label": "<script>x</script>"}],
        "nodes": [{"label": "a & b <hack>", "sticker": "box", "group": "g1"}],
    })
    svg = l3scene.compose_scene(g)
    assert "<script>" not in svg and "<hack>" not in svg
    assert "&amp;" in svg and "&lt;" in svg


def test_l3_scene_and_every_sticker_are_valid_xml():
    """Regression guard: the frontend renders the scene as an <img> data-URI, and
    WKWebView rejects invalid SVG XML (broken-image box). A duplicate `fill`
    attribute — the exact bug this asserts against — is invalid XML. Validate
    every sticker standalone AND a full scene that uses all of them + an escaped
    untrusted label."""
    from xml.dom.minidom import parseString

    for name in l3stickers.sticker_names():
        parseString(f'<svg xmlns="http://www.w3.org/2000/svg">{l3stickers.render_sticker(name)}</svg>')
    parseString(f'<svg xmlns="http://www.w3.org/2000/svg">{l3stickers.render_sticker("does-not-exist")}</svg>')

    every = l3scene.parse_graph({
        "groups": [
            {"id": "g1", "label": "Alpha", "color": "blue"},
            {"id": "g2", "label": "Beta & <Co>", "color": "green"},
        ],
        "nodes": [
            {"id": f"n{i}", "label": nm, "sticker": nm, "group": "g%d" % (i % 2 + 1)}
            for i, nm in enumerate(l3stickers.sticker_names())
        ],
    })
    parseString(l3scene.compose_scene(every))  # raises on any malformed XML


def test_sticker_render_fail_open():
    assert "<" in l3stickers.render_sticker("does-not-exist")  # neutral box, never empty
    assert "<path" in l3stickers.render_sticker("robot")
    assert len(l3stickers.sticker_names()) >= 50   # grown library (2.2.0)


# ── Build A: router honors explicit medium words (the "poster → L2" field bug) ──
from services import intent_classifier as ic  # noqa: E402


class _FakeOllama:
    """Deterministic stand-in: returns the same lane/confidence for any model, so
    Stage A and any Stage-B escalation agree (no LLM, no data_dir writes)."""
    def __init__(self, lane, conf):
        self._lane, self._conf = lane, conf

    async def generate(self, **_kw):
        import json as _json
        return {"response": _json.dumps({"lane": self._lane, "confidence": self._conf})}


def test_detect_lane_keywords():
    assert ic._detect_lane_keywords("make a poster on RAG")[0] == "L4"
    assert ic._detect_lane_keywords("an evocative cover image")[0] == "L4"
    assert ic._detect_lane_keywords("a bar chart of tokens over 10 turns")[0] == "L1"
    assert ic._detect_lane_keywords("a pipeline diagram: compile → serve")[0] == "L2"
    assert ic._detect_lane_keywords("a whiteboard sketch of the flow")[0] == "L3"
    assert ic._detect_lane_keywords("make an infographic") is None       # vague → content-shape
    assert ic._detect_lane_keywords("we started early today") is None    # no false \bart\b
    assert ic._detect_lane_keywords("the state of AI art")[0] == "L4"     # bounded 'art'
    assert ic._detect_lane_keywords("cumulative token usage grows over 10 steps")[0] == "L1"


def test_router_phrasing_boost_overrides_weak_content(monkeypatch):
    monkeypatch.setattr(ic, "_record_misroute", lambda *a, **k: None)
    out = asyncio.run(ic.classify_infographic_lane(
        content_summary="A comparison of runtime retrieval vs compile-time RAG.",
        request_text="make a poster on RAG architecture",
        ollama_service=_FakeOllama("L2", 0.7),   # content-shape wants L2 but not certain
    ))
    assert out["lane"] == "L4"          # 'poster' wins
    assert out["stage"].endswith("+kw")
    assert out["confidence"] >= 0.8


def test_router_confident_content_beats_phrasing(monkeypatch):
    monkeypatch.setattr(ic, "_record_misroute", lambda *a, **k: None)
    out = asyncio.run(ic.classify_infographic_lane(
        content_summary="Runtime vs compile-time RAG comparison.",
        request_text="make a poster on RAG architecture",
        ollama_service=_FakeOllama("L2", 0.95),  # >0.9 → Boost loses
    ))
    assert out["lane"] == "L2"
    assert "+kw" not in out["stage"]


def test_router_phrasing_reinforces_agreement(monkeypatch):
    monkeypatch.setattr(ic, "_record_misroute", lambda *a, **k: None)
    out = asyncio.run(ic.classify_infographic_lane(
        content_summary="whatever", request_text="a decorative poster",
        ollama_service=_FakeOllama("L4", 0.6),
    ))
    assert out["lane"] == "L4" and out["confidence"] >= 0.9


def test_router_vague_request_uses_content_shape(monkeypatch):
    monkeypatch.setattr(ic, "_record_misroute", lambda *a, **k: None)
    out = asyncio.run(ic.classify_infographic_lane(
        content_summary="token usage growing across 10 iterations",
        request_text="make an infographic",
        ollama_service=_FakeOllama("L1", 0.8),
    ))
    assert out["lane"] == "L1" and "+kw" not in out["stage"]


def test_router_growth_over_sequence_routes_L1(monkeypatch):
    # The field miss: content-shape read "X grows vs Y" as a comparison → L2;
    # the growth phrasing must pull it back to L1 (plot the trajectory).
    monkeypatch.setattr(ic, "_record_misroute", lambda *a, **k: None)
    out = asyncio.run(ic.classify_infographic_lane(
        content_summary="agent loop token usage vs a single-shot chatbot answer",
        request_text="show how cumulative token usage grows over a 10-step loop vs single-shot",
        ollama_service=_FakeOllama("L2", 0.7),
    ))
    assert out["lane"] == "L1" and out["stage"].endswith("+kw")


def test_router_before_after_facts_stays_L2(monkeypatch):
    # Numbers present, but a discrete before/after facts table is NOT a growth
    # trajectory — it must stay L2 (the growth keywords must not over-trigger L1).
    monkeypatch.setattr(ic, "_record_misroute", lambda *a, **k: None)
    out = asyncio.run(ic.classify_infographic_lane(
        content_summary="messy raw chunks vs a clean facts table: Revenue $10.2B, Net Income $4.5B",
        request_text="before and after: raw chunks vs a clean structured facts table",
        ollama_service=_FakeOllama("L2", 0.7),
    ))
    assert out["lane"] == "L2"


# ── anti-rut: per-generation L2 style seeding (2026-07-31) ──────────────
def test_l2_style_seed_is_deterministic_onbrand_and_varies():
    from services.infographic.builder import _seed_style, _STYLE_PRESETS
    from services.infographic import restyle
    # deterministic — same source reproduces
    assert _seed_style("compile-time RAG", "RAG") == _seed_style("compile-time RAG", "RAG")
    # every preset uses ONLY valid design-system tokens (the on-brand guardrail)
    for p in _STYLE_PRESETS:
        assert p.get("accent", "coral") in restyle.ACCENT_IDS
        assert p.get("tone", "paper") in restyle.TONE_IDS
        assert p.get("scale", "cozy") in restyle.SCALE_IDS
    # distinct topics should not collapse to a single look
    styles = {str(_seed_style(t, t)) for t in
              ["tokens", "pipeline", "compare", "facts", "timeline", "vectors", "agents", "chunks"]}
    assert len(styles) >= 3


# ── archetype selection + router fixes (round-2 field misses, 2026-07-31) ──
def test_pick_archetype_topic_beats_noisy_content():
    from services.infographic.builder import pick_archetype
    # a "stacked layers" REQUEST must win over incidental 'code'/'compare' in content
    noisy = "the code compares vectors and reranks retrieval before and after"
    assert pick_archetype(noisy, topic="Diagram the RAG stack as five stacked layers") == "layer_stack"
    # explicit code-comparison still routes to compare_code
    assert pick_archetype("", topic="compare the code side by side, two implementations") == "compare_code"
    # incidental 'code' + 'compare' (no explicit code-compare phrase) no longer forces it
    assert pick_archetype("chunks mention code and we compare latency numbers") != "compare_code"
    # tier-ladder + stepped-cards requests pick their shapes
    assert pick_archetype("", topic="four tiers of RAG maturity as a ladder") == "tier_ladder"
    assert pick_archetype("", topic="three cards with state badges and a feedback loop") == "stepped_cards"


def test_router_card_badge_layout_routes_L2(monkeypatch):
    # A "three cards + state badges" request kept landing on L3; the card/badge
    # L2 keywords must pull it back to the structured lane.
    monkeypatch.setattr(ic, "_record_misroute", lambda *a, **k: None)
    out = asyncio.run(ic.classify_infographic_lane(
        content_summary="an agent loop that plans, retrieves, then synthesizes",
        request_text="show it as three cards with state badges and a feedback loop",
        ollama_service=_FakeOllama("L3", 0.9),
    ))
    assert out["lane"] == "L2" and out["stage"].endswith("+kw")


# ── L3 build + degradation ladder (model-free via monkeypatch) ─────────
def test_build_l3_composes_from_graph(monkeypatch):
    """With the LLM returning a valid graph, build_l3 yields an L3 artifact
    whose payload carries the composed scene SVG (the source, HARD RULE §2.4)."""
    import services.infographic.builder as b

    async def _fake_slotfill(system, content, model):
        return _PLUGIN_GRAPH

    monkeypatch.setattr(b, "_run_slotfill", _fake_slotfill)
    out = asyncio.run(build_l3("plugins from scattered components", title="Plugins"))
    assert out is not None
    assert out["type"] == "json:infographic"
    p = out["payload"]
    assert p["lane"] == "L3" and p["archetype"] == "scene"
    assert p["scene_svg"].startswith("<svg")
    assert "plugin.json" in p["scene_svg"]


def test_build_l3_degrades_to_prose_when_graph_unusable(monkeypatch):
    """Unparseable graph -> build_l3 None -> the L3 branch of build_infographic
    walks the ladder L3 -> L2 -> prose. All model-free (slot-fill returns None),
    so the final rung is the prose fallback, tagged with the L3 lane."""
    import services.infographic.builder as b

    async def _none_slotfill(system, content, model):
        return None

    monkeypatch.setattr(b, "_run_slotfill", _none_slotfill)
    out = asyncio.run(build_infographic("some vague prose", "L3"))
    assert out["type"] == "json:infographic"
    assert out["payload"]["degraded"] is True
    assert out["payload"]["lane"] == "L3"


def test_build_l3_returns_none_on_bad_graph_directly(monkeypatch):
    import services.infographic.builder as b

    async def _bad_slotfill(system, content, model):
        return {"nodes": []}   # parses to None

    monkeypatch.setattr(b, "_run_slotfill", _bad_slotfill)
    assert asyncio.run(build_l3("x")) is None


# ── Archetype heuristic ────────────────────────────────────────────────
def test_pick_archetype():
    assert pick_archetype("the compile stage then index then serve") == "three_stage"
    assert pick_archetype("quarterly revenue figures from the filing") == "facts_table"
    assert pick_archetype("some generic prose about a process") == "pipeline_compare"


def test_pick_archetype_new_lanes():
    assert pick_archetype("a timeline of the company milestones") == "timeline"
    assert pick_archetype("the taxonomy breaks down into these categories") == "tree_hierarchy"
    assert pick_archetype("the key metrics dashboard at a glance") == "stat_grid"


# ── New L2 archetypes (stat_grid / timeline / tree_hierarchy) ──────────
# Full slot payloads exercised model-free: each expands, slot-checks (pass +
# fail), and fails open (leftover slots stripped).
_NEW_ARCHETYPE_SLOTS = {
    "stat_grid": {
        "GRID_TITLE": "Key figures", "FOOTER": "as of 2026",
        "STAT_1_VALUE": "$1.2B", "STAT_1_LABEL": "Revenue",
        "STAT_2_VALUE": "98%", "STAT_2_LABEL": "Uptime",
        "STAT_3_VALUE": "3.4M", "STAT_3_LABEL": "Users",
        "STAT_4_VALUE": "5x", "STAT_4_LABEL": "Growth",
    },
    "timeline": {
        "TIMELINE_TITLE": "Company history",
        "EVENT_1_DATE": "2019", "EVENT_1_TITLE": "Founded", "EVENT_1_NOTE": "in a garage",
        "EVENT_2_DATE": "2021", "EVENT_2_TITLE": "Series A", "EVENT_2_NOTE": "raised funds",
        "EVENT_3_DATE": "2023", "EVENT_3_TITLE": "Launch", "EVENT_3_NOTE": "shipped v1",
        "EVENT_4_DATE": "2026", "EVENT_4_TITLE": "IPO", "EVENT_4_NOTE": "went public",
    },
    "tree_hierarchy": {
        "TREE_TITLE": "System taxonomy", "ROOT_LABEL": "Platform",
        "CHILD_1_LABEL": "Ingest", "CHILD_1_NOTE": "pulls sources",
        "CHILD_2_LABEL": "Index", "CHILD_2_NOTE": "builds vectors",
        "CHILD_3_LABEL": "Serve", "CHILD_3_NOTE": "answers queries",
    },
}


def test_new_archetypes_registered():
    for arch in ("stat_grid", "timeline", "tree_hierarchy"):
        assert arch in ARCHETYPES
        assert get_skeleton(arch), f"{arch} produced no skeleton"


def test_new_archetypes_slot_check_pass_and_fail():
    for arch, good in _NEW_ARCHETYPE_SLOTS.items():
        ok, reason = _check_slots(arch, dict(good))
        assert ok, f"{arch} full-slot check should pass ({reason})"
        # blank almost everything -> the empty-ratio + key-slot gate rejects it
        blank = {k: "" for k in good}
        blank[next(iter(good))] = good[next(iter(good))]  # keep one filled
        ok2, _ = _check_slots(arch, blank)
        assert not ok2, f"{arch} should reject a mostly-blank slot set"


def test_new_archetypes_finalize_fails_open():
    """A partial slot-fill still yields legible HTML with NO leftover {{SLOT}}
    markers (the degradation-ladder invariant)."""
    for arch, good in _NEW_ARCHETYPE_SLOTS.items():
        sk = get_skeleton(arch)
        partial = dict(list(good.items())[:2])   # only the first couple slots
        body = _finalize_body(sk, partial)
        assert "{{" not in body and "}}" not in body, f"{arch} left an unfilled slot"
        assert "<div" in body


# ── Family-B "deck" archetypes (compare_code / stepped_cards / tier_ladder /
#    layer_stack) — the 07-31 corpus. Model-free: expand, slot-check pass+fail,
#    fail-open, cite-binding, and route. ─────────────────────────────────────
_DECK_ARCHETYPE_SLOTS = {
    "compare_code": {
        "HEADLINE": "Same pattern. Different decade.",
        "SUBHEAD": "They shipped it as a managed feature two years later.",
        "LEFT_PILL": "2024", "LEFT_TITLE": "You build it",
        "LEFT_CODE_COMMENT": "your loop", "LEFT_CODE_BODY": "while not done:\n  step()",
        "LEFT_PROSE": "You own the rubric, the retry, the observability.",
        "RIGHT_PILL": "2026", "RIGHT_TITLE": "They ship it",
        "RIGHT_CODE_COMMENT": "the api call", "RIGHT_CODE_BODY": "client.run(task)",
        "RIGHT_PROSE": "They own the loop, billed per runtime hour.",
        "TAKEAWAY": "The maintenance was the hard part. That is what they sold.",
    },
    "stepped_cards": {
        "HEADLINE": "Outcomes is a loop, not a feature",
        "SUBHEAD": "A rubric defines done; a separate grader decides done.",
        "STEP_1_TITLE": "Rubric", "STEP_1_BODY": "Markdown describing success.",
        "STEP_2_TITLE": "Agent", "STEP_2_BODY": "Works the task, produces output.",
        "STEP_3_TITLE": "Grader", "STEP_3_BODY": "Reads rubric and output only.",
        "STATE_1": "satisfied", "STATE_2": "needs_revision",
        "STATE_3": "max_iterations", "STATE_4": "failed", "STATE_5": "interrupted",
        "LOOP_NOTE": "needs_revision sends feedback back to the agent",
        "TAKEAWAY": "The grader runs in its own context window.",
    },
    "tier_ladder": {
        "EYEBROW": "Tested and costed", "HEADLINE": "Local AI, actually cheap",
        "SUBHEAD": "What you can really run and train at home.",
        "CHIP_1_LABEL": "8-12B on 16GB", "CHIP_2_LABEL": "70B on 64GB",
        "CHIP_3_LABEL": "QLoRA at home", "CHIP_4_LABEL": "3c/day power",
        "TIER_1_META": "Frontier scale", "TIER_1_LABEL": "Not at home", "TIER_1_BADGE": "RENT",
        "TIER_2_META": "8-12 GB GPU", "TIER_2_LABEL": "QLoRA 7-8B", "TIER_2_BADGE": "TRAINS",
        "TIER_3_META": "32-64 GB", "TIER_3_LABEL": "30B-70B 4-bit", "TIER_3_BADGE": "RUNS",
        "TIER_4_META": "16 GB", "TIER_4_LABEL": "8-12B models", "TIER_4_BADGE": "RUNS",
        "ANCHOR_LABEL": "Your home box",
    },
    "layer_stack": {
        "STACK_TITLE": "DESIGN.md structure", "STACK_SUBHEAD": "Two-part AI-readable design systems.",
        "LAYER_1_LABEL": "YAML frontmatter", "LAYER_1_NOTE": "Machine-readable metadata.",
        "LAYER_2_LABEL": "Design tokens", "LAYER_2_NOTE": "Names and values.",
        "LAYER_3_LABEL": "Body", "LAYER_3_NOTE": "Human-readable guidance.",
        "LAYER_4_LABEL": "Brand and style", "LAYER_4_NOTE": "The visual system.",
        "LAYER_5_LABEL": "Components", "LAYER_5_NOTE": "Reusable UI pieces.",
    },
}


def test_deck_archetypes_registered():
    for arch in ("compare_code", "stepped_cards", "tier_ladder", "layer_stack"):
        assert arch in ARCHETYPES
        sk = get_skeleton(arch)
        assert sk, f"{arch} produced no skeleton"
        assert "__" not in sk, f"{arch} left an unexpanded structural marker"
        assert "{{" in sk


def test_deck_archetypes_slot_check_pass_and_fail():
    for arch, good in _DECK_ARCHETYPE_SLOTS.items():
        ok, reason = _check_slots(arch, dict(good))
        assert ok, f"{arch} full-slot check should pass ({reason})"
        blank = {k: "" for k in good}
        blank[next(iter(good))] = good[next(iter(good))]  # keep one filled
        ok2, _ = _check_slots(arch, blank)
        assert not ok2, f"{arch} should reject a mostly-blank slot set"


def test_deck_archetypes_finalize_fails_open():
    for arch, good in _DECK_ARCHETYPE_SLOTS.items():
        sk = get_skeleton(arch)
        partial = dict(list(good.items())[:2])
        body = _finalize_body(sk, partial)
        assert "{{" not in body and "}}" not in body, f"{arch} left an unfilled slot"
        assert "<div" in body


def test_tier_ladder_chips_bind_to_real_sources():
    """tier_ladder's 2x2 stat chips carry cite superscripts bound to REAL source
    numbers (HARD RULE §2.6) — or empty, never dangling, when no provenance."""
    sk = get_skeleton("tier_ladder")
    prov = _normalize_sources([{"title": "bench"}, {"title": "receipts"}])
    slots = dict(_DECK_ARCHETYPE_SLOTS["tier_ladder"])
    slots.update(_cite_slots_for_rows(prov, n_rows=4))
    body = _finalize_body(sk, slots)
    assert '<sup class="ib-cite">1</sup>' in body
    assert '<sup class="ib-cite">2</sup>' in body

    slots2 = dict(_DECK_ARCHETYPE_SLOTS["tier_ladder"])
    slots2.update(_cite_slots_for_rows([], n_rows=4))
    body2 = _finalize_body(sk, slots2)
    assert '<sup class="ib-cite">1</sup>' not in body2
    assert '<sup class="ib-cite"></sup>' in body2   # empty, not dangling


def test_pick_archetype_deck_lanes():
    assert pick_archetype("two implementations of the same api call") == "compare_code"
    assert pick_archetype("a capability tier ladder: what runs at home") == "tier_ladder"
    assert pick_archetype("an exploded view of the stacked layers of a file") == "layer_stack"
    assert pick_archetype("three step cards joined by a feedback loop") == "stepped_cards"
    # the original heuristics still route as before (no deck-keyword bleed)
    assert pick_archetype("the compile stage then index then serve") == "three_stage"
    assert pick_archetype("quarterly revenue figures from the filing") == "facts_table"


def test_cream_tone_is_valid_and_drops_dots():
    from services.infographic import restyle
    assert "cream" in restyle.TONE_IDS
    css = restyle.restyle_css({"tone": "cream"})
    assert "--ib-bg:#faf6ee;" in css
    assert "background-image:none;" in css       # the deck look = no dots
