"""LB-10 items 3 and 5: the nightly run and retention.

The plan's gate is seven consecutive green drills, so the thing that has to work
is the ORDER and the refusals — a scheduler that prunes before backing up, or
that quietly does nothing because no destination is set, produces a green-looking
history and no backups.
"""

import asyncio
import json
import secrets
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services import backup_scheduler, backup_service, keyvault, restore_service


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
                ["security", "delete-generic-password", "-a", purpose, "-s", service],
                capture_output=True,
            )


@pytest.fixture
def data_dir(tmp_path, vault):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(d / "localbook.db")
    conn.execute("CREATE TABLE sources (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO sources VALUES ('s1')")
    conn.commit()
    conn.close()
    (d / "sources.json").write_text('{"s1": {}}')
    (d / "audio").mkdir()
    (d / "audio" / "big.mp3").write_bytes(b"a" * 40000)
    return d


@pytest.fixture
def dest(tmp_path):
    p = tmp_path / "backups"
    p.mkdir()
    return p


# ── refusals ────────────────────────────────────────────────────────────────


def test_nothing_happens_without_a_destination(data_dir, monkeypatch):
    """There is no safe default — guessing one writes the archive inside the
    data directory it is meant to survive."""
    from config import settings

    monkeypatch.setattr(settings, "backup_destination", "")
    out = asyncio.run(backup_scheduler.run_once())
    assert "no backup destination" in out["skipped"]


def test_a_destination_that_is_not_a_folder_is_refused(data_dir, tmp_path, monkeypatch):
    """A mistyped path must not silently start filling a directory nobody meant
    to exist, possibly on the wrong disk."""
    from config import settings

    ghost = tmp_path / "typo"
    monkeypatch.setattr(settings, "backup_destination", str(ghost))

    out = asyncio.run(backup_scheduler.run_once())

    assert "is not a folder" in out["skipped"]
    assert not ghost.exists()


# ── the cycle ───────────────────────────────────────────────────────────────


def test_a_cycle_backs_up_prunes_and_drills(data_dir, dest, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "backup_destination", str(dest))
    out = asyncio.run(backup_scheduler.run_once())

    assert Path(out["backup"]["path"]).is_file()
    assert "pruned" in out
    assert out["drill"]["ok"] is True


def test_the_drill_verifies_the_archive_just_written(data_dir, dest, monkeypatch):
    """Not last night's — the point of drilling after the backup."""
    from config import settings

    monkeypatch.setattr(settings, "backup_destination", str(dest))
    out = asyncio.run(backup_scheduler.run_once())
    assert out["drill"]["archive"] == out["backup"]["path"]


def test_the_nightly_run_leaves_blobs_out_by_default(data_dir, dest, monkeypatch):
    """~600 MB with generated audio versus ~80 MB without, times eleven
    retained archives."""
    from config import settings

    monkeypatch.setattr(settings, "backup_destination", str(dest))
    monkeypatch.setattr(settings, "backup_include_blobs_nightly", False)

    asyncio.run(backup_scheduler.run_once())

    archive = sorted(dest.glob(f"*{backup_service.ARCHIVE_SUFFIX}"))[-1]
    manifest = backup_service.open_archive(archive)["manifest"]
    assert "audio/big.mp3" not in manifest["files"]
    assert manifest["includes_blobs"] is False


def test_a_failed_backup_still_drills_the_previous_archive(data_dir, dest, monkeypatch):
    """If tonight's backup failed, last night's is the one that matters, and the
    record should say whether it is still good."""
    from config import settings

    monkeypatch.setattr(settings, "backup_destination", str(dest))
    asyncio.run(backup_scheduler.run_once())        # a good archive exists

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(backup_service, "create_backup", boom)
    out = asyncio.run(backup_scheduler.run_once())

    assert "error" in out["backup"]
    assert out["drill"]["ok"] is True


def test_a_failed_backup_does_not_prune(data_dir, dest, monkeypatch):
    """Pruning after a failure costs an old archive at the exact moment you can
    least afford to lose one."""
    from config import settings

    monkeypatch.setattr(settings, "backup_destination", str(dest))
    asyncio.run(backup_scheduler.run_once())
    before = set(dest.glob(f"*{backup_service.ARCHIVE_SUFFIX}"))

    monkeypatch.setattr(
        backup_service, "create_backup",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    out = asyncio.run(backup_scheduler.run_once())

    assert "pruned" not in out
    assert set(dest.glob(f"*{backup_service.ARCHIVE_SUFFIX}")) == before


# ── retention ───────────────────────────────────────────────────────────────


def _archive_template(dest: Path, data_dir: Path) -> bytes:
    """One real archive's bytes, then removed. Built ONCE.

    Calling create_backup per fake date does not work: its filename is
    `localbook-<now>Z`, so several calls in the same second write the SAME path
    — a later call overwrote an earlier fake and then deleted it, and the newest
    archive vanished from a retention test about keeping the newest archive.
    """
    result = backup_service.create_backup(dest, data_dir=data_dir)
    raw = result.path.read_bytes()
    result.path.unlink(missing_ok=True)
    return raw


def _stamped(raw: bytes, dest: Path, when: datetime) -> Path:
    """Write those bytes as an archive dated `when`."""
    raw = bytearray(raw)
    offset = len(backup_service.MAGIC)
    header_len = int.from_bytes(raw[offset:offset + 4], "big")
    header = json.loads(raw[offset + 4:offset + 4 + header_len].decode())
    header["created_at"] = when.isoformat()
    forged = json.dumps(header, sort_keys=True).encode()

    rebuilt = bytearray(raw[:offset])
    rebuilt += len(forged).to_bytes(4, "big")
    rebuilt += forged
    rebuilt += raw[offset + 4 + header_len:]

    target = dest / f"localbook-{when.strftime('%Y%m%dT%H%M%SZ')}{backup_service.ARCHIVE_SUFFIX}"
    target.write_bytes(bytes(rebuilt))
    return target


def test_the_last_seven_days_are_kept(data_dir, dest):
    now = datetime.now(timezone.utc)
    template = _archive_template(dest, data_dir)
    for days in range(10):
        _stamped(template, dest, now - timedelta(days=days))

    report = backup_service.prune(dest)

    kept = set(report["kept"])
    for days in range(7):
        stamp = (now - timedelta(days=days)).strftime("%Y%m%dT%H%M%SZ")
        assert f"localbook-{stamp}.lbbackup" in kept


def test_older_archives_are_thinned_to_weeklies(data_dir, dest):
    now = datetime.now(timezone.utc)
    template = _archive_template(dest, data_dir)
    for days in range(0, 60, 3):
        _stamped(template, dest, now - timedelta(days=days))

    report = backup_service.prune(dest)

    remaining = list(dest.glob(f"*{backup_service.ARCHIVE_SUFFIX}"))
    assert report["deleted"], "nothing was thinned at all"
    assert len(remaining) < 20
    assert len(remaining) == len(report["kept"])


def test_an_unreadable_archive_is_kept_not_deleted(data_dir, dest):
    """"I could not understand this file" is not grounds for removing the only
    copy of something. A corrupt archive is still evidence."""
    _stamped(_archive_template(dest, data_dir), dest, datetime.now(timezone.utc))
    junk = dest / f"mystery{backup_service.ARCHIVE_SUFFIX}"
    junk.write_bytes(b"not an archive")

    report = backup_service.prune(dest)

    assert "mystery.lbbackup" in report["unreadable"]
    assert junk.exists()


def test_pruning_an_empty_folder_is_harmless(tmp_path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert backup_service.prune(empty)["deleted"] == []


def test_pruning_never_deletes_everything(data_dir, dest):
    now = datetime.now(timezone.utc)
    template = _archive_template(dest, data_dir)
    for days in range(40):
        _stamped(template, dest, now - timedelta(days=days))
    backup_service.prune(dest)
    assert list(dest.glob(f"*{backup_service.ARCHIVE_SUFFIX}"))


# ── the loop's contract ─────────────────────────────────────────────────────


def test_the_schedule_id_is_registered_for_the_viewer():
    assert backup_scheduler.SCHEDULE_ID == "nightly-backup"


def test_the_cadence_is_read_from_the_store_not_hardcoded():
    """CLAUDE.md: the const is the DEFAULT; schedule_store is the authority."""
    import inspect

    src = inspect.getsource(backup_scheduler.NightlyBackup._loop)
    assert "schedule_store.get_interval" in src
    assert "schedule_store.is_enabled" in src


def test_the_loop_will_not_wake_more_often_than_the_floor():
    """A backup is minutes of disk work — an over-eager override would be
    indistinguishable from a runaway loop."""
    assert backup_scheduler.MIN_INTERVAL_SECONDS >= 3600
