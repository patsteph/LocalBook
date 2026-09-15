"""Image generation test runner — the `image_model` role, previously unmeasured.

Until 2026-09-14 an entire model ROLE had no coverage at all. The Evaluator scored chat, vision,
embeddings and TTS, and said nothing whatsoever about whether the configured image model could
produce an image — so "this combo works" was a claim about four roles out of five.

Deliberately JUDGE-FREE. Whether a generated image is *good* needs a human or a vision model,
and both make the number model-dependent and incomparable. What can be checked mechanically is
whether it works at all, and those checks catch the failures that actually happen:

  - the model is not present / mflux is missing            → skipped, not failed
  - generation raises or returns success=False             → fail, with the engine's own error
  - PNG bytes are absent or not a PNG                      → fail (a truncated write looks fine
                                                              until something tries to open it)
  - dimensions do not match what was asked for             → fail
  - the image is BLANK                                     → fail. A solid-colour PNG is the
                                                              classic silent diffusion failure:
                                                              correct size, correct header, real
                                                              file, no picture.

Kept to ONE small draft-tier generation. Klein is ~4.3 GB and a hero-tier image takes minutes;
this is a "does the role function" check, not a quality benchmark.
"""
import time
from datetime import datetime

from evaluator.models import EvalResult

_PROMPT = "a red circle centred on a plain white background, flat vector style"
_WIDTH, _HEIGHT, _STEPS = 512, 512, 4   # smallest useful draft — this is a liveness check


def _png_is_blank(png_bytes: bytes) -> bool:
    """True when every pixel is the same colour.

    The failure this catches: diffusion that completes, writes a correctly-sized valid PNG, and
    produces a flat field of one colour. Every structural check passes and there is no image.
    """
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        extrema = img.getextrema()          # ((rmin,rmax),(gmin,gmax),(bmin,bmax))
        return all(lo == hi for lo, hi in extrema)
    except Exception:
        # Cannot decode → not our call to make here; the PNG-header check already ran.
        return False


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list:
    from config import settings

    result = EvalResult(
        test_id="image_gen_basic",
        category="image_gen",
        test_name="Image Generation: draft render",
        model_combo=combo_name,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )
    image_model = getattr(settings, "image_model", "") or ""
    result.stamp_provider(image_model)

    if not image_model:
        result.mark_skipped("No image model configured")
        print("[EVAL-IMAGE] skipped — no image model configured")
        return [result]

    # Not downloaded is a CONFIGURATION fact, not a quality failure — the same distinction
    # field_edges needed. Scoring it zero would say the model is bad when it is simply absent.
    try:
        from services.model_presence import is_present
        if is_present(image_model) is False:
            result.mark_skipped(f"{image_model} is not downloaded")
            print(f"[EVAL-IMAGE] skipped — {image_model} not downloaded")
            return [result]
    except Exception:
        pass

    # An ImportError here is OUR bug, not a missing capability — and a broad "skip on any
    # exception" would report the image role as not-applicable forever while hiding it. That is
    # precisely the silence this runner exists to end, so it fails loudly instead.
    # (It did exactly that on 2026-09-14: the singleton is `klein_diffusion`, the first draft
    # imported `visual_diffusion`, and the runner cheerfully skipped.)
    try:
        from services.visual_diffusion import klein_diffusion
    except Exception as e:
        result.passed = False
        result.overall_score = 0
        result.failure_reason = (
            f"could not import the diffusion engine ({type(e).__name__}: {e}) — this is a wiring "
            f"fault in the evaluator, not a missing model"
        )
        print(f"[EVAL-IMAGE] IMPORT FAILED: {e}")
        return [result]

    try:
        start = time.time()
        res = await klein_diffusion.generate(
            _PROMPT, width=_WIDTH, height=_HEIGHT, steps=_STEPS, unload_after=True,
        )
        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed
        result.input_chars = len(_PROMPT)

        png = getattr(res, "png_bytes", None) or b""
        checks = {
            "reported_success": bool(getattr(res, "success", False)),
            "produced_bytes": len(png) > 0,
            # A PNG always starts with this 8-byte signature. Cheap, and it catches a truncated
            # or error-page write that every other check would wave through.
            "is_png": png[:8] == b"\x89PNG\r\n\x1a\n",
            "width_ok": getattr(res, "width", 0) == _WIDTH,
            "height_ok": getattr(res, "height", 0) == _HEIGHT,
            "not_blank": bool(png) and not _png_is_blank(png),
        }
        passed = sum(1 for v in checks.values() if v)
        result.overall_score = int(100 * passed / len(checks))
        result.passed = all(checks.values())
        result.output_chars = len(png)
        result.sub_scores = {
            **{k: (1 if v else 0) for k, v in checks.items()},
            "bytes": len(png),
            "elapsed_ms": round(elapsed, 1),
        }
        result.actual_output_preview = (
            f"{getattr(res, 'width', 0)}x{getattr(res, 'height', 0)} "
            f"{len(png)} bytes in {elapsed/1000:.1f}s"
        )
        if not result.passed:
            failed = [k for k, v in checks.items() if not v]
            engine_err = getattr(res, "error", None)
            result.failure_reason = (
                f"failed: {', '.join(failed)}" + (f" — engine said: {engine_err}" if engine_err else "")
            )
        print(f"[EVAL-IMAGE] score={result.overall_score} "
              f"({passed}/{len(checks)} checks) {elapsed/1000:.1f}s")

    except Exception as e:
        result.passed = False
        result.overall_score = 0
        result.failure_reason = str(e)[:200]
        print(f"[EVAL-IMAGE] FAILED: {e}")

    return [result]
