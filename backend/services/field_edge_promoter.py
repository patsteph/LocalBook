"""Field-edge → Evaluator case promoter (QS Phase 2, slice 2d).

Closes the observability loop: when a recurring quality-signal group (from
`quality_signals.get_recent`) clears the promotion thresholds
(`quality_signals.promotion_verdict` — Decision #6), we turn it into a PERMANENT
Evaluator regression case so the next model swap re-tests the exact edge that bit
a real user in daily use.

Cases live in a committed JSON file (data, not code) that the `field_edges`
Evaluator runner loads. This module appends to that file idempotently. It is
best-effort telemetry-adjacent work — it NEVER raises and NEVER runs on the
user's request path (called on demand / nightly, and manual review still gates
the actual GitHub send in 2b).

Only `misroute` groups are promotable: a misroute maps exactly onto the
`intent_classify` case shape `{query, expected_intent, name}`. Other signal
types (fallback/degraded/empty) are observability-only and are not turned into
intent cases here.

See READFIRST/in-progress/quality-signals-observability.md (2d spec).
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# The committed cases file the `field_edges` runner loads. Kept next to the
# other evaluator fixtures. Overridable via env var for CI/tests (tmp file).
_DEFAULT_CASES_PATH = (
    Path(__file__).resolve().parent.parent / "evaluator" / "test_fixtures" / "field_edge_cases.json"
)
_CASES_PATH_ENV = "LOCALBOOK_FIELD_EDGE_CASES"


def field_edge_cases_path() -> Path:
    """Path to the committed field-edge cases file (env-overridable for tests)."""
    override = os.environ.get(_CASES_PATH_ENV)
    return Path(override) if override else _DEFAULT_CASES_PATH


def load_field_edge_cases(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load the promoted cases list. Tolerant of a missing/corrupt file (→ [])."""
    p = Path(path) if path else field_edge_cases_path()
    try:
        with open(p, "r") as f:
            data = json.load(f)
    except Exception:
        return []
    if isinstance(data, dict):
        cases = data.get("cases", [])
    elif isinstance(data, list):
        cases = data
    else:
        cases = []
    return [c for c in cases if isinstance(c, dict)]


def _write_cases(path: Path, cases: List[Dict[str, Any]]) -> None:
    """Persist the cases list, preserving the {version, description, cases} wrapper."""
    wrapper: Dict[str, Any] = {"version": 1, "cases": cases}
    try:
        with open(path, "r") as f:
            existing = json.load(f)
        if isinstance(existing, dict):
            existing["cases"] = cases
            existing.setdefault("version", 1)
            wrapper = existing
    except Exception:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(wrapper, f, indent=2)
        f.write("\n")


def _incident_id(signal: Dict[str, Any]) -> str:
    """Stable inc_<sha1> id for a signal group's (type, component, key) triple."""
    from services.incident_context import _stable_incident_id

    return _stable_incident_id(
        str(signal.get("type", "")),
        str(signal.get("component", "")),
        str(signal.get("key", "")),
    )


def _distinct_days(signal: Dict[str, Any]) -> int:
    """Distinct-day span of a group from its first_seen/last_seen ISO timestamps.

    Inclusive span (a group seen on two calendar days → 2). Defaults to 1 when
    the timestamps are missing/unparseable so a single-session burst reads as 1
    day (correctly below the 2-day floor).
    """
    fs = signal.get("first_seen")
    ls = signal.get("last_seen")
    try:
        d0 = datetime.fromisoformat(fs).date()
        d1 = datetime.fromisoformat(ls).date()
        return abs((d1 - d0).days) + 1
    except Exception:
        return 1


def _build_case(signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build an intent_classify-shaped case from a promotable misroute group.

    A misroute group carries the offending input in `samples` and the weakly
    chosen intent in `key`. We seed `expected_intent` with that key as the best
    available label; a human reviewer corrects it in the JSON if wrong (the
    runner skips cases whose label is still blank).
    """
    if str(signal.get("type", "")) != "misroute":
        return None  # only misroutes map onto the intent_classify shape
    samples = signal.get("samples") or []
    query = (samples[0] if samples else signal.get("detail", "")) or ""
    query = str(query).strip()
    if not query:
        return None
    inc_id = _incident_id(signal)
    return {
        "name": f"fieldedge_{inc_id}",
        "query": query,
        "expected_intent": str(signal.get("key", "") or ""),
        "agent": "studio",
        "source": {
            "incident_id": inc_id,
            "component": signal.get("component", ""),
            "key": signal.get("key", ""),
            "count": signal.get("count", 0),
            "promoted_at": datetime.utcnow().isoformat(),
        },
        "unverified": True,
    }


def promote_signal_to_eval_case(
    signal: Dict[str, Any], *, path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """Append a promoted field-edge case for `signal` IF it clears the thresholds.

    `signal` is a `quality_signals.get_recent` group dict
    (`{type, component, key, count, severity, first_seen, last_seen, samples}`).
    Idempotent — a case whose name (`fieldedge_<inc_id>`) already exists is a
    no-op, so re-running the promoter never duplicates. Returns the appended case
    dict, or None when not eligible / already present / not promotable / on any
    error. NEVER raises.
    """
    try:
        from services.quality_signals import promotion_verdict

        if not isinstance(signal, dict):
            return None
        verdict = promotion_verdict(
            signal.get("count", 0),
            _distinct_days(signal),
            str(signal.get("severity", "notable")),
        )
        if not verdict.get("eligible"):
            return None
        case = _build_case(signal)
        if case is None:
            return None
        p = Path(path) if path else field_edge_cases_path()
        existing = load_field_edge_cases(p)
        if any(c.get("name") == case["name"] for c in existing):
            return None  # idempotent — already promoted
        existing.append(case)
        _write_cases(p, existing)
        logger.info(f"[field-edge] promoted {case['name']} → {p.name} ({verdict.get('reason')})")
        return case
    except Exception as e:
        logger.debug(f"[field-edge] promote failed (non-fatal): {e}")
        return None


def promote_recent(days: int = 7) -> List[Dict[str, Any]]:
    """Walk recent signal groups and promote every eligible one. Never raises.

    Returns the list of newly-appended cases (empty if nothing crossed the bar).
    Intended for on-demand / nightly invocation, not the user request path.
    """
    promoted: List[Dict[str, Any]] = []
    try:
        from services.quality_signals import quality_signals

        for group in quality_signals.get_recent(days):
            case = promote_signal_to_eval_case(group)
            if case is not None:
                promoted.append(case)
    except Exception as e:
        logger.debug(f"[field-edge] promote_recent failed (non-fatal): {e}")
    return promoted
