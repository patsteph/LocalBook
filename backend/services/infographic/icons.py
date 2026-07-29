"""Lucide line-art icons as inline SVG strings for infographic skeletons.

Lucide is already a frontend dep (`lucide-react`, ISC) but that is React-only.
The L2 skeletons are sanitized HTML strings rendered in a Shadow DOM / exported
via headless Chromium, so we need raw inline `<svg>` markup here. These paths
are copied from Lucide's 24x24 icon set (stroke-based, `fill=none`).

`render_icon(name, cls)` returns a wrapped `<span class="ib-icon …"><svg…></span>`.
Unknown names fail open to a neutral dot so a bad LLM icon pick never breaks
the render (HARD RULE §2.5).
"""
from __future__ import annotations

# name -> inner SVG body (paths only; wrapper added by render_icon)
ICON_PATHS: dict[str, str] = {
    "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    "arrow-down": '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    "book-open": (
        '<path d="M12 7v14"/>'
        '<path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>'
    ),
    "database": (
        '<ellipse cx="12" cy="5" rx="9" ry="3"/>'
        '<path d="M3 5V19A9 3 0 0 0 21 19V5"/>'
        '<path d="M3 12A9 3 0 0 0 21 12"/>'
    ),
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "cpu": (
        '<rect x="4" y="4" width="16" height="16" rx="2"/>'
        '<rect x="9" y="9" width="6" height="6"/>'
        '<path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/>'
        '<path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>'
    ),
    "file-text": (
        '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
        '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>'
    ),
    "globe": (
        '<circle cx="12" cy="12" r="10"/>'
        '<path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/>'
        '<path d="M2 12h20"/>'
    ),
    "rocket": (
        '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/>'
        '<path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/>'
        '<path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/>'
        '<path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>'
    ),
    "zap": '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
    "refresh-cw": (
        '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
        '<path d="M21 3v5h-5"/>'
        '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>'
        '<path d="M8 16H3v5"/>'
    ),
    "gem": (
        '<path d="M6 3h12l4 6-10 13L2 9Z"/>'
        '<path d="M11 3 8 9l4 13 4-13-3-6"/>'
        '<path d="M2 9h20"/>'
    ),
    "network": (
        '<rect x="16" y="16" width="6" height="6" rx="1"/>'
        '<rect x="2" y="16" width="6" height="6" rx="1"/>'
        '<rect x="9" y="2" width="6" height="6" rx="1"/>'
        '<path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/>'
        '<path d="M12 12V8"/>'
    ),
    "table": (
        '<path d="M12 3v18"/><rect width="18" height="18" x="3" y="3" rx="2"/>'
        '<path d="M3 9h18"/><path d="M3 15h18"/>'
    ),
    "braces": (
        '<path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1"/>'
        '<path d="M16 21h1a2 2 0 0 0 2-2v-5a2 2 0 0 1 2-2 2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"/>'
    ),
    "layers": (
        '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z"/>'
        '<path d="m2 12 9.58 4.36a2 2 0 0 0 1.66 0L22 12"/>'
        '<path d="m2 17 9.58 4.36a2 2 0 0 0 1.66 0L22 17"/>'
    ),
    "sparkles": (
        '<path d="M9.94 14.34A2 2 0 0 0 8.66 13L4.6 11.65a.5.5 0 0 1 0-.95L8.66 9.3A2 2 0 0 0 9.94 8L11.3 3.94a.5.5 0 0 1 .95 0L13.6 8a2 2 0 0 0 1.28 1.28l4.06 1.36a.5.5 0 0 1 0 .95l-4.06 1.35A2 2 0 0 0 13.6 16l-1.36 4.06a.5.5 0 0 1-.95 0z"/>'
    ),
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "monitor": (
        '<rect width="20" height="14" x="2" y="3" rx="2"/>'
        '<path d="M8 21h8"/><path d="M12 17v4"/>'
    ),
}

# Neutral fail-open glyph.
_FALLBACK = '<circle cx="12" cy="12" r="3"/>'

# Keyword -> icon, so a builder can pick a sensible default from a slot label
# when the model does not (or picks an unknown name).
KEYWORD_ICON: dict[str, str] = {
    "knowledge": "book-open", "source": "book-open", "document": "file-text",
    "doc": "file-text", "file": "file-text",
    "embed": "sparkles", "vector": "database", "search": "search",
    "index": "network", "graph": "network", "compil": "cpu", "optim": "cpu",
    "serve": "rocket", "fast": "zap", "speed": "zap", "query": "search",
    "web": "globe", "internet": "globe", "artifact": "gem", "table": "table",
    "json": "braces", "loop": "refresh-cw", "iterate": "refresh-cw",
    "store": "database", "data": "database", "layer": "layers", "folder": "folder",
    "screen": "monitor", "app": "monitor",
}

_ALLOWED = sorted(ICON_PATHS.keys())


def allowed_icon_names() -> list[str]:
    """The allowlist a slot-fill prompt should offer the model."""
    return list(_ALLOWED)


def icon_for_label(label: str) -> str:
    """Deterministic keyword -> icon name (fail-open to 'sparkles')."""
    low = (label or "").lower()
    for kw, name in KEYWORD_ICON.items():
        if kw in low:
            return name
    return "sparkles"


def render_icon(name: str, cls: str = "ib-icon") -> str:
    """Return a wrapped inline SVG for `name`. Unknown -> neutral dot."""
    body = ICON_PATHS.get((name or "").strip().lower(), _FALLBACK)
    return (
        f'<span class="{cls}"><svg viewBox="0 0 24 24" '
        f'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">{body}</svg></span>'
    )
