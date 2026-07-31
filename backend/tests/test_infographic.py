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
from pathlib import Path

import pytest

from services.infographic import icons
from services.infographic.skeletons import ARCHETYPES, get_skeleton
from services.infographic.builder import (
    _normalize_sources,
    _cite_slots_for_rows,
    _prose_fallback,
    _finalize_body,
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

    monkeypatch.setattr(vc, "get_capability", _cap)
    monkeypatch.setattr(settings, "image_engine", "ollama", raising=False)
    monkeypatch.setattr(vd, "write_klein_brief", _brief)
    monkeypatch.setattr(vd.klein_diffusion, "generate", _generate)

    out = asyncio.run(build_l4("hero image", title="My Title"))
    assert out is not None
    assert out["type"] == "json:infographic"
    p = out["payload"]
    assert p["lane"] == "L4"
    assert p["archetype"] == "decorative"
    assert p["image"].startswith("data:image/png;base64,")
    # Title is an OVERLAY, not pixels — it rides in the payload, not the prompt.
    assert p["title_overlay"] == "My Title"
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
    assert len(l3stickers.sticker_names()) >= 12


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
