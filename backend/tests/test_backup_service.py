"""LB-10 item 2: the encrypted backup archive.

Until this existed there was no backup or restore anywhere in LocalBook, which
is the largest risk in front of LB-11 moving the data dir onto an encrypted
volume and LB-12 rewriting ids for sync.

The tests that matter are the ones about what is IN it and whether it can be
trusted. "It wrote a file" is not a backup; the failure this guards against is
an archive that looks fine for months and turns out to be missing the one thing
that could not be rebuilt.
"""

import json
import secrets
import sqlite3
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

from services import backup_service, keyvault


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
    """A data dir shaped like the real one, including the traps."""
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(d / "localbook.db")
    conn.execute("CREATE TABLE sources (id TEXT PRIMARY KEY, content TEXT)")
    conn.executemany("INSERT INTO sources VALUES (?, ?)",
                     [(f"s{i}", f"body {i}") for i in range(7)])
    conn.execute("CREATE TABLE notebooks (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO notebooks VALUES ('nb1')")
    conn.commit()
    conn.close()

    (d / "memory").mkdir()
    conn = sqlite3.connect(d / "memory" / "recall_memory.db")
    conn.execute("CREATE TABLE recall (id INTEGER PRIMARY KEY, text TEXT)")
    conn.execute("INSERT INTO recall (text) VALUES ('remembered')")
    conn.commit()
    conn.close()
    (d / "memory" / "core_memory.json").write_text('{"core": "yes"}')
    (d / "memory" / "archival_memory").mkdir()
    (d / "memory" / "archival_memory" / "table.lance").write_bytes(b"archival bytes")

    (d / "curator_brain").mkdir()
    conn = sqlite3.connect(d / "curator_brain" / "brain.db")
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, action TEXT)")
    conn.commit()
    conn.close()
    (d / "curator_brain" / "lancedb").mkdir()
    (d / "curator_brain" / "lancedb" / "vectors.lance").write_bytes(b"derived")

    (d / "notebooks").mkdir()
    (d / "notebooks" / "nb1.json").write_text('{"id": "nb1"}')
    (d / "sources.json").write_text('{"s1": {}}')
    (d / "curator_config.yaml").write_text("enabled: true\n")

    # The things that must NOT be in the archive.
    (d / "lancedb").mkdir()
    (d / "lancedb" / "notebook_nb1.lance").write_bytes(b"x" * 4096)
    (d / "lancedb_backup_20260326_143748").mkdir()
    (d / "lancedb_backup_20260326_143748" / "old.lance").write_bytes(b"orphan")
    (d / "models").mkdir()
    (d / "models" / "big.bin").write_bytes(b"y" * 2048)
    (d / "answer_cache.json").write_text('{"cached": true}')
    (d / "rag_metrics.json").write_text('{"queries": 1}')
    (d / "diagnostics.log").write_text("noise")
    (d / "localbook.db-wal").write_bytes(b"wal")

    (d / "audio").mkdir()
    (d / "audio" / "episode.mp3").write_bytes(b"z" * 8192)
    return d


@pytest.fixture
def dest(tmp_path):
    p = tmp_path / "backups"
    p.mkdir()
    return p


def _members(result):
    """Every path inside the archive's manifest."""
    return set(result.manifest["files"])


# ── what goes in ────────────────────────────────────────────────────────────


def test_the_irreplaceable_things_are_included(data_dir, dest):
    files = _members(backup_service.create_backup(dest, data_dir=data_dir))

    for expected in (
        "localbook.db",
        "memory/recall_memory.db",
        "memory/core_memory.json",
        "curator_brain/brain.db",
        "notebooks/nb1.json",
        "sources.json",
        "curator_config.yaml",
    ):
        assert expected in files, f"{expected} missing from the archive"


def test_lancedb_is_excluded(data_dir, dest):
    """D19: derived from sources.content, rebuilt by /reindex/all. Excluding it
    is also what makes a HOT backup possible — no maintenance lock."""
    files = _members(backup_service.create_backup(dest, data_dir=data_dir))
    assert not any(f.startswith("lancedb/") for f in files)
    assert "curator_brain/lancedb/vectors.lance" not in files


def test_archival_memory_is_NOT_excluded_with_the_rest_of_lancedb(data_dir, dest):
    """The trap. `memory/archival_memory` is LanceDB, but unlike every other
    LanceDB table it is not derived from anything — its text exists nowhere else
    until LB-12d moves it into SQLite. Sweeping it up with the D19 exclusion
    would silently discard the user's archival memory."""
    files = _members(backup_service.create_backup(dest, data_dir=data_dir))
    assert "memory/archival_memory/table.lance" in files


def test_derived_and_disposable_things_are_excluded(data_dir, dest):
    files = _members(backup_service.create_backup(dest, data_dir=data_dir))
    for unwanted in (
        "models/big.bin",
        "lancedb_backup_20260326_143748/old.lance",
        "answer_cache.json",
        "rag_metrics.json",
        "diagnostics.log",
        "localbook.db-wal",
    ):
        assert unwanted not in files, f"{unwanted} should not be archived"


def test_blobs_can_be_left_out(data_dir, dest):
    """500 MB of generated audio dominates every archive and is regenerable."""
    with_blobs = _members(backup_service.create_backup(dest, data_dir=data_dir))
    without = _members(
        backup_service.create_backup(dest, data_dir=data_dir, include_blobs=False)
    )
    assert "audio/episode.mp3" in with_blobs
    assert "audio/episode.mp3" not in without
    assert "localbook.db" in without


# ── the databases ───────────────────────────────────────────────────────────


def test_databases_are_snapshotted_not_copied(data_dir, dest, tmp_path):
    """A file copy of a live DB with a WAL beside it is a torn page, not a
    database — it opens without complaint and is wrong."""
    result = backup_service.create_backup(dest, data_dir=data_dir)
    opened = backup_service.open_archive(result.path)

    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / "p.tar"
        tar_path.write_bytes(opened["payload"])
        with tarfile.open(tar_path) as tar:
            tar.extractall(Path(tmp) / "out", filter="data")
        db = Path(tmp) / "out" / "data" / "localbook.db"

        conn = sqlite3.connect(db)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 7
        conn.close()


def test_row_counts_are_recorded_for_every_database(data_dir, dest):
    """So a restore can prove it got everything, not merely got a file."""
    manifest = backup_service.create_backup(dest, data_dir=data_dir).manifest
    counts = manifest["row_counts"]
    assert counts["localbook.db"]["sources"] == 7
    assert counts["localbook.db"]["notebooks"] == 1
    assert counts["memory/recall_memory.db"]["recall"] == 1


def test_the_manifest_carries_the_schema_head_and_device(data_dir, dest):
    manifest = backup_service.create_backup(dest, data_dir=data_dir).manifest
    assert manifest["device_id"]
    assert "schema" in manifest
    assert manifest["format_version"] == backup_service.FORMAT_VERSION


# ── encryption ──────────────────────────────────────────────────────────────


def test_the_archive_is_not_readable_as_plaintext(data_dir, dest):
    result = backup_service.create_backup(dest, data_dir=data_dir)
    raw = result.path.read_bytes()
    assert b"remembered" not in raw
    assert b"curator_config" not in raw
    assert b"nb1.json" not in raw


def test_file_names_do_not_leak_in_the_public_header(data_dir, dest):
    """The manifest lives INSIDE the ciphertext: file names alone say a great
    deal, and an IMAP account name would otherwise sit in the clear."""
    result = backup_service.create_backup(dest, data_dir=data_dir)
    header = backup_service.read_header(result.path)
    blob = json.dumps(header)
    assert "sources.json" not in blob
    assert "core_memory" not in blob
    assert set(header) == {"format_version", "created_at", "device_id", "recipients"}


def test_this_device_can_open_its_own_archive(data_dir, dest):
    result = backup_service.create_backup(dest, data_dir=data_dir)
    opened = backup_service.open_archive(result.path)
    assert opened["manifest"]["device_id"] == result.manifest["device_id"]


def test_the_recovery_phrase_opens_it_too(data_dir, dest):
    """Two independent ways back in — a replacement Mac has no Keychain item."""
    phrase = keyvault.generate_recovery_phrase()
    keyvault.set_recovery_key(phrase)

    result = backup_service.create_backup(dest, data_dir=data_dir)
    kinds = {r["kind"] for r in backup_service.read_header(result.path)["recipients"]}
    assert kinds == {"recovery", "device"}

    # Simulate a different machine: no device key, phrase only.
    keyvault.delete("backup")
    opened = backup_service.open_archive(result.path, phrase=phrase)
    assert opened["manifest"]["files"]


def test_the_wrong_phrase_does_not_open_it(data_dir, dest):
    phrase = keyvault.generate_recovery_phrase()
    keyvault.set_recovery_key(phrase)
    result = backup_service.create_backup(dest, data_dir=data_dir)
    keyvault.delete("backup")

    with pytest.raises(ValueError, match="could not unlock"):
        backup_service.open_archive(
            result.path, phrase=keyvault.generate_recovery_phrase()
        )


def test_a_tampered_header_is_refused(data_dir, dest):
    """The header is AAD, so back-dating an archive or swapping its recipients
    breaks decryption rather than going unnoticed."""
    result = backup_service.create_backup(dest, data_dir=data_dir)
    raw = bytearray(result.path.read_bytes())

    offset = len(backup_service.MAGIC)
    header_len = int.from_bytes(raw[offset:offset + 4], "big")
    header = json.loads(raw[offset + 4:offset + 4 + header_len].decode())
    header["created_at"] = "1999-01-01T00:00:00+00:00"
    forged = json.dumps(header, sort_keys=True).encode()

    # Rewrite the length prefix too — a real forger would. The point is that
    # even a perfectly well-formed header fails, because it is the AAD.
    rebuilt = bytearray(raw[:offset])
    rebuilt += len(forged).to_bytes(4, "big")
    rebuilt += forged
    rebuilt += raw[offset + 4 + header_len:]
    result.path.write_bytes(bytes(rebuilt))

    with pytest.raises(Exception):
        backup_service.open_archive(result.path)


def test_a_truncated_archive_is_refused(data_dir, dest):
    result = backup_service.create_backup(dest, data_dir=data_dir)
    raw = result.path.read_bytes()
    result.path.write_bytes(raw[: len(raw) // 2])
    with pytest.raises(Exception):
        backup_service.open_archive(result.path)


def test_something_that_is_not_an_archive_is_refused(dest):
    bogus = dest / "not-a-backup.lbbackup"
    bogus.write_bytes(b"just some bytes")
    with pytest.raises(ValueError, match="not a LocalBook backup"):
        backup_service.open_archive(bogus)


# ── verification, which is what the drill runs ──────────────────────────────


def test_verify_checks_every_file_against_its_hash(data_dir, dest):
    result = backup_service.create_backup(dest, data_dir=data_dir)
    report = backup_service.verify_archive(result.path)
    assert report["ok"] is True
    # MANIFEST.json is in the tar but not in its own file list — it cannot
    # contain its own hash — so every listed file is checked.
    assert report["checked"] == len(result.manifest["files"])
    assert "MANIFEST.json" not in result.manifest["files"]
    assert report["missing"] == []
    assert report["mismatched"] == []


def test_verify_notices_a_corrupted_member(data_dir, dest, monkeypatch):
    """"The file exists and is the right size" is not verification — a silently
    corrupted archive passes that and fails when it is finally needed."""
    result = backup_service.create_backup(dest, data_dir=data_dir)
    manifest = result.manifest
    # Corrupt the RECORD rather than the ciphertext, which is what a drill would
    # catch if a file had been swapped before sealing.
    victim = next(k for k in manifest["files"] if k.endswith("core_memory.json"))

    real_open = backup_service.open_archive

    def tampered(archive, **kw):
        opened = real_open(archive, **kw)
        opened["manifest"]["files"][victim]["sha256"] = "0" * 64
        return opened

    monkeypatch.setattr(backup_service, "open_archive", tampered)
    report = backup_service.verify_archive(result.path)

    assert report["ok"] is False
    assert victim in report["mismatched"]


# ── destination safety ──────────────────────────────────────────────────────


def test_backing_up_into_the_data_dir_is_refused(data_dir):
    """An archive stored inside what it backs up is not a backup — and after
    LB-11 it would also be inside the encrypted volume it must survive."""
    with pytest.raises(ValueError, match="outside the data directory"):
        backup_service.create_backup(data_dir / "backups", data_dir=data_dir)


def test_backing_up_into_the_data_dir_itself_is_refused(data_dir):
    with pytest.raises(ValueError, match="outside the data directory"):
        backup_service.create_backup(data_dir, data_dir=data_dir)


def test_the_archive_is_owner_only(data_dir, dest):
    result = backup_service.create_backup(dest, data_dir=data_dir)
    assert result.path.stat().st_mode & 0o777 == 0o600


def test_two_backups_do_not_collide(data_dir, dest):
    a = backup_service.create_backup(dest, data_dir=data_dir)
    b = backup_service.create_backup(dest, data_dir=data_dir)
    assert a.path != b.path or a.path.exists()


# ── running while the app is writing ────────────────────────────────────────


def test_a_file_vanishing_mid_backup_does_not_fail_the_backup(data_dir, dest, monkeypatch):
    """A hot backup runs while the enrichment worker, collector, folder watcher
    and audio generation are all free to write. Between rglob listing a path and
    copy2 reading it, that path can be rotated or evicted — and an unguarded
    copy propagated FileNotFoundError and failed the ENTIRE backup.

    Failing a whole backup because one disposable file moved is the wrong trade.
    """
    real_copy = backup_service.shutil.copy2
    victim = data_dir / "notebooks" / "nb1.json"

    def flaky(src, dst, *a, **k):
        if Path(src) == victim:
            raise FileNotFoundError(src)
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(backup_service.shutil, "copy2", flaky)

    result = backup_service.create_backup(dest, data_dir=data_dir)

    assert result.path.is_file()
    assert "localbook.db" in result.manifest["files"]


def test_a_vanished_file_is_recorded_not_swallowed(data_dir, dest, monkeypatch):
    """A restore has to be able to see what was not captured."""
    real_copy = backup_service.shutil.copy2
    victim = data_dir / "notebooks" / "nb1.json"

    def flaky(src, dst, *a, **k):
        if Path(src) == victim:
            raise FileNotFoundError(src)
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(backup_service.shutil, "copy2", flaky)
    result = backup_service.create_backup(dest, data_dir=data_dir)

    assert "notebooks/nb1.json" in result.manifest["skipped"]
    assert "notebooks/nb1.json" not in result.manifest["files"]


def test_a_clean_backup_records_nothing_as_skipped(data_dir, dest):
    assert backup_service.create_backup(dest, data_dir=data_dir).manifest["skipped"] == []


def test_a_skipped_file_does_not_make_the_drill_red(data_dir, dest, monkeypatch):
    """The manifest lists what it actually captured, so verification stays
    consistent — a skipped file must not look like a corrupt one."""
    from services import restore_service

    real_copy = backup_service.shutil.copy2
    # A flag, NOT monkeypatch.undo(): undo() unwinds EVERY patch this test's
    # fixtures made, including the throwaway keychain service name — so the
    # unseal then read a different key and failed with InvalidTag. Second time
    # this exact trap has bitten in this codebase.
    breaking = {"on": True}

    def flaky(src, dst, *a, **k):
        if breaking["on"] and Path(src).name == "nb1.json":
            raise FileNotFoundError(src)
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(backup_service.shutil, "copy2", flaky)
    result = backup_service.create_backup(dest, data_dir=data_dir)
    breaking["on"] = False

    assert restore_service.verify(result.path).ok is True


def test_the_wrapped_keys_are_backed_up_from_where_they_live_now(data_dir, dest):
    """LB-11 moved the wrapped keys BESIDE the data dir. Backups kept reading the old
    spot inside it, so an archive restored on a new Mac had no recovery copy of the
    credential key — the mail passwords were gone even with the phrase."""
    from services import keyvault

    keys = keyvault._keys_dir()
    assert keys.parent == data_dir.parent                # beside, not inside
    (keys / "dev-a").mkdir(parents=True, exist_ok=True)
    (keys / "dev-a" / "credentials.wrapped").write_text("sealed")

    files = _members(backup_service.create_backup(dest, data_dir=data_dir))
    assert "LocalBook.keys/dev-a/credentials.wrapped" in files


def test_a_restore_hands_the_old_macs_keys_to_keyvault(data_dir):
    """A restored archive lands its keys in the data dir's legacy spot. They must
    reach the keys dir — without touching this Mac's own set."""
    from services import keyvault

    keys = keyvault._keys_dir()
    (keys / "this-mac").mkdir(parents=True, exist_ok=True)
    (keys / "this-mac" / "backup.wrapped").write_text("mine")
    restored = data_dir / "LocalBook.keys"
    for dev, body in (("old-mac", "theirs"), ("this-mac", "stale")):
        (restored / dev).mkdir(parents=True, exist_ok=True)
        (restored / dev / "backup.wrapped").write_text(body)

    assert keyvault.adopt_restored_keys(data_dir) == ["old-mac"]
    assert (keys / "old-mac" / "backup.wrapped").read_text() == "theirs"
    assert (keys / "this-mac" / "backup.wrapped").read_text() == "mine"
