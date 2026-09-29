"""K-1: migrating the credential locker off the machine-derived key.

The old key was `PBKDF2(hostname + username, "LocalBook-Default-Key")` — every
input public, and invalidated by renaming the Mac. These tests cover the move to
the keyvault key, and in particular the ways it must refuse to proceed: the
files hold the user's IMAP and SMTP passwords, and a half-finished migration
that eats the only copy is the failure that actually matters.

Isolation: `settings.data_dir` is a tmp_path and the keychain service name is a
throwaway. `backend/.venv` resolves `settings.data_dir` to the REAL production
data dir, so neither may be left at its default.
"""

import asyncio
import base64
import json
import secrets
import subprocess

import pytest
from cryptography.fernet import Fernet

from services import keyvault
from services.credential_locker import CredentialLocker


@pytest.fixture
def locker(tmp_path, monkeypatch):
    service = f"LocalBook-keyvault-test-{secrets.token_hex(6)}"
    monkeypatch.setattr(keyvault, "SERVICE_NAME", service)

    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    real_run = subprocess.run
    try:
        yield CredentialLocker()
    finally:
        for purpose in keyvault.PURPOSES:
            real_run(
                ["security", "delete-generic-password", "-a", purpose, "-s", service],
                capture_output=True,
            )


def _write_legacy(locker, payload: dict) -> bytes:
    """Write credentials.enc exactly as a pre-K-1 build would have."""
    legacy = Fernet(locker._derive_key())
    blob = legacy.encrypt(json.dumps(payload).encode())
    locker._data_dir.mkdir(parents=True, exist_ok=True)
    locker._credentials_file.write_bytes(blob)
    return blob


IMAP_ENTRY = {
    "imap:person@example.com": {
        "site_domain": "imap:person@example.com",
        "site_name": "IMAP: person@example.com",
        "username": "person@example.com",
        "password": "app-specific-password-that-must-survive",
        "login_method": "imap_app_password",
        "notes": json.dumps({"imap_host": "imap.example.com", "last_uid": 41}),
    }
}


# ── the happy paths ─────────────────────────────────────────────────────────


def test_a_fresh_install_claims_the_marker_without_migrating(locker):
    result = locker.migrate_to_keyvault()
    assert result["migrated"] is False
    assert result["reason"] == "nothing to migrate"
    assert locker._keyvault_marker.exists()


def test_a_real_legacy_file_migrates_and_the_password_survives(locker):
    _write_legacy(locker, IMAP_ENTRY)

    result = locker.migrate_to_keyvault()
    assert result["migrated"] is True
    assert "credentials.enc" in result["files"]

    # Readable under the NEW key...
    new = Fernet(locker._keyvault_key())
    restored = json.loads(new.decrypt(locker._credentials_file.read_bytes()))
    assert (
        restored["imap:person@example.com"]["password"]
        == "app-specific-password-that-must-survive"
    )

    # ...and no longer under the old one.
    with pytest.raises(Exception):
        Fernet(locker._derive_key()).decrypt(locker._credentials_file.read_bytes())


def test_the_original_is_kept(locker):
    original = _write_legacy(locker, IMAP_ENTRY)
    locker.migrate_to_keyvault()

    kept = locker._credentials_file.with_suffix(".enc.pre-keyvault")
    assert kept.exists()
    assert kept.read_bytes() == original


def test_auth_state_files_migrate_too(locker):
    """social_auth reuses the locker's Fernet, so its .enc files move with it."""
    _write_legacy(locker, IMAP_ENTRY)
    auth_dir = locker._data_dir / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    state = {"cookies": [{"name": "session", "value": "abc"}]}
    (auth_dir / "linkedin_state.enc").write_bytes(
        Fernet(locker._derive_key()).encrypt(json.dumps(state).encode())
    )

    result = locker.migrate_to_keyvault()
    assert sorted(result["files"]) == ["credentials.enc", "linkedin_state.enc"]

    new = Fernet(locker._keyvault_key())
    assert json.loads(new.decrypt((auth_dir / "linkedin_state.enc").read_bytes())) == state


def test_migration_is_idempotent(locker):
    _write_legacy(locker, IMAP_ENTRY)
    assert locker.migrate_to_keyvault()["migrated"] is True

    second = locker.migrate_to_keyvault()
    assert second["migrated"] is False
    assert second["reason"] == "already migrated"

    # And it did not re-wrap an already-wrapped file.
    new = Fernet(locker._keyvault_key())
    assert json.loads(new.decrypt(locker._credentials_file.read_bytes())) == IMAP_ENTRY


def test_the_locker_reads_and_writes_across_the_migration(locker):
    _write_legacy(locker, IMAP_ENTRY)

    async def exercise():
        existing = await locker.get_credential("imap:person@example.com")
        assert existing["password"] == "app-specific-password-that-must-survive"

        await locker.add_credential(
            site_domain="example.org",
            site_name="Example",
            username="someone",
            password="hunter2",
        )
        fresh = CredentialLocker()  # a new process, reading what we just wrote
        assert (await fresh.get_credential("example.org"))["password"] == "hunter2"
        assert (await fresh.get_credential("imap:person@example.com")) is not None

    asyncio.run(exercise())


# ── the refusals ────────────────────────────────────────────────────────────


def test_an_undecryptable_legacy_file_aborts_and_changes_nothing(locker):
    """The renamed-Mac case. Better to stop than to write an empty store over it."""
    locker._data_dir.mkdir(parents=True, exist_ok=True)
    locker._credentials_file.write_bytes(b"not fernet ciphertext at all")

    with pytest.raises(RuntimeError, match="cannot decrypt"):
        locker.migrate_to_keyvault()

    assert locker._credentials_file.read_bytes() == b"not fernet ciphertext at all"
    assert not locker._keyvault_marker.exists()
    assert not locker._credentials_file.with_suffix(".enc.pre-keyvault").exists()


def test_it_fails_closed_when_the_keyvault_key_is_unavailable(locker, monkeypatch):
    """It must NOT quietly fall back to the legacy key — that would undo K-1 and
    say nothing."""
    _write_legacy(locker, IMAP_ENTRY)
    monkeypatch.setattr(
        keyvault, "get_or_create", lambda purpose: (_ for _ in ()).throw(
            keyvault.KeyVaultError("keychain is locked")
        )
    )

    with pytest.raises(keyvault.KeyVaultError):
        locker._ensure_initialized()

    assert not locker._keyvault_marker.exists()
    # Still decryptable with the legacy key: nothing was rewritten.
    assert json.loads(
        Fernet(locker._derive_key()).decrypt(locker._credentials_file.read_bytes())
    ) == IMAP_ENTRY


def test_a_partial_batch_leaves_every_original_in_place(locker, monkeypatch):
    """One unreadable file in the batch must not half-migrate the others."""
    _write_legacy(locker, IMAP_ENTRY)
    auth_dir = locker._data_dir / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "broken_state.enc").write_bytes(b"corrupt")

    with pytest.raises(RuntimeError, match="cannot decrypt"):
        locker.migrate_to_keyvault()

    # credentials.enc was decryptable, but nothing was swapped.
    assert json.loads(
        Fernet(locker._derive_key()).decrypt(locker._credentials_file.read_bytes())
    ) == IMAP_ENTRY
    assert not list(locker._data_dir.glob("*.keyvault-tmp"))
    assert not list(auth_dir.glob("*.keyvault-tmp"))


def test_the_legacy_key_is_still_the_public_value_it_always_was(locker):
    """Guards the premise K-1 rests on. If this ever stops holding, the migration
    path above can no longer read old files and needs revisiting."""
    import getpass
    import hashlib
    import platform
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt = hashlib.sha256(
        f"{platform.node()}-{getpass.getuser()}-LocalBook-v1".encode()
    ).digest()
    expected = base64.urlsafe_b64encode(
        PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000)
        .derive(b"LocalBook-Default-Key")
    )
    assert locker._derive_key() == expected
