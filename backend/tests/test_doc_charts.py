"""CI-pure tests for the P2 doc-chart compute path (services/doc_charts.py).

The model call is mocked; the py_compute sandbox is REAL, so these exercise the actual
generate-Python → run → emit → fence pipeline end to end. No network, no app data.

Async is driven with plain `asyncio.run` per this repo's convention (no pytest-asyncio dep).
"""
import asyncio
import json

import pytest

from services import doc_charts as dc


class _FakeOllama:
    """Stands in for ollama_service.generate — returns canned model output."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": self._response}


@pytest.fixture
def patch_model(monkeypatch):
    def _apply(response):
        fake = _FakeOllama(response)
        import services.ollama_service as os_mod
        monkeypatch.setattr(os_mod, "ollama_service", fake)
        return fake

    return _apply


def _run(**kw):
    """Call compute_chart_fences with sensible defaults; returns the fence list."""
    kw.setdefault("content", "A doc about regional sales.")
    kw.setdefault("topic_focus", "regional sales")
    kw.setdefault("source_context", "North sold 120. South sold 80.")
    return asyncio.run(dc.compute_chart_fences(**kw))


def _cfg(fence: str) -> dict:
    return json.loads(fence.split("\n", 1)[1].rsplit("\n```", 1)[0])


def test_strip_code_fence_variants():
    assert dc._strip_code_fence("```python\nx = 1\n```") == "x = 1"
    assert dc._strip_code_fence("```\ny = 2\n```") == "y = 2"
    assert dc._strip_code_fence("z = 3") == "z = 3"


def test_arithmetic_is_executed_not_typed(patch_model):
    """The core claim: the model supplies figures, PYTHON computes the derived values."""
    patch_model(
        "```python\n"
        "figures = [('North', 120.0), ('South', 80.0)]  # from source material\n"
        "total = sum(v for _, v in figures)\n"
        "rows = [{'label': k, 'value': round(v / total * 100, 1)} for k, v in figures]\n"
        "emit_chart('bar', rows, [{'key': 'value', 'label': 'Share (%)'}],\n"
        "           title='Share by region', x_key='label')\n"
        "```"
    )
    fences = _run()
    assert len(fences) == 1
    assert fences[0].startswith("```lb-chart\n") and fences[0].endswith("\n```")

    cfg = _cfg(fences[0])
    assert cfg["chart_type"] == "bar"
    assert cfg["title"] == "Share by region"
    assert cfg["x_axis"] == {"key": "label"}
    # 120/200 and 80/200 — computed in the sandbox; these numbers never appear in the model output.
    assert cfg["data"] == [{"label": "North", "value": 60.0}, {"label": "South", "value": 40.0}]


def test_multiple_charts_are_returned(patch_model):
    patch_model(
        "rows = [{'label': 'a', 'value': 1}, {'label': 'b', 'value': 2}]\n"
        "emit_chart('bar', rows, ['value'], title='One', x_key='label')\n"
        "emit_chart('line', rows, ['value'], title='Two', x_key='label')\n"
    )
    fences = _run()
    assert len(fences) == 2
    assert _cfg(fences[0])["title"] == "One" and _cfg(fences[1])["title"] == "Two"


def test_n_charts_caps_output(patch_model):
    patch_model(
        "rows = [{'label': 'a', 'value': 1}]\n"
        + "".join(f"emit_chart('bar', rows, ['value'], title='C{i}', x_key='label')\n"
                  for i in range(5))
    )
    assert len(_run(n_charts=2)) == 2


def test_broken_code_falls_back_to_empty(patch_model):
    """An exception in generated code must NOT bubble — the caller falls back to LLM-JSON."""
    patch_model("emit_chart('bar', [{'label': 'a', 'value': 1 / 0}], ['value'])")
    assert _run() == []


def test_invalid_chart_type_yields_no_fence(patch_model):
    # ChartConfig validation in py_compute's parent rejects it -> ok False -> no fences.
    patch_model("emit_chart('donut', [{'label': 'a', 'value': 1}], ['value'])")
    assert _run() == []


def test_empty_model_output_yields_no_fence(patch_model):
    patch_model("")
    assert _run() == []


def test_code_emitting_nothing_yields_no_fence(patch_model):
    """The prompt tells the model to emit nothing rather than invent figures — honor that."""
    patch_model("# the material has no numbers worth plotting\npass")
    assert _run() == []


def test_chart_with_empty_data_is_dropped(patch_model):
    patch_model("emit_chart('bar', [], ['value'], title='Empty', x_key='label')")
    assert _run() == []


def test_network_is_unavailable_to_generated_code(patch_model):
    patch_model(
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80))\n"
        "except Exception:\n"
        "    pass\n"
        "emit_chart('bar', [{'label': 'a', 'value': 1}], ['value'], x_key='label')\n"
    )
    assert len(_run()) == 1  # blocked, but the chart still gets built


def test_chart_brief_is_passed_to_the_model(patch_model):
    fake = patch_model("emit_chart('bar', [{'label': 'a', 'value': 1}], ['value'], x_key='label')")
    _run(chart_brief="This document is a two-sided DEBATE.")
    assert "two-sided DEBATE" in fake.calls[0]["prompt"]


def test_flag_is_off_by_default():
    """P2 must not change built-app behavior until explicitly enabled."""
    from config import settings
    assert settings.py_compute_doc_charts_enabled is False
