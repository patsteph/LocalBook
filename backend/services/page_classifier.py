"""
Page Classifier — heuristic-first content type detection.

The previous classifier called the vision LLM for every page (~1-2s per
page). For ~80% of typical scans the answer is obvious from cheap image
statistics: low-saturation horizontal-edge-dominant pages are documents;
high-saturation isotropic-edge pages are photos.

This module computes those stats (using OpenCV; ~50ms) and only invokes
the LLM when the heuristic confidence is low — in practice math pages,
whiteboards, and hand-drawn illustrations.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Tuple

logger = logging.getLogger(__name__)

VALID_MODES = {"document", "math", "whiteboard", "drawing", "photo"}


async def classify_page(
    image_bytes: bytes,
    llm_fallback: Callable[[bytes], Awaitable[str]],
) -> str:
    """Return one of: document, math, whiteboard, drawing, photo.

    Uses fast image statistics for the high-confidence cases. Falls back
    to the supplied async LLM classifier for everything else.

    `llm_fallback` is an async callable that takes the same `image_bytes`
    and returns the LLM-classified mode string. The classifier owns
    invoking it (so callers don't need a try/except around heuristics).
    """
    decision = _heuristic_classify(image_bytes)
    if decision is not None:
        logger.info(f"[classify] heuristic → {decision}")
        return decision

    logger.info("[classify] heuristic ambiguous → LLM fallback")
    try:
        mode = await llm_fallback(image_bytes)
    except Exception as e:
        logger.warning(f"[classify] LLM fallback failed: {e}; defaulting to document")
        return "document"

    mode = (mode or "").strip().lower().split()[0] if mode else "document"
    if mode not in VALID_MODES:
        mode = "document"
    return mode


def _heuristic_classify(image_bytes: bytes) -> str | None:
    """Return a confident classification, or None if ambiguous.

    Signals:
      - Saturation: documents are low-saturation (printed B&W or near-B&W);
        photos / whiteboards / drawings are higher saturation.
      - Edge density (after Canny): text-heavy pages have 5-15% edge pixels
        clustered in regular horizontal bands; photos have sparser, more
        isotropic edges.
      - Edge orientation entropy: text lines produce strong horizontal-
        edge dominance; photos are isotropic.

    Decision rules (tuned conservatively — when in doubt, return None
    and let the LLM decide):
      - low saturation + horizontal-edge dominance + 4-20% edge density
        → "document"
      - high saturation + isotropic edges + low edge density
        → "photo"
      - everything else → None (LLM fallback)
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    try:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None

        # Resize for speed — these statistics are scale-invariant.
        h, w = img.shape[:2]
        if max(h, w) > 800:
            ratio = 800 / max(h, w)
            img = cv2.resize(img, (int(w * ratio), int(h * ratio)))

        # ── Saturation (HSV) ────────────────────────────────────────
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        sat = hsv[:, :, 1]
        # Pct of pixels with sat < 40 (1/255 scale) → "almost grayscale"
        low_sat_pct = float((sat < 40).sum()) / sat.size

        # ── Edge density ────────────────────────────────────────────
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_density = float(edges.sum()) / 255.0 / edges.size

        # ── Edge orientation (horizontal vs vertical Sobel) ─────────
        sx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        sy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        h_strength = float(np.abs(sy).sum())  # horizontal lines = strong dy
        v_strength = float(np.abs(sx).sum())  # vertical lines = strong dx
        # Ratio > 1 means horizontal-edge dominant (text lines)
        h_dominance = h_strength / max(v_strength, 1.0)

        # ── Decision rules ──────────────────────────────────────────

        # Document: mostly grayscale, horizontal edges dominate, moderate
        # edge density. The 0.04-0.20 range catches both prose-only pages
        # (low end) and dense academic pages with figures/tables (high end).
        if (
            low_sat_pct > 0.85
            and h_dominance > 1.15
            and 0.04 <= edge_density <= 0.20
        ):
            return "document"

        # Photo: highly saturated, no horizontal-edge dominance, low-to-
        # moderate edge density. The "no h-dominance" check protects against
        # photos of paper documents which would otherwise look like the
        # document case.
        if (
            low_sat_pct < 0.55
            and 0.85 <= h_dominance <= 1.20
            and edge_density < 0.10
        ):
            return "photo"

        # Everything else (math, whiteboard, drawing, ambiguous documents)
        # falls through to the LLM. We deliberately do not try to heuristic
        # math/whiteboard/drawing — they're rare and the LLM gets them right.
        return None
    except Exception as e:
        logger.debug(f"[classify] heuristic failed: {e}")
        return None


def explain_signals(image_bytes: bytes) -> dict:
    """Diagnostic helper: return the raw heuristic signals for an image.

    Useful for tuning thresholds when the classifier misroutes a page.
    Not used in production paths.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {"error": "opencv unavailable"}

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"error": "decode failed"}

    h, w = img.shape[:2]
    if max(h, w) > 800:
        ratio = 800 / max(h, w)
        img = cv2.resize(img, (int(w * ratio), int(h * ratio)))

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    sx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)

    return {
        "low_sat_pct": float((sat < 40).sum()) / sat.size,
        "edge_density": float(edges.sum()) / 255.0 / edges.size,
        "h_dominance": float(np.abs(sy).sum()) / max(float(np.abs(sx).sum()), 1.0),
        "decision": _heuristic_classify(image_bytes),
    }
