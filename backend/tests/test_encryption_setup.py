"""LB-11: the setup screen's pre-flight and background job."""

import time

import pytest

from services import encryption_migration as em
from services import encryption_setup


@pytest.fixture
def env(tmp_path, monkeypatch):
    from config import settings

    data = tmp_path / "LocalBook"
    data.mkdir()
    (data / "a.txt").write_text("x" * 1000)
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "backup_destination", str(tmp_path))
    from services import keyvault

    monkeypatch.setattr(keyvault, "has_recovery_key", lambda: True)
    monkeypatch.setattr(encryption_setup, "_job", None)
    return data


def test_preflight_names_each_check(env):
    out = encryption_setup.preflight()
    assert set(out["checks"]) == {
        "not_already_encrypted", "nothing_staged", "recovery_phrase",
        "backup_destination", "free_space", "not_running",
    }
    assert out["ready"] is True
    assert out["data_bytes"] >= 1000


def test_preflight_blocks_without_a_recovery_phrase(env, monkeypatch):
    from services import keyvault

    monkeypatch.setattr(keyvault, "has_recovery_key", lambda: False)
    out = encryption_setup.preflight()
    assert out["ready"] is False
    assert out["checks"]["recovery_phrase"]["ok"] is False


def test_preflight_blocks_on_too_little_space(env, monkeypatch):
    monkeypatch.setattr(encryption_setup, "FREE_SPACE_HEADROOM_BYTES", 10 ** 18)
    assert encryption_setup.preflight()["checks"]["free_space"]["ok"] is False


def test_the_job_runs_in_the_background_and_reports(env, monkeypatch):
    def fake_prepare(*, skip_backup, report):
        report.stage = "copying"
        report.bytes_total = 10
        time.sleep(0.2)
        report.ok = True
        report.stage = "staged"
        return report

    monkeypatch.setattr(em, "prepare", fake_prepare)
    assert encryption_setup.start_prepare_job() is True
    assert encryption_setup.start_prepare_job() is False     # one at a time
    assert encryption_setup.job_status()["running"] is True

    for _ in range(50):
        if not encryption_setup.job_status()["running"]:
            break
        time.sleep(0.05)
    status = encryption_setup.job_status()
    assert status["running"] is False
    assert status["report"]["ok"] is True
    assert status["report"]["stage"] == "staged"


def test_a_crashing_job_reports_instead_of_vanishing(env, monkeypatch):
    def boom(**_):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(em, "prepare", boom)
    encryption_setup.start_prepare_job()
    for _ in range(50):
        if not encryption_setup.job_status()["running"]:
            break
        time.sleep(0.05)
    report = encryption_setup.job_status()["report"]
    assert report["ok"] is False
    assert any("kaboom" in e for e in report["errors"])
