"""doc_charts — build Studio document charts by RUNNING PYTHON instead of asking for JSON.

P2 of the Python hard-compute tier (`services/py_compute.py`). First caller of the tier.

**The problem this addresses.** `api/content.py::_inject_doc_visuals` asks the model to emit finished
`ChartConfig` JSON — every plotted number is whatever the model typed. It is the only place in the
app where displayed figures are model-authored rather than derived (see
`READFIRST/planning/python-tier-dashboard-workflows.md` §2); everything else already computes in
Python. Downstream repair (`_make_plottable`) can fix a value's *shape* (`'42%'` → `42.0`) but has no
way to know whether the number is right.

**What routing through Python actually buys — and what it does not.** The sources here are prose
(retrieved chunks + the generated doc), not a queryable table, so the model must still *read* the
figures out of the text; this does not make extraction infallible. What changes is everything after
extraction: totals, shares, percentages, growth rates, ranking and sorting become **executed code
rather than mental arithmetic**, and the code is captured in the artifact's metadata, so a wrong
chart can be traced to a specific line instead of being unfalsifiable. Arithmetic error is a large,
silent slice of chart wrongness, and this removes it.

(Notebooks that *do* have tabular data can pass `data_files` through to the sandbox for genuinely
queried numbers — the same code path, no changes needed here.)

Off by default (`settings.py_compute_doc_charts_enabled`). The caller falls back to the existing
LLM-JSON path whenever this returns no fences, so the flag is safe to flip either way.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Keys stay LOCKED to label/value. Learned 2026-07-06 (see `_inject_doc_visuals`): free-form key
# choices failed plottability ~3/4 of the time in built-app testing. Python removes the arithmetic
# freedom, not the schema freedom — keep the schema pinned.
_SYSTEM = "You write short, correct Python. You output ONLY code — no prose, no markdown fences."

_INSTRUCTIONS = """\
Write PYTHON that builds {n} data visualization(s) for the document below.

Do NOT write the final numbers by hand if they can be derived. Put the figures you read from the
material into variables, then let Python compute every total, share, percentage, difference or
growth rate. Sort with Python. The point is that the arithmetic is executed, not guessed.

Call this helper once per chart:

    emit_chart(chart_type, data, series, title=..., x_key='label')

with EXACTLY this shape:

    figures = [("Alpha", 120.0), ("Beta", 80.0)]      # values read from the material
    total = sum(v for _, v in figures)                 # derive, don't hand-type
    rows = [{{"label": k, "value": round(v / total * 100, 1)}} for k, v in figures]
    emit_chart("bar", rows, [{{"key": "value", "label": "Share of total (%)"}}],
               title="Share by segment", x_key="label")

HARD RULES:
- chart_type is one of: bar, line, pie.
- every row is exactly {{"label": <string>, "value": <plain number>}} — no %, $, or units in the
  value; put units in the series label.
- 3-6 rows per chart. Use ONLY figures supported by the material — if the material has no numbers
  worth plotting, emit nothing at all rather than inventing any.
- add a short `#` comment on each figure saying where it came from.
- no imports, no file or network access, no input(). Just compute and call emit_chart.
"""


def _strip_code_fence(text: str) -> str:
    """Models wrap code in ```python fences despite instructions — unwrap the first block."""
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text or "", re.S)
    return (m.group(1) if m else (text or "")).strip()


async def compute_chart_fences(
    *,
    content: str,
    topic_focus: str,
    source_context: str,
    chart_brief: str = "",
    temperature: float = 0.3,
    n_charts: int = 2,
    data_files: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Generate Python → run it sandboxed → return ready-to-insert ```lb-chart fences.

    Returns `[]` on ANY failure so the caller can fall back to the LLM-JSON path. Never raises."""
    try:
        from config import settings
        from services.llm_runtime import llm_runtime
        from services import py_compute

        instructions = (f"{chart_brief}\n\n" if chart_brief else "") + _INSTRUCTIONS.format(n=n_charts)
        prompt = (
            f"{instructions}\n\n"
            f"DOCUMENT TOPIC: {topic_focus[:500]}\n\n"
            f"DOCUMENT:\n{content[:6000]}\n\n"
            f"SOURCE MATERIAL:\n{source_context[:4000]}\n\n"
            "Python:"
        )

        # llm_runtime routes to in-process MLX when enabled and falls back to Ollama — the
        # dual-engine seam, per the MLX-first hedge. No new Ollama coupling.
        result = await llm_runtime.generate(
            prompt=prompt,
            system=_SYSTEM,
            model=settings.ollama_model,
            temperature=max(0.1, temperature - 0.2),
            num_predict=800,
            timeout=120.0,
        )
        code = _strip_code_fence(result.get("response", ""))
        if not code:
            logger.info("[doc-charts] model returned no code")
            return []

        run = await py_compute.run_async(
            code,
            data_files=data_files or {},
            limits=py_compute.SandboxLimits(timeout_s=15.0, cpu_s=10, mem_mb=512),
        )
        if not run.get("ok"):
            logger.info(f"[doc-charts] sandbox run failed: {run.get('error')}")
            return []

        fences: List[str] = []
        for art in run.get("artifacts") or []:
            if art.get("type") != "json:chart":
                continue
            cfg: Any = art.get("payload") or {}
            # Already ChartConfig-validated in py_compute's parent; visual_resolver re-validates
            # the fence downstream, so a malformed one degrades to a placeholder, never a crash.
            if not cfg.get("data") or not cfg.get("series"):
                continue
            fences.append("```lb-chart\n" + json.dumps(cfg, ensure_ascii=False) + "\n```")
            if len(fences) >= n_charts:
                break

        if fences:
            logger.info(
                f"[doc-charts] computed {len(fences)} chart(s) in the sandbox "
                f"({len(code)} chars of Python)"
            )
        else:
            logger.info("[doc-charts] sandbox emitted no usable chart")
        return fences
    except Exception as e:  # never break document generation
        logger.debug(f"[doc-charts] skipped: {type(e).__name__}: {e}")
        return []
