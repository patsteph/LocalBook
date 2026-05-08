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

VALID_MODES = {
    # Auto-classifiable broad categories (in CLASSIFY_PROMPT).
    "document",
    "handwriting",
    "mixed",
    "math",
    "whiteboard",
    "drawing",
    "diagram",
    "photo",
    "receipt",
    "business_card",
    "code",
    "slide",
    # User-pick specialized modes (not in CLASSIFY_PROMPT but accepted as input).
    "recipe",
    "resume",
    "glossary",
    "title_page",
    "calendar",
    "form",
    "map",
    "index_page",
    "collage",
}


def _normalize_mode(raw: str) -> str:
    """Coerce an LLM classification response into a known mode.

    Tolerates extra whitespace, trailing punctuation, capitalization,
    and common synonyms ("invoice" → "receipt", "card" → "business_card",
    "presentation"/"deck" → "slide", "flowchart"/"mindmap" → "diagram").
    Returns "document" if the response can't be matched.
    """
    if not raw:
        return "document"
    cleaned = raw.strip().lower().rstrip('.!?,:;"\'')
    # Take the first whitespace-separated token to defend against the model
    # echoing the prompt list back.
    cleaned = cleaned.split()[0] if cleaned.split() else cleaned
    cleaned = cleaned.replace("-", "_")
    if cleaned in VALID_MODES:
        return cleaned
    synonyms = {
        "invoice": "receipt",
        "bill": "receipt",
        "card": "business_card",
        "businesscard": "business_card",
        "vcard": "business_card",
        "presentation": "slide",
        "deck": "slide",
        "ppt": "slide",
        "powerpoint": "slide",
        "flowchart": "diagram",
        "mindmap": "diagram",
        "mind_map": "diagram",
        "graph": "diagram",
        "chart": "diagram",
        "code_screen": "code",
        "terminal": "code",
        "source_code": "code",
        "screenshot": "code",
        "handwritten": "handwriting",
        "notes": "handwriting",
        "page": "document",
        "text": "document",
    }
    return synonyms.get(cleaned, "document")


async def classify_page(
    image_bytes: bytes,
    llm_fallback: Callable[[bytes], Awaitable[str]],
) -> str:
    """Return one of the 12 known modes (see VALID_MODES).

    Strategy:
      1. Heuristic returns "document", "photo", or None.
         These two are cheap to detect from image stats alone (~50ms).
      2. Photo classification requires ALL strong photo signals — book
         pages with imperfect lighting / off-axis capture no longer leak
         into 'photo'.
      3. For the other 10 modes (handwriting, mixed, math, whiteboard,
         drawing, diagram, receipt, business_card, code, slide), the
         LLM fallback is the source of truth — the visual cues are too
         subtle for cheap heuristics. Misclassification is recoverable
         because every prompt is tolerant of a wider input than its
         strict semantics suggest.
      4. Ambiguous heuristic + missing LLM → defaults to "document".
         A misclassified document still produces readable text;
         a misclassified photo wastes time on scene description.
    """
    decision, signals = _heuristic_classify(image_bytes)
    logger.info(f"[classify] signals={signals} → {decision}")

    if decision is not None:
        return decision

    # Ambiguous heuristic — call the LLM if available, otherwise document.
    if llm_fallback is not None:
        try:
            raw = await llm_fallback(image_bytes)
            mode = _normalize_mode(raw)
            logger.info(f"[classify] LLM said {raw!r} → {mode}")
            return mode
        except Exception as e:
            logger.warning(f"[classify] LLM fallback failed: {e}; defaulting to document")

    logger.info("[classify] ambiguous + no LLM → defaulting to document")
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
