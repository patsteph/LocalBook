"""
Image Preprocessor — OCR-optimal preprocessing for scanned pages.

Pipeline (per `enhance_for_ocr`):
  1. Decode bytes via OpenCV (cv2)
  2. Detect document quad → perspective-correct (only when high confidence)
  3. Deskew via Hough lines on horizontal text edges (capped ±15°)
  4. CLAHE adaptive contrast on the L-channel of LAB color space
  5. Re-encode JPEG quality 92

All steps are failure-safe — if any operation fails, the original bytes
are returned. Preprocessing must NEVER kill a page.

Mode-aware: photographs and hand drawings skip the document-oriented
steps (perspective correction would warp a portrait, deskew would tilt
a landscape).
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# Modes that benefit from full document-style preprocessing.
# Photo and drawing skip everything — preprocessing hurts them.
_DOC_MODES = {"document", "math", "whiteboard"}

# Deskew tolerance — never rotate more than this many degrees.
# Beyond ±15° the image is almost certainly photographed at angle and
# requires perspective correction, not rotation.
_MAX_DESKEW_DEG = 15.0

# Blur threshold — Laplacian variance below this means the image is too
# blurry to OCR. Tuned empirically: a focused iPhone photo is ~250-1500,
# motion blur drops to ~20-80, completely out of focus drops below 30.
_BLUR_THRESHOLD = 80.0

# JPEG quality used by `_to_jpeg_bytes` and matched by capture.py upload
# normalization. Centralised here so both stages stay in sync.
_DEFAULT_JPEG_QUALITY = 92


def enhance_for_ocr(image_bytes: bytes, mode: str) -> bytes:
    """Apply OCR-optimal preprocessing for the given content mode.

    Returns processed JPEG bytes. On any failure, returns original bytes.

    Args:
        image_bytes: Raw image bytes (typically JPEG already, post-normalize).
        mode: Content type — one of document/math/whiteboard/drawing/photo.
    """
    if mode not in _DOC_MODES:
        return image_bytes

    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.warning("[preprocess] OpenCV not available; skipping enhancement")
        return image_bytes

    try:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            logger.warning("[preprocess] cv2.imdecode returned None — bad image bytes")
            return image_bytes

        # Try perspective correction. Only applied if a high-confidence
        # 4-corner page boundary is detected; otherwise no-op.
        try:
            quad = _detect_document_quad(img)
            if quad is not None:
                img = _perspective_correct(img, quad)
        except Exception as e:
            logger.debug(f"[preprocess] perspective step failed: {e}")

        try:
            img = _deskew(img)
        except Exception as e:
            logger.debug(f"[preprocess] deskew step failed: {e}")

        try:
            img = _clahe_contrast(img)
        except Exception as e:
            logger.debug(f"[preprocess] CLAHE step failed: {e}")

        return _to_jpeg_bytes(img, quality=_DEFAULT_JPEG_QUALITY)
    except Exception as e:
        logger.warning(f"[preprocess] enhance_for_ocr failed; using original: {e}")
        return image_bytes


def check_blur(image_bytes: bytes) -> Tuple[bool, float]:
    """Return (is_too_blurry, laplacian_variance).

    Threshold of ~80 catches catastrophically blurry images (motion blur,
    out-of-focus) while letting normal handheld iPhone photos through.

    On any error (e.g. corrupt bytes), returns (False, 0.0) so the
    pipeline continues — we don't want a blur check failure to block
    a perfectly readable page.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return (False, 0.0)

    try:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return (False, 0.0)
        score = float(cv2.Laplacian(img, cv2.CV_64F).var())
        return (score < _BLUR_THRESHOLD, score)
    except Exception as e:
        logger.debug(f"[preprocess] blur check failed: {e}")
        return (False, 0.0)


# ── Internal helpers ──────────────────────────────────────────────────────


def _detect_document_quad(img):
    """Detect the largest 4-corner quadrilateral in the image.

    Returns a (4, 2) numpy array of corner coordinates, or None if no
    high-confidence page boundary is found. We require:
      - Contour area >= 25% of the full image (no postage stamps)
      - Convex (proper page shape)
      - Approximated to exactly 4 vertices

    These guards prevent us from warping when the image is e.g. a
    photograph with no document in it.
    """
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    img_area = h * w
    min_area = img_area * 0.25

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # Sort by area descending; only consider the top few candidates.
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            break  # all subsequent are smaller (sorted)
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx.reshape(4, 2)

    return None


def _perspective_correct(img, quad):
    """Warp `img` so that `quad` becomes a rectangle filling the frame."""
    import cv2
    import numpy as np

    # Order corners as [top-left, top-right, bottom-right, bottom-left]
    s = quad.sum(axis=1)
    diff = np.diff(quad, axis=1)
    ordered = np.zeros((4, 2), dtype="float32")
    ordered[0] = quad[np.argmin(s)]   # top-left has smallest x+y
    ordered[2] = quad[np.argmax(s)]   # bottom-right has largest x+y
    ordered[1] = quad[np.argmin(diff)]  # top-right has smallest x-y diff
    ordered[3] = quad[np.argmax(diff)]  # bottom-left has largest x-y diff

    tl, tr, br, bl = ordered
    width_top = np.linalg.norm(tr - tl)
    width_bot = np.linalg.norm(br - bl)
    max_w = int(max(width_top, width_bot))
    height_l = np.linalg.norm(bl - tl)
    height_r = np.linalg.norm(br - tr)
    max_h = int(max(height_l, height_r))

    if max_w < 50 or max_h < 50:
        # Sanity: degenerate quad. Skip warp.
        return img

    dst = np.array([
        [0, 0],
        [max_w - 1, 0],
        [max_w - 1, max_h - 1],
        [0, max_h - 1],
    ], dtype="float32")

    M = cv2.getPerspectiveTransform(ordered, dst)
    return cv2.warpPerspective(img, M, (max_w, max_h))


def _deskew(img):
    """Rotate image so dominant text lines become horizontal.

    Caps correction at ±15°; anything larger is almost certainly a
    misdetection or a photo taken at angle (which perspective correction
    should have handled). Capping prevents us from making a marginally
    tilted document look catastrophically rotated.
    """
    import cv2
    import numpy as np

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)

    # HoughLinesP returns line segments. We measure each segment's angle
    # off horizontal; the median is our deskew angle.
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=100,
        minLineLength=img.shape[1] // 4,
        maxLineGap=20,
    )
    if lines is None or len(lines) == 0:
        return img

    angles = []
    for x1, y1, x2, y2 in lines[:, 0]:
        if x2 == x1:
            continue
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        # Normalise to (-45, 45] — text lines that drift more than this
        # are vertical features (table column rules, page borders) and
        # would skew the median if included.
        if -45 < angle <= 45:
            angles.append(angle)

    if not angles:
        return img

    median_angle = float(np.median(angles))
    if abs(median_angle) < 0.5:
        return img  # already straight enough; rotation costs interpolation
    if abs(median_angle) > _MAX_DESKEW_DEG:
        return img  # almost certainly a misdetection — leave it

    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), median_angle, 1.0)
    return cv2.warpAffine(
        img, M, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _clahe_contrast(img):
    """Adaptive contrast enhancement on the L-channel of LAB.

    Uneven lighting (shadows from the user's hand, glare on glossy paper)
    is the #1 cause of OCR character dropouts. CLAHE evens out local
    contrast without globally darkening or brightening the page.
    """
    import cv2

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l)
    merged = cv2.merge([l_enhanced, a, b])
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def _to_jpeg_bytes(img, quality: int = _DEFAULT_JPEG_QUALITY) -> bytes:
    """Encode an OpenCV BGR image as JPEG bytes."""
    import cv2

    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("cv2.imencode JPEG failed")
    return buf.tobytes()
