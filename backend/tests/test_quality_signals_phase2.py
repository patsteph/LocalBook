"""Model-free tests for Quality Signals Phase 2 slice 2a (incident triage plumbing).

None of these touch the network, GitHub, or production data — the escalate test
monkeypatches the sink's `signals_dir` to a tmp path so NO production write occurs.

Run:  cd backend && python -m pytest tests/test_quality_signals_phase2.py -q
"""
import json
from pathlib import Path

import pytest

from services.app_version import get_app_version_info
from services.incident_scrubber import scrub, scrub_text
from services.incident_context import build_incident_context
import services.quality_signals as qs


# ── Scrubber: allowlist default-drop ─────────────────────────────────────────
def test_scrub_drops_non_allowlisted_keys():
    payload = {
        "type": "misroute",           # allowed
        "component": "intent_classifier",  # allowed
        "notebook_id": "nb_secret123",     # NOT allowed → must drop
        "source_path": "/Users/alice/docs/private.pdf",  # NOT allowed → drop
        "email": "alice@example.com",       # NOT allowed → drop
        "api_key": "ghp_abcdefghijklmnop",  # NOT allowed → drop
    }
    out = scrub(payload)
    assert out["type"] == "misroute"
    assert out["component"] == "intent_classifier"
    for leaked in ("notebook_id", "source_path", "email", "api_key"):
        assert leaked not in out, f"{leaked} leaked through the allowlist"


def test_scrub_recurses_and_drops_nested_keys():
    payload = {
        "signal": {
            "type": "empty",
            "notebook_id": "nb_leak",   # nested non-allowlisted → drop
            "detail": "0 chunks",
        }
    }
    out = scrub(payload)
    assert "notebook_id" not in out["signal"]
    assert out["signal"]["type"] == "empty"
    assert out["signal"]["detail"] == "0 chunks"


def test_scrub_is_total_on_bad_input():
    assert scrub(None) == {}
    assert scrub("not a dict") == {}
    assert scrub(42) == {}


def test_scrub_text_redacts_secrets_paths_emails():
    raw = (
        "user alice@example.com token ghp_ABCDEFGHIJKLMNOP "
        "pat github_pat_11ABCDEFGH0000 header Bearer abcdef123456 "
        "openai sk-abcdef1234567890 file /Users/alice/notes.txt"
    )
    out = scrub_text(raw)
    assert "alice@example.com" not in out
    assert "[redacted-email]" in out
    assert "ghp_ABCDEFGHIJKLMNOP" not in out
    assert "github_pat_11ABCDEFGH0000" not in out
    assert "sk-abcdef1234567890" not in out
    assert "/Users/alice" not in out
    assert "[redacted-token]" in out
    assert "~" in out


def test_scrub_caps_samples_and_length():
    payload = {"samples": ["a" * 500, "b", "c", "d", "e"]}
    out = scrub(payload)
    assert len(out["samples"]) == 3          # capped to _MAX_SAMPLES
    assert len(out["samples"][0]) <= 241      # length-capped + ellipsis


# ── build_incident_context shape ─────────────────────────────────────────────
def test_build_incident_context_shape():
    group = {
        "type": "fallback",
        "component": "llm_service",
        "key": "mlx->ollama",
        "severity": "warn",
        "detail": "MLX failed, fell to Ollama",
        "count": 6,
        "first_seen": "2026-07-29T10:00:00",
        "last_seen": "2026-07-31T09:00:00",
        "samples": ["summarize /Users/bob/x.pdf for me"],
    }
    fake_env = {
        "app_version": "2.1.1",
        "git_sha": "abc1234",
        "platform": "Darwin",
        "platform_release": "14.5",
        "arch": "arm64",
        "python_version": "3.13.1",
    }
    inc = build_incident_context(
        group,
        version_info=fake_env,
        metrics={"count": 6, "distinct_days": 3, "window_days": 7},
        promotion={"eligible": True, "min_count": 5, "min_days": 2, "reason": "ok"},
    )
    assert inc["schema_version"] == 1
    assert inc["incident_id"].startswith("inc_")
    assert inc["signal"]["type"] == "fallback"
    assert inc["signal"]["component"] == "llm_service"
    assert inc["environment"]["app_version"] == "2.1.1"
    assert inc["environment"]["arch"] == "arm64"
    assert inc["metrics"]["distinct_days"] == 3
    assert inc["promotion"]["eligible"] is True
    # sample text was scrubbed of the home path
    assert "/Users/bob" not in inc["samples"][0]
    # no forbidden keys anywhere in the payload
    assert "notebook_id" not in json.dumps(inc)


def test_build_incident_context_never_raises_on_junk():
    # Missing/degenerate signal must still yield a scrubbed scaffold, not raise.
    inc = build_incident_context({})
    assert inc["schema_version"] == 1
    assert "environment" in inc


def test_incident_id_is_stable():
    g = {"type": "empty", "component": "rag_engine", "key": "no_chunks"}
    a = build_incident_context(g, version_info={})
    b = build_incident_context(g, version_info={})
    assert a["incident_id"] == b["incident_id"]


# ── promotion_verdict threshold math (Decision #6) ───────────────────────────
def test_promotion_verdict_thresholds():
    # eligible: count≥5, days≥2, severity≥notable
    v = qs.promotion_verdict(5, 2, "notable")
    assert v["eligible"] is True
    # single burst (1 day) rejected
    assert qs.promotion_verdict(9, 1, "warn")["eligible"] is False
    # too few occurrences rejected
    assert qs.promotion_verdict(4, 3, "warn")["eligible"] is False
    # info severity rejected even if count/days pass
    assert qs.promotion_verdict(10, 5, "info")["eligible"] is False
    v = qs.promotion_verdict(0, 0)
    assert v["min_count"] == qs.PROMOTE_MIN_COUNT
    assert v["min_days"] == qs.PROMOTE_MIN_DAYS


# ── escalate_to_incident persists locally + never raises (NO prod write) ─────
def test_escalate_persists_to_tmp(monkeypatch, tmp_path):
    # Redirect the sink's ledger dir so nothing hits production data.
    monkeypatch.setattr(qs.quality_signals, "signals_dir", tmp_path)

    group = {
        "type": "misroute",
        "component": "intent_classifier",
        "key": "cross_notebook_search",
        "severity": "notable",
        "detail": "low confidence 0.31",
        "count": 6,
        "samples": ["email me at bob@example.com about this"],
    }
    inc = qs.escalate_to_incident(
        group,
        metrics={"count": 6, "distinct_days": 3, "window_days": 7},
        version_info={"app_version": "2.1.1", "arch": "arm64"},
    )
    assert inc is not None
    assert inc["incident_id"].startswith("inc_")

    queue = tmp_path / "incident_queue.jsonl"
    assert queue.exists(), "incident was not queued to jsonl"
    lines = queue.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert "queued_at" in rec
    assert rec["incident"]["signal"]["component"] == "intent_classifier"
    # promotion verdict auto-derived from metrics
    assert rec["incident"]["promotion"]["eligible"] is True
    # scrubbed: no email leaked into the queued sample
    assert "bob@example.com" not in queue.read_text()


def test_escalate_from_signal_key_and_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(qs.quality_signals, "signals_dir", tmp_path)
    inc = qs.escalate_to_incident(signal_key="fallback|llm_service|mlx")
    assert inc is not None
    assert inc["signal"]["type"] == "fallback"
    assert inc["signal"]["component"] == "llm_service"


def test_escalate_never_raises_on_bad_dir(monkeypatch):
    # Point the sink at an unwritable path — escalate must return None, not raise.
    monkeypatch.setattr(qs.quality_signals, "signals_dir", Path("/nonexistent/deeply/nope"))
    result = qs.escalate_to_incident({"type": "empty", "component": "rag_engine"})
    assert result is None


# ── get_app_version_info shape ───────────────────────────────────────────────
def test_get_app_version_info_shape():
    info = get_app_version_info()
    for k in (
        "app_version",
        "git_sha",
        "platform",
        "platform_release",
        "arch",
        "python_version",
    ):
        assert k in info, f"missing key {k}"
    assert isinstance(info["app_version"], str)
    # git_sha is str-or-None
    assert info["git_sha"] is None or isinstance(info["git_sha"], str)
    assert isinstance(info["platform"], str)
