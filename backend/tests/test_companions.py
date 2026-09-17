"""Companions — install, connect, and the shared-engine endpoint.

A companion is a separate local tool LocalBook knows how to find, configure and
take input from. The first is Meeting Notes
(github.com/kvango/Meeting-Summarizer).

The point of the architecture is that everything tool-specific is DATA. These
tests therefore check the manifest contract rather than the one tool, so a
second companion is a new JSON file rather than a new test file.

The load-bearing behaviour is `write_config`: LocalBook edits a file it does not
own. It must change exactly the keys it declared and leave everything else —
the user's microphone choice, their email address, their comments — untouched.
"""
import json
from pathlib import Path

import pytest

from services import companions as svc


@pytest.fixture(autouse=True)
def _isolated_key(tmp_path, monkeypatch):
    """The companion key lives in data_dir; keep each test's key its own."""
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)


# ── the manifest contract ───────────────────────────────────────────────────

def test_every_shipped_manifest_is_valid():
    """A malformed manifest silently disappears from the UI. Fail here instead."""
    manifests = svc.load_manifests()
    assert manifests, "no companion manifests were loaded"
    for m in manifests:
        assert m.get("id") and m.get("name"), m
        assert m.get("homepage"), f"{m['id']} has no homepage — users must be able to read the source"
        if "configure" in m:
            cfg = m["configure"]
            assert cfg.get("file") and cfg.get("keys"), f"{m['id']} configure block is incomplete"
        if "produces" in m:
            assert m["produces"].get("dir"), f"{m['id']} produces no directory"


def test_the_meeting_notes_manifest_points_at_our_engine():
    m = svc.get_manifest("meeting-notes")
    assert m is not None
    keys = m["configure"]["keys"]
    assert keys["BASE_URL"] == "{localbook_openai_base}"
    assert keys["LLM"] == "{localbook_main_model}"
    assert keys["API_KEY"] == "{companion_key}"


def test_placeholders_resolve_to_real_values():
    from config import settings
    m = svc.get_manifest("meeting-notes")
    desired = svc.desired_config(m)
    assert desired["BASE_URL"].endswith("/v1")
    assert str(settings.api_port) in desired["BASE_URL"]
    assert desired["LLM"] == settings.main_model
    assert desired["API_KEY"].startswith("lb-")


# ── the companion key ───────────────────────────────────────────────────────

def test_the_key_is_stable_across_reads():
    """Unlike the app token, which rotates every launch — a companion holds a
    config file on disk, so a rotating secret would break it on every restart."""
    a = svc.get_companion_key()
    b = svc.get_companion_key()
    assert a and a == b


def test_the_key_file_is_not_world_readable(tmp_path):
    svc.get_companion_key()
    mode = (tmp_path / "companion_key").stat().st_mode & 0o777
    assert mode == 0o600, f"companion key is {oct(mode)}"


def test_only_the_real_key_verifies():
    key = svc.get_companion_key()
    assert svc.verify_companion_key(key) is True
    assert svc.verify_companion_key("lb-wrong") is False
    assert svc.verify_companion_key("") is False


def test_revoking_cuts_everything_off_at_once():
    svc.get_companion_key()
    svc.revoke_companion_key()
    assert svc.verify_companion_key("anything") is False


# ── editing a file we do not own ────────────────────────────────────────────

REAL_CONFIG = '''EMAIL="me@example.com"
MIC_DEVICE="Microphone"
MIC_FALLBACK_DEVICE="MacBook Pro Microphone"
SYSTEM_DEVICE="BlackHole 2ch"
OUTPUT_DEVICE="Meeting Output"
# --- LLM provider (any OpenAI-compatible server). Edit these to switch. ---
# llama.cpp :8080/v1 (default) | Ollama :11434/v1 | LM Studio :1234/v1
LLM="gemma4:e4b"
BASE_URL="http://localhost:8080/v1"
API_KEY="not-needed"
'''


@pytest.fixture
def fake_companion(tmp_path, monkeypatch):
    """A manifest pointed at a throwaway config, shaped exactly like the real one."""
    cfg = tmp_path / "config.sh"
    cfg.write_text(REAL_CONFIG)
    m = json.loads(json.dumps(svc.get_manifest("meeting-notes")))
    m["configure"]["file"] = str(cfg)
    m["detect"] = {"support_dir": str(tmp_path)}
    m["produces"]["dir"] = str(tmp_path / "out")
    return m, cfg


def test_connecting_rewrites_only_the_keys_we_declared(fake_companion):
    m, cfg = fake_companion
    assert svc.write_config(m)["ok"]
    after = cfg.read_text()

    # ours, changed
    assert 'BASE_URL="http://127.0.0.1:' in after
    assert 'API_KEY="lb-' in after
    # theirs, untouched — including the comments
    assert 'EMAIL="me@example.com"' in after
    assert 'SYSTEM_DEVICE="BlackHole 2ch"' in after
    assert "# --- LLM provider" in after
    assert "llama.cpp :8080/v1 (default)" in after


def test_the_original_config_is_backed_up_before_the_first_edit(fake_companion):
    m, cfg = fake_companion
    svc.write_config(m)
    backup = Path(str(cfg) + ".localbook-backup")
    assert backup.is_file()
    assert backup.read_text() == REAL_CONFIG


def test_the_backup_is_never_overwritten_by_a_later_connect(fake_companion):
    """Otherwise the second connect backs up our own edit, and the user's
    original settings are gone for good."""
    m, cfg = fake_companion
    svc.write_config(m)
    svc.write_config(m)
    backup = Path(str(cfg) + ".localbook-backup")
    assert backup.read_text() == REAL_CONFIG, "the backup was overwritten with our own edit"


def test_disconnecting_restores_what_was_there(fake_companion):
    m, cfg = fake_companion
    svc.write_config(m)
    assert cfg.read_text() != REAL_CONFIG
    assert svc.disconnect(m)["ok"]
    assert cfg.read_text() == REAL_CONFIG


def test_a_missing_key_is_appended_rather_than_lost(tmp_path, fake_companion):
    m, cfg = fake_companion
    cfg.write_text('EMAIL="x@y.com"\nMIC_DEVICE="Microphone"\n')
    assert svc.write_config(m)["ok"]
    after = cfg.read_text()
    assert 'API_KEY="lb-' in after
    assert 'EMAIL="x@y.com"' in after


def test_connecting_a_tool_with_no_config_file_fails_clearly(fake_companion):
    m, cfg = fake_companion
    cfg.unlink()
    result = svc.write_config(m)
    assert not result["ok"]
    assert "install" in result["error"].lower()


# ── state ───────────────────────────────────────────────────────────────────

def test_connected_means_its_config_actually_points_at_us(fake_companion):
    m, _ = fake_companion
    assert svc.is_connected(m) is False
    svc.write_config(m)
    assert svc.is_connected(m) is True


def test_a_tool_pointed_elsewhere_is_not_connected(fake_companion):
    """Connected is measured, not remembered — the user may have edited the
    config back to LM Studio by hand, and the card must tell the truth."""
    m, cfg = fake_companion
    svc.write_config(m)
    cfg.write_text(cfg.read_text().replace(
        svc.desired_config(m)["BASE_URL"], "http://localhost:1234/v1"))
    assert svc.is_connected(m) is False


def test_status_reports_one_word_for_the_card(fake_companion):
    m, _ = fake_companion
    assert svc.status(m)["state"] == "installed"
    svc.write_config(m)
    assert svc.status(m)["state"] == "connected"


def test_a_stale_pidfile_does_not_read_as_recording(fake_companion, tmp_path):
    m, _ = fake_companion
    pid = tmp_path / "recording.pid"
    pid.write_text("999999")        # a PID that cannot be alive
    m["control"]["state"]["pidfile"] = str(pid)
    assert svc.is_running(m) is False


def test_the_manifest_directory_ships_in_the_built_app():
    """PyInstaller only bundles data directories it is told about. Without an
    --add-data entry the manifests vanish and Settings → Companions is empty in
    the built app while working perfectly in dev — the same shape of bug that
    shipped image generation broken in v2.3.0.
    """
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    build = (backend / "build_backend.sh").read_text()
    assert "companions:companions" in build, (
        "companions/ is not --add-data'd in build_backend.sh — the manifests "
        "will not exist in the packaged app"
    )


def test_the_manifest_dir_is_resolved_relative_to_the_package():
    """It must not depend on the working directory: the packaged backend is
    launched by Tauri from somewhere else entirely."""
    import services.companions as c
    assert c._MANIFEST_DIR.is_absolute()
    assert c._MANIFEST_DIR.name == "companions"
