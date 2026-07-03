"""SVG → PNG renderer via headless Chromium.

Used by the visual critic to rasterize freeform SVG output so a vision
model can score it, and by the API layer when returning PNG variants.

Reuses the long-lived browser pattern from mermaid_renderer.py. Distinct
module so the dependency is explicit: this is plain SVG, not Mermaid.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# S3/C4 (2026-07-03): the module-global browser moved to playwright_utils —
# ONE shared chromium for svg/mermaid/slide rendering.
async def _get_browser():
    from services.playwright_utils import get_shared_browser
    return await get_shared_browser()


async def render_svg_to_png(
    svg_markup: str,
    width: int = 1600,
    height: int = 900,
    scale: float = 2.0,
    timeout_ms: int = 10000,
) -> Optional[bytes]:
    """Render an SVG element string to PNG bytes.

    Args:
        svg_markup: A complete <svg>...</svg> element string
        width: Viewport width
        height: Viewport height
        scale: Device scale factor (2.0 = retina)
        timeout_ms: Max time for content settle
    """
    if not svg_markup or "<svg" not in svg_markup.lower():
        return None

    browser = await _get_browser()
    if not browser:
        return None

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;background:#fff}</style>"
        f"</head><body>{svg_markup}</body></html>"
    )

    page = None
    try:
        page = await browser.new_page(
            viewport={'width': width, 'height': height},
            device_scale_factor=scale,
        )
        await page.set_content(html, wait_until='networkidle', timeout=timeout_ms)
        svg_el = await page.query_selector('svg')
        if not svg_el:
            logger.warning("[svg_renderer] no svg element in page")
            return None
        png = await svg_el.screenshot(type='png')
        return png
    except Exception as e:
        logger.error(f"[svg_renderer] render failed: {e}")
        return None
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def shutdown():
    """Close the shared browser (delegates to playwright_utils)."""
    from services.playwright_utils import shutdown_shared_browser
    await shutdown_shared_browser()
