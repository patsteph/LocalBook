"""Recovery copies held by paired Macs, and the guarded cross-Mac restore.

Isolation as everywhere in K-1: a tmp data dir and a throwaway keychain service
name. The synced documents table is replaced by a dict — what is under test is
what goes in and what comes back out, not the sync engine (tested elsewhere).
"""

import os
import secrets
import subprocess
import time

import pytest

from services import key_escrow, keyvault


@pytest.fixture
def mac(tmp_path, monkeypatch):
    service = f"LocalBook-keyvault-test-{secrets.token_hex(6)}"
    monkeypatch.setattr(keyvault, "SERVICE_NAME", service)
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "LocalBook")

    docs = {}
    from storage import documents
    monkeypatch.setattr(documents, "put", lambda k, key, body: docs.__setitem__((k, key), body))
    monkeypatch.setattr(documents, "get", lambda k, key, default=None: docs.get((k, key), default))
    monkeypatch.setattr(documents, "items", lambda k, prefix="": [(key, b) for (kk, key), b in docs.items() if kk == k])
    monkeypatch.setattr("services.sync.identity.device_name", lambda: "Old MacBook")
    try:
        yield docs
    finally:
        for purpose in keyvault.PURPOSES:
            subprocess.run(["security", "delete-generic-password", "-a", purpose,
                            "-s", keyvault.service_name()], capture_output=True)


def _set_up(phrase):
    keyvault.set_recovery_key(phrase)
    keyvault.get_or_create("credentials")
    keyvault.get_or_create("volume")
    keyvault.wrap_all()


def test_a_macs_wrapped_keys_are_published_for_its_paired_macs(mac):
    phrase = keyvault.generate_recovery_phrase()
    _set_up(phrase)
    assert key_escrow.publish() is True
    body = mac[("key_escrow", keyvault.device_id())]
    assert set(body["keys"]) >= {"credentials", "volume"}
    assert body["name"] == "Old MacBook"
    words = phrase.split()                                  # nothing secret goes out (a single
    assert " ".join(words[:3]) not in str(body)             # BIP-39 word can collide with a JSON key)
    assert key_escrow.publish() is False                    # unchanged → no rewrite


def test_a_replacement_mac_recovers_the_old_macs_keys_from_a_paired_mac(mac, monkeypatch):
    """Dead disk: the old Mac's keys dir is gone. A paired Mac's copy plus the
    phrase brings back the credential key — and the new Mac's own volume password
    is NOT replaced by the old one, which would lock it out of its own volume."""
    phrase = keyvault.generate_recovery_phrase()
    _set_up(phrase)
    old = keyvault.device_id()
    old_creds = keyvault._keychain_read("credentials")
    key_escrow.publish()

    import shutil
    shutil.rmtree(keyvault._keys_dir() / old)              # the old disk is gone
    monkeypatch.setattr(keyvault, "device_id", lambda: "replacement-mac")
    subprocess.run(["security", "delete-generic-password", "-a", "credentials",
                    "-s", keyvault.service_name()], capture_output=True)
    keyvault._keychain_write("volume", b"the-new-macs-own-volume-key-32b!")

    sets = {s["device_id"]: s for s in key_escrow.key_sets()}
    assert sets[old]["name"] == "Old MacBook" and "a paired Mac" in sets[old]["sources"]

    out = key_escrow.restore(phrase, device=old)
    assert "credentials" in out["restored"]
    assert keyvault._keychain_read("credentials") == old_creds
    assert "volume" in out["kept"]
    assert keyvault._keychain_read("volume") == b"the-new-macs-own-volume-key-32b!"


def test_replace_never_applies_to_the_volume_or_identity(mac, monkeypatch):
    phrase = keyvault.generate_recovery_phrase()
    _set_up(phrase)
    old = keyvault.device_id()
    key_escrow.publish()
    monkeypatch.setattr(keyvault, "device_id", lambda: "replacement-mac")
    keyvault._keychain_write("volume", b"the-new-macs-own-volume-key-32b!")
    out = key_escrow.restore(phrase, device=old, replace=True)
    assert "credentials" in out["restored"]                # replace honoured here
    assert "volume" in out["kept"]                         # never here
    assert keyvault._keychain_read("volume") == b"the-new-macs-own-volume-key-32b!"


def test_the_wrong_phrase_restores_nothing(mac, monkeypatch):
    _set_up(keyvault.generate_recovery_phrase())
    old = keyvault.device_id()
    key_escrow.publish()
    monkeypatch.setattr(keyvault, "device_id", lambda: "replacement-mac")
    subprocess.run(["security", "delete-generic-password", "-a", "credentials",
                    "-s", keyvault.service_name()], capture_output=True)
    out = key_escrow.restore(keyvault.generate_recovery_phrase(), device=old, purposes=["credentials"])
    assert out["restored"] == [] and "credentials" in out["failed"]


def test_the_phrase_check_comes_due_after_ninety_days(mac):
    assert key_escrow.phrase_check()["configured"] is False
    _set_up(keyvault.generate_recovery_phrase())
    assert key_escrow.phrase_check()["due"] is False       # setting it up proves it
    old = time.time() - 91 * 86400
    os.utime(keyvault._recovery_pub_path(), (old, old))
    assert key_escrow.phrase_check()["due"] is True
    key_escrow.mark_phrase_checked()
    assert key_escrow.phrase_check() == {"configured": True, "days": 0, "due": False}

    from services.data_health import _overall
    st = _overall({"keys": {"unprotected_purposes": [],
                            "phrase_check": {"configured": True, "days": 120, "due": True}}})
    assert any("120 days" in w for w in st["warnings"])
