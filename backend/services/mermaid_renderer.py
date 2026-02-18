"""Mermaid Diagram Renderer

Renders Mermaid diagram code to PNG bytes using Playwright.
Used for embedding visuals in PPTX slides and other image contexts.

Architecture:
  - Lazy-loads a headless Chromium browser on first render call
  - Loads Mermaid.js from local node_modules (no internet required)
  - Sets the diagram code, waits for render, screenshots the SVG element
  - Caches the browser instance for reuse across calls
  - Falls back gracefully if Playwright/Chromium unavailable
"""
import asyncio
import os
from pathlib import Path
from typing import Optional

# Browser instance — lazy loaded, reused across renders
_browser = None
_browser_lock = asyncio.Lock()

# Resolve Mermaid.js from local node_modules (no CDN dependency)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # LocalBook/
_MERMAID_LOCAL = _PROJECT_ROOT / "node_modules" / "mermaid" / "dist" / "mermaid.min.js"
_MERMAID_SRC = (
    f"file://{_MERMAID_LOCAL}" if _MERMAID_LOCAL.exists()
    else "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"  # last-resort fallback
)

# Minimal HTML template that loads Mermaid.js and renders a diagram
_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <script src="__MERMAID_SRC__"></script>
  <style>
    body { margin: 0; padding: 20px; background: white; }
    #diagram { display: inline-block; }
  </style>
</head>
<body>
  <pre class="mermaid" id="diagram">
  </pre>
  <script>
    mermaid.initialize({
      startOnLoad: false,
      theme: 'default',
      securityLevel: 'loose',
      flowchart: { curve: 'basis', padding: 15 },
      themeVariables: {
        fontSize: '14px',
        fontFamily: 'Inter, system-ui, sans-serif'
      }
    });

    async function renderDiagram(code) {
      const el = document.getElementById('diagram');
      el.textContent = code;
      try {
        const { svg } = await mermaid.render('rendered', code);
        el.innerHTML = svg;
        return true;
      } catch (e) {
        el.textContent = 'Render error: ' + e.message;
        return false;
      }
    }

    window.renderDiagram = renderDiagram;
  </script>
</body>
</html>""".replace("__MERMAID_SRC__", _MERMAID_SRC)


async def _get_browser():
    """Get or create the shared Playwright browser instance."""
    global _browser
    async with _browser_lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            _browser = await pw.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage']
            )
            print("[MermaidRenderer] Browser launched")
            return _browser
        except Exception as e:
            print(f"[MermaidRenderer] Failed to launch browser: {e}")
            _browser = None
            return None


async def render_mermaid_to_png(
    mermaid_code: str,
    width: int = 1200,
    height: int = 800,
    scale: float = 2.0,
    timeout_ms: int = 15000,
) -> Optional[bytes]:
    """Render Mermaid diagram code to PNG bytes.

    Args:
        mermaid_code: Valid Mermaid diagram code
        width: Viewport width in pixels
        height: Viewport height in pixels
        scale: Device scale factor (2.0 = retina quality)
        timeout_ms: Max time to wait for Mermaid render

    Returns:
        PNG bytes on success, None on failure
    """
    browser = await _get_browser()
    if not browser:
        return None

    page = None
    try:
        page = await browser.new_page(
            viewport={'width': width, 'height': height},
            device_scale_factor=scale,
        )

        # Load the HTML template
        await page.set_content(_HTML_TEMPLATE, wait_until='networkidle')

        # Call the renderDiagram function with our code
        success = await page.evaluate(f'renderDiagram({repr(mermaid_code)})')

        if not success:
            print(f"[MermaidRenderer] Mermaid.js failed to render diagram")
            return None

        # Wait for SVG to appear
        await page.wait_for_selector('#diagram svg', timeout=timeout_ms)

        # Screenshot just the SVG element for tight cropping
        svg_element = await page.query_selector('#diagram svg')
        if svg_element:
            png_bytes = await svg_element.screenshot(type='png')
            print(f"[MermaidRenderer] Rendered {len(png_bytes)} bytes PNG")
            return png_bytes

        # Fallback: screenshot the whole diagram container
        diagram = await page.query_selector('#diagram')
        if diagram:
            png_bytes = await diagram.screenshot(type='png')
            return png_bytes

        return None

    except Exception as e:
        print(f"[MermaidRenderer] Render failed: {e}")
        return None
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def shutdown():
    """Close the browser instance. Call on app shutdown."""
    global _browser
    async with _browser_lock:
        if _browser:
            try:
                await _browser.close()
            except Exception:
                pass
            _browser = None
            print("[MermaidRenderer] Browser closed")


def is_available() -> bool:
    """Check if Playwright + Chromium are available."""
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        return True
    except ImportError:
        return False
