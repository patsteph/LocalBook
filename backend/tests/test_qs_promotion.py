"""CI-pure tests for the field-edge → Evaluator promotion path (QS Phase 2, slice 2d).

No production data, no LLM, no network:
  - The cases file is a tmp path (passed explicitly or via the LOCALBOOK_FIELD_EDGE_CASES
    env override) — the committed fixture is never touched.
  - `promote_signal_to_eval_case` appends a case when `promotion_verdict` passes, is
    idempotent, only promotes MISROUTE groups, and NEVER raises on garbage input.
  - The `field_edges` runner loads the cases file; with no cases it emits a single SKIPPED
    result (so the category is excluded from the weighted average) — exercised without any
    LLM call.

Run:  cd backend && python -m pytest tests/test_qs_promotion.py -q
"""
import asyncio
import json

import pytest

from services.field_edge_promoter import (
    field_edge_cases_path,
    load_field_edge_cases,
    promote_signal_to_eval_case,
)


# ── Fixtures / helpers ───────────────────────────────────────────────────────
def _eligible_misroute():
    """A recurring misroute that clears the thresholds (count 6 over 3 days, notable)."""
    return {
        "type": "misroute",
        "component": "intent_classifier",
        "key": "cross_notebook_search",
        "count": 6,
        "severity": "notable",
        "first_seen": "2026-07-29T10:00:00",
        "last_seen": "2026-07-31T09:00:00",
        "samples": ["what did we say about topic X"],
        "detail": "low confidence 0.31",
    }


@pytest.fixture
def cases_file(tmp_path):
    """A tmp cases-file path that does not exist yet (promoter creates it)."""
    return tmp_path / "field_edge_cases.json"


def _read_cases(path):
    return load_field_edge_cases(path)


# ── promote appends when eligible ────────────────────────────────────────────
def test_promote_appends_case_when_eligible(cases_file):
    case = promote_signal_to_eval_case(_eligible_misroute(), path=cases_file)
    assert case is not None
    assert case["name"].startswith("fieldedge_inc_")
    assert case["query"] == "what did we say about topic X"
    assert case["expected_intent"] == "cross_notebook_search"

    on_disk = _read_cases(cases_file)
    assert len(on_disk) == 1
    assert on_disk[0]["name"] == case["name"]
    # Wrapper is preserved as {version, cases}.
    raw = json.loads(cases_file.read_text())
    assert raw["version"] == 1
    assert isinstance(raw["cases"], list)


# ── idempotent: same group never duplicates ──────────────────────────────────
def test_promote_is_idempotent(cases_file):
    first = promote_signal_to_eval_case(_eligible_misroute(), path=cases_file)
    assert first is not None
    second = promote_signal_to_eval_case(_eligible_misroute(), path=cases_file)
    assert second is None  # already present → no-op
    assert len(_read_cases(cases_file)) == 1


# ── below-threshold groups are not promoted ──────────────────────────────────
def test_below_count_threshold_not_promoted(cases_file):
    sig = _eligible_misroute()
    sig["count"] = 1  # < PROMOTE_MIN_COUNT
    assert promote_signal_to_eval_case(sig, path=cases_file) is None
    assert _read_cases(cases_file) == []


def test_single_day_burst_not_promoted(cases_file):
    sig = _eligible_misroute()
    sig["first_seen"] = "2026-07-31T08:00:00"
    sig["last_seen"] = "2026-07-31T09:00:00"  # same calendar day → distinct_days=1
    assert promote_signal_to_eval_case(sig, path=cases_file) is None
    assert _read_cases(cases_file) == []


def test_info_severity_not_promoted(cases_file):
    sig = _eligible_misroute()
    sig["severity"] = "info"  # below the notable floor
    assert promote_signal_to_eval_case(sig, path=cases_file) is None
    assert _read_cases(cases_file) == []


# ── only misroutes map onto the intent_classify shape ────────────────────────
def test_non_misroute_eligible_group_not_promoted(cases_file):
    sig = _eligible_misroute()
    sig["type"] = "degraded"  # eligible by thresholds, but not intent-shaped
    sig["severity"] = "warn"
    assert promote_signal_to_eval_case(sig, path=cases_file) is None
    assert _read_cases(cases_file) == []


# ── never raises on garbage ──────────────────────────────────────────────────
def test_never_raises_on_bad_input(cases_file):
    assert promote_signal_to_eval_case(None, path=cases_file) is None
    assert promote_signal_to_eval_case("not a dict", path=cases_file) is None
    assert promote_signal_to_eval_case({}, path=cases_file) is None
    # Misroute with no samples/detail → nothing to build a query from.
    assert promote_signal_to_eval_case(
        {"type": "misroute", "count": 9, "severity": "warn",
         "first_seen": "2026-07-01T00:00:00", "last_seen": "2026-07-05T00:00:00"},
        path=cases_file,
    ) is None
    assert _read_cases(cases_file) == []


def test_load_missing_file_returns_empty(cases_file):
    assert load_field_edge_cases(cases_file) == []


# ── the field_edges runner loads the cases file ──────────────────────────────
def test_runner_loads_promoted_cases_via_env(monkeypatch, cases_file):
    promote_signal_to_eval_case(_eligible_misroute(), path=cases_file)
    monkeypatch.setenv("LOCALBOOK_FIELD_EDGE_CASES", str(cases_file))
    # The runner resolves the same path the promoter wrote.
    assert field_edge_cases_path() == cases_file
    loaded = load_field_edge_cases()  # default path == env override
    assert len(loaded) == 1
    assert loaded[0]["expected_intent"] == "cross_notebook_search"


def test_runner_empty_cases_is_skipped(monkeypatch, tmp_path):
    """No promoted cases → runner emits ONE skipped result, no LLM call."""
    empty = tmp_path / "empty_cases.json"
    empty.write_text(json.dumps({"version": 1, "cases": []}))
    monkeypatch.setenv("LOCALBOOK_FIELD_EDGE_CASES", str(empty))

    from evaluator.test_runners import field_edges

    results = asyncio.run(field_edges.run("nb", {}, "combo", "hw"))
    assert len(results) == 1
    assert results[0].skipped is True
    assert results[0].category == "field_edges"
