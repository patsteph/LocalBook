"""Tests for client-reported Quality Signals (`POST /signals/record`).

Quality Signals only ever watched the backend, which is how three chat charts rendered
"Unsupported chart type: undefined" for months with nothing recorded anywhere. The renderer can
see what the backend cannot, so it now reports — but the sink stays bounded, because these entries
feed promotion into Evaluator regression cases.
"""
import asyncio

import pytest
from fastapi import HTTPException

from api import signals as sig_api
from services.quality_signals import quality_signals


def _post(**kw):
    kw.setdefault("type", "render_failed")
    kw.setdefault("component", "chart_renderer")
    kw.setdefault("detail", "unsupported chart type: undefined")
    return asyncio.run(sig_api.record_client_signal(sig_api.ClientSignal(**kw)))


def test_render_failure_is_recorded_and_surfaces_in_the_rollup():
    assert _post(key="undefined")["recorded"] is True
    groups = asyncio.run(sig_api.recent_signals(days=7))["groups"]
    assert any(g["component"] == "chart_renderer" and g["type"] == "render_failed" for g in groups)


def test_type_is_allowlisted():
    """The ledger must not become a free-form sink for arbitrary client strings."""
    with pytest.raises(HTTPException) as e:
        _post(type="whatever_i_want")
    assert e.value.status_code == 400


def test_all_client_types_accepted():
    for t in ("render_failed", "degraded", "empty", "fallback"):
        assert _post(type=t, key=f"k-{t}")["recorded"] is True


def test_detail_is_length_capped():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        sig_api.ClientSignal(type="render_failed", component="c", detail="x" * 5000)


def test_unknown_severity_is_coerced_not_rejected():
    """A bad severity shouldn't lose the signal — it degrades to `warn`."""
    _post(key="sev-probe", severity="catastrophic")
    groups = asyncio.run(sig_api.recent_signals(days=7))["groups"]
    g = next(g for g in groups if g.get("key") == "sev-probe")
    assert g["severity"] == "warn"


def test_recurrence_groups_by_key():
    for _ in range(3):
        quality_signals.record("render_failed", "chart_renderer", "unsupported", key="grouped")
    groups = asyncio.run(sig_api.recent_signals(days=7))["groups"]
    g = next(g for g in groups if g.get("key") == "grouped")
    assert g["count"] >= 3
