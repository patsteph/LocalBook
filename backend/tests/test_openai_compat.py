"""The OpenAI-compatible endpoint, exercised the way a companion uses it.

Meeting Notes calls `client.chat.completions.create(model, temperature,
messages=[system, user])` through the official OpenAI Python SDK, then reads
`resp.choices[0].message.content`. So these tests drive the real SDK against
the real router — a hand-rolled JSON assertion would pass while the SDK choked
on a missing field, and the whole point is that an unmodified third-party client
works.

Why this endpoint exists: without it, the companion installs llama.cpp and loads
its own gemma while LocalBook already has one resident. Two copies of the same
4.8 GB model on a 16 GB Mac, for a summary.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import companions as svc


@pytest.fixture
def client(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)

    async def _fake_generate(system_prompt, prompt, **kw):
        _fake_generate.seen = {"system": system_prompt, "prompt": prompt, **kw}
        return "## TL;DR\nShipped on Tuesday."
    _fake_generate.seen = {}

    async def _fake_stream(system_prompt, prompt, **kw):
        for piece in ["## TL;DR\n", "Shipped ", "on Tuesday."]:
            yield piece

    import services.llm_service as ls
    monkeypatch.setattr(ls, "generate_text", _fake_generate)
    monkeypatch.setattr(ls, "stream_text", _fake_stream)

    from api import openai_compat
    app = FastAPI()
    app.include_router(openai_compat.router, prefix="/v1")
    c = TestClient(app)
    c.fake = _fake_generate
    return c


def _sdk(client, key):
    from openai import OpenAI
    return OpenAI(base_url="http://testserver/v1", api_key=key, http_client=client)


# ── auth ────────────────────────────────────────────────────────────────────

def test_the_endpoint_is_not_open(client):
    r = client.post("/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_a_wrong_key_is_rejected(client):
    svc.get_companion_key()
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer lb-not-the-key"},
                    json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_the_401_explains_where_to_get_a_key(client):
    r = client.post("/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "hi"}]})
    assert "Companions" in r.json()["detail"]


def test_a_revoked_key_stops_working(client):
    key = svc.get_companion_key()
    h = {"Authorization": f"Bearer {key}"}
    assert client.get("/v1/models", headers=h).status_code == 200
    svc.revoke_companion_key()
    assert client.get("/v1/models", headers=h).status_code == 401


# ── the real client ─────────────────────────────────────────────────────────

def test_the_openai_sdk_gets_a_completion_it_can_read(client):
    """Exactly the call Meeting Notes makes."""
    key = svc.get_companion_key()
    sdk = _sdk(client, key)
    resp = sdk.chat.completions.create(
        model="gemma4:e4b",
        temperature=0.2,
        messages=[
            {"role": "system", "content": "You are an expert meeting-notes assistant."},
            {"role": "user", "content": "Here is the meeting transcript:\n\nWe ship Tuesday."},
        ],
    )
    assert resp.choices[0].message.content.startswith("## TL;DR")
    assert resp.usage.total_tokens > 0


def test_the_system_and_user_messages_reach_the_seam_intact(client):
    """His system prompt dictates the exact section headings. Losing or
    reordering it produces notes our own parser can no longer read."""
    sdk = _sdk(client, svc.get_companion_key())
    sdk.chat.completions.create(
        model="x", messages=[
            {"role": "system", "content": "SYSTEM-MARKER"},
            {"role": "user", "content": "USER-MARKER"}])
    seen = client.fake.seen
    assert seen["system"] == "SYSTEM-MARKER"
    assert "USER-MARKER" in seen["prompt"]


def test_the_voice_modifier_is_off_for_companions(client):
    """LocalBook's tone preamble would fight a format-sensitive system prompt
    and corrupt the section structure the notes depend on."""
    sdk = _sdk(client, svc.get_companion_key())
    sdk.chat.completions.create(model="x", messages=[{"role": "user", "content": "hi"}])
    assert client.fake.seen["voice_modifier"] is False


def test_an_unknown_model_name_is_served_rather_than_refused(client):
    """A companion's config may still name a model from whatever server it used
    before. Refusing would be pedantry; the response says what actually ran."""
    from config import settings
    sdk = _sdk(client, svc.get_companion_key())
    resp = sdk.chat.completions.create(
        model="llama3:70b-from-some-other-server",
        messages=[{"role": "user", "content": "hi"}])
    assert resp.model == settings.main_model


def test_max_tokens_defaults_high_enough_for_meeting_notes(client):
    """Five sections do not fit in the seam's 500-token default, and a silently
    truncated summary reads as a broken tool."""
    sdk = _sdk(client, svc.get_companion_key())
    sdk.chat.completions.create(model="x", messages=[{"role": "user", "content": "hi"}])
    assert client.fake.seen["num_predict"] >= 2000


def test_models_lists_the_real_configured_checkpoints(client):
    from config import settings
    sdk = _sdk(client, svc.get_companion_key())
    ids = [m.id for m in sdk.models.list().data]
    assert settings.main_model in ids


def test_streaming_yields_sdk_readable_chunks(client):
    sdk = _sdk(client, svc.get_companion_key())
    chunks = list(sdk.chat.completions.create(
        model="x", stream=True,
        messages=[{"role": "user", "content": "hi"}]))
    text = "".join(c.choices[0].delta.content or "" for c in chunks)
    assert text == "## TL;DR\nShipped on Tuesday."
    assert chunks[-1].choices[0].finish_reason == "stop"


# ── honest failure ──────────────────────────────────────────────────────────

def test_unsupported_features_are_refused_not_ignored(client):
    """Silently dropping a tool definition returns something that looks fine
    and is wrong. The caller deserves to know."""
    key = svc.get_companion_key()
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"messages": [{"role": "user", "content": "hi"}],
                          "tools": [{"type": "function"}]})
    assert r.status_code == 400
    assert "not supported" in r.json()["detail"].lower()


def test_an_empty_generation_is_an_error_not_an_empty_answer(client, monkeypatch):
    """The seam returns "" when generation fails. Passing that through would
    have the companion write an empty notes file and call it success."""
    async def _empty(*a, **kw):
        return ""
    import services.llm_service as ls
    monkeypatch.setattr(ls, "generate_text", _empty)

    key = svc.get_companion_key()
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503
    assert "no output" in r.json()["detail"].lower()
