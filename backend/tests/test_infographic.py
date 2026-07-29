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
    """L4 now builds (Klein) — it must NOT collapse to L2. L3 still defers."""
    from services.intent_classifier import _normalize_lane
    assert _normalize_lane("L4") == "L4"
    assert _normalize_lane("l4") == "L4"
    assert _normalize_lane("L3") == "L2"       # still deferred -> volume lane
    assert _normalize_lane("nonsense") == "L2"  # fail-open


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


# ── Archetype heuristic ────────────────────────────────────────────────
def test_pick_archetype():
    assert pick_archetype("the compile stage then index then serve") == "three_stage"
    assert pick_archetype("quarterly revenue figures from the filing") == "facts_table"
    assert pick_archetype("some generic prose about a process") == "pipeline_compare"
