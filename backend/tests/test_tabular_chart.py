"""CI-pure tests for tabular_query's `_maybe_chart` (Phase 2 — charts from results).
Model-free: pure result-shape → `json-chart` fence heuristic. Verifies the fence JSON
parses and matches the ChartConfig shape the frontend ChartArtifactRenderer expects.
"""
import json
import re

from services import tabular_query as tq


def _extract_chart(block: str) -> dict:
    """Pull the JSON out of a `\\n\\n```json-chart\\n{...}\\n```\\n` block."""
    m = re.search(r"```json-chart\n(.*?)\n```", block, re.DOTALL)
    assert m, f"no json-chart fence in: {block!r}"
    return json.loads(m.group(1))


def test_category_plus_numeric_produces_bar_chart():
    cols = ["region", "total"]
    rows = [["West", 1600], ["East", 800], ["North", 400]]
    block = tq._maybe_chart("total sales by region", cols, rows)
    assert block.startswith("\n\n```json-chart\n") and block.endswith("```\n")
    cfg = _extract_chart(block)

    # ChartConfig shape parity (chart_spec.ChartConfig / ChartRenderer.tsx).
    assert cfg["chart_type"] == "bar"
    assert cfg["x_axis"]["key"] == "region"
    assert [s["key"] for s in cfg["series"]] == ["total"]
    assert cfg["data"] == [
        {"region": "West", "total": 1600},
        {"region": "East", "total": 800},
        {"region": "North", "total": 400},
    ]
    # Numbers stay numbers, not strings.
    assert all(isinstance(d["total"], (int, float)) for d in cfg["data"])


def test_validates_against_chart_spec_model():
    from services.chart_spec import ChartConfig
    cols = ["product", "revenue", "units"]
    rows = [["A", 100.5, 3], ["B", 200.0, 5], ["C", 50.0, 1]]
    cfg = _extract_chart(tq._maybe_chart("revenue by product", cols, rows))
    model = ChartConfig(**cfg)  # raises if shape is wrong
    assert model.chart_type == "bar"
    assert len(model.series) == 2  # revenue + units
    assert model.x_axis.key == "product"


def test_time_like_label_column_produces_line_chart():
    cols = ["month", "signups"]
    rows = [["2024-01", 10], ["2024-02", 22], ["2024-03", 31]]
    cfg = _extract_chart(tq._maybe_chart("signups per month", cols, rows))
    assert cfg["chart_type"] == "line"
    assert cfg["x_axis"]["key"] == "month"


def test_time_hint_by_column_name():
    # A "year" column named as time but stored as text (label) → line chart via name hint.
    cols = ["year", "count"]
    rows = [["FY2021", 5], ["FY2022", 8], ["FY2023", 12]]
    cfg = _extract_chart(tq._maybe_chart("count by year", cols, rows))
    assert cfg["chart_type"] == "line"


def test_bare_integer_year_is_numeric_not_charted():
    # Two integer columns → no non-numeric label column → not chartable (spec rule).
    assert tq._maybe_chart("count by year", ["year", "count"],
                           [[2021, 5], [2022, 8], [2023, 12]]) == ""


def test_scalar_result_is_not_charted():
    assert tq._maybe_chart("how many accounts", ["count"], [[42]]) == ""


def test_single_row_is_not_charted():
    assert tq._maybe_chart("total", ["region", "total"], [["West", 100]]) == ""


def test_too_many_categories_is_not_charted():
    cols = ["account", "amount"]
    rows = [[f"acct-{i}", i] for i in range(40)]  # > 30 categories
    assert tq._maybe_chart("amount by account", cols, rows) == ""


def test_no_numeric_column_is_not_charted():
    cols = ["region", "status"]
    rows = [["West", "active"], ["East", "churned"], ["North", "active"]]
    assert tq._maybe_chart("status by region", cols, rows) == ""


def test_two_label_columns_is_not_charted():
    # Two non-numeric columns → ambiguous category; skip.
    cols = ["region", "status", "amount"]
    rows = [["West", "active", 1], ["East", "churned", 2], ["North", "active", 3]]
    assert tq._maybe_chart("q", cols, rows) == ""


def test_shared_render_answer_has_NO_chart():
    # ISOLATION: the shared spreadsheet renderer is byte-identical to master — NO chart appended.
    # (Charts are appended only on the Cursor path, in cursor_sql.answer via _maybe_chart.)
    result = {"columns": ["region", "total"],
              "rows": [["West", 1600], ["East", 800], ["North", 400]]}
    out = tq._render_answer("total by region", "SELECT ...", "sales.db", result)
    assert "| Region | Total |" in out          # table present
    assert "```json-chart" not in out           # NO chart on the shared path


def test_maybe_chart_helper_still_charts_multirow():
    # The pure chart helper (reused by cursor_sql) still produces a chart for chartable data.
    cols, rows = ["region", "total"], [["West", 1600], ["East", 800], ["North", 400]]
    chart = tq._maybe_chart("total by region", cols, rows)
    assert "```json-chart" in chart


def test_render_answer_scalar_has_no_chart():
    result = {"columns": ["count"], "rows": [[42]]}
    out = tq._render_answer("how many", "SELECT COUNT(*)", "sales.db", result)
    assert out == "**42**" and "json-chart" not in out


def test_cursor_prompt_has_by_dimension_rule():
    # The GROUP-BY / by-dimension rule lives on the Cursor prompt builder now (isolation).
    from services import cursor_sql as cs
    schema = [{"table_name": "sales", "filename": "sales.db", "sheet_name": "sales",
               "row_count": 3, "columns": [
                   {"sanitized": "region", "dtype": "text"},
                   {"sanitized": "amount", "dtype": "number"}]}]
    p = cs._build_cursor_prompt("total by region", schema, [], [], [], governance="")
    assert "GROUP BY" in p
    # And the shared spreadsheet prompt stays minimal (no GROUP-BY rule — master behavior).
    assert "GROUP BY" not in tq._build_prompt("total by region", schema, [])
