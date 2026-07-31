"""Incident scrubber — an ALLOWLIST-based redactor for shareable incident payloads.

Privacy is the product. Before a Quality-Signals near-miss can EVER be assembled into
something a human might paste into a public tracker (Phase 2b/2c), it passes through
this scrubber. The design is deliberately paranoid:

  - **Allowlist, not denylist (default-drop).** Any key NOT explicitly named is removed —
    at every nesting level. A future field that carries `notebook_id`, `source_path`,
    `email`, or `api_key` leaks NOTHING because it was never on the allowlist. New keys
    are opt-in, so forgetting to redact a new field fails CLOSED.
  - **Line-scrub what survives.** Free-text fields that DO pass (a signal `detail`, the
    triggering `samples`) still get their contents redacted: home paths → `~`, emails →
    `[redacted-email]`, secret-shaped tokens → `[redacted-token]`, and a length cap.
  - **Pure + never-raises.** `scrub()` is a total function: bad input yields `{}`, never
    an exception. Unit-testable with synthetic payloads, no I/O.

Slice 2a of the QS-Phase-2 build. See READFIRST/in-progress/quality-signals-observability.md.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

# ── Allowlist ────────────────────────────────────────────────────────────────
# The set of key names permitted ANYWHERE in an incident payload. Applied
# recursively: at each dict, keys outside this set are dropped. Deliberately does
# NOT include notebook_id / source_id / paths / user identity — those are the
# leak class this whole module exists to prevent.
_ALLOWED_KEYS = frozenset(
    {
        # incident envelope
        "incident_id",
        "title",
        "created_at",
        "schema_version",
        # signal facts (what kind of near-miss, where, how often)
        "signal",
        "type",
        "component",
        "key",
        "severity",
        "detail",
        "count",
        "first_seen",
        "last_seen",
        # environment / build facts (from app_version)
        "environment",
        "app_version",
        "git_sha",
        "platform",
        "platform_release",
        "arch",
        "python_version",
        # promotion / threshold math
        "metrics",
        "promotion",
        "distinct_days",
        "window_days",
        "eligible",
        "min_count",
        "min_days",
        "reason",
        # scrubbed free-text samples of the triggering input
        "samples",
    }
)

# Longest a single free-text value (detail / sample) may be after scrubbing.
_MAX_TEXT_CHARS = 240
# Cap on how many query samples survive — bound the blast radius of user text.
_MAX_SAMPLES = 3

# ── Line-scrub patterns (order matters: tokens before generic paths/emails) ──
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Secret-shaped tokens: GitHub PATs, fine-grained PATs, Bearer headers, OpenAI keys.
_TOKEN_RES = (
    re.compile(r"ghp_[A-Za-z0-9]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}"),
    re.compile(r"gho_[A-Za-z0-9]{10,}"),
    re.compile(r"sk-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"),
)
# macOS/Linux home-dir paths → `~`. Handles both the running user's home and any
# generic /Users/<name> or /home/<name> that shows up in captured text.
_HOME_PATH_RES = (
    re.compile(r"/Users/[^/\s\"']+"),
    re.compile(r"/home/[^/\s\"']+"),
)


def scrub_text(value: str) -> str:
    """Redact secrets/PII from a single free-text string. Never raises.

    Applied to every string that survives the allowlist projection.
    """
    try:
        text = str(value)
    except Exception:
        return ""
    try:
        for token_re in _TOKEN_RES:
            text = token_re.sub("[redacted-token]", text)
        text = _EMAIL_RE.sub("[redacted-email]", text)
        for home_re in _HOME_PATH_RES:
            text = home_re.sub("~", text)
        # Also collapse the *running* user's literal home path (covers odd usernames).
        try:
            home = str(Path.home())
            if home and home not in ("/", ""):
                text = text.replace(home, "~")
        except Exception:
            pass
        if len(text) > _MAX_TEXT_CHARS:
            text = text[:_MAX_TEXT_CHARS].rstrip() + "…"
        return text
    except Exception:
        # Fail closed: on any unexpected error, drop the content rather than leak it.
        return "[redacted]"


def _scrub_value(value: Any) -> Any:
    """Recursively project + scrub a value against the allowlist."""
    if isinstance(value, dict):
        return _scrub_dict(value)
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    # Unknown scalar type → stringify + scrub (fail safe, never leak raw objects).
    return scrub_text(str(value))


def _scrub_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only allowlisted keys; recurse into their values."""
    out: Dict[str, Any] = {}
    for k, v in payload.items():
        if not isinstance(k, str) or k not in _ALLOWED_KEYS:
            continue  # default-drop
        scrubbed = _scrub_value(v)
        # Special-case: bound the number of query samples.
        if k == "samples" and isinstance(scrubbed, list):
            scrubbed = scrubbed[:_MAX_SAMPLES]
        out[k] = scrubbed
    return out


def scrub(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return an allowlist-projected, line-scrubbed copy of `payload`. Never raises.

    Non-dict input → `{}`. Every surviving string is redacted for emails, secret
    tokens, and home paths; query samples are capped.
    """
    try:
        if not isinstance(payload, dict):
            return {}
        return _scrub_dict(payload)
    except Exception:
        return {}
