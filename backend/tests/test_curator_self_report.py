"""Tests for the curator's Quality-Signals self-report (QS Phase 2).

The morning brief tells the user what happened while they were away; "where I worked badly"
belongs there too. Read-only over the ledger, and never fatal — a brief must still render when
the sink is unavailable.
"""
import pytest

from services.quality_signals import quality_signals


def _self_report():
    """Mirror of the selection logic in _brief.py (notable+ and count > 1, capped at 5)."""
    out = []
    for g in quality_signals.get_recent(7):
        if g.get("severity") in ("notable", "warn") and g.get("count", 0) > 1:
            out.append(g)
    return out[:5]


def test_recurring_warn_signals_are_reported():
    for _ in range(3):
        quality_signals.record("misroute", "intent_classifier", "phrasing overrode shape",
                               severity="warn", key="selfreport-a")
    keys = [g.get("key") for g in _self_report()]
    assert "selfreport-a" in keys


def test_info_signals_are_not_confessed():
    """An `info` cache-hit is not something to apologise for in a brief."""
    for _ in range(9):
        quality_signals.record("fallback", "rag_cache", "served from cache",
                               severity="info", key="selfreport-info")
    assert "selfreport-info" not in [g.get("key") for g in _self_report()]


def test_one_off_is_not_reported():
    quality_signals.record("degraded", "mlx_engine", "one-off blip",
                           severity="warn", key="selfreport-once")
    assert "selfreport-once" not in [g.get("key") for g in _self_report()]


def test_report_is_capped():
    for i in range(9):
        for _ in range(2):
            quality_signals.record("degraded", f"component_{i}", "noisy",
                                   severity="warn", key=f"selfreport-cap-{i}")
    assert len(_self_report()) <= 5


def test_brief_model_defaults_to_empty_self_report():
    """A clean week must not break the brief — the section is simply absent.

    Importing the curator models pulls in `agents/state.py` → langchain_core, which the slim CI
    dep set deliberately excludes (see requirements-test.txt). Skip there rather than drag the
    heavy agent stack into the unit tier; this runs locally where the full env exists.
    """
    pytest.importorskip("langchain_core", reason="heavy agent stack — not in the slim CI set")
    from datetime import datetime
    from agents.curator._models import MorningBrief

    b = MorningBrief(away_duration="2 days", notebook_summaries=[], generated_at=datetime.utcnow())
    assert b.self_report == []
