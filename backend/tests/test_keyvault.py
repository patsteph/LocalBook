"""K-1 key custody tests.

These touch the REAL login keychain, because the whole point of the module is the
permissive ACL that only a real keychain item has — a mocked `security(1)` would
prove nothing about the property under test, which is the lesson recorded as
"verify the artifact, not the step".

They stay safe by writing under a throwaway, per-run service name and deleting
every item afterwards, and by pointing `settings.data_dir` at a tmp_path. Nothing
here may touch the production data dir: `backend/.venv` resolves `settings.data_dir`
to the user's real one.
"""

import base64
import json
import secrets
import subprocess

import pytest

from services import keyvault
from services.keyvault import KeyVaultError


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Isolate the vault: temp data dir + a throwaway keychain service name."""
    service = f"LocalBook-keyvault-test-{secrets.token_hex(6)}"
    monkeypatch.setattr(keyvault, "SERVICE_NAME", service)

    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    # Pinned before the test body can monkeypatch subprocess.run. Cleanup runs
    # before monkeypatch unwinds, and a throwaway keychain item that outlives its
    # test is exactly the litter this fixture exists to prevent.
    real_run = subprocess.run
    try:
        yield keyvault
    finally:
        for purpose in keyvault.PURPOSES:
            real_run(
                ["security", "delete-generic-password", "-a", purpose, "-s", service],
                capture_output=True,
            )


@pytest.fixture
def phrase():
    return keyvault.generate_recovery_phrase()


# ── basics ──────────────────────────────────────────────────────────────────


def test_generate_recovery_phrase_is_24_valid_words():
    from mnemonic import Mnemonic

    p = keyvault.generate_recovery_phrase()
    assert len(p.split()) == 24
    assert Mnemonic("english").check(p)
    assert p != keyvault.generate_recovery_phrase()


def test_get_or_create_is_stable_and_isolated_per_purpose(vault):
    first = vault.get_or_create("credentials")
    assert len(first) == vault.KEY_BYTES
    assert vault.get_or_create("credentials") == first
    assert vault.get_or_create("backup") != first


def test_unknown_purpose_is_refused(vault):
    with pytest.raises(KeyVaultError, match="unknown purpose"):
        vault.get_or_create("volume")  # belongs to macOS per D20, not to us


def test_device_id_is_stable_and_not_the_hostname(vault, tmp_path):
    import platform

    first = vault.device_id()
    assert first == vault.device_id()
    assert platform.node() not in first
    assert (tmp_path / "LocalBook.keys" / "device_id").exists()


# ── recovery: the round trip ────────────────────────────────────────────────


def test_wrap_then_unwrap_round_trips(vault, phrase):
    vault.set_recovery_key(phrase)
    key = vault.get_or_create("credentials")

    path = vault.wrap_for_recovery("credentials")
    assert path.exists()

    assert vault.unwrap_with_phrase(phrase, "credentials") == key


def test_only_the_public_half_is_ever_written(vault, phrase):
    """D6': the private key must not exist anywhere on the device."""
    vault.set_recovery_key(phrase)
    vault.get_or_create("backup")
    vault.wrap_for_recovery("backup")

    priv_raw = vault._recovery_private_key(phrase).private_bytes_raw()
    words = phrase.split()
    # Consecutive PAIRS, not single words. Scanning for individual words was
    # flaky at roughly 1 run in 5: the envelope JSON contains the field name
    # `device_id`, and `device` is itself a BIP-39 word — so the test failed on
    # a structural key name while no secret had leaked at all. A real leak
    # preserves word ORDER, which a pair catches and a lone common word does not.
    pairs = [f"{a} {b}".encode() for a, b in zip(words, words[1:])]

    for path in (vault._keys_dir()).rglob("*"):
        if path.is_file():
            blob = path.read_bytes()
            assert priv_raw not in blob
            assert base64.b64encode(priv_raw) not in blob
            assert phrase.encode() not in blob
            for pair in pairs:
                assert pair not in blob


def test_wrong_phrase_is_refused(vault, phrase):
    vault.set_recovery_key(phrase)
    vault.get_or_create("credentials")
    vault.wrap_for_recovery("credentials")

    other = vault.generate_recovery_phrase()
    with pytest.raises(KeyVaultError, match="does not match"):
        vault.unwrap_with_phrase(other, "credentials")


def test_an_invalid_phrase_is_refused_before_any_crypto(vault):
    with pytest.raises(KeyVaultError, match="not a valid 24-word"):
        vault.unwrap_with_phrase("not actually a bip39 phrase at all", "credentials")


def test_phrase_is_case_and_whitespace_insensitive(vault, phrase):
    vault.set_recovery_key(phrase)
    key = vault.get_or_create("credentials")
    vault.wrap_for_recovery("credentials")

    messy = "  " + "   ".join(phrase.upper().split()) + "\n"
    assert vault.unwrap_with_phrase(messy, "credentials") == key


def test_the_same_phrase_gives_the_same_public_key(phrase):
    """A replacement Mac must derive the identical key from the phrase alone."""
    assert keyvault.recovery_public_key_from_phrase(
        phrase
    ) == keyvault.recovery_public_key_from_phrase(phrase)


# ── recovery: the failure modes that matter ─────────────────────────────────


def test_a_missing_keychain_item_with_a_wrapped_copy_routes_to_recovery(vault, phrase):
    """The one that protects the data: never mint a new key over a recoverable one."""
    vault.set_recovery_key(phrase)
    original = vault.get_or_create("credentials")
    vault.wrap_for_recovery("credentials")

    vault.delete("credentials")  # simulate a wiped Keychain

    with pytest.raises(KeyVaultError, match="RECOVERABLE"):
        vault.get_or_create("credentials")

    # ...and the recovery flow puts the ORIGINAL key back, not a new one.
    assert vault.restore_from_phrase(phrase, "credentials") == original
    assert vault.get_or_create("credentials") == original


def test_a_missing_keychain_item_with_no_wrapped_copy_creates_one(vault):
    """The genuine first-run case still has to work."""
    key = vault.get_or_create("backup")
    assert len(key) == vault.KEY_BYTES


def test_a_renamed_envelope_is_caught_by_its_purpose_field(vault, phrase):
    """Copying backup.wrapped over credentials.wrapped must not pass one off as
    the other."""
    vault.set_recovery_key(phrase)
    vault.get_or_create("backup")
    envelope = vault.wrapped_path("backup").read_text()
    (vault._keys_dir() / vault.device_id() / "credentials.wrapped").write_text(envelope)

    with pytest.raises(KeyVaultError, match="is for 'backup'"):
        vault.unwrap_with_phrase(phrase, "credentials")


def test_editing_the_purpose_field_is_caught_by_the_aead(vault, phrase):
    """And editing the field to match doesn't help: purpose is authenticated as
    AES-GCM associated data, so the ciphertext itself refuses."""
    vault.set_recovery_key(phrase)
    vault.get_or_create("backup")

    envelope = json.loads(vault.wrapped_path("backup").read_text())
    envelope["purpose"] = "credentials"
    (vault._keys_dir() / vault.device_id() / "credentials.wrapped").write_text(
        json.dumps(envelope)
    )

    with pytest.raises(KeyVaultError, match="does not match"):
        vault.unwrap_with_phrase(phrase, "credentials")


def test_a_future_envelope_version_is_refused_not_guessed(vault, phrase):
    vault.set_recovery_key(phrase)
    vault.get_or_create("backup")
    path = vault.wrap_for_recovery("backup")

    envelope = json.loads(path.read_text())
    envelope["version"] = 99
    path.write_text(json.dumps(envelope))

    with pytest.raises(KeyVaultError, match="version 99"):
        vault.unwrap_with_phrase(phrase, "backup")


def test_wrapping_without_a_recovery_key_is_refused(vault):
    vault.get_or_create("credentials")
    with pytest.raises(KeyVaultError, match="no recovery key is configured"):
        vault.wrap_for_recovery("credentials")


def test_another_devices_keys_can_be_recovered(vault, phrase):
    """A replacement Mac recovers the dead machine's key set by device id."""
    vault.set_recovery_key(phrase)
    key = vault.get_or_create("credentials")
    vault.wrap_for_recovery("credentials")
    dead_device = vault.device_id()

    # A new machine: same data dir (restored from backup), different device id.
    (vault._keys_dir() / "device_id").write_text(secrets.token_hex(8))
    assert vault.device_id() != dead_device

    assert vault.unwrap_with_phrase(phrase, "credentials", device=dead_device) == key


# ── fail closed ─────────────────────────────────────────────────────────────


def test_a_keychain_error_raises_rather_than_minting_a_key(vault, monkeypatch):
    """keyvault fails CLOSED. keychain_manager's fail-open is deliberate and separate."""

    def boom(args):
        return subprocess.CompletedProcess(args, 1, "", "keychain is locked")

    monkeypatch.setattr(vault, "_run_security", boom)
    with pytest.raises(KeyVaultError, match="keychain read"):
        vault.get_or_create("credentials")


def test_a_prompting_security_call_times_out_rather_than_hanging(vault, monkeypatch):
    """A prompt means the permissive ACL was lost. Startup must fail fast and say
    so, not block forever on a dialog nobody is looking at."""

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="security", timeout=1)

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(KeyVaultError, match="showing a prompt"):
        vault.get_or_create("credentials")


def test_a_truncated_key_is_refused(vault, monkeypatch):
    monkeypatch.setattr(vault, "_keychain_read", lambda purpose: b"tooshort")
    with pytest.raises(KeyVaultError, match="expected 32"):
        vault.get_or_create("credentials")


# ── the item really is permissive-ACL ───────────────────────────────────────


def test_the_item_is_readable_by_an_unrelated_binary_with_no_prompt(vault):
    """D20's actual claim: no app identity is consulted, so any build reads it.

    Guarded by a timeout — if the ACL were not permissive this would prompt, and a
    prompt in CI is a hang, not a failure.
    """
    key = vault.get_or_create("credentials")
    proc = subprocess.run(
        [
            "/usr/bin/python3",
            "-c",
            "import subprocess,sys;"
            f"r=subprocess.run(['security','find-generic-password','-a','credentials',"
            f"'-s','{vault.SERVICE_NAME}','-w'],capture_output=True,text=True);"
            "sys.stdout.write(r.stdout.strip())",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0
    assert base64.b64decode(proc.stdout.strip(), validate=True) == key


def test_the_bip39_wordlist_is_loadable_and_will_be_bundled():
    """Two halves, deliberately in one test.

    The functional half loads the real wordlist, so this proves something runs
    rather than that a string appears in a file. The structural half asserts the
    build collects it: `mnemonic` ships its wordlists as package DATA, which
    PyInstaller does not pick up from the import alone — without `--collect-all`
    the bundle would raise on `Mnemonic("english")` while every test here passed.
    That is the "verify the artifact, not the step" failure exactly.
    """
    from pathlib import Path

    from mnemonic import Mnemonic

    assert len(Mnemonic("english").wordlist) == 2048

    build_sh = Path(__file__).resolve().parents[1] / "build_backend.sh"
    contents = build_sh.read_text()
    assert "--collect-all=mnemonic" in contents
    assert "--hidden-import=services.keyvault" in contents


def test_status_reports_without_leaking_key_material(vault, phrase):
    vault.set_recovery_key(phrase)
    key = vault.get_or_create("credentials")
    vault.wrap_for_recovery("credentials")

    st = vault.status()
    assert st["recovery_key_configured"] is True
    assert st["purposes"]["credentials"] == {
        "in_keychain": True,
        "wrapped": True,
        "error": None,
    }
    assert st["purposes"]["backup"]["in_keychain"] is False
    assert base64.b64encode(key).decode() not in json.dumps(st)
