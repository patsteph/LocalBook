"""Vision / Image analysis test runner — tests vision model accuracy."""

import time
import base64
from datetime import datetime
from pathlib import Path
from evaluator.models import EvalResult
from evaluator.capabilities import capabilities_for, FEATURES

EVALUATOR_DIR = Path(__file__).parent.parent


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Test vision model with a generated chart image.

    Capability-aware (v1.8.2): if no vision model is configured or the
    configured vision model does not support vision (e.g. because the main
    model was swapped to a text-only sidecar), the test is *skipped* rather
    than marked failing, so category scores aren't unfairly penalised.
    """
    from config import settings

    vision_model = getattr(settings, 'vision_model', '') or ''
    vision_config = config.get("vision_test", {})

    result = EvalResult(
        test_id="vision_image_analysis",
        category="vision",
        test_name="Image Description Accuracy",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    result.stamp_provider(vision_model)

    # ── Capability gate ────────────────────────────────────────────────
    if not vision_model:
        result.mark_skipped("No vision model configured for this combo")
        print("[EVAL-VISION] skipped — no vision model configured")
        return [result]

    caps = capabilities_for(vision_model)
    if not caps.supports(FEATURES.VISION):
        reason = caps.skip_reason(FEATURES.VISION) or f"{vision_model} does not support vision"
        result.mark_skipped(reason)
        print(f"[EVAL-VISION] skipped — {reason}")
        return [result]

    try:
        # Generate a simple test image (bar chart) as PNG
        image_data = _generate_test_chart()
        if not image_data:
            raise ValueError("Could not generate test chart image")

        # Encode as base64 for Ollama vision API
        b64_image = base64.b64encode(image_data).decode("utf-8")

        # Apply the model's full vision_profile so the eval mirrors what
        # production uses. Without this we'd test with global defaults
        # (num_predict=1500, num_ctx=8192, temp=0.3) regardless of the
        # combo's tuning — defeating apples-to-apples comparison once a
        # second vision model joins the catalog.
        from evaluator.model_registry import model_registry
        model_info = model_registry.get_model(vision_model)
        api_style = model_info.vision_api_style if model_info else "generate"
        vp = (model_info.vision_profile if model_info else {}) or {}
        # The chart prompt is short and the answer should be too — cap at 300
        # tokens unless the profile explicitly wants something longer.
        eval_num_predict = min(int(vp.get("num_predict", 300)), 300)
        eval_num_ctx = vp.get("num_ctx")  # None lets ollama_client pick its default
        eval_temperature = vp.get("temperature", 0.3)

        start = time.time()

        from services.ollama_client import ollama_client
        description = await ollama_client.vision_describe(
            image_b64=b64_image,
            prompt="Describe this chart in detail. What data does it show? What are the values?",
            model=vision_model,
            api_style=api_style,
            timeout=60.0,
            num_predict=eval_num_predict,
            num_ctx=eval_num_ctx,
            temperature=eval_temperature,
        )

        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed

        if description.startswith("Error:"):
            raise ValueError(f"Vision API error: {description}")

        result.output_chars = len(description)
        result.actual_output_preview = description[:500]

        # Score
        expected_terms = vision_config.get("expected_terms", ["chart", "bar", "model"])
        term_hits = sum(1 for t in expected_terms if t.lower() in description.lower())
        accuracy_score = int((term_hits / max(1, len(expected_terms))) * 100)

        speed_score = 100 if elapsed < 30000 else max(0, int(100 - (elapsed - 30000) / 500))
        length_score = 100 if len(description) > 50 else max(0, int(len(description) * 2))

        # Check it's not a generic fallback
        generic_phrases = ["i cannot", "i can't", "the image does not", "unable to"]
        is_generic = any(p in description.lower() for p in generic_phrases)
        generic_score = 0 if is_generic else 100

        result.accuracy_score = accuracy_score
        result.overall_score = int(
            accuracy_score * 0.40 + speed_score * 0.30 + length_score * 0.15 + generic_score * 0.15
        )
        result.passed = result.overall_score >= 40 and not is_generic

        if is_generic:
            result.failure_reason = "Vision model returned generic/fallback description"

        print(f"[EVAL-VISION] Score={result.overall_score}, {len(description)} chars, {elapsed:.0f}ms")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-VISION] FAILED: {e}")

    return [result]


def _generate_test_chart() -> bytes | None:
    """Generate a simple bar chart as PNG bytes for vision testing using PIL."""
    import io
    try:
        from PIL import Image, ImageDraw

        # Create basic image
        img = Image.new('RGB', (400, 250), color=(250, 250, 250))
        draw = ImageDraw.Draw(img)

        # Draw Title
        draw.text((120, 15), "Model Throughput (tokens/sec)", fill=(50, 50, 50))
        
        # Data and scaling
        data = [
            ("OLMo 3 7B", 18),
            ("Phi-4 Mini", 35),
            ("Arctic Embed", 120)
        ]
        
        # Draw axes
        draw.line([(50, 40), (50, 210)], fill=(100, 100, 100), width=2)
        draw.line([(50, 210), (380, 210)], fill=(100, 100, 100), width=2)

        # Draw grid lines for Y axis
        for val in [0, 30, 60, 90, 120, 150]:
            y = 210 - int((val / 150) * 170)
            draw.line([(45, y), (380, y)], fill=(220, 220, 220), width=1)
            draw.text((20, y - 5), str(val), fill=(100, 100, 100))

        # Draw bars
        bar_width = 60
        spacing = 40
        x_start = 80

        for i, (label, val) in enumerate(data):
            x = x_start + i * (bar_width + spacing)
            height = int((val / 150) * 170)
            y = 210 - height
            
            # Draw bar
            draw.rectangle([x, y, x + bar_width, 210], fill=(74, 144, 217))
            
            # Draw label
            draw.text((x + 5, 220), label, fill=(50, 50, 50))

        # Save to bytes
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        return img_byte_arr.getvalue()

    except ImportError:
        print("[EVAL-VISION] PIL not available for chart generation")
        return None
    except Exception as e:
        print(f"[EVAL-VISION] Chart generation failed: {e}")
        return None
