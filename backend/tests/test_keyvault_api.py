"""K-1 recovery-phrase setup.

The property under test throughout: **`begin` must not commit anything.** A
phrase the user never wrote down, silently accepted, would show "recovery
configured" on every screen while being worth nothing — which is worse than no
recovery at all, because it is a false assurance someone would rely on.

Isolation: a tmp data dir and a throwaway keychain service name, as everywhere
else in K-1. `backend/.venv` resolves `settings.data_dir` to the REAL production
data dir.
"""

import secrets
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import keyvault as keyvault_api
from services import keyvault


@pytest.fixture
def client(tmp_path, monkeypatch):
    service = f"LocalBook-keyvault-test-{secrets.token_hex(6)}"
    monkeypatch.setattr(keyvault, "SERVICE_NAME", service)

    from config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    app = FastAPI()
    app.include_router(keyvault_api.router)
    real_run = subprocess.run
    try:
        yield TestClient(app)
    finally:
        for purpose in keyvault.PURPOSES:
            real_run(
                ["security", "delete-generic-password", "-a", purpose, "-s", service],
                capture_output=True,
            )


def _answers(payload):
    """Type back the requested words correctly."""
    words = payload["words"]
    return {str(i): words[i - 1] for i in payload["verify_indices"]}


# ── status ──────────────────────────────────────────────────────────────────


def test_a_fresh_install_reports_not_configured(client):
    st = client.get("/keyvault/status").json()
    assert st["recovery_key_configured"] is False
    assert st["fully_protected"] is False


def test_an_existing_key_with_no_recovery_shows_as_unprotected(client):
    """The exact state the first built run left the user in, and the reason
    this endpoint exists."""
    keyvault.get_or_create("credentials")
    st = client.get("/keyvault/status").json()
    assert st["recovery_key_configured"] is False
    assert "credentials" in st["unprotected_purposes"]
    assert st["fully_protected"] is False


# ── begin commits nothing ───────────────────────────────────────────────────


def test_begin_returns_24_words_and_four_positions(client):
    payload = client.post("/keyvault/recovery/begin").json()
    assert len(payload["words"]) == 24
    assert len(payload["verify_indices"]) == keyvault_api.VERIFY_WORD_COUNT
    assert len(set(payload["verify_indices"])) == keyvault_api.VERIFY_WORD_COUNT
    assert all(1 <= i <= 24 for i in payload["verify_indices"])


def test_begin_stores_nothing(client):
    """The load-bearing assertion of this whole file."""
    client.post("/keyvault/recovery/begin")
    assert keyvault.has_recovery_key() is False
    assert client.get("/keyvault/status").json()["recovery_key_configured"] is False


def test_begin_twice_gives_different_phrases(client):
    a = client.post("/keyvault/recovery/begin").json()["phrase"]
    b = client.post("/keyvault/recovery/begin").json()["phrase"]
    assert a != b
    assert keyvault.has_recovery_key() is False


def test_abandoning_setup_leaves_no_trace(client):
    for _ in range(3):
        client.post("/keyvault/recovery/begin")
    st = client.get("/keyvault/status").json()
    assert st["recovery_key_configured"] is False
    assert st["fully_protected"] is False


# ── confirm ─────────────────────────────────────────────────────────────────


def test_confirming_with_the_right_words_configures_recovery(client):
    keyvault.get_or_create("credentials")
    payload = client.post("/keyvault/recovery/begin").json()

    r = client.post("/keyvault/recovery/confirm",
                    json={"phrase": payload["phrase"], "answers": _answers(payload)})
    assert r.status_code == 200, r.text

    st = r.json()["status"]
    assert st["recovery_key_configured"] is True
    assert st["fully_protected"] is True
    assert st["unprotected_purposes"] == []


def test_confirming_wraps_keys_that_already_existed(client):
    """The backfill. Without it, a key created before setup stays unrecoverable
    forever while the UI says 'configured'."""
    keyvault.get_or_create("credentials")
    assert not keyvault.wrapped_path("credentials").exists()

    payload = client.post("/keyvault/recovery/begin").json()
    r = client.post("/keyvault/recovery/confirm",
                    json={"phrase": payload["phrase"], "answers": _answers(payload)})

    assert "credentials" in r.json()["wrapped"]
    assert keyvault.wrapped_path("credentials").exists()
    assert keyvault.unwrap_with_phrase(payload["phrase"], "credentials") == \
        keyvault.get_or_create("credentials")


def test_a_wrong_word_is_refused_and_names_which(client):
    payload = client.post("/keyvault/recovery/begin").json()
    answers = _answers(payload)
    bad_position = payload["verify_indices"][1]
    answers[str(bad_position)] = "definitelynotthatword"

    r = client.post("/keyvault/recovery/confirm",
                    json={"phrase": payload["phrase"], "answers": answers})
    assert r.status_code == 400
    assert str(bad_position) in r.json()["detail"]
    assert keyvault.has_recovery_key() is False


def test_typing_back_too_few_words_is_refused(client):
    payload = client.post("/keyvault/recovery/begin").json()
    answers = _answers(payload)
    answers.pop(next(iter(answers)))

    r = client.post("/keyvault/recovery/confirm",
                    json={"phrase": payload["phrase"], "answers": answers})
    assert r.status_code == 400
    assert keyvault.has_recovery_key() is False


def test_the_typed_words_are_case_and_space_insensitive(client):
    payload = client.post("/keyvault/recovery/begin").json()
    answers = {k: f"  {v.upper()} " for k, v in _answers(payload).items()}
    r = client.post("/keyvault/recovery/confirm",
                    json={"phrase": payload["phrase"], "answers": answers})
    assert r.status_code == 200, r.text


def test_a_phrase_that_is_not_24_words_is_refused(client):
    r = client.post("/keyvault/recovery/confirm",
                    json={"phrase": "too short", "answers": {"1": "too"}})
    assert r.status_code == 400
    assert "24-word" in r.json()["detail"]


# ── check ───────────────────────────────────────────────────────────────────


def test_checking_the_right_phrase_matches(client):
    payload = client.post("/keyvault/recovery/begin").json()
    client.post("/keyvault/recovery/confirm",
                json={"phrase": payload["phrase"], "answers": _answers(payload)})

    r = client.post("/keyvault/recovery/check", json={"phrase": payload["phrase"]})
    assert r.json()["matches"] is True


def test_checking_a_different_valid_phrase_does_not_match(client):
    payload = client.post("/keyvault/recovery/begin").json()
    client.post("/keyvault/recovery/confirm",
                json={"phrase": payload["phrase"], "answers": _answers(payload)})

    other = keyvault.generate_recovery_phrase()
    assert client.post("/keyvault/recovery/check", json={"phrase": other}).json()["matches"] is False


def test_checking_before_setup_is_a_400_not_a_false_match(client):
    r = client.post("/keyvault/recovery/check",
                    json={"phrase": keyvault.generate_recovery_phrase()})
    assert r.status_code == 400


# ── restore: the whole point ────────────────────────────────────────────────


def test_a_wiped_keychain_is_recovered_from_the_phrase_alone(client):
    """K-1's 'done when'. Everything above exists to make this work."""
    original = keyvault.get_or_create("credentials")
    payload = client.post("/keyvault/recovery/begin").json()
    client.post("/keyvault/recovery/confirm",
                json={"phrase": payload["phrase"], "answers": _answers(payload)})

    keyvault.delete("credentials")                       # the Keychain is wiped
    with pytest.raises(keyvault.KeyVaultError, match="RECOVERABLE"):
        keyvault.get_or_create("credentials")            # refuses to mint a new one

    r = client.post("/keyvault/recovery/restore",
                    json={"phrase": payload["phrase"], "purpose": "credentials"})
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == ["credentials"]
    assert keyvault.get_or_create("credentials") == original


def test_restoring_with_the_wrong_phrase_is_refused(client):
    keyvault.get_or_create("credentials")
    payload = client.post("/keyvault/recovery/begin").json()
    client.post("/keyvault/recovery/confirm",
                json={"phrase": payload["phrase"], "answers": _answers(payload)})
    keyvault.delete("credentials")

    r = client.post("/keyvault/recovery/restore",
                    json={"phrase": keyvault.generate_recovery_phrase(),
                          "purpose": "credentials"})
    assert r.status_code == 400
    assert "nothing could be restored" in r.json()["detail"]


def test_restore_reports_a_total_failure_rather_than_an_empty_success(client):
    """An empty restored list with a 200 would read as success on any UI."""
    payload = client.post("/keyvault/recovery/begin").json()
    client.post("/keyvault/recovery/confirm",
                json={"phrase": payload["phrase"], "answers": _answers(payload)})

    r = client.post("/keyvault/recovery/restore", json={"phrase": payload["phrase"]})
    assert r.status_code == 400


# ── rewrap ──────────────────────────────────────────────────────────────────


def test_rewrap_covers_a_key_created_after_setup(client):
    payload = client.post("/keyvault/recovery/begin").json()
    client.post("/keyvault/recovery/confirm",
                json={"phrase": payload["phrase"], "answers": _answers(payload)})

    # get_or_create auto-wraps once a recovery key exists, so force the gap.
    keyvault._keychain_write("backup", keyvault.secrets.token_bytes(32))
    assert "backup" in client.get("/keyvault/status").json()["unprotected_purposes"]

    r = client.post("/keyvault/rewrap")
    assert r.status_code == 200
    assert r.json()["status"]["unprotected_purposes"] == []


def test_rewrap_before_setup_is_refused(client):
    assert client.post("/keyvault/rewrap").status_code == 400


def test_a_new_key_is_wrapped_automatically_once_recovery_exists(client):
    payload = client.post("/keyvault/recovery/begin").json()
    client.post("/keyvault/recovery/confirm",
                json={"phrase": payload["phrase"], "answers": _answers(payload)})

    keyvault.get_or_create("backup")
    assert keyvault.wrapped_path("backup").exists()
    assert client.get("/keyvault/status").json()["fully_protected"] is True
