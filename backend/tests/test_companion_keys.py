"""LB-0: per-companion keys — hashed, scoped, independently revocable.

The store lives in the data dir, so every test points `settings.data_dir` at a
tmp_path. `backend/.venv` resolves it to the REAL production data dir.
"""

import json

import pytest

from services import companion_keys


@pytest.fixture
def store(tmp_path, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


# ── issuing ─────────────────────────────────────────────────────────────────


def test_a_key_looks_like_a_key_and_verifies(store):
    key = companion_keys.issue("jocasta", ["mcp"])
    assert key.startswith(companion_keys.KEY_PREFIX)
    assert len(key) > 30

    identity = companion_keys.verify(key)
    assert identity.companion_id == "jocasta"
    assert identity.scopes == ("mcp",)


def test_two_companions_get_different_keys(store):
    a = companion_keys.issue("meeting-notes", ["llm"])
    b = companion_keys.issue("jocasta", ["mcp"])
    assert a != b
    assert companion_keys.verify(a).companion_id == "meeting-notes"
    assert companion_keys.verify(b).companion_id == "jocasta"


def test_no_plaintext_key_is_on_disk(store):
    key = companion_keys.issue("jocasta", ["mcp"])
    blob = (store / companion_keys.STORE_FILE).read_text()
    assert key not in blob
    record = json.loads(blob)["companions"]["jocasta"]
    assert record["key_hash"] and record["salt"]
    assert "key" not in record


def test_the_store_is_owner_only(store):
    companion_keys.issue("jocasta", ["mcp"])
    mode = (store / companion_keys.STORE_FILE).stat().st_mode & 0o777
    assert mode == 0o600


def test_the_same_key_hashes_differently_per_companion(store):
    """Per-record salt: two records must not be comparable by eye."""
    companion_keys.issue("a", ["llm"])
    companion_keys.issue("b", ["llm"])
    data = json.loads((store / companion_keys.STORE_FILE).read_text())["companions"]
    assert data["a"]["salt"] != data["b"]["salt"]
    assert data["a"]["key_hash"] != data["b"]["key_hash"]


def test_reissuing_rotates(store):
    old = companion_keys.issue("jocasta", ["mcp"])
    new = companion_keys.issue("jocasta", ["mcp"])
    assert companion_keys.verify(old) is None
    assert companion_keys.verify(new) is not None


def test_an_unknown_scope_is_refused_not_ignored(store):
    """A typo in a manifest must fail loudly. Silently dropping it would grant
    less than asked; treating it as a wildcard would grant more."""
    with pytest.raises(ValueError, match="unknown scope"):
        companion_keys.issue("jocasta", ["mpc"])
    assert companion_keys.list_keys() == []


def test_a_companion_id_is_required(store):
    with pytest.raises(ValueError):
        companion_keys.issue("", ["llm"])


def test_the_default_scope_is_the_narrowest(store):
    key = companion_keys.issue("something")
    assert companion_keys.verify(key).scopes == ("llm",)


# ── verifying ───────────────────────────────────────────────────────────────


def test_rubbish_does_not_verify(store):
    companion_keys.issue("jocasta", ["mcp"])
    assert companion_keys.verify("lb-nope") is None
    assert companion_keys.verify("") is None
    assert companion_keys.verify(None) is None


def test_scopes_are_enforced_per_companion(store):
    recorder = companion_keys.verify(companion_keys.issue("meeting-notes", ["llm"]))
    jocasta = companion_keys.verify(companion_keys.issue("jocasta", ["mcp", "memory"]))

    assert recorder.has("llm") and not recorder.has("mcp")
    assert jocasta.has("mcp") and jocasta.has("memory") and not jocasta.has("llm")


def test_last_used_is_recorded(store):
    key = companion_keys.issue("jocasta", ["mcp"])
    assert companion_keys.list_keys()[0]["last_used_at"] is None
    companion_keys.verify(key)
    assert companion_keys.list_keys()[0]["last_used_at"] is not None


def test_an_unreadable_store_denies_rather_than_grants(store):
    companion_keys.issue("jocasta", ["mcp"])
    (store / companion_keys.STORE_FILE).write_text("{ this is not json")
    assert companion_keys.verify("anything") is None


# ── revoking ────────────────────────────────────────────────────────────────


def test_revoking_one_leaves_the_others(store):
    recorder = companion_keys.issue("meeting-notes", ["llm"])
    jocasta = companion_keys.issue("jocasta", ["mcp"])

    assert companion_keys.revoke("jocasta") is True

    assert companion_keys.verify(jocasta) is None
    assert companion_keys.verify(recorder).companion_id == "meeting-notes"


def test_revoking_something_that_is_not_there_is_not_an_error(store):
    assert companion_keys.revoke("ghost") is False


def test_revoke_all(store):
    companion_keys.issue("meeting-notes", ["llm"])
    companion_keys.issue("jocasta", ["mcp"])
    assert companion_keys.revoke_all() == 2
    assert companion_keys.list_keys() == []


def test_listing_leaks_nothing(store):
    key = companion_keys.issue("jocasta", ["mcp"])
    listed = companion_keys.list_keys()
    assert listed[0]["companion_id"] == "jocasta"
    assert listed[0]["scopes"] == ["mcp"]
    blob = json.dumps(listed)
    assert key not in blob
    assert "key_hash" not in blob
    assert "salt" not in blob


# ── the legacy migration ────────────────────────────────────────────────────


def test_the_legacy_key_keeps_working_after_migration(store):
    """The recorder already has this value in its own config file. Issuing a new
    one at upgrade time would disconnect a working tool for no reason."""
    legacy = "lb-the-key-the-recorder-already-has"
    (store / companion_keys.LEGACY_KEY_FILE).write_text(legacy)

    identity = companion_keys.verify(legacy)

    assert identity is not None
    assert identity.companion_id == companion_keys.LEGACY_COMPANION_ID
    assert identity.scopes == ("llm",)


def test_migration_removes_the_plaintext_file(store):
    (store / companion_keys.LEGACY_KEY_FILE).write_text("lb-old")
    companion_keys.migrate_legacy_key()
    assert not (store / companion_keys.LEGACY_KEY_FILE).exists()
    assert (store / companion_keys.STORE_FILE).exists()


def test_the_migrated_key_is_hashed_like_any_other(store):
    legacy = "lb-old-plaintext"
    (store / companion_keys.LEGACY_KEY_FILE).write_text(legacy)
    companion_keys.migrate_legacy_key()
    assert legacy not in (store / companion_keys.STORE_FILE).read_text()


def test_the_legacy_key_is_not_widened_during_migration(store):
    """It was only ever accepted at /v1. Migration must not hand the recorder
    MCP access it never had."""
    (store / companion_keys.LEGACY_KEY_FILE).write_text("lb-old")
    identity = companion_keys.verify("lb-old")
    assert identity.has("llm") is True
    for scope in ("mcp", "memory", "events", "audio"):
        assert identity.has(scope) is False


def test_migration_is_idempotent(store):
    (store / companion_keys.LEGACY_KEY_FILE).write_text("lb-old")
    assert companion_keys.migrate_legacy_key() is True
    assert companion_keys.migrate_legacy_key() is False
    assert companion_keys.verify("lb-old") is not None


def test_a_recreated_legacy_file_does_not_override_the_store(store):
    """If something writes the old file again after migration, the store wins —
    otherwise a stale file could resurrect a revoked key."""
    (store / companion_keys.LEGACY_KEY_FILE).write_text("lb-old")
    companion_keys.migrate_legacy_key()
    companion_keys.revoke(companion_keys.LEGACY_COMPANION_ID)

    (store / companion_keys.LEGACY_KEY_FILE).write_text("lb-old")
    companion_keys.issue(companion_keys.LEGACY_COMPANION_ID, ["llm"])

    assert companion_keys.verify("lb-old") is None


def test_an_empty_legacy_file_is_just_cleaned_up(store):
    (store / companion_keys.LEGACY_KEY_FILE).write_text("   \n")
    assert companion_keys.migrate_legacy_key() is False
    assert not (store / companion_keys.LEGACY_KEY_FILE).exists()
    assert companion_keys.list_keys() == []
