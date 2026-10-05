"""LB-11's test matrix (plan §5) — the failure rows that run without the app.

Each row is a way a real user's Mac goes wrong. Every one must end with the data
recoverable and the app either working or honestly LOCKED — never open and empty.
Real encrypted volumes throughout.
"""

import errno
import hashlib
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from services import encryption_migration as em
from services import keyvault, volume_gate, volume_service

from tests.test_encryption_migration import env  # noqa: F401

PHRASE = None


@pytest.fixture
def encrypted(env):  # noqa: F811
    """Encrypted with a recovery phrase this test knows."""
    global PHRASE
    PHRASE = keyvault.generate_recovery_phrase()
    keyvault.set_recovery_key(PHRASE)
    em.prepare()
    assert em.apply_pending()["applied"] is True
    return env


def _rows(db: Path) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    finally:
        conn.close()


def _next_launch(env):
    """What a restart after a reboot looks like: the volume is not attached."""
    if volume_service.is_mounted(env):
        volume_service.detach()
    return volume_gate.evaluate()


# ── Keychain reset → recover with the phrase ────────────────────────────────


def test_a_wiped_keychain_locks_and_the_phrase_gets_back_in(encrypted):
    keyvault._keychain_delete("volume")
    gate = _next_launch(encrypted)

    assert gate.locked is True
    assert "Keychain" in gate.reason

    keyvault.restore_from_phrase(PHRASE, "volume")      # what /system/volume/recover does
    assert volume_gate.unlock().locked is False
    assert _rows(encrypted / "localbook.db") == 25


def test_a_wrong_phrase_changes_nothing(encrypted):
    keyvault._keychain_delete("volume")
    _next_launch(encrypted)
    with pytest.raises(keyvault.KeyVaultError):
        keyvault.restore_from_phrase(keyvault.generate_recovery_phrase(), "volume")
    assert volume_gate.current().locked is True
    assert volume_service.image_path().exists()


# ── wrong key in the Keychain ───────────────────────────────────────────────


def test_a_wrong_key_locks_rather_than_opening_empty(encrypted):
    keyvault._keychain_write("volume", os.urandom(keyvault.KEY_BYTES))
    _next_launch(encrypted)

    gate = volume_gate.unlock()
    assert gate.locked is True
    assert not (encrypted / "localbook.db").exists()     # nothing written into the mount point

    keyvault.restore_from_phrase(PHRASE, "volume")
    assert volume_gate.unlock().locked is False
    assert _rows(encrypted / "localbook.db") == 25


# ── volume absent → locked, not empty ───────────────────────────────────────


def test_a_missing_image_locks_and_says_the_data_is_not_lost(encrypted, tmp_path):
    _next_launch(encrypted)
    volume_service.image_path().rename(tmp_path / "elsewhere.sparsebundle")

    gate = volume_gate.evaluate()
    assert gate.locked is True
    assert "not lost" in gate.detail


# ── disk full during the copy ───────────────────────────────────────────────


def test_disk_full_mid_copy_stages_nothing_and_touches_nothing(env, monkeypatch):  # noqa: F811
    real_copy = em.shutil.copy2

    def full(src, dst, *a, **k):
        if str(src).endswith("nb1.lance"):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(em.shutil, "copy2", full)
    report = em.prepare()

    assert report.ok is False
    assert any("No space" in e for e in report.errors)
    assert em.pending() is None
    assert not os.path.ismount(env.parent / em.STAGING_MOUNT)
    assert _rows(env / "localbook.db") == 25


# ── kill -9 during prepare ──────────────────────────────────────────────────


def test_kill_9_mid_prepare_leaves_the_plaintext_and_a_retry_works(env):  # noqa: F811
    """A real SIGKILL, in a real process, with the volume attached at the staging
    point — the state a force-quit mid-copy leaves behind."""
    script = (
        "import sys, time\n"
        "from services import keyvault, encryption_migration as em\n"
        "keyvault.SERVICE_NAME = sys.argv[1]\n"
        "real = em._sync_into\n"
        "def slow(*a, **k):\n"
        "    print('COPYING', flush=True); time.sleep(60); return real(*a, **k)\n"
        "em._sync_into = slow\n"
        "em.prepare(skip_backup=True)\n"
    )
    child_env = dict(os.environ, LOCALBOOK_DATA_DIR=str(env))
    proc = subprocess.Popen([sys.executable, "-c", script, keyvault.SERVICE_NAME],
                            env=child_env, cwd=str(Path(__file__).parents[1]),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    staging = env.parent / em.STAGING_MOUNT
    try:
        for line in proc.stdout:
            if "COPYING" in line:
                break
        assert os.path.ismount(staging), "the child should be mid-copy with the volume attached"
        proc.send_signal(signal.SIGKILL)
        proc.wait(10)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert em.pending() is None
    assert _rows(env / "localbook.db") == 25
    assert os.path.ismount(staging)                     # the leak a real quit leaves

    report = em.prepare()                               # the user tries again
    assert report.ok is True, report.errors
    assert em.apply_pending()["applied"] is True
    assert _rows(env / "localbook.db") == 25


# ── force-eject while running ───────────────────────────────────────────────


def test_a_force_eject_mid_session_locks_the_app(encrypted):
    assert volume_gate.evaluate().locked is False
    subprocess.run(["hdiutil", "detach", "-force", str(encrypted)], check=True,
                   capture_output=True)

    gate = volume_gate.check_still_mounted()
    assert gate.locked is True
    assert "disconnected" in gate.reason
    assert volume_gate.path_is_allowed("/notebooks/") is False     # data routes refused


def test_the_watch_does_nothing_while_the_volume_is_there(encrypted):
    volume_gate.evaluate()
    assert volume_gate.check_still_mounted().locked is False


def test_the_watch_does_nothing_when_encryption_is_off(env):  # noqa: F811
    volume_gate.evaluate()
    assert volume_gate.check_still_mounted().locked is False


def test_writes_made_after_an_eject_are_kept_and_the_volume_still_mounts(encrypted):
    """A background writer that kept going after the eject leaves files at the
    bare mount point. They are moved aside and kept — never mounted over, never
    deleted — and the recovery screen's unlock works."""
    subprocess.run(["hdiutil", "detach", "-force", str(encrypted)], check=True,
                   capture_output=True)
    volume_gate.check_still_mounted()
    (encrypted / "stray.json").write_text('{"written": "after the eject"}')

    gate = volume_gate.unlock()

    assert gate.locked is False
    assert _rows(encrypted / "localbook.db") == 25
    kept = list(encrypted.parent.glob(f"{encrypted.name}.unmounted-writes-*"))
    assert len(kept) == 1 and (kept[0] / "stray.json").is_file()
