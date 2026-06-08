"""visual_resolver — Phase 4 of v2-information-cortex.

Post-processes LLM-generated markdown for inline visualization fences,
resolving them before the doc reaches the user.

Two fence languages are handled:
  - ` ```lb-chart\\n{ChartConfig JSON}\\n``` ` → validated against
    `chart_spec.ChartConfig`. On success, rewritten as
    ` ```json-chart\\n{validated JSON}\\n``` ` (the frontend
    MarkdownArtifactRenderer dispatches this to `ChartArtifactRenderer`).
    On failure, replaced with `*chart unavailable*`.
  - ` ```lb-visual-hint\\n{description}\\n``` ` → resolved by calling
    `visual_composer.compose(hint)`. On success, rewritten as
    ` ```svg\\n{svg_markup}\\n``` `. On failure or non-SVG output,
    replaced with `*visual unavailable*`.

Failure is graceful: the prose around the fence always survives. A
malformed visual never blocks the doc.

Used by `backend/api/content.py` after the doc-generation LLM response
returns and before the response is sent to the frontend.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# ` ```<lang>\n<body>\n``` ` — non-greedy body, multi-line.
_FENCE_RE = re.compile(
    r"```(?P<lang>[a-zA-Z0-9_-]+)\n(?P<body>.*?)\n```",
    re.DOTALL,
)


async def resolve(markdown: str) -> str:
    """Resolve all `lb-chart` and `lb-visual-hint` fences in the markdown.

    Returns the rewritten markdown. Idempotent on text that contains no
    such fences. Failures are swallowed — a malformed fence becomes a
    short italic placeholder.
    """
    if not markdown or ("lb-chart" not in markdown and "lb-visual-hint" not in markdown):
        return markdown

    # Lazy import: visual_composer pulls in heavy modules (capability
    # detection, sub-LLM clients) we don't want at module load time.
    from services.chart_spec import ChartConfig

    async def _resolve_visual_hint(hint: str) -> Optional[str]:
        try:
            from services.visual_composer import VisualComposer
            composer = VisualComposer()
            composed = await composer.compose(content=hint, topic=hint)
            if composed and composed.success and composed.svg_markup:
                return composed.svg_markup
        except Exception as e:
            logger.debug(f"[visual_resolver] visual_composer failed for hint: {e}")
        return None

    # Walk all fences. We rebuild the string rather than re.sub-with-callback
    # because the resolver is async (re.sub doesn't await callbacks).
    out_parts: list[str] = []
    last_end = 0
    for match in _FENCE_RE.finditer(markdown):
        out_parts.append(markdown[last_end:match.start()])
        lang = match.group("lang")
        body = match.group("body").strip()

        if lang == "lb-chart":
            replacement = _resolve_chart(body, ChartConfig)
        elif lang == "lb-visual-hint":
            svg = await _resolve_visual_hint(body) if body else None
            replacement = (
                f"```svg\n{svg}\n```"
                if svg
                else "*visual unavailable*"
            )
        else:
            # Leave other fences untouched.
            replacement = match.group(0)

        out_parts.append(replacement)
        last_end = match.end()
    out_parts.append(markdown[last_end:])
    return "".join(out_parts)


def _resolve_chart(body: str, ChartConfig) -> str:  # noqa: N803 — class arg
    """Parse + Pydantic-validate a chart body. Returns rewritten fence
    or placeholder."""
    if not body:
        return "*chart unavailable*"
    try:
        data = json.loads(body)
        if not isinstance(data, dict) or not data.get("chart_type"):
            return "*chart unavailable*"
        cfg = ChartConfig(**data)
        # `json-chart` matches the language the frontend code-fence
        # handler in MarkdownArtifactRenderer.tsx looks for.
        return f"```json-chart\n{json.dumps(cfg.model_dump(exclude_none=True))}\n```"
    except Exception as e:
        logger.debug(f"[visual_resolver] chart parse/validation failed: {e}")
        return "*chart unavailable*"
