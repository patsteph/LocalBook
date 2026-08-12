"""Session-wide test isolation from the user's PRODUCTION data.

On a dev machine `settings.data_dir` resolves to the real
`~/Library/Application Support/LocalBook/` — the live data dir, not a sandbox (see
COLLABORATION_NOTES, 2026-07-22, where a persistence test overwrote `user_preferences.json`).
Any test that reaches a state-mutating path therefore writes to the user's actual data.

That is not hypothetical. `test_canvas_api.test_edge_lifecycle` POSTs an edge with `state="user"`,
which runs the real feedback pipeline and appends a `user_connection` signal to the **production
Quality Signals ledger on every test run**. By 2026-08-12, 116 of the 128 signals in it (91%) were
test noise — polluting the Health "Rough Edges" panel and, worse, the recurrence counts that
QS Phase 2 uses to promote field edges into Evaluator regression cases. Synthetic signals recurring
across many days are exactly what that promoter is designed to escalate.

This fixture repoints `data_dir` at a temp directory for the whole session, so no test can write
production data by accident. It is autouse and session-scoped: correctness here must not depend on
individual tests remembering to opt in.

CI is unaffected (a fresh runner has no data dir anyway) — this protects the dev machines.
"""
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_production_data_dir(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("localbook-data")

    from config import settings
    original = settings.data_dir
    # Must stay a Path — stores do `settings.data_dir / "file.json"`.
    settings.data_dir = Path(tmp)

    # Sinks that cache a path at construction must be repointed explicitly — reading `settings`
    # later is not enough. `quality_signals` is a singleton built at import time.
    signals = None
    original_signals_dir = None
    try:
        from services.quality_signals import quality_signals as signals
        original_signals_dir = signals.signals_dir
        signals.signals_dir = Path(tmp) / "signals"
        signals.signals_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # sink unavailable in a slim env — nothing to isolate
        pass

    yield tmp

    settings.data_dir = original
    if signals is not None and original_signals_dir is not None:
        signals.signals_dir = original_signals_dir
