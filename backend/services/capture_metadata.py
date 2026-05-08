"""
Capture metadata helpers — image hash + QR-code extraction for capture flow.

Adds two cross-cutting capabilities to every uploaded image:
  - **Dedup hash** (C2): SHA256 over the normalized image bytes. Stored in
    a JSON ledger so a re-uploaded receipt / page can be flagged as a
    duplicate without re-running OCR.
  - **QR / 2D-code detection** (C4): cv2.QRCodeDetector (bundled with
    OpenCV — no new dependency). Decoded URL/ISBN values are returned to
    the caller as suggested follow-ups (ingest URL, fetch book metadata,
    etc.).

Both functions are best-effort and failure-safe — every error returns a
neutral value (empty hash / no codes / no dedup record) so capture is
never blocked by a metadata pass.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Image hash + dedup ledger ────────────────────────────────────────────

def compute_image_hash(file_path: str) -> str:
    """SHA256 of the file's bytes. Returns '' on any read error."""
    try:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logger.debug(f"[capture-meta] hash failed for {file_path}: {e}")
        return ""


def _ledger_path() -> Path:
    """Resolve the capture-hash ledger location.

    Lives next to other LocalBook user data so it persists across app
    restarts but is per-user.
    """
    try:
        from config import settings
        return Path(settings.data_dir) / "capture_hashes.json"
    except Exception:
        # Fallback to a relative path; not ideal but never crashes capture.
        return Path("capture_hashes.json")


_LEDGER_LOCK = threading.Lock()


def _read_ledger() -> Dict[str, Dict]:
    path = _ledger_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.warning(f"[capture-meta] ledger read failed: {e}")
        return {}


def _write_ledger(data: Dict[str, Dict]) -> None:
    path = _ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning(f"[capture-meta] ledger write failed: {e}")


def check_dedup(image_hash: str) -> Optional[Dict]:
    """Return the prior capture record for `image_hash` if one exists.

    Record shape: {first_seen_at, first_seen_path, note_id (optional)}.
    Returns None if no match — caller should treat as a new capture.
    """
    if not image_hash:
        return None
    with _LEDGER_LOCK:
        ledger = _read_ledger()
    return ledger.get(image_hash)


def record_capture(
    image_hash: str,
    file_path: str,
    note_id: Optional[str] = None,
) -> None:
    """Insert a new entry into the dedup ledger. Idempotent — does NOT
    overwrite an existing entry's first-seen timestamp / path.
    """
    if not image_hash:
        return
    with _LEDGER_LOCK:
        ledger = _read_ledger()
        if image_hash in ledger:
            # Update note_id if it was missing originally and we have one now.
            if note_id and not ledger[image_hash].get("note_id"):
                ledger[image_hash]["note_id"] = note_id
                _write_ledger(ledger)
            return
        ledger[image_hash] = {
            "first_seen_at": datetime.utcnow().isoformat() + "Z",
            "first_seen_path": file_path,
            "note_id": note_id,
        }
        _write_ledger(ledger)


# ── QR / 2D-code detection ───────────────────────────────────────────────

def detect_qr_codes(file_path: str) -> List[Dict[str, str]]:
    """Run cv2.QRCodeDetector on the image and return any decoded values.

    Returns a list of dicts: [{value: <str>, kind: <"url"|"isbn"|"text">}].
    Empty list if no codes are found, OpenCV is unavailable, or detection
    fails. The kind hint helps callers offer follow-up actions.
    """
    try:
        import cv2  # type: ignore
    except ImportError:
        return []
    try:
        img = cv2.imread(file_path)
        if img is None:
            return []
        detector = cv2.QRCodeDetector()
        # detectAndDecodeMulti returns a tuple; the first element is True if
        # any code was found and the second is a list of decoded strings.
        try:
            ok, decoded_info, _points, _ = detector.detectAndDecodeMulti(img)
        except Exception:
            # Older OpenCV: fall back to single-code API.
            decoded, _points, _ = detector.detectAndDecode(img)
            ok, decoded_info = bool(decoded), [decoded] if decoded else []
        if not ok or not decoded_info:
            return []
        results: List[Dict[str, str]] = []
        for value in decoded_info:
            if not value:
                continue
            results.append({"value": value, "kind": _classify_qr_value(value)})
        return results
    except Exception as e:
        logger.debug(f"[capture-meta] QR detection failed: {e}")
        return []


def _classify_qr_value(value: str) -> str:
    """Best-effort classification of a decoded QR / barcode value."""
    v = value.strip()
    lower = v.lower()
    if lower.startswith("http://") or lower.startswith("https://"):
        return "url"
    # ISBN: 10 or 13 digits with optional hyphens.
    digits = "".join(c for c in v if c.isdigit())
    if len(digits) in (10, 13) and digits.isdigit():
        return "isbn"
    if lower.startswith("mailto:"):
        return "email"
    if lower.startswith("tel:"):
        return "phone"
    if lower.startswith("wifi:"):
        return "wifi"
    if lower.startswith("begin:vcard"):
        return "vcard"
    return "text"
