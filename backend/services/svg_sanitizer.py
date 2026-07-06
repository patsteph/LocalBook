"""SVG sanitizer (2026-07-01) — strips XSS vectors from model-authored inline SVG.

Cross-medium visuals: quizzes/flashcards embed LLM-authored inline `<svg>` for
`visual_diagram` questions. That SVG is rendered with `dangerouslySetInnerHTML`
(flashcards / quiz panel) and injected raw into the interactive-quiz iframe, so
it MUST be sanitized server-side. This is the canonical pass; the frontend adds a
DOMPurify layer as defense in depth (see `src/lib/sanitizeSvg.ts`).

Approach mirrors `correspondent_processor.sanitize_html_for_display` (BeautifulSoup
+ style-property regexes) but with an SVG-specific allowlist and threat model:
drop script/foreignObject/image/external-ref vectors, strip `on*` handlers and
non-local `href`s. Never raises — returns "" when nothing safe survives so callers
render "no visual" rather than leaking raw markup.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Allowed SVG element tags (compared lowercased). Anything else is unwrapped
# (children/text kept, wrapper dropped).
_ALLOWED_TAGS = {
    "image",  # data:image/* href only — enforced in _scrub_attrs (Klein hero rasters)
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "text", "tspan", "textpath", "defs", "marker", "lineargradient",
    "radialgradient", "stop", "clippath", "use", "symbol", "pattern", "mask",
    "title", "desc", "style", "filter", "fegaussianblur", "feoffset",
    "femerge", "femergenode", "feflood", "fecomposite", "feblend",
    "fecolormatrix", "fedropshadow",
}
# Hard XSS / exfiltration vectors — decompose the whole subtree.
# NOTE (2026-07-06): "image" is NOT dropped — Klein full-bleed/hero visuals embed
# their raster as <image href="data:image/png;base64,...">. _scrub_attrs allows
# image hrefs ONLY when they are data:image/* URIs (no external fetch, no JS).
_DROP_TAGS = {
    "script", "foreignobject", "a", "animate", "animatetransform",
    "animatemotion", "set", "iframe", "object", "embed", "handler", "audio",
    "video", "link", "meta", "base", "style-import",
}

# url() is allowed ONLY when it references a local anchor — url(#gradientId) —
# which SVG needs for fills/clips; any other url(...) is stripped.
_STYLE_URL_RE = re.compile(r"url\s*\(\s*(?!['\"]?#)[^)]*\)", re.IGNORECASE)
_STYLE_IMPORT_RE = re.compile(r"@import\b[^;]*;?", re.IGNORECASE)
_STYLE_EXPRESSION_RE = re.compile(r"expression\s*\([^)]*\)", re.IGNORECASE)
_JS_URI_RE = re.compile(r"(javascript:|vbscript:|data\s*:\s*text/html)", re.IGNORECASE)

# Cap raised 200KB→8MB (2026-07-06): Klein hero SVGs embed a ~1MB+ base64 PNG;
# the old cap TRUNCATED them mid-base64 → guaranteed-invalid SVG (the "Invalid
# SVG" canvas error). Oversize input is now REJECTED outright (returns ""),
# never truncated — truncation always corrupts.
_MAX_SVG_BYTES = 8_000_000
_DATA_IMAGE_RE = re.compile(r"^\s*data:image/(png|jpe?g|gif|webp);base64,", re.IGNORECASE)


def sanitize_svg(svg: str) -> str:
    """Return a sanitized copy of `svg`, or "" if nothing safe survives."""
    if not svg or "<svg" not in svg.lower():
        return ""
    if len(svg) > _MAX_SVG_BYTES:
        return ""  # reject, never truncate (truncation corrupts base64/markup)

    try:
        from bs4 import BeautifulSoup
    except Exception:
        # No parser → safest is to drop the SVG rather than emit it unchecked.
        logger.warning("[svg_sanitizer] BeautifulSoup unavailable; dropping SVG")
        return ""

    # "xml" (lxml) preserves SVG's case-sensitive camelCase tags (linearGradient,
    # clipPath, …); fall back to the stdlib html.parser if lxml is unavailable.
    soup = None
    for parser in ("xml", "html.parser"):
        try:
            soup = BeautifulSoup(svg, parser)
            break
        except Exception:
            continue
    if soup is None:
        return ""

    try:
        # 1. Drop dangerous subtrees outright.
        for tag in list(soup.find_all(True)):
            if (tag.name or "").lower() in _DROP_TAGS:
                tag.decompose()

        # 2. Enforce the allowlist + scrub attributes on survivors.
        for tag in list(soup.find_all(True)):
            name = (tag.name or "").lower()
            if name not in _ALLOWED_TAGS:
                tag.unwrap()  # keep children/text, drop the unknown wrapper
                continue
            _scrub_attrs(tag)
            if name == "style" and tag.string:
                cleaned = _STYLE_URL_RE.sub("", tag.string)
                cleaned = _STYLE_IMPORT_RE.sub("", cleaned)
                cleaned = _STYLE_EXPRESSION_RE.sub("", cleaned)
                tag.string.replace_with(cleaned)

        # 3. Require a surviving <svg> root.
        root = soup.find("svg")
        if root is None:
            return ""
        return str(root)
    except Exception as e:  # noqa: BLE001 — never propagate to the render path
        logger.warning(f"[svg_sanitizer] sanitize failed: {e}")
        return ""


def _scrub_attrs(tag) -> None:
    """Strip event handlers, non-local hrefs, and dangerous style/URI values."""
    for attr in list(tag.attrs.keys()):
        low = attr.lower()
        raw = tag.attrs.get(attr)
        val = " ".join(raw) if isinstance(raw, list) else str(raw or "")

        if low.startswith("on"):
            del tag.attrs[attr]
            continue
        if low in ("href", "xlink:href"):
            # Local anchors (#id) for <use>/gradients; and on <image> elements
            # ONLY, inline data:image/* rasters (Klein hero) — no external URLs.
            if val.lstrip().startswith("#"):
                continue
            if tag.name and tag.name.lower() == "image" and _DATA_IMAGE_RE.match(val):
                continue
            del tag.attrs[attr]
            continue
        if low == "style":
            cleaned = _STYLE_URL_RE.sub("", val)
            cleaned = _STYLE_IMPORT_RE.sub("", cleaned)
            cleaned = _STYLE_EXPRESSION_RE.sub("", cleaned)
            tag.attrs[attr] = cleaned
            continue
        if _JS_URI_RE.search(val):
            del tag.attrs[attr]
