"""LB-11 measure 1: fail closed.

This is the difference between encryption being a safety feature and encryption
being a way to lose everything quietly.

The failure under test: the data dir is a MOUNT POINT. If the volume does not
attach, that path is an ordinary empty directory — and nothing above it can tell
"the volume failed to mount" from "this is a new install". So LocalBook would
come up blank AND start writing a fresh corpus into the directory, which is the
unrecoverable half: it puts a new localbook.db where the mount belongs, and the
next successful attach then refuses because the mount point is not empty.
"""

import json
import secrets
import subprocess

import pytest

from services import keyvault, volume_gate, volume_service
from services.volume_gate import GateState


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Temp mount point, throwaway keychain, gate reset between tests."""
    service = f"LocalBook-keyvault-test-{secrets.token_hex(6)}"
    monkeypatch.setattr(keyvault, "SERVICE_NAME", service)

    from config import settings

    mount = tmp_path / "LocalBook"
    monkeypatch.setattr(settings, "data_dir", mount)
    monkeypatch.setattr(settings, "db_path", mount / "db")
    monkeypatch.setattr(settings, "volume_max_size_gb", 1)
    monkeypatch.setattr(settings, "encryption_enabled", False)
    monkeypatch.setattr(volume_gate, "_gate",
                        volume_gate.Gate(state=GateState.OPEN), raising=False)

    real_run = subprocess.run
    try:
        yield mount
    finally:
        try:
            real_run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)
        except Exception:
            pass
        for purpose in keyvault.PURPOSES:
            real_run(
                ["security", "delete-generic-password", "-a", purpose, "-s", keyvault.service_name()],
                capture_output=True,
            )


# ── the flag ────────────────────────────────────────────────────────────────


def test_encryption_off_means_the_gate_is_open(isolated):
    gate = volume_gate.evaluate()
    assert gate.state is GateState.OPEN
    assert gate.locked is False


def test_the_flag_is_per_machine(isolated, monkeypatch):
    """D11: never synced. A synced flag would switch encryption on for a machine
    with no volume and lock it out of its own data."""
    from config import settings

    monkeypatch.setattr(settings, "encryption_enabled", True)
    assert volume_gate.encryption_enabled() is True
    monkeypatch.setattr(settings, "encryption_enabled", False)
    assert volume_gate.encryption_enabled() is False


# ── locked ──────────────────────────────────────────────────────────────────


def test_encryption_on_with_no_volume_locks(isolated, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "encryption_enabled", True)
    gate = volume_gate.evaluate()

    assert gate.locked is True
    assert "missing" in gate.reason


def test_the_locked_message_says_the_data_is_not_lost(isolated, monkeypatch):
    """The natural reading of "LocalBook cannot open your data" is "LocalBook has
    lost my data". It has not, and the wording has to say so."""
    from config import settings

    monkeypatch.setattr(settings, "encryption_enabled", True)
    gate = volume_gate.evaluate()

    assert "not lost" in gate.detail or "safe" in gate.detail or "inside it" in gate.detail


def test_an_existing_volume_that_is_not_mounted_locks(isolated, monkeypatch):
    from config import settings

    volume_service.create()
    monkeypatch.setattr(settings, "encryption_enabled", True)

    gate = volume_gate.evaluate()
    assert gate.locked is True
    assert "not mounted" in gate.reason


def test_a_mounted_volume_opens_the_gate(isolated, monkeypatch):
    from config import settings

    volume_service.initialise_new_volume()
    monkeypatch.setattr(settings, "encryption_enabled", True)

    gate = volume_gate.evaluate()
    assert gate.state is GateState.OPEN
    assert gate.volume["mounted"] is True


def test_an_unknown_state_locks_rather_than_opens(isolated, monkeypatch):
    """Guessing "open" here is the one mistake that lets the app write into an
    unmounted mount point, which is the unrecoverable outcome."""
    from config import settings

    monkeypatch.setattr(settings, "encryption_enabled", True)
    monkeypatch.setattr(
        volume_service, "state",
        lambda: (_ for _ in ()).throw(RuntimeError("hdiutil exploded")),
    )

    gate = volume_gate.evaluate()
    assert gate.locked is True
    assert "hdiutil exploded" in gate.reason
    assert "not been touched" in gate.detail


# ── the data dir is not created when locked ─────────────────────────────────


def test_the_data_dir_is_not_created_at_import_when_encryption_is_on():
    """config.py used to mkdir unconditionally. That single line is what made a
    failed mount look like a new install."""
    import inspect

    import config

    src = inspect.getsource(config)
    assert 'if not bool(getattr(settings, "encryption_enabled", False)):' in src
    marker = src.index("encryption_enabled\", False)):")
    after = src[marker:marker + 300]
    assert "data_dir.mkdir" in after, "the mkdir must be inside the guard"


def test_subdirs_are_created_only_after_a_successful_mount(isolated, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "encryption_enabled", True)
    volume_service.initialise_new_volume()

    volume_gate.ensure_subdirs()
    assert (isolated / "db").is_dir()


# ── the middleware ──────────────────────────────────────────────────────────


def _drive(app, path):
    import asyncio

    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"through"})

    asyncio.run(
        volume_gate.LockedGateMiddleware(inner)(
            {"type": "http", "method": "GET", "path": path, "headers": []},
            receive, send,
        )
    )
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


def _lock(monkeypatch, detail="your notebooks are safe"):
    monkeypatch.setattr(
        volume_gate, "_gate",
        volume_gate.Gate(state=GateState.LOCKED, reason="test", detail=detail),
    )


def test_an_open_gate_lets_everything_through(isolated):
    assert _drive(None, "/notebooks")[0] == 200


def test_a_locked_gate_refuses_data_routes(isolated, monkeypatch):
    _lock(monkeypatch)
    status, body = _drive(None, "/notebooks")

    assert status == 503
    payload = json.loads(body)
    assert payload["locked"] is True
    assert "safe" in payload["detail"]


@pytest.mark.parametrize("path", [
    "/health", "/system/volume", "/system/volume/unlock",
    "/keyvault/status", "/auth/bootstrap", "/updates/startup-status",
    "/assets/index.js", "/",
])
def test_the_recovery_surface_stays_reachable_while_locked(isolated, monkeypatch, path):
    """These ARE the way back in. Refusing them would leave the user with a
    locked app and no route out of it."""
    _lock(monkeypatch)
    assert _drive(None, path)[0] == 200


@pytest.mark.parametrize("path", [
    "/notebooks", "/sources/x", "/chat", "/reindex/all", "/backup",
    "/settings/preferences", "/memory/search", "/mcp/",
])
def test_every_data_route_is_refused_while_locked(isolated, monkeypatch, path):
    """A generous allowlist is how a data route slips through and writes into an
    unmounted mount point."""
    _lock(monkeypatch)
    assert _drive(None, path)[0] == 503


def test_a_locked_response_is_distinguishable_from_an_outage(isolated, monkeypatch):
    """A client needs to tell "locked, here is what to do" from "the backend is
    down"."""
    _lock(monkeypatch)
    import asyncio

    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        sent.append(m)

    async def inner(scope, receive, send):
        pass

    asyncio.run(
        volume_gate.LockedGateMiddleware(inner)(
            {"type": "http", "method": "GET", "path": "/notebooks", "headers": []},
            receive, send,
        )
    )
    headers = dict(next(m for m in sent if m["type"] == "http.response.start")["headers"])
    assert headers[b"x-localbook-locked"] == b"1"


def test_non_http_traffic_is_passed_straight_through(isolated, monkeypatch):
    """Websockets must not be answered with an HTTP 503 body."""
    _lock(monkeypatch)
    import asyncio

    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["type"])

    async def receive():
        return {}

    async def send(m):
        pass

    asyncio.run(
        volume_gate.LockedGateMiddleware(inner)(
            {"type": "websocket", "path": "/ws"}, receive, send
        )
    )
    assert reached == ["websocket"]


# ── unlocking ───────────────────────────────────────────────────────────────


def test_unlocking_a_present_volume_opens_the_gate(isolated, monkeypatch):
    from config import settings

    volume_service.initialise_new_volume()
    volume_service.detach()
    monkeypatch.setattr(settings, "encryption_enabled", True)
    assert volume_gate.evaluate().locked is True

    gate = volume_gate.unlock()
    assert gate.locked is False
    assert volume_service.is_mounted() is True


def test_a_failed_unlock_stays_locked(isolated, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "encryption_enabled", True)
    volume_gate.evaluate()

    gate = volume_gate.unlock()      # no image at all
    assert gate.locked is True


def test_the_phrase_recovers_the_volume_key(isolated, monkeypatch):
    """A wiped Keychain, or a replacement Mac. The wrapped copy sits beside the
    image precisely so this works when the volume cannot mount."""
    from config import settings

    phrase = keyvault.generate_recovery_phrase()
    keyvault.set_recovery_key(phrase)
    volume_service.initialise_new_volume()
    (isolated / "canary.txt").write_text("mine")
    volume_service.detach()

    keyvault.delete("volume")        # the Keychain is wiped
    monkeypatch.setattr(settings, "encryption_enabled", True)
    assert volume_gate.evaluate().locked is True

    keyvault.restore_from_phrase(phrase, "volume")
    gate = volume_gate.unlock()

    assert gate.locked is False
    assert (isolated / "canary.txt").read_text() == "mine"
