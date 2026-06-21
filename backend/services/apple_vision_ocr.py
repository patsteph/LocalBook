"""Apple Vision on-device OCR — free, fast, no model load.

The engine-strategy first win (audit doc 19/20: "Apple Vision OCR = GO"):
document/page text extraction is pure OCR, which Apple's Vision framework does
on-device far faster than routing an image through gemma4 (9.6 GB) — and it
doesn't touch the Ollama model lane or RAM at all. We use it for the
TEXT-EXTRACTION vision calls (scanned PDFs, page renders); scene/chart
*description* still needs the LLM and stays on gemma.

Fully optional + lazy: if PyObjC's Vision framework isn't importable (non-mac,
or the dep not bundled), `recognize_text` returns None and callers fall back to
the gemma vision path. Nothing here is imported at module load of the app.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_AVAILABLE: Optional[bool] = None  # tri-state cache: None=unknown, True/False


def is_available() -> bool:
    """True when Apple's Vision framework can be imported on this machine."""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    try:
        import Vision  # noqa: F401
        import Quartz  # noqa: F401
        _AVAILABLE = True
    except Exception as e:  # pragma: no cover - platform dependent
        logger.info(f"[apple-vision] OCR unavailable, will fall back to LLM vision: {e}")
        _AVAILABLE = False
    return _AVAILABLE


def _recognize_sync(image_bytes: bytes) -> Optional[str]:
    """Run VNRecognizeTextRequest on raw image bytes. Synchronous (the Vision
    API is blocking) — call via `recognize_text` which offloads to a thread."""
    import Vision
    import Quartz
    from Foundation import NSData

    data = NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
    src = Quartz.CGImageSourceCreateWithData(data, None)
    if not src or Quartz.CGImageSourceGetCount(src) == 0:
        return None
    cg_image = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if cg_image is None:
        return None

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    # Accurate level = best quality; language correction helps real prose.
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    ok, _err = handler.performRequests_error_([request], None)
    if not ok:
        return None
    results = request.results() or []
    lines = []
    for obs in results:
        cands = obs.topCandidates_(1)
        if cands and len(cands) > 0:
            txt = cands[0].string()
            if txt:
                lines.append(str(txt))
    return "\n".join(lines) if lines else ""


async def recognize_text(image_b64: str) -> Optional[str]:
    """OCR a base64-encoded image with Apple Vision. Returns recognized text,
    "" when the image has no text, or None when Vision is unavailable / errored
    (caller should then fall back to the LLM vision path)."""
    if not is_available():
        return None
    try:
        image_bytes = base64.b64decode(image_b64)
    except Exception as e:
        logger.warning(f"[apple-vision] bad base64: {e}")
        return None
    try:
        return await asyncio.to_thread(_recognize_sync, image_bytes)
    except Exception as e:
        logger.warning(f"[apple-vision] OCR failed, falling back to LLM vision: {e}")
        return None
