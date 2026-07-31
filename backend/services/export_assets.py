"""Shared assets for the unified export pipeline (Phase 5).

Holds:
- `TAILWIND_SUBSET_CSS` — Python port of `src/components/artifact/renderers/
  htmlArtifactTailwindSubset.ts`. Scoped under `.lb-html-artifact` so any
  HTML embedded in an exported page can use the same utility classes the
  frontend uses without needing a full Tailwind build.
- CDN URLs pinned to specific versions so the export pipeline is
  reproducible across environments. These load inside Playwright (a
  server-side headless Chromium), not the Tauri WebView — Tauri CSP does
  not apply.

Phase 5 of v2-information-cortex.
"""

# Pinned CDN URLs. Fail-open fallback only — the export pipeline inlines the
# vendored copies below (offline guarantee). Mermaid is pinned to the 11.x line
# to match the frontend app (package.json `mermaid: ^11.4.0`, resolved 11.16.0)
# so `look:'handDrawn'` and other 11.x features render identically on export.
MARKED_CDN = "https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js"
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11.16.0/dist/mermaid.min.js"
CHARTJS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"

# ---------------------------------------------------------------------------
# Offline asset bundling — LocalBook's offline guarantee means the export
# pipeline must not reach the network. marked / mermaid / Chart.js are vendored
# into `backend/static/vendor/` (bundled by PyInstaller via
# `--add-data static:static`) and inlined into the export page as a
# `<script>…</script>` instead of a `<script src="{CDN}">`. Mirrors the
# frozen-aware resolution in `api/health_portal.get_static_dir`. Falls open to
# the pinned CDN tag only if the vendored file is genuinely missing (dev tree
# without the asset), logging a warning so the offline violation is visible
# rather than silent.
# ---------------------------------------------------------------------------
import logging as _logging
import sys as _sys
from pathlib import Path as _Path

_logger = _logging.getLogger(__name__)
_CHARTJS_VENDOR_REL = ("static", "vendor", "chart.umd.min.js")
_MARKED_VENDOR_REL = ("static", "vendor", "marked.min.js")
_MERMAID_VENDOR_REL = ("static", "vendor", "mermaid.min.js")
_chartjs_src_cache: str | None = None
_marked_src_cache: str | None = None
_mermaid_src_cache: str | None = None


def _vendor_base() -> _Path:
    """Static-asset base, handling PyInstaller-frozen bundles (mirrors
    `api.health_portal.get_static_dir`)."""
    if getattr(_sys, "frozen", False):
        return _Path(_sys._MEIPASS)  # type: ignore[attr-defined]
    # export_assets.py lives in backend/services/ → backend/ is two up.
    return _Path(__file__).resolve().parent.parent


def _chartjs_source() -> str | None:
    """Return the vendored Chart.js UMD source (cached), or None if missing."""
    global _chartjs_src_cache
    if _chartjs_src_cache is not None:
        return _chartjs_src_cache or None
    path = _vendor_base().joinpath(*_CHARTJS_VENDOR_REL)
    try:
        _chartjs_src_cache = path.read_text(encoding="utf-8")
    except Exception as e:
        _logger.warning(
            "[export_assets] vendored Chart.js not found at %s (%s) — export "
            "will fall back to the CDN, breaking the offline guarantee.", path, e
        )
        _chartjs_src_cache = ""
    return _chartjs_src_cache or None


def _marked_source() -> str | None:
    """Return the vendored marked source (cached), or None if missing."""
    global _marked_src_cache
    if _marked_src_cache is not None:
        return _marked_src_cache or None
    path = _vendor_base().joinpath(*_MARKED_VENDOR_REL)
    try:
        _marked_src_cache = path.read_text(encoding="utf-8")
    except Exception as e:
        _logger.warning(
            "[export_assets] vendored marked not found at %s (%s) — export "
            "will fall back to the CDN, breaking the offline guarantee.", path, e
        )
        _marked_src_cache = ""
    return _marked_src_cache or None


def _mermaid_source() -> str | None:
    """Return the vendored mermaid source (cached), or None if missing."""
    global _mermaid_src_cache
    if _mermaid_src_cache is not None:
        return _mermaid_src_cache or None
    path = _vendor_base().joinpath(*_MERMAID_VENDOR_REL)
    try:
        _mermaid_src_cache = path.read_text(encoding="utf-8")
    except Exception as e:
        _logger.warning(
            "[export_assets] vendored mermaid not found at %s (%s) — export "
            "will fall back to the CDN, breaking the offline guarantee.", path, e
        )
        _mermaid_src_cache = ""
    return _mermaid_src_cache or None


def chartjs_script_tag() -> str:
    """Offline-first Chart.js `<script>` for the export renderer.

    Inlines the vendored UMD source so Playwright needs no network. Only if the
    vendored file is absent does it degrade to the pinned CDN `<script src>`
    (fail-open — a networked chart beats no chart in a broken-bundle dev tree)."""
    src = _chartjs_source()
    if src:
        return f"<script>{src}</script>"
    return f'<script src="{CHARTJS_CDN}"></script>'


def marked_script_tag() -> str:
    """Offline-first marked `<script>` for the export renderer.

    Inlines the vendored source so Playwright needs no network. Degrades to the
    pinned CDN `<script src>` only if the vendored file is absent (fail-open)."""
    src = _marked_source()
    if src:
        return f"<script>{src}</script>"
    return f'<script src="{MARKED_CDN}"></script>'


def mermaid_script_tag() -> str:
    """Offline-first mermaid `<script>` for the export renderer.

    Inlines the vendored source so Playwright needs no network. Degrades to the
    pinned CDN `<script src>` only if the vendored file is absent (fail-open)."""
    src = _mermaid_source()
    if src:
        return f"<script>{src}</script>"
    return f'<script src="{MERMAID_CDN}"></script>'

# ---------------------------------------------------------------------------
# Tailwind subset — light theme only. Dark-mode is intentional drop for the
# export path: PDFs / PNGs are read in print or document viewers where dark
# mode behavior is unreliable. The frontend renderer keeps its own dark
# styles for canvas display.
# ---------------------------------------------------------------------------
TAILWIND_SUBSET_CSS = """
body { margin: 0; padding: 0; }
.lb-html-artifact {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  color: #111827;
  box-sizing: border-box;
}
.lb-html-artifact *,
.lb-html-artifact *::before,
.lb-html-artifact *::after { box-sizing: border-box; }
.lb-html-artifact h1,
.lb-html-artifact h2,
.lb-html-artifact h3,
.lb-html-artifact h4 { font-weight: 600; margin: 0 0 0.5rem 0; line-height: 1.25; }
.lb-html-artifact h1 { font-size: 1.5rem; }
.lb-html-artifact h2 { font-size: 1.25rem; }
.lb-html-artifact h3 { font-size: 1.125rem; }
.lb-html-artifact h4 { font-size: 1rem; }
.lb-html-artifact p { margin: 0 0 0.75rem 0; }
.lb-html-artifact ul,
.lb-html-artifact ol { margin: 0 0 0.75rem 0; padding-left: 1.25rem; }
.lb-html-artifact li { margin: 0.125rem 0; }
.lb-html-artifact hr { border: none; border-top: 1px solid #e5e7eb; margin: 1rem 0; }
.lb-html-artifact blockquote { margin: 0 0 0.75rem 0; padding-left: 0.75rem; border-left: 3px solid #d1d5db; color: #4b5563; }
.lb-html-artifact code { font-family: ui-monospace, SFMono-Regular, monospace; font-size: 0.875em; background: rgba(0,0,0,0.06); padding: 0.125rem 0.25rem; border-radius: 0.25rem; }
.lb-html-artifact pre { background: rgba(0,0,0,0.06); padding: 0.75rem; border-radius: 0.5rem; overflow-x: auto; margin: 0 0 0.75rem 0; }
.lb-html-artifact pre code { background: transparent; padding: 0; }
.lb-html-artifact table { width: 100%; border-collapse: collapse; margin: 0 0 0.75rem 0; }
.lb-html-artifact th,
.lb-html-artifact td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #e5e7eb; text-align: left; }
.lb-html-artifact th { font-weight: 600; background: rgba(0,0,0,0.03); }
.lb-html-artifact a { color: #2563eb; text-decoration: underline; }
.lb-html-artifact img { max-width: 100%; height: auto; }

/* Layout utilities */
.lb-html-artifact .flex { display: flex; }
.lb-html-artifact .grid { display: grid; }
.lb-html-artifact .block { display: block; }
.lb-html-artifact .hidden { display: none; }
.lb-html-artifact .flex-col { flex-direction: column; }
.lb-html-artifact .items-center { align-items: center; }
.lb-html-artifact .justify-between { justify-content: space-between; }
.lb-html-artifact .gap-1 { gap: 0.25rem; }
.lb-html-artifact .gap-2 { gap: 0.5rem; }
.lb-html-artifact .gap-3 { gap: 0.75rem; }
.lb-html-artifact .gap-4 { gap: 1rem; }
.lb-html-artifact .gap-6 { gap: 1.5rem; }
.lb-html-artifact .grid-cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.lb-html-artifact .grid-cols-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }

/* Spacing */
.lb-html-artifact .p-2 { padding: 0.5rem; }
.lb-html-artifact .p-3 { padding: 0.75rem; }
.lb-html-artifact .p-4 { padding: 1rem; }
.lb-html-artifact .p-6 { padding: 1.5rem; }
.lb-html-artifact .px-3 { padding-left: 0.75rem; padding-right: 0.75rem; }
.lb-html-artifact .py-1 { padding-top: 0.25rem; padding-bottom: 0.25rem; }
.lb-html-artifact .mt-2 { margin-top: 0.5rem; }
.lb-html-artifact .mt-4 { margin-top: 1rem; }
.lb-html-artifact .mb-2 { margin-bottom: 0.5rem; }
.lb-html-artifact .mb-4 { margin-bottom: 1rem; }
.lb-html-artifact .mx-auto { margin-left: auto; margin-right: auto; }

/* Typography utilities */
.lb-html-artifact .text-xs { font-size: 0.75rem; line-height: 1rem; }
.lb-html-artifact .text-sm { font-size: 0.875rem; line-height: 1.25rem; }
.lb-html-artifact .text-base { font-size: 1rem; line-height: 1.5rem; }
.lb-html-artifact .text-lg { font-size: 1.125rem; line-height: 1.75rem; }
.lb-html-artifact .font-medium { font-weight: 500; }
.lb-html-artifact .font-semibold { font-weight: 600; }
.lb-html-artifact .font-bold { font-weight: 700; }
.lb-html-artifact .uppercase { text-transform: uppercase; }
.lb-html-artifact .tracking-wide { letter-spacing: 0.025em; }
.lb-html-artifact .italic { font-style: italic; }
.lb-html-artifact .text-gray-400 { color: #9ca3af; }
.lb-html-artifact .text-gray-500 { color: #6b7280; }
.lb-html-artifact .text-gray-600 { color: #4b5563; }
.lb-html-artifact .text-gray-700 { color: #374151; }
.lb-html-artifact .text-gray-800 { color: #1f2937; }
.lb-html-artifact .text-gray-900 { color: #111827; }
.lb-html-artifact .text-blue-600 { color: #2563eb; }
.lb-html-artifact .text-emerald-700 { color: #047857; }
.lb-html-artifact .text-red-700 { color: #b91c1c; }

/* Chrome */
.lb-html-artifact .rounded-md { border-radius: 0.375rem; }
.lb-html-artifact .rounded-lg { border-radius: 0.5rem; }
.lb-html-artifact .rounded-xl { border-radius: 0.75rem; }
.lb-html-artifact .border { border-width: 1px; border-style: solid; border-color: #e5e7eb; }
.lb-html-artifact .border-gray-200 { border-color: #e5e7eb; }
.lb-html-artifact .border-blue-200 { border-color: #bfdbfe; }
.lb-html-artifact .border-purple-200 { border-color: #e9d5ff; }
.lb-html-artifact .bg-white { background-color: #ffffff; }
.lb-html-artifact .bg-gray-50 { background-color: #f9fafb; }
.lb-html-artifact .bg-blue-50 { background-color: #eff6ff; }
.lb-html-artifact .bg-purple-50 { background-color: #faf5ff; }
.lb-html-artifact .bg-emerald-50 { background-color: #ecfdf5; }
.lb-html-artifact .shadow-sm { box-shadow: 0 1px 2px 0 rgba(0,0,0,0.05); }
.lb-html-artifact .shadow { box-shadow: 0 1px 3px 0 rgba(0,0,0,0.1), 0 1px 2px 0 rgba(0,0,0,0.06); }

.lb-html-artifact .w-full { width: 100%; }
.lb-html-artifact .w-6 { width: 1.5rem; }
.lb-html-artifact .w-24 { width: 6rem; }
/* Fractional widths — added 2026-06-08 for inline consensus-cluster bar
   charts. Bucketed twelfths cover any pct → class mapping cleanly. */
.lb-html-artifact .w-1\\/12 { width: 8.333333%; }
.lb-html-artifact .w-2\\/12 { width: 16.666667%; }
.lb-html-artifact .w-3\\/12 { width: 25%; }
.lb-html-artifact .w-4\\/12 { width: 33.333333%; }
.lb-html-artifact .w-5\\/12 { width: 41.666667%; }
.lb-html-artifact .w-6\\/12 { width: 50%; }
.lb-html-artifact .w-7\\/12 { width: 58.333333%; }
.lb-html-artifact .w-8\\/12 { width: 66.666667%; }
.lb-html-artifact .w-9\\/12 { width: 75%; }
.lb-html-artifact .w-10\\/12 { width: 83.333333%; }
.lb-html-artifact .w-11\\/12 { width: 91.666667%; }
.lb-html-artifact .h-2 { height: 0.5rem; }
.lb-html-artifact .overflow-hidden { overflow: hidden; }
.lb-html-artifact .truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lb-html-artifact .flex { display: flex; }
.lb-html-artifact .flex-1 { flex: 1 1 0%; }
.lb-html-artifact .items-center { align-items: center; }
.lb-html-artifact .gap-2 { gap: 0.5rem; }
.lb-html-artifact .text-right { text-align: right; }
.lb-html-artifact .bg-blue-100 { background-color: #dbeafe; }
.lb-html-artifact .bg-blue-500 { background-color: #3b82f6; }
.lb-html-artifact .max-w-3xl { max-width: 48rem; }
.lb-html-artifact .max-w-2xl { max-width: 42rem; }
.lb-html-artifact .max-w-md { max-width: 28rem; }
"""
