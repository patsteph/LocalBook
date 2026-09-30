"""LB-11 measure 5: moving the corpus onto the encrypted volume.

The most dangerous operation in the release — it relocates ~690 MB that exists
nowhere else. So these tests are almost entirely about what must NOT happen:
nothing is deleted, a failure leaves the plaintext exactly where it was, and a
half-copied volume is never marked ready.

Real encrypted volumes, not mocks. The property under test is that the user's
data survives a round trip through encryption, and a mocked hdiutil proves
nothing about that.
"""

import json
import secrets
import sqlite3
import subprocess
from pathlib import Path

import pytest

from services import encryption_migration as em
from services import keyvault, volume_service


@pytest.fixture
def env(tmp_path, monkeypatch):
    service = f"LocalBook-keyvault-test-{secrets.token_hex(6)}"
    monkeypatch.setattr(keyvault, "SERVICE_NAME", service)

    from config import settings

    data = tmp_path / "LocalBook"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "volume_max_size_gb", 1)
    monkeypatch.setattr(settings, "encryption_enabled", False)

    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(settings, "backup_destination", str(backups))

    # The migration refuses without one: the volume key would have no recovery
    # copy. Keys dir is named after the data dir, so this lands in tmp_path.
    keyvault.set_recovery_key(keyvault.generate_recovery_phrase())

    # A corpus with the shapes that matter: a live database, nested files, a WAL.
    conn = sqlite3.connect(data / "localbook.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE sources (id TEXT PRIMARY KEY, content TEXT)")
    conn.executemany("INSERT INTO sources VALUES (?,?)",
                     [(f"s{i}", f"irreplaceable text {i}") for i in range(25)])
    conn.commit()
    # HELD OPEN on purpose. SQLite checkpoints and REMOVES the -wal on the last
    # connection close, so closing here would leave no live WAL and the test
    # would not exercise the thing it claims to — production always has one
    # open. Closed in teardown.

    (data / "notebooks").mkdir()
    (data / "notebooks" / "nb1.json").write_text('{"id": "nb1"}')
    (data / "lancedb").mkdir()
    (data / "lancedb" / "nb1.lance").write_bytes(b"vectors" * 500)
    (data / "sources.json").write_text('{"s1": {}}')
    (data / ".app_token").write_text("should-not-travel")

    real_run = subprocess.run
    try:
        yield data
    finally:
        try:
            conn.close()
        except Exception:
            pass
        for mp in (data, tmp_path / em.STAGING_MOUNT):
            try:
                real_run(["hdiutil", "detach", str(mp), "-force"], capture_output=True)
            except Exception:
                pass
        for purpose in keyvault.PURPOSES:
            real_run(
                ["security", "delete-generic-password", "-a", purpose, "-s", service],
                capture_output=True,
            )


# ── the backup comes first, non-negotiably ──────────────────────────────────


def test_no_backup_destination_means_no_migration(env, monkeypatch):
    """The backup is taken BEFORE the migration, not after. Without one there is
    nothing behind the operation at all."""
    from config import settings

    monkeypatch.setattr(settings, "backup_destination", "")
    report = em.prepare()

    assert report.ok is False
    assert any("backup destination" in e for e in report.errors)
    assert em.pending() is None


def test_a_backup_is_taken_before_anything_else(env):
    report = em.prepare()
    assert report.ok is True
    assert report.backup_path and Path(report.backup_path).is_file()


def test_a_failed_backup_aborts_the_migration(env, monkeypatch):
    from services import backup_service

    monkeypatch.setattr(
        backup_service, "create_backup",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    report = em.prepare()

    assert report.ok is False
    assert any("backup failed" in e for e in report.errors)
    assert not volume_service.image_path().exists()


# ── phase 1 changes nothing live ────────────────────────────────────────────


def _content_files(root):
    """Everything except SQLite's own scratch.

    Reading a WAL-mode database read-only CREATES a `-shm`, and checkpointing
    rewrites `-wal`. Neither is a change to the user's data, but both show up in
    a naive mtime comparison — so the test would fail on SQLite doing its job.
    """
    return {
        str(p.relative_to(root)): p.stat().st_mtime
        for p in root.rglob("*")
        if p.is_file() and not p.name.endswith(("-wal", "-shm"))
    }


def test_preparing_leaves_the_plaintext_untouched(env):
    before = _content_files(env)

    em.prepare()

    assert _content_files(env) == before


def test_preparing_does_not_mount_over_the_data_dir(env):
    em.prepare()
    # The staged volume is detached again; the data dir is still plain files.
    assert volume_service.is_mounted(env) is False
    assert (env / "localbook.db").is_file()


def test_the_database_is_copied_with_the_backup_api_not_cp(env):
    """`localbook.db-wal` is open right now. A file copy of that is a torn page
    plus whatever the WAL happened to hold — it opens without complaint and is
    wrong, and here the copy becomes the only copy."""
    assert (env / "localbook.db-wal").exists(), "the fixture must leave a WAL"

    report = em.prepare()

    assert report.databases["localbook.db"]["integrity"] == "ok"
    assert report.databases["localbook.db"]["ok"] is True
    assert report.row_count_drift == {}


def test_the_stale_token_does_not_travel(env):
    """A stale `.app_token` carried into the volume would be read by the next
    launch, and every client's cached token would then be wrong."""
    em.prepare()
    em.apply_pending()

    assert not (env / ".app_token").exists()


def test_the_sources_wal_is_not_copied_as_a_file(env):
    """A `-wal` belongs to a snapshot that no longer exists once the database is
    copied through the backup API — carrying the old one across would pair a
    fresh database with a stranger's journal.

    The DESTINATION may well have a -wal of its own afterwards, created by
    legitimately opening it; that is SQLite working normally and is not what
    this guards.
    """
    source_wal = (env / "localbook.db-wal").read_bytes()
    assert source_wal, "the fixture must hold a live WAL"

    report = em.prepare()

    assert "localbook.db-wal" not in report.skipped
    assert not any("-wal" in f for f in report.mismatched_files)

    em.apply_pending()
    migrated = env / "localbook.db-wal"
    if migrated.exists():
        assert migrated.read_bytes() != source_wal, "the old WAL was copied across"

    conn = sqlite3.connect(f"file:{env / 'localbook.db'}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 25
    finally:
        conn.close()


# ── verification refuses to stage a bad copy ────────────────────────────────


def test_row_count_drift_blocks_the_migration(env, monkeypatch):
    """A file that copied to the right size and hashes correctly can still be a
    database with no rows in it."""
    real = em._row_counts
    calls = {"n": 0}

    def lying(db):
        calls["n"] += 1
        counts = real(db)
        if calls["n"] % 2 == 0:          # the DESTINATION read
            counts = {k: 0 for k in counts}
        return counts

    monkeypatch.setattr(em, "_row_counts", lying)
    report = em.prepare()

    assert report.ok is False
    assert report.row_count_drift
    assert em.pending() is None, "a failed verification must not stage"


def test_a_corrupted_file_blocks_the_migration(env, monkeypatch):
    real_copy = em.shutil.copy2

    def corrupting(src, dst, *a, **k):
        real_copy(src, dst, *a, **k)
        if Path(src).name == "nb1.json":
            Path(dst).write_text('{"id": "WRONG"}')

    monkeypatch.setattr(em.shutil, "copy2", corrupting)
    report = em.prepare()

    assert report.ok is False
    assert any("nb1.json" in f for f in report.mismatched_files)
    assert em.pending() is None


def test_a_failed_verification_leaves_no_sentinel(env, monkeypatch):
    """A half-copied volume must never be marked as a LocalBook volume — a later
    attach would then accept it and mount over the real data."""
    monkeypatch.setattr(
        em, "_row_counts",
        lambda db: {"sources": 1} if "LocalBook/" in str(db) else {"sources": 999},
    )
    em.prepare()

    volume_service.attach(initialise_sentinel=False) if False else None
    # The image exists but was never stamped, so a normal attach refuses it.
    with pytest.raises(volume_service.VolumeError):
        volume_service.attach(initialise_sentinel=False)


# ── phase 2: the swap ───────────────────────────────────────────────────────


def test_the_swap_puts_the_corpus_inside_the_volume(env):
    em.prepare()
    result = em.apply_pending()

    assert result["applied"] is True
    assert volume_service.is_mounted(env) is True

    conn = sqlite3.connect(f"file:{env / 'localbook.db'}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 25
    finally:
        conn.close()
    assert json.loads((env / "notebooks" / "nb1.json").read_text())["id"] == "nb1"


def test_the_plaintext_is_kept_never_deleted(env):
    em.prepare()
    result = em.apply_pending()

    kept = Path(result["plaintext_kept_at"])
    assert kept.is_dir()
    conn = sqlite3.connect(f"file:{kept / 'localbook.db'}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 25
    finally:
        conn.close()


def test_the_swap_requires_confirmation_afterwards(env):
    """The migration is not finished until the user has seen their own notebooks
    and said so."""
    em.prepare()
    assert em.apply_pending()["confirm_required"] is True


def test_the_swap_runs_once(env):
    em.prepare()
    assert em.apply_pending()["applied"] is True
    assert em.apply_pending() is None


def test_applying_with_nothing_staged_is_a_no_op(env):
    assert em.apply_pending() is None


def test_a_staged_migration_can_be_abandoned(env):
    em.prepare()
    assert em.cancel_pending() is True
    assert em.apply_pending() is None
    assert (env / "localbook.db").is_file()       # still plaintext, untouched


def test_a_failed_swap_puts_the_plaintext_back(env, monkeypatch):
    """An app with no data directory is worse than an unencrypted one."""
    em.prepare()
    monkeypatch.setattr(
        em, "_attach_at",
        lambda mp: (_ for _ in ()).throw(RuntimeError("attach exploded")),
    )

    result = em.apply_pending()

    assert result["applied"] is False
    assert (env / "localbook.db").is_file(), "the plaintext was not restored"
    conn = sqlite3.connect(f"file:{env / 'localbook.db'}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 25
    finally:
        conn.close()


def test_a_missing_prepared_volume_does_not_move_anything(env):
    em.prepare()
    import shutil as _sh

    _sh.rmtree(volume_service.image_path())

    result = em.apply_pending()
    assert result["applied"] is False
    assert (env / "localbook.db").is_file()


def test_encryption_is_only_enabled_after_a_successful_swap(env):
    """Setting the flag earlier would lock the app out of a data directory that
    is still plaintext."""
    from config import settings

    em.prepare()
    assert settings.encryption_enabled is False

    em.apply_pending()
    assert settings.encryption_enabled is True


# ── the only destructive call ───────────────────────────────────────────────


def test_the_plaintext_copy_is_listed_for_the_user(env):
    em.prepare()
    em.apply_pending()

    copies = em.plaintext_copies()
    assert len(copies) == 1
    assert copies[0]["bytes"] > 0


def test_discarding_is_refused_while_the_volume_is_not_open(env):
    """Deleting the plaintext while the volume is unavailable turns a
    recoverable situation into a total loss — and that is exactly the moment a
    frustrated user is most likely to click it."""
    em.prepare()
    kept = Path(em.apply_pending()["plaintext_kept_at"])
    volume_service.detach()

    result = em.discard_plaintext(str(kept))

    assert result["deleted"] is False
    assert "not open" in result["error"]
    assert kept.is_dir()


def test_discarding_works_once_the_volume_is_open(env):
    em.prepare()
    kept = Path(em.apply_pending()["plaintext_kept_at"])

    result = em.discard_plaintext(str(kept))

    assert result["deleted"] is True
    assert not kept.exists()
    assert volume_service.is_mounted(env) is True


def test_discarding_refuses_anything_that_is_not_a_kept_copy(env):
    em.prepare()
    em.apply_pending()
    assert em.discard_plaintext(str(env))["deleted"] is False
    assert em.discard_plaintext("/tmp")["deleted"] is False


# ── the whole point ─────────────────────────────────────────────────────────


def test_the_migrated_corpus_is_unreadable_without_the_key(env):
    em.prepare()
    em.apply_pending()
    kept = em.plaintext_copies()[0]["path"]
    em.discard_plaintext(kept)
    volume_service.detach()

    blob = b""
    for band in volume_service.image_path().rglob("*"):
        if band.is_file():
            blob += band.read_bytes()

    assert b"irreplaceable text" not in blob
    assert b"nb1.json" not in blob


# ── the flag lives outside the volume (measure 1's other half) ──────────────


def test_the_encryption_flag_is_written_beside_the_data_dir_not_inside(env):
    """Inside the volume, a failed mount would hide the flag that says a mount is
    required — and the app would open empty."""
    from config import encryption_flag_path

    em.prepare()
    em.apply_pending()

    assert encryption_flag_path(env).is_file()
    assert encryption_flag_path(env).parent == env.parent
    env_file = env / ".env"
    assert not env_file.exists() or "ENCRYPTION" not in env_file.read_text()


def test_the_gate_sees_the_flag_while_the_volume_is_unmounted(env, monkeypatch):
    from config import settings
    from services import volume_gate

    em.prepare()
    em.apply_pending()
    volume_service.detach()
    monkeypatch.setattr(settings, "encryption_enabled", False)   # a fresh process

    assert volume_gate.encryption_enabled() is True
    assert volume_gate.evaluate().locked is True


# ── the catch-up pass at swap time ──────────────────────────────────────────


def test_writes_made_after_prepare_reach_the_volume(env):
    """The app keeps running between prepare and the restart. Those writes must
    not stay behind in the plaintext copy."""
    em.prepare()

    conn = sqlite3.connect(env / "localbook.db")
    conn.execute("INSERT INTO sources VALUES ('late', 'written after prepare')")
    conn.commit()
    conn.close()
    (env / "notebooks" / "nb2.json").write_text('{"id": "nb2"}')
    (env / "sources.json").unlink()

    result = em.apply_pending()
    assert result["applied"] is True, result

    conn = sqlite3.connect(f"file:{env / 'localbook.db'}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 26
    finally:
        conn.close()
    assert (env / "notebooks" / "nb2.json").is_file()
    assert not (env / "sources.json").exists()
    assert volume_service.is_mounted(env)            # the sentinel survived the sync


def test_a_failed_catch_up_leaves_the_plaintext_in_place(env, monkeypatch):
    em.prepare()
    monkeypatch.setattr(em, "_verify",
                        lambda s, t, r, only=None: r.mismatched_files.append("x"))

    result = em.apply_pending()

    assert result["applied"] is False
    assert (env / "localbook.db").is_file()
    assert not volume_service.is_mounted(env)
    assert em.pending() is None
    assert em.last_apply()["applied"] is False


def test_the_swap_outcome_is_recorded_for_the_setup_screen(env):
    em.prepare()
    em.apply_pending()
    last = em.last_apply()
    assert last["applied"] is True and last["plaintext_kept_at"]


# ── preconditions ───────────────────────────────────────────────────────────


def test_no_recovery_phrase_means_no_migration(env, monkeypatch):
    """Without one the volume key has no recovery copy, and a wiped Keychain
    would cost the whole corpus."""
    monkeypatch.setattr(keyvault, "has_recovery_key", lambda: False)
    report = em.prepare()
    assert report.ok is False
    assert any("recovery phrase" in e for e in report.errors)
    assert not volume_service.image_path().exists()


def test_the_volume_key_has_a_recovery_copy_once_prepared(env):
    em.prepare()
    assert "volume" not in keyvault.unprotected_purposes()


def test_a_staging_mount_left_by_a_quit_is_released(env):
    """A quit mid-prepare leaves the volume attached at the staging point."""
    volume_service.create()
    staging = env.parent / em.STAGING_MOUNT
    staging.mkdir()
    em._attach_at(staging)

    report = em.prepare()
    assert report.ok is True, report.errors


def test_progress_reports_a_total(env):
    report = em.prepare()
    assert report.bytes_total > 0


def test_a_lost_flag_still_locks_when_the_data_dir_is_empty(env, monkeypatch):
    """Losing one flag file must not reopen the empty-app hole."""
    from config import encryption_flag_path, settings
    from services import volume_gate

    em.prepare()
    em.apply_pending()
    volume_service.detach()
    encryption_flag_path(env).unlink()
    monkeypatch.setattr(settings, "encryption_enabled", False)

    assert volume_gate.evaluate().locked is True


def test_an_abandoned_prepare_does_not_lock_a_live_plaintext_dir(env):
    from services import volume_gate

    em.prepare()
    em.cancel_pending()
    assert volume_service.image_path().exists()
    assert volume_gate.evaluate().locked is False
