"""artifact_renderer — Phase 5 of v2-information-cortex.

Server-side rendering of any Artifact envelope to PNG or PDF via the
already-running Playwright Chromium singleton (reused from
`services.mermaid_renderer`).

Public API:
    await render_artifact_to_png(artifact, *, width, scale) -> bytes | None
    await render_artifact_to_pdf(artifact, *, page_size) -> bytes | None

Design notes:
- We do NOT run React server-side. The skeleton HTML built here uses
  small inline JS (`marked`, `mermaid`, `chart.js` from pinned CDNs) plus
  the Tailwind subset to render payloads in a way that matches the
  frontend renderers closely enough for export. Pixel-match is not the
  goal; "this looks like the same artifact" is.
- For `json:comparison`, payload is rendered server-side in Python
  (mirrors `ComparisonArtifactRenderer.tsx` layout) because it's all
  structured data — no JS needed.
- For `markdown`, charts/SVG/Klein/mermaid embedded as code-fences are
  resolved during page load by marked + the same fence handlers the
  frontend uses (`svg`, `klein`, `json-chart`, etc.). Phase 4's
  visual_resolver runs *before* the artifact reaches export, so any
  `lb-chart` / `lb-visual-hint` fences have already been resolved
  upstream — the export renderer only sees `json-chart` / `svg` fences.
- Failure returns None. Caller (export endpoint) decides whether to
  fall back to a simpler render or surface an error.
"""
from __future__ import annotations

import html as html_lib
import json
import logging
from typing import Any, Dict, Optional

from services.export_assets import (
    CHARTJS_CDN,
    MARKED_CDN,
    MERMAID_CDN,
    TAILWIND_SUBSET_CSS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-artifact-type inner-HTML builders.
# ---------------------------------------------------------------------------

def _esc(s: Optional[str]) -> str:
    return html_lib.escape(s or "", quote=True)


def _render_comparison_payload(payload: Dict[str, Any]) -> str:
    """Server-side mirror of ComparisonArtifactRenderer.tsx."""
    title_a = _esc((payload.get("source_a") or {}).get("title") or "Source A")
    title_b = _esc((payload.get("source_b") or {}).get("title") or "Source B")

    def _section(title: str, items_or_text, *, emphasis: str = "shared") -> str:
        accent = {
            "a": "border-blue-200 bg-blue-50",
            "b": "border-purple-200 bg-purple-50",
            "shared": "border-gray-200 bg-gray-50",
        }.get(emphasis, "border-gray-200 bg-gray-50")
        if isinstance(items_or_text, list):
            if not items_or_text:
                body = '<p class="text-xs italic text-gray-500">(none)</p>'
            else:
                lis = "".join(f"<li>{_esc(it)}</li>" for it in items_or_text)
                body = f'<ul class="text-sm text-gray-800">{lis}</ul>'
        else:
            body = f'<p class="text-sm text-gray-800">{_esc(items_or_text)}</p>'
        return (
            f'<div class="rounded-lg border {accent} p-3">'
            f'<h4 class="text-xs font-semibold uppercase tracking-wide text-gray-600 mb-2">{_esc(title)}</h4>'
            f'{body}'
            f'</div>'
        )

    return (
        '<div class="flex flex-col gap-3">'
        '<div class="grid grid-cols-2 gap-3">'
        + _section(f"Unique to {title_a}", payload.get("unique_to_a") or [], emphasis="a")
        + _section(f"Unique to {title_b}", payload.get("unique_to_b") or [], emphasis="b")
        + '</div>'
        + _section("Similarities", payload.get("similarities") or [])
        + _section("Differences", payload.get("differences") or [])
        + (_section("Synthesis", payload.get("synthesis") or "") if payload.get("synthesis") else "")
        + '</div>'
    )


def _build_artifact_page(artifact: Dict[str, Any]) -> str:
    """Build a self-contained HTML page for the artifact, suitable for
    Playwright to load + screenshot or PDF."""
    a_type = artifact.get("type") or "markdown"
    payload = artifact.get("payload")
    title = artifact.get("title")
    extra_head: list[str] = []
    extra_scripts: list[str] = []
    inner_html: str

    if a_type == "markdown":
        text = payload if isinstance(payload, str) else ""
        extra_head.append(f'<script src="{MARKED_CDN}"></script>')
        extra_head.append(f'<script src="{MERMAID_CDN}"></script>')
        # marked supplies the prose; we also handle the inline fence
        # languages the frontend handles: mermaid, svg, klein, json-chart.
        inner_html = '<div id="md-root"></div>'
        extra_scripts.append(f"""
<script type="application/json" id="md-source">{json.dumps(text)}</script>
<script>
(function() {{
  const source = JSON.parse(document.getElementById('md-source').textContent);
  const renderer = new marked.Renderer();
  renderer.code = function(code, lang) {{
    const c = (lang || '').trim();
    if (c === 'mermaid') {{
      return '<div class="mermaid">' + code + '</div>';
    }}
    if (c === 'svg' || c === 'klein') {{
      return '<div class="my-4">' + code + '</div>';
    }}
    if (c === 'json-chart' || c === 'lb-chart') {{
      const cid = 'chart-' + Math.random().toString(36).slice(2, 9);
      window.__lb_pending_charts = window.__lb_pending_charts || [];
      try {{
        window.__lb_pending_charts.push({{ id: cid, cfg: JSON.parse(code) }});
      }} catch (e) {{}}
      return '<div class="my-4" style="height: 320px"><canvas id="' + cid + '"></canvas></div>';
    }}
    return '<pre><code class="language-' + c + '">' + code + '</code></pre>';
  }};
  document.getElementById('md-root').innerHTML = marked.parse(source, {{ renderer }});
  if (window.mermaid) mermaid.initialize({{ startOnLoad: true, securityLevel: 'loose' }});
}})();
</script>
<script src="{CHARTJS_CDN}"></script>
<script>
(function() {{
  if (!window.Chart || !window.__lb_pending_charts) return;
  window.__lb_pending_charts.forEach(({{ id, cfg }}) => {{
    const el = document.getElementById(id);
    if (!el || !cfg) return;
    const type = (cfg.chart_type === 'composed' ? 'bar' : cfg.chart_type) || 'bar';
    const labels = (cfg.data || []).map(d => d[(cfg.x_axis && cfg.x_axis.key) || 'x'] || '');
    const datasets = (cfg.series || []).map(s => ({{
      label: s.label || s.key,
      data: (cfg.data || []).map(d => d[s.key]),
      backgroundColor: s.color || '#6366f1',
      borderColor: s.color || '#6366f1',
    }}));
    new Chart(el, {{ type, data: {{ labels, datasets }}, options: {{ responsive: true, maintainAspectRatio: false }} }});
  }});
}})();
</script>
""")
    elif a_type == "html":
        # Trust the payload — it's our own pipeline. Wrapping <div> takes
        # the Tailwind subset; payload uses the same utility classes.
        inner_html = payload if isinstance(payload, str) else ""
    elif a_type in ("svg", "klein"):
        inner_html = payload if isinstance(payload, str) else ""
    elif a_type == "mermaid":
        extra_head.append(f'<script src="{MERMAID_CDN}"></script>')
        code = payload if isinstance(payload, str) else ""
        inner_html = f'<div class="mermaid">{_esc(code)}</div>'
        extra_scripts.append("<script>mermaid.initialize({startOnLoad: true, securityLevel: 'loose'});</script>")
    elif a_type == "json:chart":
        extra_head.append(f'<script src="{CHARTJS_CDN}"></script>')
        cfg_json = json.dumps(payload or {})
        inner_html = '<div style="height: 360px"><canvas id="chart"></canvas></div>'
        extra_scripts.append(f"""
<script>
(function() {{
  const cfg = {cfg_json};
  if (!window.Chart || !cfg.chart_type) return;
  const type = (cfg.chart_type === 'composed' ? 'bar' : cfg.chart_type);
  const labels = (cfg.data || []).map(d => d[(cfg.x_axis && cfg.x_axis.key) || 'x'] || '');
  const datasets = (cfg.series || []).map(s => ({{
    label: s.label || s.key,
    data: (cfg.data || []).map(d => d[s.key]),
    backgroundColor: s.color || '#6366f1',
    borderColor: s.color || '#6366f1',
  }}));
  new Chart(document.getElementById('chart'), {{ type, data: {{ labels, datasets }}, options: {{ responsive: true, maintainAspectRatio: false }} }});
}})();
</script>
""")
    elif a_type == "json:comparison":
        inner_html = _render_comparison_payload(payload or {})
    else:
        inner_html = f'<p class="text-gray-500 italic">Renderer not available for type: {_esc(a_type)}</p>'

    title_html = f'<h2 class="text-lg font-semibold mb-4 text-gray-800">{_esc(title)}</h2>' if title else ""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>{TAILWIND_SUBSET_CSS}</style>
  {''.join(extra_head)}
</head>
<body>
  <div class="lb-html-artifact p-6 max-w-3xl mx-auto">
    {title_html}
    {inner_html}
  </div>
  {''.join(extra_scripts)}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Public renderers
# ---------------------------------------------------------------------------

async def render_artifact_to_png(
    artifact: Dict[str, Any],
    *,
    width: int = 1200,
    scale: float = 2.0,
    timeout_ms: int = 20000,
) -> Optional[bytes]:
    """Render an Artifact envelope (dict) to PNG bytes via headless Chromium."""
    # Reuse the mermaid_renderer browser singleton — same Chromium for
    # both pipelines so we pay the launch cost once.
    from services.mermaid_renderer import _get_browser
    browser = await _get_browser()
    if not browser:
        return None
    page = None
    try:
        page = await browser.new_page(
            viewport={"width": width, "height": 800},
            device_scale_factor=scale,
        )
        await page.set_content(_build_artifact_page(artifact), wait_until="networkidle", timeout=timeout_ms)
        # Slight settle delay so charts/mermaid finish painting.
        await page.wait_for_timeout(500)
        body = await page.query_selector("body")
        png = await body.screenshot(type="png", full_page=True) if body else await page.screenshot(type="png", full_page=True)
        return png
    except Exception as e:
        logger.error(f"[artifact_renderer] PNG render failed: {e}")
        return None
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def render_artifact_to_pdf(
    artifact: Dict[str, Any],
    *,
    page_size: str = "Letter",
    timeout_ms: int = 20000,
) -> Optional[bytes]:
    """Render an Artifact envelope (dict) to PDF bytes via headless Chromium."""
    from services.mermaid_renderer import _get_browser
    browser = await _get_browser()
    if not browser:
        return None
    page = None
    try:
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.set_content(_build_artifact_page(artifact), wait_until="networkidle", timeout=timeout_ms)
        await page.wait_for_timeout(500)
        pdf = await page.pdf(format=page_size, print_background=True, margin={"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"})
        return pdf
    except Exception as e:
        logger.error(f"[artifact_renderer] PDF render failed: {e}")
        return None
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
