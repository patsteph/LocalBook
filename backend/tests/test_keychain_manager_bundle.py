"""API-key bundle: stored rebuild-proof, migrated once from keyring, safely.

Real keychain items under a throwaway service name; `keyring` is faked so the
user's real "LocalBook"/"api_keys" item is never read, written or deleted.
"""

import json
import secrets
import subprocess

import pytest

from services import keychain_manager as km
from services import keyvault


@pytest.fixture
def store(tmp_path, monkeypatch):
    from config import settings

    service = f"LocalBook-keyvault-test-{secrets.token_hex(6)}"
    monkeypatch.setattr(keyvault, "SERVICE_NAME", service)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "LocalBook")
    monkeypatch.setattr(km, "_bundle_migrated", False)
    monkeypatch.setattr(km, "_migration_done", True)          # skip the per-key legacy pass

    legacy = {}
    monkeypatch.setattr(km.keyring, "get_password", lambda s, k: legacy.get((s, k)))

    def _delete(s, k):
        legacy.pop((s, k), None)

    monkeypatch.setattr(km.keyring, "delete_password", _delete)
    names = {"svc": None}
    try:
        yield legacy, names
    finally:
        for svc in {keyvault.service_name(), service}:
            subprocess.run(["security", "delete-generic-password", "-a", "api_keys", "-s", svc],
                           capture_output=True)


def _as_production(monkeypatch):
    # The test's throwaway name stands in for production's — never the real one.
    monkeypatch.setattr(keyvault, "service_name", lambda: keyvault.SERVICE_NAME)


def test_keys_round_trip_through_the_keyvault_item(store):
    km.set_api_key("brave_api_key", "k-123")
    assert km._load_bundle() == {"brave_api_key": "k-123"}
    raw = keyvault._keychain_read("api_keys")
    assert json.loads(raw.decode()) == {"brave_api_key": "k-123"}


def test_production_moves_the_keyring_bundle_once_and_removes_it(store, monkeypatch):
    legacy, _ = store
    _as_production(monkeypatch)
    legacy[(km.SERVICE_NAME, km.BUNDLE_KEY)] = json.dumps({"openai_api_key": "sk-x"})

    assert km._load_bundle() == {"openai_api_key": "sk-x"}
    assert (km.SERVICE_NAME, km.BUNDLE_KEY) not in legacy


def test_a_non_production_install_never_touches_the_keyring_bundle(store):
    """A dev/test install must not copy the user's keys and delete the original."""
    legacy, _ = store
    legacy[(km.SERVICE_NAME, km.BUNDLE_KEY)] = json.dumps({"openai_api_key": "sk-x"})

    assert km._load_bundle() == {}
    assert (km.SERVICE_NAME, km.BUNDLE_KEY) in legacy


def test_an_unreadable_keyring_bundle_is_left_alone(store, monkeypatch):
    legacy, _ = store
    _as_production(monkeypatch)
    legacy[(km.SERVICE_NAME, km.BUNDLE_KEY)] = "not json"

    assert km._load_bundle() == {}
    assert legacy[(km.SERVICE_NAME, km.BUNDLE_KEY)] == "not json"


def test_an_existing_new_item_wins_and_the_keyring_is_not_read(store, monkeypatch):
    legacy, _ = store
    _as_production(monkeypatch)
    keyvault._keychain_write("api_keys", json.dumps({"brave_api_key": "new"}).encode())
    legacy[(km.SERVICE_NAME, km.BUNDLE_KEY)] = json.dumps({"brave_api_key": "old"})

    assert km._load_bundle() == {"brave_api_key": "new"}
