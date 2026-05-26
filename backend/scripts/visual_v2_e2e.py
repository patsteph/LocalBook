"""End-to-end test of the Visual System v2 composer.

Runs the full pipeline (capability → freeform → render → critic → adaptive retry)
on a single prompt. Reports the path taken, critic scores, retry behavior,
and writes the final SVG + PNG to disk for eyeball review.

Usage:
    python backend/scripts/visual_v2_e2e.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.visual_composer import GenerationPath, visual_composer  # noqa: E402
from services.svg_renderer import render_svg_to_png  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "e2e_output"

PROMPT = (
    "A three-tier system architecture for a SaaS platform: web/mobile clients "
    "at the top, an API gateway routing to four backend microservices (auth, "
    "orders, inventory, payments), each microservice connected to its own "
    "database. Include a load balancer in front of the gateway and a Redis "
    "cache shared by the services. Label every component. This will be shown "
    "to enterprise customers in a sales presentation."
)


async def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Visual v2 e2e — composer pipeline test")
    print(f"Output dir: {OUT_DIR}\n")

    t0 = time.time()
    result = await visual_composer.compose(
        content=PROMPT,
        # Force Gemma freeform path even on Setup A (since Gemma is installed)
        force_path=GenerationPath.GEMMA_FREEFORM,
    )
    elapsed = time.time() - t0

    print(f"Path        : {result.path.value}")
    print(f"Setup       : {result.setup.value}")
    print(f"Format      : {result.output_format.value}")
    print(f"Success     : {result.success}")
    print(f"Title       : {result.title}")
    print(f"Idiom       : {result.template_id}")
    print(f"Model used  : {result.model_used}")
    print(f"Retry count : {result.retry_count}")
    print(f"Total ms    : {result.generation_ms}")
    print(f"Wall time   : {elapsed:.1f}s")

    if result.critic_score:
        c = result.critic_score
        print()
        print("Critic scores:")
        print(f"  legibility      : {c.legibility:.2f}")
        print(f"  hierarchy       : {c.hierarchy:.2f}")
        print(f"  balance         : {c.balance:.2f}")
        print(f"  color_harmony   : {c.color_harmony:.2f}")
        print(f"  message_clarity : {c.message_clarity:.2f}")
        print(f"  OVERALL         : {c.overall:.2f}  "
              f"({'PASS' if c.overall >= 0.70 else 'FAIL'} @ 0.70)")
        if c.weaknesses:
            print("\nWeaknesses cited:")
            for w in c.weaknesses:
                print(f"  - {w}")
        if c.suggestions:
            print("\nSuggestions:")
            for s in c.suggestions:
                print(f"  → {s}")

    if result.error:
        print(f"\nError: {result.error}")

    if result.svg_markup:
        svg_path = OUT_DIR / "e2e_visual.svg"
        svg_path.write_text(result.svg_markup)
        print(f"\nSVG written: {svg_path} ({len(result.svg_markup)} chars)")
        png = await render_svg_to_png(result.svg_markup)
        if png:
            png_path = OUT_DIR / "e2e_visual.png"
            png_path.write_bytes(png)
            print(f"PNG written: {png_path} ({len(png) // 1024} KB)")


if __name__ == "__main__":
    asyncio.run(main())
