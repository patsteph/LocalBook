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
import logging
logger = logging.getLogger(__name__)

# Browser instance — lazy loaded, reused across renders
# S3/C4 (2026-07-03): the module-global browser moved to playwright_utils —
# ONE shared chromium for svg/mermaid/slide rendering. Public name kept
# (artifact_renderer imports _get_browser).
async def _get_browser():
    from services.playwright_utils import get_shared_browser
    return await get_shared_browser()


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
            except Exception as _e:
                logger.debug(f"[mermaid-renderer] {type(_e).__name__}: {_e}")


async def shutdown():
    """Close the browser instance. Call on app shutdown."""
    global _browser
    async with _browser_lock:
        if _browser:
            try:
                await _browser.close()
            except Exception as _e:
                logger.debug(f"[mermaid-renderer] {type(_e).__name__}: {_e}")
            _browser = None
            print("[MermaidRenderer] Browser closed")


def is_available() -> bool:
    """Check if Playwright + Chromium are available."""
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        return True
    except ImportError:
        return False
