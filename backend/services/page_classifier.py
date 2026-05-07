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

    Strategy (revised after empirical misclassification of book pages
    as 'photo'):
      1. Heuristic returns "document", "photo", or None.
      2. Photo classification requires ALL strong photo signals — book
         pages with imperfect lighting / off-axis capture no longer leak
         into 'photo'.
      3. Ambiguous cases default to "document" — the safe fallback.
         A misclassified document still produces readable text;
         a misclassified photo wastes time and produces useless prose.
      4. The LLM fallback is reserved for math/whiteboard/drawing
         distinctions, which heuristics can't reliably make. We only
         call it when explicitly hinted (currently never from the
         per-page QR flow).
    """
    decision, signals = _heuristic_classify(image_bytes)
    logger.info(f"[classify] signals={signals} → {decision}")

    if decision is not None:
        return decision

    # Ambiguous — bias to document. Most captures from a phone are
    # documents; the cost of a wrong "document" classification (text
    # transcription) is far lower than a wrong "photo" (scene description).
    logger.info("[classify] ambiguous → defaulting to document")
    return "document"


def _heuristic_classify(image_bytes: bytes) -> Tuple[str | None, dict]:
    """Return (decision, signals).

    decision is one of "document", "photo", or None (ambiguous).

    Signals returned for diagnostic logging — `low_sat_pct`,
    `edge_density`, `h_dominance`, plus `mean_sat` (mean saturation).

    Threshold philosophy (revised):
      - "document" is the lenient, default-friendly path. Cream paper,
        slightly warm lighting, mild perspective skew, and even a chapter
        title page with sparse text should still classify as document.
      - "photo" is the strict path. Requires ALL of: high mean
        saturation, near-isotropic edges, low edge density. A book page
        photographed in lamp light might have elevated low_sat_pct
        but will still fail the mean-saturation check (since text
        occupies a large fraction of pixels and is grayscale).
      - Ambiguous cases return None and the caller defaults to document.
    """
    signals: dict = {}
    try:
        import cv2
        import numpy as np
    except ImportError:
        return (None, {"error": "opencv unavailable"})

    try:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return (None, {"error": "decode failed"})

        # Resize for speed — these statistics are scale-invariant.
        h, w = img.shape[:2]
        if max(h, w) > 800:
            ratio = 800 / max(h, w)
            img = cv2.resize(img, (int(w * ratio), int(h * ratio)))

        # ── Saturation (HSV) ────────────────────────────────────────
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        sat = hsv[:, :, 1]
        low_sat_pct = float((sat < 40).sum()) / sat.size
        mean_sat = float(sat.mean())  # 0-255

        # ── Edge density ────────────────────────────────────────────
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_density = float(edges.sum()) / 255.0 / edges.size

        # ── Edge orientation (horizontal vs vertical Sobel) ─────────
        sx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        sy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        h_strength = float(np.abs(sy).sum())
        v_strength = float(np.abs(sx).sum())
        h_dominance = h_strength / max(v_strength, 1.0)

        signals = {
            "low_sat_pct": round(low_sat_pct, 3),
            "mean_sat": round(mean_sat, 1),
            "edge_density": round(edge_density, 4),
            "h_dominance": round(h_dominance, 3),
        }

        # ── Document detection (lenient) ────────────────────────────
        # Any of these stronger signals → document:
        #   1. Highly grayscale (printed page) AND has any horizontal
        #      edge bias (text lines) AND has measurable edge density
        #   2. Even more relaxed: mostly grayscale + lots of horizontal
        #      edges, regardless of edge density (catches sparse pages
        #      like chapter titles)
        is_grayscale = low_sat_pct > 0.65 or mean_sat < 30
        has_text_lines = h_dominance > 1.10
        has_edges = edge_density >= 0.02

        if is_grayscale and has_text_lines and has_edges:
            return ("document", signals)

        # Even with some color cast (warm lamp, cream paper), strong
        # text-line dominance is a confident document signal:
        if has_text_lines and h_dominance > 1.30 and has_edges:
            return ("document", signals)

        # ── Photo detection (strict) ────────────────────────────────
        # All four must hold. The mean_sat check is the main guard
        # against book-pages-in-warm-light being misread as photos:
        # text-heavy pages have low mean_sat even when low_sat_pct dips,
        # because the text pixels themselves are dark gray.
        if (
            mean_sat > 50            # genuinely colorful
            and low_sat_pct < 0.40   # most pixels have real saturation
            and 0.90 <= h_dominance <= 1.15  # truly isotropic, no text
            and edge_density < 0.06  # photos are smoother than pages
        ):
            return ("photo", signals)

        # Ambiguous — caller defaults to document.
        return (None, signals)
    except Exception as e:
        logger.debug(f"[classify] heuristic failed: {e}")
        return (None, {"error": str(e)})


def explain_signals(image_bytes: bytes) -> dict:
    """Diagnostic helper: return the raw heuristic signals + decision.

    Useful for tuning thresholds when the classifier misroutes a page.
    Not used in production paths.
    """
    decision, signals = _heuristic_classify(image_bytes)
    return {**signals, "decision": decision}
