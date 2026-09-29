"""LB-10 items 6, 8 and 9: is the data safe, and would you know?

Everything else in LB-10 produces a fact. This is where they become an answer,
so the tests are mostly about the summary being HONEST — a panel that rounds
"unproven" up to "healthy" is worse than no panel, because it converts a real
risk into a reassurance the user acts on.
"""

import secrets
import sqlite3
import subprocess
from pathlib import Path

import pytest

from services import data_health, keyvault


@pytest.fixture
def vault(tmp_path, monkeypatch):
    service = f"LocalBook-keyvault-test-{secrets.token_hex(6)}"
    monkeypatch.setattr(keyvault, "SERVICE_NAME", service)
    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "backup_destination", "")
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
    return d


# ── the shutdown sentinel (item 6) ──────────────────────────────────────────


def test_a_clean_exit_leaves_a_marker(data_dir):
    data_health.mark_clean_shutdown(data_dir)
    assert (data_dir / data_health.CLEAN_MARKER).is_file()
    assert data_health.was_unclean(data_dir) is False


def test_a_missing_marker_means_the_last_run_crashed(data_dir):
    assert data_health.was_unclean(data_dir) is True


def test_a_clean_previous_exit_skips_the_check(data_dir):
    data_health.mark_clean_shutdown(data_dir)
    assert data_health.startup_check(data_dir) is None


def test_the_marker_is_cleared_at_startup(data_dir):
    """From launch until a clean exit the marker is absent — which is exactly
    what makes the NEXT crash detectable."""
    data_health.mark_clean_shutdown(data_dir)
    data_health.startup_check(data_dir)
    assert data_health.was_unclean(data_dir) is True


def test_an_unclean_exit_triggers_an_integrity_check(data_dir):
    report = data_health.startup_check(data_dir)
    assert report is not None
    assert report["ok"] is True
    assert report["checked"]["localbook.db"] == "ok"


def test_a_first_run_is_not_treated_as_a_crash(tmp_path, vault):
    """A brand-new data directory has no marker either. Reporting that as an
    unclean shutdown would alarm every new user on first launch."""
    empty = tmp_path / "data"
    empty.mkdir(parents=True, exist_ok=True)
    assert data_health.startup_check(empty) is None


def test_a_corrupt_database_is_reported_not_repaired(data_dir):
    """SQLite's own guidance is to recover from a backup. A well-meant in-place
    rebuild can turn a partially readable file into a confidently wrong one."""
    before = (data_dir / "localbook.db").read_bytes()
    (data_dir / "localbook.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)

    report = data_health.check_integrity(data_dir)

    assert report["ok"] is False
    assert "localbook.db" in report["problems"]
    # Untouched — reporting only.
    assert (data_dir / "localbook.db").read_bytes() != before


def test_the_integrity_check_does_not_stop_the_app(data_dir):
    """A corrupt database should stop the USER, not the process — if the app
    refuses to boot they cannot reach the restore screen."""
    (data_dir / "localbook.db").write_bytes(b"garbage")
    report = data_health.startup_check(data_dir)   # must not raise
    assert report["ok"] is False


# ── dead weight (item 9) ────────────────────────────────────────────────────


def test_orphaned_migration_leftovers_are_found(data_dir):
    (data_dir / "lancedb_backup_20260326_143748").mkdir()
    (data_dir / "lancedb_backup_20260326_143748" / "old.lance").write_bytes(b"x" * 100)
    (data_dir / "credentials.enc.pre-keyvault").write_bytes(b"y" * 50)

    found = {item["name"] for item in data_health.find_dead_weight(data_dir)}

    assert "lancedb_backup_20260326_143748" in found
    assert "credentials.enc.pre-keyvault" in found


def test_real_data_is_never_listed_as_dead_weight(data_dir):
    (data_dir / "sources.json").write_text("{}")
    (data_dir / "lancedb").mkdir()
    names = {item["name"] for item in data_health.find_dead_weight(data_dir)}
    assert "sources.json" not in names
    assert "lancedb" not in names
    assert "localbook.db" not in names


def test_finding_dead_weight_deletes_nothing(data_dir):
    (data_dir / "lancedb_backup_1").mkdir()
    data_health.find_dead_weight(data_dir)
    assert (data_dir / "lancedb_backup_1").is_dir()


def test_removal_is_explicit_and_reports_what_went(data_dir):
    (data_dir / "lancedb_backup_1").mkdir()
    (data_dir / "lancedb_backup_1" / "f.lance").write_bytes(b"x")
    (data_dir / "old.pre-keyvault").write_bytes(b"y")

    result = data_health.remove_dead_weight(data_dir)

    assert set(result["removed"]) == {"lancedb_backup_1", "old.pre-keyvault"}
    assert not (data_dir / "lancedb_backup_1").exists()
    assert (data_dir / "localbook.db").is_file()


# ── the summary (item 8) ────────────────────────────────────────────────────


def test_no_backup_destination_is_a_PROBLEM_not_a_warning(data_dir):
    """The whole point of the panel. "Nothing is being backed up" is the most
    consequential thing it can say."""
    st = data_health.status(data_dir)
    assert st["overall"]["state"] == "problem"
    assert any("no backup destination" in p.lower() for p in st["overall"]["problems"])


def test_a_destination_with_no_archives_is_still_a_problem(data_dir, tmp_path, monkeypatch):
    from config import settings

    dest = tmp_path / "backups"
    dest.mkdir()
    monkeypatch.setattr(settings, "backup_destination", str(dest))

    st = data_health.status(data_dir)
    assert st["overall"]["state"] == "problem"
    assert any("no backup has been taken" in p for p in st["overall"]["problems"])


def test_an_unproven_backup_is_flagged(data_dir, tmp_path, monkeypatch):
    """Backups that exist but have never been drilled are a hypothesis."""
    from config import settings
    from services import backup_service

    dest = tmp_path / "backups"
    dest.mkdir()
    monkeypatch.setattr(settings, "backup_destination", str(dest))
    backup_service.create_backup(dest, data_dir=data_dir)

    st = data_health.status(data_dir)
    assert any("drill" in w.lower() for w in st["overall"]["warnings"])


def test_the_drill_record_is_reported_without_a_countdown(data_dir, tmp_path, monkeypatch):
    """Was `test_the_drill_streak_is_reported_out_of_seven`. The 7-night gate was
    dropped on 2026-09-29, so the panel no longer nags toward it — but the
    underlying streak is still COMPUTED, because it is genuine information and
    something may want it later."""
    from config import settings
    from services import backup_service, restore_service

    dest = tmp_path / "backups"
    dest.mkdir()
    monkeypatch.setattr(settings, "backup_destination", str(dest))
    backup_service.create_backup(dest, data_dir=data_dir)
    restore_service.run_drill(dest, data_dir=data_dir)

    st = data_health.status(data_dir)
    assert st["drills"]["runs"] == 1
    assert st["drills"]["consecutive_green"] == 1
    assert not any("of 7" in w for w in st["overall"]["warnings"])
    # A passing drill is not something to warn about at all.
    assert not any("drill" in w.lower() for w in st["overall"]["warnings"])


def test_a_key_with_no_recovery_copy_is_a_problem(data_dir, tmp_path, monkeypatch):
    """Moving off the machine-derived key made a wiped Keychain fatal. The panel
    has to say so rather than showing green."""
    from config import settings

    dest = tmp_path / "backups"
    dest.mkdir()
    monkeypatch.setattr(settings, "backup_destination", str(dest))
    keyvault.get_or_create("credentials")          # exists, unwrapped

    st = data_health.status(data_dir)
    assert any("recovery copy" in p for p in st["overall"]["problems"])


def test_a_pending_migration_is_a_problem(data_dir, monkeypatch):
    """Unapplied migrations mean the data is not the shape the code expects —
    which is exactly the state LB-11 must not start from."""
    from services import migration_ledger

    monkeypatch.setattr(migration_ledger, "schema_version", lambda *a, **k: "0.6.5")
    monkeypatch.setattr(migration_ledger, "head", lambda *a, **k: 1)
    monkeypatch.setattr(
        migration_ledger, "pending",
        lambda *a, **k: [migration_ledger.Migration(2, "waiting", "0.7.0", lambda ctx: None)],
    )

    st = data_health.status(data_dir)

    assert st["schema"]["pending"] == 1
    assert any("migration" in p for p in st["overall"]["problems"])


def test_the_summary_survives_a_broken_probe(data_dir, monkeypatch):
    """A panel that 500s because one probe failed tells the user nothing about
    the other five."""
    monkeypatch.setattr(
        data_health, "_codec",
        lambda: (_ for _ in ()).throw(RuntimeError("which() blew up")),
    )
    st = data_health.status(data_dir)
    assert "error" in st["codec"]
    assert "backup" in st and "drills" in st


def test_a_genuinely_missing_codec_is_reported(data_dir, monkeypatch):
    """ffmpeg comes from Homebrew, so it is genuinely absent on a machine that
    never had Homebrew — the work Mac, for instance.

    Both probes have to fail for that verdict: `which` AND the known install
    locations. Checking only `which` reported it missing on a machine that
    plainly had it, because a Finder-launched app has no Homebrew on its PATH.
    """
    import shutil as _shutil
    from pathlib import Path as _Path

    monkeypatch.setattr(_shutil, "which", lambda name: None)
    monkeypatch.setattr(_Path, "exists", lambda self: False)

    st = data_health.status(data_dir)
    assert st["codec"]["ok"] is False
    assert any("codec" in w.lower() for w in st["overall"]["warnings"])


def test_a_staged_restore_is_surfaced(data_dir, tmp_path, monkeypatch):
    from config import settings
    from services import backup_service, restore_service

    dest = tmp_path / "backups"
    dest.mkdir()
    monkeypatch.setattr(settings, "backup_destination", str(dest))
    archive = backup_service.create_backup(dest, data_dir=data_dir).path
    restore_service.stage_restore(archive, data_dir=data_dir)

    st = data_health.status(data_dir)
    assert st["pending_restore"] is not None
    assert any("restore is staged" in w for w in st["overall"]["warnings"])


def test_a_fully_healthy_install_says_so(data_dir, tmp_path, monkeypatch):
    """The panel must be ABLE to go green, or nobody will believe it when it
    does not."""
    from config import settings
    from services import backup_service, restore_service

    dest = tmp_path / "backups"
    dest.mkdir()
    monkeypatch.setattr(settings, "backup_destination", str(dest))
    monkeypatch.setattr(data_health, "_codec", lambda: {"ffmpeg": "/usr/bin/ffmpeg", "ok": True})

    # The schema probe reads the process's own database, not `data_dir` (see
    # data_health.status) — so it is pinned here rather than left to whatever
    # the shared dev database happens to hold when this test runs. Without
    # this the test passed alone and failed in a full run.
    from services import migration_ledger

    monkeypatch.setattr(migration_ledger, "pending", lambda *a, **k: [])
    monkeypatch.setattr(migration_ledger, "schema_version", lambda *a, **k: "0.7.0")
    monkeypatch.setattr(migration_ledger, "head", lambda *a, **k: 1)

    phrase = keyvault.generate_recovery_phrase()
    keyvault.set_recovery_key(phrase)
    keyvault.get_or_create("credentials")
    keyvault.wrap_all()

    backup_service.create_backup(dest, data_dir=data_dir)
    for _ in range(7):
        restore_service.run_drill(dest, data_dir=data_dir)

    st = data_health.status(data_dir)
    assert st["overall"]["problems"] == []
    assert st["overall"]["warnings"] == []
    assert st["overall"]["state"] == "healthy"


# ── the reporting bugs found in the built app, 2026-09-29 ───────────────────


def test_the_codec_is_found_even_when_it_is_not_on_PATH(data_dir, monkeypatch):
    """A Finder-launched .app inherits a minimal PATH with no Homebrew in it, so
    `shutil.which` alone reported ffmpeg missing on a machine that plainly has
    it at /opt/homebrew/bin/ffmpeg."""
    import shutil as _shutil
    from pathlib import Path as _Path

    monkeypatch.setattr(_shutil, "which", lambda name: None)
    monkeypatch.setattr(
        _Path, "exists",
        lambda self: str(self) == "/opt/homebrew/bin/ffmpeg",
    )

    codec = data_health._codec()

    assert codec["ok"] is True
    assert codec["ffmpeg"] == "/opt/homebrew/bin/ffmpeg"
    assert codec["on_path"] is False


def test_the_panel_no_longer_counts_toward_seven_nights(data_dir, tmp_path, monkeypatch):
    """That gate was dropped on 2026-09-29 after being argued through. A panel
    still counting toward it would keep nagging about something nobody is
    waiting for."""
    from config import settings
    from services import backup_service, restore_service

    dest = tmp_path / "backups"
    dest.mkdir()
    monkeypatch.setattr(settings, "backup_destination", str(dest))
    backup_service.create_backup(dest, data_dir=data_dir)
    restore_service.run_drill(dest, data_dir=data_dir)

    st = data_health.status(data_dir)
    everything = " ".join(st["overall"]["problems"] + st["overall"]["warnings"])

    assert "of 7" not in everything
    assert "nights" not in everything


def test_a_failing_drill_is_a_problem_not_a_countdown(data_dir, tmp_path, monkeypatch):
    from config import settings
    from services import backup_service, restore_service

    dest = tmp_path / "backups"
    dest.mkdir()
    monkeypatch.setattr(settings, "backup_destination", str(dest))
    archive = backup_service.create_backup(dest, data_dir=data_dir).path
    raw = archive.read_bytes()
    archive.write_bytes(raw[: len(raw) // 2])
    restore_service.run_drill(dest, data_dir=data_dir)

    st = data_health.status(data_dir)
    assert any("FAILED" in p for p in st["overall"]["problems"])


def test_an_undrilled_backup_warns_only_once_a_backup_exists(data_dir, tmp_path, monkeypatch):
    """Nagging "backups are unproven" before any backup exists buries the real
    message, which is that there are no backups."""
    from config import settings

    dest = tmp_path / "backups"
    dest.mkdir()
    monkeypatch.setattr(settings, "backup_destination", str(dest))

    st = data_health.status(data_dir)
    assert not any("test-restored" in w for w in st["overall"]["warnings"])
    assert any("no backup has been taken" in p for p in st["overall"]["problems"])


def test_the_key_warning_names_what_is_unprotected_and_where_to_fix_it(data_dir, tmp_path, monkeypatch):
    from config import settings

    dest = tmp_path / "backups"
    dest.mkdir()
    monkeypatch.setattr(settings, "backup_destination", str(dest))
    keyvault.get_or_create("credentials")

    problems = " ".join(data_health.status(data_dir)["overall"]["problems"])
    assert "credentials" in problems
    assert "Settings → Recovery" in problems
