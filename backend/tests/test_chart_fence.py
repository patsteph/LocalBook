"""Tests for `chart_spec.chart_fence` — the one translator from Chart.js-shaped data to the
row-oriented ChartConfig the frontend actually renders.

Why this exists: three call sites (correspondent routing-confidence, correspondent topic trends,
curator entity-mentions) each hand-rolled that translation and each emitted
`{kind, labels, series:[{label,data}]}`. `ChartRenderer` destructures `chart_type` — absent in that
shape — falls through every branch and renders "Unsupported chart type: undefined". The charts had
never worked. The final test in this file is a regression guard against the shape coming back.
"""
import json
import re
from pathlib import Path

from services.chart_spec import ChartConfig, chart_fence

BACKEND = Path(__file__).resolve().parent.parent


def _payload(fence: str) -> dict:
    m = re.search(r"```json-chart\n(.*?)\n```", fence, re.S)
    assert m, f"no json-chart fence in: {fence!r}"
    return json.loads(m.group(1))


def test_produces_a_valid_chartconfig():
    fence = chart_fence(
        chart_type="bar", title="T", labels=["a", "b"],
        series=[{"label": "recent", "data": [1, 2]}],
    )
    cfg = _payload(fence)
    ChartConfig(**cfg)                      # must round-trip through the schema
    assert cfg["chart_type"] == "bar"       # the field whose absence broke every chart
    assert cfg["title"] == "T"


def test_zips_labels_and_series_into_rows():
    """The whole translation: parallel arrays → one row per label."""
    cfg = _payload(chart_fence(
        chart_type="bar", labels=["x", "y"],
        series=[{"label": "last 7d", "data": [10, 20]},
                {"label": "prior 7d", "data": [5, 6]}],
    ))
    assert cfg["data"] == [
        {"label": "x", "last_7d": 10, "prior_7d": 5},
        {"label": "y", "last_7d": 20, "prior_7d": 6},
    ]
    assert [s["key"] for s in cfg["series"]] == ["last_7d", "prior_7d"]
    assert [s["label"] for s in cfg["series"]] == ["last 7d", "prior 7d"]
    assert cfg["x_axis"]["key"] == "label"


def test_ragged_series_is_truncated_not_half_rendered():
    cfg = _payload(chart_fence(
        chart_type="bar", labels=["a", "b", "c"],
        series=[{"label": "s", "data": [1, 2]}],
    ))
    assert len(cfg["data"]) == 2


def test_custom_x_key_and_axis_labels():
    cfg = _payload(chart_fence(
        chart_type="line", labels=["2026-W01"], series=[{"label": "mentions", "data": [3]}],
        x_key="week", x_label="Week", y_label="Mentions",
    ))
    assert cfg["x_axis"] == {"key": "week", "label": "Week"}
    assert cfg["y_axis"] == {"label": "Mentions"}
    assert cfg["data"] == [{"week": "2026-W01", "mentions": 3}]


def test_legend_defaults_to_multi_series_only():
    single = _payload(chart_fence(chart_type="bar", labels=["a"],
                                  series=[{"label": "one", "data": [1]}]))
    multi = _payload(chart_fence(chart_type="bar", labels=["a"],
                                 series=[{"label": "one", "data": [1]},
                                         {"label": "two", "data": [2]}]))
    assert single["show_legend"] is False and multi["show_legend"] is True


def test_awkward_labels_become_usable_keys():
    cfg = _payload(chart_fence(
        chart_type="bar", labels=["a"],
        series=[{"label": "Auto-routed (%)", "data": [1]}, {"label": "", "data": [2]}],
    ))
    keys = [s["key"] for s in cfg["series"]]
    assert keys == ["auto_routed", "series_2"]
    assert set(cfg["data"][0]) == {"label", "auto_routed", "series_2"}


def test_invalid_chart_type_emits_nothing_rather_than_a_broken_box():
    assert chart_fence(chart_type="donut", labels=["a"],
                       series=[{"label": "s", "data": [1]}]) == ""


def test_empty_inputs_emit_nothing():
    assert chart_fence(chart_type="bar", labels=[], series=[]) == ""
    assert chart_fence(chart_type="bar", labels=["a"], series=[]) == ""
    assert chart_fence(chart_type="bar", labels=["a"], series=[{"label": "s", "data": []}]) == ""


def test_no_backend_code_emits_the_legacy_chartjs_shape():
    """REGRESSION GUARD. `{"kind": ...}` alongside `"labels"` is the broken Chart.js-style payload
    that silently rendered as "Unsupported chart type: undefined". New charts must go through
    chart_fence(). If this fails, you hand-rolled a chart dict — use the helper."""
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if any(part in path.parts for part in (".venv", "build", "tests", "__pycache__")):
            continue
        text = path.read_text(errors="ignore")
        if '"kind":' in text and '"labels":' in text and "json-chart" in text:
            offenders.append(str(path.relative_to(BACKEND)))
    assert offenders == [], f"legacy chart payload shape found in: {offenders}"
