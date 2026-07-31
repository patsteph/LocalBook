"""Incident context assembler — turns a Quality signal into a scrubbed incident.

`build_incident_context()` is the single place that projects a raw QS group (from
`quality_signals.get_recent()`, or a manually-supplied context) plus build/env facts
into the structured, SCRUBBED incident dict that Phase 2b will preview + send.

It is pure + never-raises: it composes a plain dict and runs it through
`incident_scrubber.scrub()`, so the returned payload is always allowlist-safe. No
I/O beyond the cached `get_app_version_info()` fact read.

Slice 2a of QS Phase 2. See READFIRST/in-progress/quality-signals-observability.md.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.app_version import get_app_version_info
from services.incident_scrubber import scrub

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


def _stable_incident_id(sig_type: str, component: str, key: str) -> str:
    """Deterministic short id for a (type, component, key) triple.

    Same near-miss group → same id (lets 2b dedup against an existing tracker issue).
    """
    basis = f"{sig_type}|{component}|{key}".encode("utf-8", "replace")
    digest = hashlib.sha1(basis).hexdigest()[:10]
    return f"inc_{digest}"


def _title(sig_type: str, component: str, key: str) -> str:
    key_part = f" [{key}]" if key else ""
    return f"{sig_type} in {component}{key_part}"


def build_incident_context(
    signal: Dict[str, Any],
    *,
    version_info: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    promotion: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble a scrubbed incident dict from a signal group + build/env facts.

    Args:
        signal:  a QS group/context dict — expects best-effort keys `type`,
                 `component`, `key`, `severity`, `detail`, `count`,
                 `first_seen`, `last_seen`, `samples`. Missing keys degrade.
        version_info: override for `get_app_version_info()` (tests inject a fixture).
        metrics: optional `{count, distinct_days, window_days}` threshold facts.
        promotion: optional `{eligible, min_count, min_days, reason}` verdict.

    Returns:
        A fixed-shape, allowlist-scrubbed dict. Never raises — on any error returns
        a minimal `{schema_version, created_at, environment}` scaffold.
    """
    created_at = datetime.utcnow().isoformat()
    try:
        sig = signal if isinstance(signal, dict) else {}
        sig_type = str(sig.get("type", "unknown"))
        component = str(sig.get("component", "unknown"))
        key = str(sig.get("key", "") or "")

        env = version_info if isinstance(version_info, dict) else get_app_version_info()

        raw_samples = sig.get("samples")
        samples: List[Any] = raw_samples if isinstance(raw_samples, list) else []

        incident: Dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "incident_id": _stable_incident_id(sig_type, component, key),
            "title": _title(sig_type, component, key),
            "created_at": created_at,
            "signal": {
                "type": sig_type,
                "component": component,
                "key": key,
                "severity": sig.get("severity", "notable"),
                "detail": sig.get("detail", ""),
                "count": sig.get("count", 1),
                "first_seen": sig.get("first_seen", ""),
                "last_seen": sig.get("last_seen", ""),
            },
            "environment": {
                "app_version": env.get("app_version"),
                "git_sha": env.get("git_sha"),
                "platform": env.get("platform"),
                "platform_release": env.get("platform_release"),
                "arch": env.get("arch"),
                "python_version": env.get("python_version"),
            },
            "samples": samples,
        }
        if isinstance(metrics, dict):
            incident["metrics"] = metrics
        if isinstance(promotion, dict):
            incident["promotion"] = promotion

        # The scrubber is the last gate — allowlist projection + line-scrub.
        return scrub(incident)
    except Exception as e:
        logger.debug(f"[incident-context] build failed (non-fatal): {e}")
        return scrub(
            {
                "schema_version": _SCHEMA_VERSION,
                "created_at": created_at,
                "environment": get_app_version_info(),
            }
        )
