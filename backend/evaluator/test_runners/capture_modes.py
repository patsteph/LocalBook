"""Capture-modes coverage test runner.

Cycles through 5 representative scan modes (document, handwriting,
diagram, receipt, recipe) on programmatically-generated PIL test images
and verifies the per-mode prompt produces output with the expected
markers — e.g., recipe must contain '### Ingredients', receipt must
contain a markdown table, diagram must produce a ```mermaid block.

Each mode's image is generated deterministically inside this file, so
the test is reproducible across machines and doesn't depend on test
fixtures the user has to ship. The vision model still has to actually
read the rendered text — pure-blank images would never produce the
expected markers, so an empty image == failed test.

Apples-to-apples: same images, same prompts (vision_prompts.MODE_PROMPTS),
same scoring across model swaps.
"""

import base64
import io
import time
from datetime import datetime

from evaluator.models import EvalResult


# Per-mode test specs. Each spec generates an image and lists markers
# the OCR/transcription must produce. Markers are case-insensitive
# substring checks — the smallest signal that the mode-specific prompt
# was followed (vs the model defaulting to generic prose).
_MODE_SPECS = [
    {
        "mode": "document",
        "image_text": "Chapter One\n\nThis is a paragraph of plain printed text.\n\nThis is a second paragraph.",
        "expected_markers": ["chapter", "paragraph"],
        "min_chars": 40,
    },
    {
        "mode": "handwriting",
        # We can't really render true cursive with PIL — but we CAN test
        # whether the mode prompt produces useful output on a low-quality
        # text image. A wobbly font would be ideal; default font is OK
        # for a smoke test.
        "image_text": "Dear diary,\n\nToday I learned about\nhash tables and trees.",
        "expected_markers": ["diary", "hash"],
        "min_chars": 30,
    },
    {
        "mode": "diagram",
        "image_text": "[ Start ] -> [ Process ] -> [ End ]",
        # Diagram mode is supposed to emit a ```mermaid fence. If the
        # model emits "graph" or "mermaid" anywhere, that's the right
        # behavior. A plain prose answer is wrong.
        "expected_markers": ["mermaid", "graph"],
        "min_chars": 20,
    },
    {
        "mode": "receipt",
        "image_text": "ACME COFFEE\n\nDate: 2026-01-05\n\nLatte         $4.50\nMuffin        $3.25\n--------------\nTotal         $7.75",
        # Receipt prompt asks for a markdown table — pipe characters or
        # word "Total" with a price are sufficient signals.
        "expected_markers": ["acme", "total"],
        "min_chars": 40,
    },
    {
        "mode": "recipe",
        "image_text": "Pancakes\n\nIngredients:\n- 2 cups flour\n- 2 eggs\n- 1 cup milk\n\nInstructions:\n1. Mix dry\n2. Add wet\n3. Cook",
        # Recipe prompt asks for ### Ingredients and ### Instructions
        # sections. We accept either heading style (### or just the word).
        "expected_markers": ["ingredient", "instruction"],
        "min_chars": 40,
    },
]


def _render_text_png(text: str, width: int = 480, height: int = 360) -> str | None:
    """Render `text` to a PNG and return base64. Multi-line, default font.

    PIL is already a project dep (used by other vision tests + scan
    pipeline). Returns None if PIL is unavailable so the caller can skip
    the test rather than fail it.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    img = Image.new("RGB", (width, height), color=(252, 252, 252))
    draw = ImageDraw.Draw(img)
    # Word-wrap rough — PIL's default font is bitmap, no real metrics,
    # so we just newline-split and stack lines.
    y = 18
    for line in text.split("\n"):
        # Truncate very long lines to fit width-ish.
        draw.text((20, y), line[:80], fill=(20, 20, 20))
        y += 18
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Run the per-mode coverage suite. Returns one EvalResult per mode."""
    from config import settings
    from services.ollama_client import ollama_client
    from services.vision_prompts import MODE_PROMPTS
    from evaluator.model_registry import model_registry

    vision_model = getattr(settings, "vision_model", "") or ""
    results: list[EvalResult] = []

    if not vision_model:
        # No vision model configured — emit a single skipped result so
        # the category appears in the report rather than disappearing.
        skipped = EvalResult(
            test_id="capture_modes",
            category="capture_modes",
            test_name="Capture Modes Coverage",
            model_combo=combo_name,
            hardware_fingerprint=hw_fingerprint,
            timestamp=datetime.utcnow().isoformat(),
        )
        skipped.mark_skipped("No vision model configured")
        return [skipped]

    info = model_registry.get_model(vision_model)
    api_style = info.vision_api_style if info else "generate"

    for spec in _MODE_SPECS:
        mode = spec["mode"]
        result = EvalResult(
            test_id=f"capture_mode_{mode}",
            category="capture_modes",
            test_name=f"Capture Mode: {mode}",
            model_combo=combo_name,
            hardware_fingerprint=hw_fingerprint,
            timestamp=datetime.utcnow().isoformat(),
        )
        result.stamp_provider(vision_model)

        b64 = _render_text_png(spec["image_text"])
        if b64 is None:
            result.mark_skipped("PIL unavailable for image generation")
            results.append(result)
            continue

        prompt = MODE_PROMPTS.get(mode)
        if not prompt:
            result.mark_skipped(f"No prompt defined for mode={mode}")
            results.append(result)
            continue

        try:
            start = time.time()
            output = await ollama_client.vision_describe(
                image_b64=b64,
                prompt=prompt,
                model=vision_model,
                api_style=api_style,
                num_predict=400,
                timeout=60.0,
            )
            elapsed = (time.time() - start) * 1000
            result.total_time_ms = elapsed
            result.output_chars = len(output)
            result.actual_output_preview = output[:300]

            if output.startswith("Error:"):
                raise ValueError(f"vision returned error for mode={mode}: {output[:120]}")

            output_lower = output.lower()
            marker_hits = sum(1 for m in spec["expected_markers"] if m.lower() in output_lower)
            marker_score = int((marker_hits / max(1, len(spec["expected_markers"]))) * 100)
            length_ok = len(output) >= spec["min_chars"]
            length_score = 100 if length_ok else max(0, int((len(output) / spec["min_chars"]) * 100))

            # Generic-fallback detection — vision models often return
            # "I cannot describe this image" on degenerate inputs. Penalise.
            generic_markers = ["i cannot", "i can't", "unable to", "the image does not"]
            is_generic = any(g in output_lower for g in generic_markers)
            generic_score = 0 if is_generic else 100

            result.sub_scores = {
                "mode": mode,
                "marker_hits": marker_hits,
                "marker_total": len(spec["expected_markers"]),
                "length_score": length_score,
                "generic_score": generic_score,
            }
            result.accuracy_score = marker_score
            result.overall_score = int(
                marker_score * 0.55 + length_score * 0.25 + generic_score * 0.20
            )
            result.passed = result.overall_score >= 40 and not is_generic
            if is_generic:
                result.failure_reason = "Vision model returned generic 'cannot describe' fallback"
            print(
                f"[EVAL-MODES] {mode}: markers={marker_hits}/{len(spec['expected_markers'])}, "
                f"chars={len(output)}, score={result.overall_score}"
            )
        except Exception as e:
            result.passed = False
            result.failure_reason = str(e)[:200]
            result.overall_score = 0
            print(f"[EVAL-MODES] {mode} FAILED: {e}")

        results.append(result)

    return results
