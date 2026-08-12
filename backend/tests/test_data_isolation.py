"""Guard: the test suite must never write to the user's production data dir.

The dev venv points `settings.data_dir` at the real
`~/Library/Application Support/LocalBook/`. Before `conftest.py` existed, a single API test
(`test_canvas_api.test_edge_lifecycle`) appended a `user_connection` signal to the PRODUCTION
Quality Signals ledger on every run — 91% of that ledger turned out to be test noise, feeding the
Health panel and the field-edge→Evaluator promoter with synthetic recurrence.

If these fail, the isolation fixture in conftest.py has regressed and tests are touching real data.
"""
import os
from pathlib import Path

from config import settings


def _real_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "LocalBook"


def test_data_dir_is_redirected_away_from_production():
    assert Path(settings.data_dir) != _real_data_dir()
    assert "localbook-data" in str(settings.data_dir)  # the tmp dir conftest created
    assert isinstance(settings.data_dir, Path)  # stores do `data_dir / "x.json"` — must stay a Path


def test_signal_sink_writes_into_the_temp_dir():
    from services.quality_signals import quality_signals, record_signal

    assert str(quality_signals.signals_dir).startswith(str(Path(settings.data_dir)))

    before = sorted(os.listdir(quality_signals.signals_dir))
    record_signal("degraded", "test_data_isolation", "isolation probe", severity="info", key="probe")
    after = sorted(os.listdir(quality_signals.signals_dir))
    assert after >= before  # wrote somewhere...
    written = "".join((quality_signals.signals_dir / f).read_text() for f in after)
    assert "isolation probe" in written  # ...and it was the temp ledger


def test_production_ledger_is_untouched_by_this_run():
    """Belt-and-braces: the real ledger must not gain this run's probe signal."""
    prod = _real_data_dir() / "signals"
    if not prod.exists():
        return  # fresh machine / CI — nothing to protect
    for f in prod.glob("*.jsonl"):
        assert "isolation probe" not in f.read_text(errors="ignore")
