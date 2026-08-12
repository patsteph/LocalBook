"""ChartConfig Pydantic schema — mirrors frontend `ChartRenderer` shape.

Phase 4 of v2-information-cortex. Used by:
  - `structured_llm.generate_chart()` to validate LLM JSON output before
    embedding into a doc as a `json-chart` code fence
  - `visual_resolver` to validate `lb-chart` fences emitted inline by the
    doc generator before they reach the frontend

Schema parity reference: `src/components/shared/ChartRenderer.tsx:25-46`.
Any field added there must be added here too (or marked optional with a
sensible default so backward-compat holds).
"""
from typing import Any, Dict, List, Literal, Optional, Sequence
from pydantic import BaseModel, Field


class ChartSeries(BaseModel):
    """A single data series within a chart."""
    key: str = Field(description="Data field name this series reads from")
    label: Optional[str] = Field(default=None, description="Display label")
    color: Optional[str] = Field(default=None, description="Hex color override")
    type: Optional[Literal["line", "bar", "area"]] = Field(
        default=None, description="Series type — only meaningful for 'composed' charts"
    )
    strokeDasharray: Optional[str] = Field(default=None, description="e.g. '5 5' for dashed lines")
    yAxisId: Optional[Literal["left", "right"]] = Field(default=None)


class ChartAxis(BaseModel):
    """Axis configuration."""
    label: Optional[str] = None
    key: Optional[str] = None
    domain: Optional[List[Any]] = Field(default=None, description="[min, max] — two-element list")


class ChartConfig(BaseModel):
    """Chart configuration; mirrors the frontend `ChartConfig` interface."""
    chart_type: Literal["line", "bar", "area", "composed", "scatter", "pie"]
    title: Optional[str] = None
    x_axis: Optional[ChartAxis] = None
    y_axis: Optional[ChartAxis] = None
    y_axis_right: Optional[ChartAxis] = None
    series: List[ChartSeries] = Field(default_factory=list)
    data: List[Dict[str, Any]] = Field(default_factory=list)
    show_grid: Optional[bool] = True
    show_legend: Optional[bool] = True
    show_tooltip: Optional[bool] = True
    stacked: Optional[bool] = False


# ─── Fence builder ────────────────────────────────────────────────────────────
#
# `ChartConfig` is ROW-oriented (recharts): `data` is a list of row dicts and each series names the
# column it reads. Callers, though, naturally hold Chart.js-shaped data — parallel arrays of labels
# plus one array of values per series. Three call sites hand-rolled that translation and all three
# got it wrong the same way, emitting `{kind, labels, series:[{label,data}]}`. The frontend
# destructures `chart_type` (absent), falls through every branch, and renders
# "Unsupported chart type: undefined" — silently, for months.
#
# So: ONE translator, and it validates before emitting. A shape error becomes a loud log line
# instead of a broken box in the UI.

import json as _json
import logging as _logging
import re as _re

_logger = _logging.getLogger(__name__)


def _series_key(label: str, index: int) -> str:
    """Stable column name for a series. Row keys must be valid, distinct identifiers."""
    slug = _re.sub(r"[^a-z0-9_]+", "_", str(label or "").lower()).strip("_")
    return slug or f"series_{index + 1}"


def chart_fence(
    *,
    chart_type: str,
    labels: Sequence[Any],
    series: Sequence[Dict[str, Any]],
    title: Optional[str] = None,
    x_key: str = "label",
    x_label: Optional[str] = None,
    y_label: Optional[str] = None,
    stacked: bool = False,
    show_legend: Optional[bool] = None,
) -> str:
    """Build a VALIDATED ```json-chart fence from parallel label/value arrays.

    `series` items are `{"label": str, "data": [...]}` (optionally `key`, `color`, `type`) — the
    shape callers already compute. Rows are zipped to the shortest array so a ragged series can
    never emit a half-populated chart.

    Returns `""` (and logs a warning) if the result wouldn't validate, so a chart problem degrades
    to "no chart" with a trace in the log rather than a broken box in the UI. Never raises.
    """
    try:
        labels = list(labels or [])
        series = [s for s in (series or []) if s]
        if not labels or not series:
            return ""

        # Zip to the shortest run — a ragged series is a bug, not something to render partially.
        n = min([len(labels)] + [len(s.get("data") or []) for s in series])
        if n <= 0:
            return ""

        keys = [s.get("key") or _series_key(s.get("label", ""), i) for i, s in enumerate(series)]
        rows: List[Dict[str, Any]] = []
        for i in range(n):
            row: Dict[str, Any] = {x_key: labels[i]}
            for key, s in zip(keys, series):
                row[key] = (s.get("data") or [])[i]
            rows.append(row)

        config = ChartConfig(
            chart_type=chart_type,  # type: ignore[arg-type]  — validated by pydantic
            title=title,
            x_axis=ChartAxis(key=x_key, label=x_label),
            y_axis=ChartAxis(label=y_label) if y_label else None,
            series=[
                ChartSeries(key=key, label=s.get("label"), color=s.get("color"), type=s.get("type"))
                for key, s in zip(keys, series)
            ],
            data=rows,
            show_legend=len(series) > 1 if show_legend is None else show_legend,
            stacked=stacked,
        )
        payload = _json.dumps(config.model_dump(exclude_none=True))
        return "\n\n```json-chart\n" + payload + "\n```\n"
    except Exception as e:
        _logger.warning(f"[chart_spec] refusing to emit an invalid chart ({title!r}): {e}")
        return ""
