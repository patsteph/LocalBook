"""CI-pure tests for the GitHub incident emitter (Quality-Signals Phase 2, slice 2b).

Safety contract proven here:
  - NO real GitHub call: the `gh`/subprocess seam AND the Keychain-PAT/httpx seam are
    monkeypatched in every test that touches the send path. A guard test asserts that with
    the default-OFF flag OR without confirm, the send path is NEVER even reached.
  - Idempotency: an already-filed incident_id is skipped.
  - Preview never sends (no auth/subprocess seam is invoked).
  - Verbatim: the issue body carries the already-scrubbed queued bytes (no re-scrub).
  - Never-raises on a broken queue line.

Run:  cd backend && python -m pytest tests/test_github_incident.py -q
"""
import json

import pytest

import services.github_incident as ghi


# ── Fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture
def emitter(monkeypatch, tmp_path):
    """A fresh-ish emitter whose signals dir is a tmp path (no production data).

    We repoint the 2a sink's `signals_dir` so `_queue_path`, `_filed_path`, and
    `_config_path` all resolve under tmp. Also hard-disable any settings override.
    """
    from services.quality_signals import quality_signals
    monkeypatch.setattr(quality_signals, "signals_dir", tmp_path, raising=False)
    # NOTE: `settings` (pydantic BaseSettings) has no `incidents_enabled` field, so
    # `getattr(settings, "incidents_enabled", None)` returns None and the default-OFF
    # state falls to the local config file (absent under tmp ⇒ disabled). Nothing to stub.
    em = ghi.GithubIncidentEmitter()
    return em


def _queue_incident(tmp_path, incident, queued_at="2026-07-31T00:00:00"):
    """Append one 2a-shaped queue line."""
    q = tmp_path / "incident_queue.jsonl"
    with open(q, "a") as f:
        f.write(json.dumps({"queued_at": queued_at, "incident": incident}) + "\n")


SCRUBBED_INCIDENT = {
    "schema_version": 1,
    "incident_id": "inc_abc1234567",
    "title": "misroute in intent_classifier [cross_notebook_search]",
    "created_at": "2026-07-31T00:00:00",
    "signal": {
        "type": "misroute",
        "component": "intent_classifier",
        "key": "cross_notebook_search",
        "severity": "notable",
        "detail": "low confidence 0.31",
        "count": 6,
        "first_seen": "2026-07-29T10:00:00",
        "last_seen": "2026-07-31T09:00:00",
    },
    "environment": {"app_version": "2.1.1", "arch": "arm64", "platform": "Darwin"},
    "metrics": {"count": 6, "distinct_days": 3, "window_days": 7},
    "promotion": {"eligible": True, "min_count": 5, "min_days": 2, "reason": "ok"},
    "samples": ["what did we say about topic X"],
}


def _kill_network(monkeypatch, em):
    """Blow up if ANY real send seam is reached. Attached in guard tests."""
    def _boom(*a, **k):
        raise AssertionError("real network/gh seam was reached")
    monkeypatch.setattr(em, "_gh_auth_ok", _boom)
    monkeypatch.setattr(em, "_gh_create_issue", _boom)
    monkeypatch.setattr(em, "_pat_token", _boom)
    monkeypatch.setattr(em, "_pat_create_issue", _boom)


# ── Preview never sends ──────────────────────────────────────────────────────
def test_preview_returns_verbatim_scrubbed_body_and_never_sends(emitter, tmp_path, monkeypatch):
    _kill_network(monkeypatch, emitter)  # preview must not touch any send seam
    _queue_incident(tmp_path, SCRUBBED_INCIDENT)

    previews = emitter.preview_incidents()
    assert len(previews) == 1
    p = previews[0]
    assert p["incident_id"] == "inc_abc1234567"
    assert "inc_abc1234567" in p["title"]
    # Verbatim scrubbed content carried into the body …
    assert "low confidence 0.31" in p["body"]
    assert "what did we say about topic X" in p["body"]
    assert "arm64" in p["body"]
    # … plus the dedup marker for the GitHub-layer idempotency.
    assert "<!-- lb-incident: inc_abc1234567 -->" in p["body"]
    assert p["already_filed"] is False


# ── Default-OFF + confirm gating (no send seam reachable) ─────────────────────
def test_file_refused_when_disabled(emitter, tmp_path, monkeypatch):
    _kill_network(monkeypatch, emitter)
    _queue_incident(tmp_path, SCRUBBED_INCIDENT)
    # default: disabled
    assert emitter.is_enabled() is False
    res = emitter.file_incidents(confirm=True)
    assert res["ok"] is False
    assert res["enabled"] is False
    assert res["refused"]
    assert res["sent"] == []


def test_file_refused_when_enabled_but_not_confirmed(emitter, tmp_path, monkeypatch):
    _kill_network(monkeypatch, emitter)
    _queue_incident(tmp_path, SCRUBBED_INCIDENT)
    emitter.set_enabled(True)
    assert emitter.is_enabled() is True
    res = emitter.file_incidents(confirm=False)
    assert res["ok"] is False
    assert res["confirmed"] is False
    assert res["refused"]
    assert res["sent"] == []


# ── Happy path: send with mocked gh seam ─────────────────────────────────────
def test_file_sends_via_mocked_gh(emitter, tmp_path, monkeypatch):
    _queue_incident(tmp_path, SCRUBBED_INCIDENT)
    emitter.set_enabled(True)

    calls = {}

    def _fake_auth_ok():
        return True

    def _fake_create(title, body):
        calls["title"] = title
        calls["body"] = body
        return {"ok": True, "issue": "https://github.com/patsteph/LocalBook/issues/42"}

    # PAT seam must never be reached when gh succeeds.
    def _pat_boom(*a, **k):
        raise AssertionError("PAT path reached though gh succeeded")

    monkeypatch.setattr(emitter, "_gh_auth_ok", _fake_auth_ok)
    monkeypatch.setattr(emitter, "_gh_create_issue", _fake_create)
    monkeypatch.setattr(emitter, "_pat_token", _pat_boom)

    res = emitter.file_incidents(confirm=True)
    assert res["ok"] is True
    assert len(res["sent"]) == 1
    assert res["sent"][0]["incident_id"] == "inc_abc1234567"
    assert res["sent"][0]["method"] == "gh"
    assert res["sent"][0]["issue"].endswith("/42")
    # verbatim scrubbed body was what got sent
    assert "low confidence 0.31" in calls["body"]
    # filed-map persisted
    filed = json.loads((tmp_path / "incident_filed.json").read_text())
    assert "inc_abc1234567" in filed


# ── PAT fallback when gh is unavailable ──────────────────────────────────────
def test_file_falls_back_to_pat(emitter, tmp_path, monkeypatch):
    _queue_incident(tmp_path, SCRUBBED_INCIDENT)
    emitter.set_enabled(True)

    monkeypatch.setattr(emitter, "_gh_auth_ok", lambda: False)
    monkeypatch.setattr(emitter, "_pat_token", lambda: "ghp_faketoken")

    captured = {}

    def _fake_pat_create(token, title, body):
        captured["token"] = token
        return {"ok": True, "issue": "https://github.com/patsteph/LocalBook/issues/7"}

    monkeypatch.setattr(emitter, "_pat_create_issue", _fake_pat_create)

    res = emitter.file_incidents(confirm=True)
    assert res["ok"] is True
    assert res["sent"][0]["method"] == "pat"
    assert captured["token"] == "ghp_faketoken"


def test_file_reports_failure_when_no_auth_provider(emitter, tmp_path, monkeypatch):
    _queue_incident(tmp_path, SCRUBBED_INCIDENT)
    emitter.set_enabled(True)
    monkeypatch.setattr(emitter, "_gh_auth_ok", lambda: False)
    monkeypatch.setattr(emitter, "_pat_token", lambda: None)

    res = emitter.file_incidents(confirm=True)
    assert res["ok"] is False
    assert len(res["failed"]) == 1
    assert res["failed"][0]["incident_id"] == "inc_abc1234567"
    # nothing recorded as filed
    assert not (tmp_path / "incident_filed.json").exists()


# ── Idempotency: already-filed id is skipped ─────────────────────────────────
def test_already_filed_is_skipped(emitter, tmp_path, monkeypatch):
    _queue_incident(tmp_path, SCRUBBED_INCIDENT)
    emitter.set_enabled(True)
    # Pre-seed the filed map.
    (tmp_path / "incident_filed.json").write_text(
        json.dumps({"inc_abc1234567": {"issue": "old", "sent_at": "x", "method": "gh"}})
    )

    # If the send seam is touched, we double-file — so make it explode.
    def _boom(*a, **k):
        raise AssertionError("attempted to re-file an already-filed incident")

    monkeypatch.setattr(emitter, "_gh_auth_ok", _boom)
    monkeypatch.setattr(emitter, "_pat_token", _boom)

    res = emitter.file_incidents(confirm=True)
    assert res["ok"] is True
    assert res["sent"] == []
    assert len(res["skipped"]) == 1
    assert res["skipped"][0]["incident_id"] == "inc_abc1234567"


def test_second_file_is_noop_after_first(emitter, tmp_path, monkeypatch):
    _queue_incident(tmp_path, SCRUBBED_INCIDENT)
    emitter.set_enabled(True)

    n = {"created": 0}

    def _create(title, body):
        n["created"] += 1
        return {"ok": True, "issue": "https://github.com/patsteph/LocalBook/issues/1"}

    monkeypatch.setattr(emitter, "_gh_auth_ok", lambda: True)
    monkeypatch.setattr(emitter, "_gh_create_issue", _create)
    monkeypatch.setattr(emitter, "_pat_token", lambda: None)

    r1 = emitter.file_incidents(confirm=True)
    r2 = emitter.file_incidents(confirm=True)
    assert n["created"] == 1                     # only one real create across two calls
    assert len(r1["sent"]) == 1
    assert r2["sent"] == []
    assert len(r2["skipped"]) == 1


# ── Robustness: a broken queue line never breaks the read ────────────────────
def test_broken_queue_line_never_raises(emitter, tmp_path, monkeypatch):
    q = tmp_path / "incident_queue.jsonl"
    q.write_text(
        "this is not json\n"
        + json.dumps({"queued_at": "t", "incident": SCRUBBED_INCIDENT}) + "\n"
        + json.dumps({"queued_at": "t", "no_incident_key": True}) + "\n"
    )
    previews = emitter.preview_incidents()
    assert len(previews) == 1          # only the one valid incident survives
    assert previews[0]["incident_id"] == "inc_abc1234567"


def test_missing_queue_is_empty_not_error(emitter, tmp_path):
    # No queue file at all.
    assert emitter.preview_incidents() == []
    res = emitter.file_incidents(confirm=True)  # disabled → refused, still no raise
    assert res["sent"] == []


# ── Later duplicate queue line refreshes payload (same id, one preview) ──────
def test_duplicate_queue_lines_collapse_to_latest(emitter, tmp_path):
    _queue_incident(tmp_path, SCRUBBED_INCIDENT)
    refreshed = dict(SCRUBBED_INCIDENT)
    refreshed = json.loads(json.dumps(SCRUBBED_INCIDENT))  # deep copy
    refreshed["signal"]["detail"] = "low confidence 0.10"
    _queue_incident(tmp_path, refreshed)

    previews = emitter.preview_incidents()
    assert len(previews) == 1
    assert "low confidence 0.10" in previews[0]["body"]


# ── Enable flag round-trips through the local config file ────────────────────
def test_enabled_flag_defaults_off_and_round_trips(emitter, tmp_path):
    assert emitter.is_enabled() is False
    emitter.set_enabled(True)
    assert emitter.is_enabled() is True
    cfg = json.loads((tmp_path / "github_incident_config.json").read_text())
    assert cfg["enabled"] is True
    emitter.set_enabled(False)
    assert emitter.is_enabled() is False
