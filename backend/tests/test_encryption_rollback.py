"""LB-11's escape hatch: turning encryption off, and exporting a decrypted copy.

Real volumes, like the migration tests. The plan requires the result to open in
2.4.0 — an ordinary data directory — so the assertions open it as one.
"""

import sqlite3
from pathlib import Path

import pytest

from services import encryption_migration as em
from services import encryption_rollback as er
from services import volume_gate, volume_service

# The encrypted-volume fixture: a live WAL database, nested files, a recovery key.
from tests.test_encryption_migration import env  # noqa: F401


@pytest.fixture
def encrypted(env):  # noqa: F811
    em.prepare()
    assert em.apply_pending()["applied"] is True
    return env


def _rows(db: Path) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    finally:
        conn.close()


def test_turning_encryption_off_leaves_an_ordinary_data_dir(encrypted):
    from config import encryption_flag_path

    assert er.prepare().ok is True
    result = er.apply_pending()

    assert result["applied"] is True, result
    assert not volume_service.is_mounted(encrypted)
    assert not encryption_flag_path(encrypted).exists()
    assert volume_gate.encryption_enabled() is False
    assert volume_gate.evaluate().locked is False
    # Opens as a plain directory — what 2.4.0 expects.
    assert _rows(encrypted / "localbook.db") == 25
    assert (encrypted / "notebooks" / "nb1.json").is_file()
    assert not (encrypted / ".volume_id").exists()


def test_the_encrypted_image_is_kept(encrypted):
    er.prepare()
    er.apply_pending()
    assert volume_service.image_path().exists()
    assert er.leftover_image() is not None


def test_writes_after_prepare_are_carried_out(encrypted):
    er.prepare()
    conn = sqlite3.connect(encrypted / "localbook.db")
    conn.execute("INSERT INTO sources VALUES ('late', 'after decrypt prepare')")
    conn.commit()
    conn.close()

    assert er.apply_pending()["applied"] is True
    assert _rows(encrypted / "localbook.db") == 26


def test_nothing_changes_while_the_volume_is_not_open(encrypted):
    """The recent writes are inside the volume. Switching without them would
    drop them silently, so it waits for a launch where the volume opens."""
    er.prepare()
    volume_service.detach()

    result = er.apply_pending()
    assert result["applied"] is False
    assert er.pending() is not None          # tried again next launch


def test_a_failed_detach_leaves_the_app_encrypted(encrypted, monkeypatch):
    er.prepare()
    monkeypatch.setattr(em, "_detach", lambda mp: (_ for _ in ()).throw(RuntimeError("busy")))

    result = er.apply_pending()
    assert result["applied"] is False
    assert volume_service.is_mounted(encrypted)
    assert volume_gate.encryption_enabled() is True
    assert _rows(encrypted / "localbook.db") == 25


def test_a_staged_switch_can_be_abandoned(encrypted):
    report = er.prepare()
    staged = Path(er.pending()["staged"])
    assert staged.is_dir()

    assert er.cancel_pending() is True
    assert not staged.exists()
    assert er.apply_pending() is None
    assert volume_service.is_mounted(encrypted)
    assert report.ok


def test_prepare_refuses_when_not_encrypted(env):  # noqa: F811
    assert er.prepare().ok is False


def test_a_stale_env_flag_is_cleaned_so_it_cannot_relock(encrypted):
    """A pre-fix build wrote the flag into the data dir's .env."""
    (encrypted / ".env").write_text("OTHER=1\nLOCALBOOK_ENCRYPTION_ENABLED=true\n")
    er.prepare()
    er.apply_pending()
    assert (encrypted / ".env").read_text() == "OTHER=1\n"


# ── export only ─────────────────────────────────────────────────────────────


def test_export_is_a_verified_plaintext_copy_and_encryption_stays_on(encrypted, tmp_path):
    out = tmp_path / "exports"
    out.mkdir()

    report = er.export(out)
    assert report.ok is True, report.errors
    dest = Path(report.backup_path)
    assert _rows(dest / "localbook.db") == 25
    assert not (dest / ".volume_id").exists()
    assert volume_service.is_mounted(encrypted)
    assert er.pending() is None


def test_export_refuses_a_destination_inside_the_volume(encrypted):
    (encrypted / "inside").mkdir()
    assert er.export(encrypted / "inside").ok is False


# ── deleting the image ──────────────────────────────────────────────────────


def test_the_image_cannot_be_deleted_while_encryption_is_on(encrypted):
    result = er.discard_image()
    assert result["deleted"] is False
    assert volume_service.image_path().exists()


def test_the_image_can_be_deleted_once_encryption_is_off(encrypted):
    er.prepare()
    er.apply_pending()
    assert er.discard_image()["deleted"] is True
    assert not volume_service.image_path().exists()
    assert _rows(encrypted / "localbook.db") == 25
