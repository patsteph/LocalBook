"""Tests for `POST /incidents/scan` — the missing middle of the Quality Signals pipe.

Phase 2 shipped the sink, the promoter, the incident queue and the filing path, but nothing ever
walked the ledger: `escalate_to_incident` and `promote_recent` had no production caller, so the
queue stayed permanently empty and the review/file UI had nothing to show. This endpoint connects
them, and these tests pin the connection.

Data isolation comes from `conftest.py` (session-scoped temp `data_dir`) — no production writes.
"""
import asyncio
from datetime import datetime, timedelta

import pytest

from api import incidents as inc_api
from services import quality_signals as qs


@pytest.fixture
def clean_ledger(tmp_path, monkeypatch):
    """Point the signal sink + incident queue at a per-test dir."""
    d = tmp_path / "signals"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(qs.quality_signals, "signals_dir", d)
    return d


def _group(count, first_days_ago, last_days_ago, severity="warn", type_="misroute"):
    now = datetime.utcnow()
    return {
        "type": type_, "component": "intent_classifier", "key": "k",
        "count": count, "severity": severity,
        "first_seen": (now - timedelta(days=first_days_ago)).isoformat(),
        "last_seen": (now - timedelta(days=last_days_ago)).isoformat(),
        "detail": "phrasing overrode content-shape", "samples": ["compare x to y"],
    }


def _scan(monkeypatch, groups, days=7):
    monkeypatch.setattr(qs.quality_signals, "get_recent", lambda d=7: groups)
    return asyncio.run(inc_api.scan_signals(days=days))


def test_recurring_signal_becomes_a_queued_incident(clean_ledger, monkeypatch):
    """The core connection: a group over the bar lands in the queue for review."""
    res = _scan(monkeypatch, [_group(count=8, first_days_ago=4, last_days_ago=0)])
    assert res["considered"] == 1
    assert res["escalated"] == 1
    assert res["incident_ids"] and res["incident_ids"][0]

    # ...and it is visible to the preview endpoint the UI reads.
    preview = asyncio.run(inc_api.preview_incidents())
    assert preview["count"] >= 1
    assert any(i["title"] for i in preview["incidents"])


def test_one_session_burst_is_not_escalated(clean_ledger, monkeypatch):
    """5+ hits inside a single day is a burst, not a recurring problem (min 2 distinct days)."""
    res = _scan(monkeypatch, [_group(count=50, first_days_ago=0, last_days_ago=0)])
    assert res["escalated"] == 0


def test_rare_signal_is_not_escalated(clean_ledger, monkeypatch):
    res = _scan(monkeypatch, [_group(count=2, first_days_ago=5, last_days_ago=0)])
    assert res["escalated"] == 0


def test_info_severity_is_excluded(clean_ledger, monkeypatch):
    """`info` signals (e.g. cache hits) are noise for escalation purposes."""
    res = _scan(monkeypatch, [_group(count=99, first_days_ago=6, last_days_ago=0, severity="info")])
    assert res["escalated"] == 0


def test_scan_is_idempotent(clean_ledger, monkeypatch):
    """Re-scanning must not pile up duplicates of the same incident."""
    groups = [_group(count=8, first_days_ago=4, last_days_ago=0)]
    _scan(monkeypatch, groups)
    first = asyncio.run(inc_api.preview_incidents())["count"]
    _scan(monkeypatch, groups)
    second = asyncio.run(inc_api.preview_incidents())["count"]
    assert second == first


def test_scan_never_sends(clean_ledger, monkeypatch):
    """Scanning is local-only: filing stays gated behind the default-OFF flag."""
    _scan(monkeypatch, [_group(count=8, first_days_ago=4, last_days_ago=0)])
    preview = asyncio.run(inc_api.preview_incidents())
    assert preview["enabled"] is False           # default-OFF
    assert all(not i["already_filed"] for i in preview["incidents"])


def test_empty_ledger_is_a_clean_noop(clean_ledger, monkeypatch):
    res = _scan(monkeypatch, [])
    assert res == {"days": 7, "considered": 0, "escalated": 0,
                   "incident_ids": [], "promoted_cases": []}


def test_file_refuses_while_disabled():
    """The send path must refuse (409) while the flag is off — no auth, no network touched."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        asyncio.run(inc_api.file_incidents(inc_api.FileRequest(confirm=True)))
    assert e.value.status_code == 409


def test_file_requires_explicit_confirm(monkeypatch):
    """Even with filing enabled, confirm=false is a 400 — never an accidental send."""
    from fastapi import HTTPException
    monkeypatch.setattr(inc_api.github_incident, "is_enabled", lambda: True)
    with pytest.raises(HTTPException) as e:
        asyncio.run(inc_api.file_incidents(inc_api.FileRequest(confirm=False)))
    assert e.value.status_code == 400
