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
from typing import List, Optional, Dict, Any, Literal
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
