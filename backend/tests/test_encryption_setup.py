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


# ── the startup prompt ──────────────────────────────────────────────────────


@pytest.fixture
def plain(env, monkeypatch):
    from services import volume_gate, volume_service

    monkeypatch.setattr(volume_gate, "encryption_enabled", lambda: False)
    monkeypatch.setattr(volume_service, "is_mounted", lambda mp=None: False)
    return env


def test_the_offer_shows_on_an_unencrypted_mac(plain):
    assert encryption_setup.prompt() == {"show": True, "kind": "offer"}


def test_not_now_snoozes_and_dont_ask_again_dismisses(plain):
    assert encryption_setup.respond_to_prompt("snooze")["show"] is False
    encryption_setup._prompt_path().unlink()
    assert encryption_setup.respond_to_prompt("dismiss")["show"] is False


def test_a_snooze_expires(plain):
    encryption_setup._save_prompt_state({"snoozed_until": "2000-01-01T00:00:00+00:00"})
    assert encryption_setup.prompt()["kind"] == "offer"


def test_the_prompt_state_lives_beside_the_data_dir_not_in_it(plain):
    """Inside, LB-12 would sync one Mac's dismissal to the others."""
    encryption_setup.respond_to_prompt("dismiss")
    assert encryption_setup._prompt_path().parent == plain.parent


def test_quiet_while_a_migration_is_staged(plain, monkeypatch):
    monkeypatch.setattr(em, "pending", lambda: {"prepared_at": "x"})
    assert encryption_setup.prompt()["show"] is False


def test_a_failed_switch_is_reported_until_acknowledged(plain, monkeypatch):
    """The user pressed Restart expecting encryption. Saying nothing is not okay."""
    monkeypatch.setattr(em, "last_apply",
                        lambda: {"applied": False, "error": "attach exploded", "at": "t1"})
    encryption_setup.respond_to_prompt("dismiss")        # dismissing the OFFER…
    p = encryption_setup.prompt()
    assert p["kind"] == "failed" and p["error"] == "attach exploded"   # …does not hide this
    assert encryption_setup.respond_to_prompt("acknowledge_failure")["show"] is False


def test_finish_nags_while_the_plaintext_copy_remains(env, monkeypatch):
    from services import volume_gate, volume_service

    monkeypatch.setattr(volume_gate, "encryption_enabled", lambda: True)
    monkeypatch.setattr(volume_service, "is_mounted", lambda mp=None: True)
    monkeypatch.setattr(em, "plaintext_copies", lambda: [{"bytes": 100}])
    p = encryption_setup.prompt()
    assert (p["show"], p["kind"], p["bytes"]) == (True, "finish", 100)
    encryption_setup.respond_to_prompt("dismiss")        # cannot be dismissed away
    assert encryption_setup.prompt()["kind"] == "finish"

    monkeypatch.setattr(em, "plaintext_copies", lambda: [])
    assert encryption_setup.prompt()["show"] is False


def test_finish_carries_the_check_for_the_kept_copy(env, monkeypatch):
    """The wizard offers one-click removal on the strength of this."""
    from services import volume_gate, volume_service

    monkeypatch.setattr(volume_gate, "encryption_enabled", lambda: True)
    monkeypatch.setattr(volume_service, "is_mounted", lambda mp=None: True)
    monkeypatch.setattr(em, "plaintext_copies", lambda: [{"path": "/x/copy", "bytes": 1}])
    monkeypatch.setattr(em, "last_apply", lambda: {"applied": True,
                        "plaintext_kept_at": "/x/copy", "verified": {"ok": True}})
    assert encryption_setup.prompt()["verified"] == {"ok": True}

    monkeypatch.setattr(em, "last_apply", lambda: {"applied": True,
                        "plaintext_kept_at": "/x/other", "verified": {"ok": True}})
    assert encryption_setup.prompt()["verified"] is None


# ── the default backup folder ───────────────────────────────────────────────


def test_a_default_backup_folder_is_proposed_not_saved(env, monkeypatch, tmp_path):
    from config import settings

    monkeypatch.setattr(settings, "backup_destination", "")
    monkeypatch.setattr(encryption_setup.Path, "home", lambda: tmp_path / "home")
    check = encryption_setup.preflight()["checks"]["backup_destination"]
    assert check["ok"] is False                      # still needs the user's OK
    assert check["proposed_path"] == str(tmp_path / "home" / "LocalBook Backups")
    assert not (tmp_path / "home").exists()          # nothing written


def test_no_proposal_once_one_is_configured(env):
    assert encryption_setup.preflight()["checks"]["backup_destination"]["proposed_path"] is None


@pytest.mark.parametrize("inside", ["data", "image", "copy"])
def test_a_backup_folder_inside_what_it_backs_up_is_refused(env, monkeypatch, inside):
    target = {
        "data": env / "backups",
        "image": env.parent / f"{env.name}.sparsebundle" / "x",
        "copy": env.parent / "LocalBook.plaintext-1" / "b",
    }[inside]
    monkeypatch.setattr(em, "plaintext_copies",
                        lambda: [{"path": str(env.parent / "LocalBook.plaintext-1")}])
    assert encryption_setup.backup_destination_problem(target)
    assert encryption_setup.backup_destination_problem(env.parent / "elsewhere") is None


def test_the_proposal_is_dropped_if_home_would_be_inside_the_data(env, monkeypatch):
    monkeypatch.setattr(encryption_setup.Path, "home", lambda: env)
    assert encryption_setup.proposed_backup_destination() is None


# ── saving it: /settings/backup-destination {create} ────────────────────────


def _save(monkeypatch, path, create):
    import asyncio

    from api import settings as settings_api

    written = {}
    monkeypatch.setattr(settings_api, "_write_env", lambda k, v: written.update({k: v}))
    req = settings_api.BackupDestinationRequest(path=str(path), create=create)
    return asyncio.run(settings_api.set_backup_destination(req)), written


def test_create_makes_the_accepted_default(env, monkeypatch, tmp_path):
    folder = tmp_path / "home" / "LocalBook Backups"
    out, written = _save(monkeypatch, folder, create=True)
    assert folder.is_dir() and out["destination"] == str(folder)
    assert written == {"LOCALBOOK_BACKUP_DESTINATION": str(folder)}


def test_without_create_a_missing_folder_is_still_refused(env, monkeypatch, tmp_path):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _save(monkeypatch, tmp_path / "nope", create=False)
    assert not (tmp_path / "nope").exists()


def test_create_never_makes_a_folder_inside_the_data(env, monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _save(monkeypatch, env / "backups", create=True)
    assert not (env / "backups").exists()
