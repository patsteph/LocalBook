"""LB-10 items 4 and 5: restore, and the drill that proves it would work.

A backup nobody has restored is a hypothesis. These tests are mostly about the
ways a restore can appear to succeed while losing data — a pristine empty
database passes `integrity_check`, a truncated archive has a valid header, and a
drill with no archive to check reports nothing wrong unless it is made to.
"""

import json
import secrets
import sqlite3
import subprocess
from pathlib import Path

import pytest

from services import backup_service, keyvault, restore_service


@pytest.fixture
def vault(tmp_path, monkeypatch):
    service = f"LocalBook-keyvault-test-{secrets.token_hex(6)}"
    monkeypatch.setattr(keyvault, "SERVICE_NAME", service)
    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    real_run = subprocess.run
    try:
        yield
    finally:
        for purpose in keyvault.PURPOSES:
            real_run(
                ["security", "delete-generic-password", "-a", purpose, "-s", keyvault.service_name()],
                capture_output=True,
            )


@pytest.fixture
def data_dir(tmp_path, vault):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(d / "localbook.db")
    conn.execute("CREATE TABLE sources (id TEXT PRIMARY KEY, content TEXT)")
    conn.executemany("INSERT INTO sources VALUES (?, ?)",
                     [(f"s{i}", f"body {i}") for i in range(5)])
    conn.commit()
    conn.close()
    (d / "notebooks").mkdir()
    (d / "notebooks" / "nb1.json").write_text('{"id": "nb1"}')
    (d / "sources.json").write_text('{"s1": {}}')
    return d


@pytest.fixture
def dest(tmp_path):
    p = tmp_path / "backups"
    p.mkdir()
    return p


@pytest.fixture
def archive(data_dir, dest):
    return backup_service.create_backup(dest, data_dir=data_dir).path


# ── verification ────────────────────────────────────────────────────────────


def test_a_good_archive_verifies(archive, data_dir):
    report = restore_service.verify(archive)
    assert report.ok is True
    assert report.checked_files > 0
    assert report.missing_files == []
    assert report.row_count_drift == {}


def test_verification_checks_integrity_AND_row_counts(archive):
    """Both, not either. `integrity_check` proves the file is a well-formed
    database; it says nothing about it being the RIGHT database — a pristine
    empty localbook.db passes integrity and has lost everything."""
    report = restore_service.verify(archive)
    db = report.databases["localbook.db"]
    assert db["integrity"] == "ok"
    assert db["tables"]["sources"] == 5


def test_a_corrupt_archive_does_not_verify(archive):
    raw = archive.read_bytes()
    archive.write_bytes(raw[: len(raw) // 2])
    report = restore_service.verify(archive)
    assert report.ok is False
    assert report.errors


def test_a_missing_archive_does_not_verify(tmp_path):
    report = restore_service.verify(tmp_path / "nope.lbbackup")
    assert report.ok is False
    assert report.errors


def test_row_count_drift_is_reported(archive, monkeypatch):
    """The failure that matters: files all present and hashing correctly, but
    the database has fewer rows than the manifest recorded."""
    real_open = backup_service.open_archive

    def understated(a, **kw):
        opened = real_open(a, **kw)
        opened["manifest"]["row_counts"]["localbook.db"]["sources"] = 999
        return opened

    monkeypatch.setattr(backup_service, "open_archive", understated)
    report = restore_service.verify(archive)

    assert report.ok is False
    assert report.row_count_drift["localbook.db"]["sources"] == {
        "expected": 999, "actual": 5
    }


def test_verification_always_reports_that_a_reindex_is_needed(archive):
    """lancedb is never in the archive (D19), so a restored install has no
    vector index until /reindex/all rebuilds it. Chat would find nothing."""
    assert restore_service.verify(archive).needs_reindex is True


def test_verification_changes_nothing(archive, data_dir):
    before = {p.name: p.stat().st_mtime for p in data_dir.rglob("*") if p.is_file()}
    restore_service.verify(archive)
    after = {p.name: p.stat().st_mtime for p in data_dir.rglob("*") if p.is_file()}
    assert before == after


# ── staging, not swapping ───────────────────────────────────────────────────


def test_staging_does_not_touch_the_live_data_dir(archive, data_dir):
    """Restoring into a running app is refused — the backend holds four
    databases open with live WALs, and swapping under that produces exactly the
    torn state a restore exists to escape."""
    (data_dir / "sources.json").write_text('{"LIVE": true}')

    report = restore_service.stage_restore(archive, data_dir=data_dir)

    assert report.restart_required is True
    assert report.staged_to
    assert json.loads((data_dir / "sources.json").read_text()) == {"LIVE": True}


def test_staging_writes_a_marker_the_next_launch_can_find(archive, data_dir):
    restore_service.stage_restore(archive, data_dir=data_dir)
    pending = restore_service.pending_restore(data_dir)
    assert pending["archive"] == str(archive)
    assert pending["verified"] is True


def test_a_failing_archive_is_not_staged(archive, data_dir):
    raw = archive.read_bytes()
    archive.write_bytes(raw[: len(raw) // 2])

    report = restore_service.stage_restore(archive, data_dir=data_dir)

    assert report.ok is False
    assert report.staged_to is None
    assert restore_service.pending_restore(data_dir) is None
    assert any("nothing was staged" in e for e in report.errors)


def test_a_bad_archive_can_be_forced_but_is_recorded_as_forced(archive, data_dir, monkeypatch):
    """Sometimes a damaged archive is better than nothing — but the record has
    to say so rather than reading like a clean restore."""
    real_open = backup_service.open_archive

    def drifted(a, **kw):
        opened = real_open(a, **kw)
        opened["manifest"]["row_counts"]["localbook.db"]["sources"] = 999
        return opened

    monkeypatch.setattr(backup_service, "open_archive", drifted)

    report = restore_service.stage_restore(archive, data_dir=data_dir, force=True)
    assert report.staged_to
    assert restore_service.pending_restore(data_dir)["forced"] is True


def test_staging_twice_replaces_the_first(archive, data_dir, dest):
    restore_service.stage_restore(archive, data_dir=data_dir)
    second = backup_service.create_backup(dest, data_dir=data_dir).path
    restore_service.stage_restore(second, data_dir=data_dir)
    assert restore_service.pending_restore(data_dir)["archive"] == str(second)


def test_a_pending_restore_can_be_cancelled(archive, data_dir):
    restore_service.stage_restore(archive, data_dir=data_dir)
    assert restore_service.cancel_pending(data_dir) is True
    assert restore_service.pending_restore(data_dir) is None


# ── the swap ────────────────────────────────────────────────────────────────


def test_applying_swaps_the_data_in(archive, data_dir):
    (data_dir / "sources.json").write_text('{"LIVE": true}')
    restore_service.stage_restore(archive, data_dir=data_dir)

    result = restore_service.apply_pending(data_dir)

    assert result["applied"] is True
    assert json.loads((data_dir / "sources.json").read_text()) == {"s1": {}}
    assert result["needs_reindex"] is True


def test_the_previous_data_is_kept_not_deleted(archive, data_dir):
    """A restore that turns out to be the wrong archive is a bad day; one that
    destroyed what it replaced is unrecoverable."""
    (data_dir / "sources.json").write_text('{"IRREPLACEABLE": true}')
    restore_service.stage_restore(archive, data_dir=data_dir)

    result = restore_service.apply_pending(data_dir)
    kept = Path(result["previous_data_kept_at"])

    assert kept.is_dir()
    assert json.loads((kept / "sources.json").read_text()) == {"IRREPLACEABLE": True}


def test_applying_clears_the_marker_so_it_runs_once(archive, data_dir):
    restore_service.stage_restore(archive, data_dir=data_dir)
    assert restore_service.apply_pending(data_dir)["applied"] is True
    assert restore_service.pending_restore(data_dir) is None
    assert restore_service.apply_pending(data_dir) is None


def test_applying_with_nothing_staged_is_a_no_op(data_dir):
    assert restore_service.apply_pending(data_dir) is None


def test_a_marker_pointing_at_nothing_does_not_wipe_the_data_dir(archive, data_dir):
    """The dangerous shape: a stale marker whose staged tree is gone. Moving the
    live data aside and then finding nothing to put back would leave the app
    with no data directory at all."""
    restore_service.stage_restore(archive, data_dir=data_dir)
    import shutil

    shutil.rmtree(restore_service.pending_restore(data_dir)["pending_dir"])

    result = restore_service.apply_pending(data_dir)

    assert result["applied"] is False
    assert (data_dir / "sources.json").is_file()
    assert restore_service.pending_restore(data_dir) is None


# ── the nightly drill ───────────────────────────────────────────────────────


def test_a_drill_against_a_good_archive_is_green(archive, dest, data_dir):
    entry = restore_service.run_drill(dest, data_dir=data_dir)
    assert entry["ok"] is True
    assert entry["problems"] == []


def test_a_drill_with_no_archive_at_all_is_RED(tmp_path, data_dir):
    """The worst possible state is "no backup exists". A drill that skips
    quietly there reports green forever on a machine with nothing to restore."""
    empty = tmp_path / "empty-backups"
    empty.mkdir()

    entry = restore_service.run_drill(empty, data_dir=data_dir)

    assert entry["ok"] is False
    assert any("no backup archive" in p for p in entry["problems"])


def test_a_drill_against_a_corrupt_archive_is_red(archive, dest, data_dir):
    raw = archive.read_bytes()
    archive.write_bytes(raw[: len(raw) // 2])
    entry = restore_service.run_drill(dest, data_dir=data_dir)
    assert entry["ok"] is False
    assert entry["problems"]


def test_the_drill_uses_the_newest_archive(data_dir, dest):
    backup_service.create_backup(dest, data_dir=data_dir)
    newest = backup_service.create_backup(dest, data_dir=data_dir).path
    entry = restore_service.run_drill(dest, data_dir=data_dir)
    assert entry["archive"] == str(newest)


def test_drill_results_accumulate(archive, dest, data_dir):
    for _ in range(3):
        restore_service.run_drill(dest, data_dir=data_dir)
    assert restore_service.drill_status(data_dir)["runs"] == 3


def test_the_seven_night_gate_needs_seven_consecutive_greens(archive, dest, data_dir):
    for _ in range(6):
        restore_service.run_drill(dest, data_dir=data_dir)
    assert restore_service.drill_status(data_dir)["gate_met"] is False

    restore_service.run_drill(dest, data_dir=data_dir)
    status = restore_service.drill_status(data_dir)
    assert status["consecutive_green"] == 7
    assert status["gate_met"] is True


def test_one_red_night_resets_the_streak(archive, dest, data_dir, tmp_path):
    """A drill green 20 times and red last night has not earned the gate."""
    for _ in range(8):
        restore_service.run_drill(dest, data_dir=data_dir)
    assert restore_service.drill_status(data_dir)["gate_met"] is True

    empty = tmp_path / "gone"
    empty.mkdir()
    restore_service.run_drill(empty, data_dir=data_dir)

    status = restore_service.drill_status(data_dir)
    assert status["consecutive_green"] == 0
    assert status["gate_met"] is False


def test_drill_status_with_no_history_is_not_green(data_dir):
    status = restore_service.drill_status(data_dir)
    assert status["runs"] == 0
    assert status["gate_met"] is False


def test_an_unreadable_drill_log_does_not_claim_the_gate(data_dir):
    (data_dir / restore_service.DRILL_LOG_NAME).write_text("{ not json")
    status = restore_service.drill_status(data_dir)
    assert status["gate_met"] is False
