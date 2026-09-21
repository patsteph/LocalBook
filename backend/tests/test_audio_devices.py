"""CoreAudio: building the Multi-Output Device the meeting recorder needs.

Meeting Notes needs a device that plays to your speakers AND to BlackHole at
once, so you can hear a call while it is being captured. Its installer opens
Audio MIDI Setup and shows a dialog explaining how to build one by hand — the
comment above that line reads "can't be safely scripted".

It can: a Multi-Output Device is an aggregate device with the `stacked` flag.
Automating it turns that dialog into a rubber stamp.

The round-trip test below creates a REAL device and destroys it again. It is
skipped anywhere CoreAudio is unavailable, and it never leaves anything behind.
"""
import pytest

from services import audio_devices as ad

pytestmark = pytest.mark.skipif(
    ad._frameworks() == (None, None), reason="CoreAudio unavailable")

_PROBE_NAME = "LocalBook Test Device"
_PROBE_UID = "com.localbook.test.probe"


# ── reading ─────────────────────────────────────────────────────────────────

def test_devices_come_back_with_the_fields_we_need():
    """A UID is what an aggregate description references. A device without one
    cannot be a member, so an empty UID is a real failure, not cosmetics."""
    devices = ad.list_devices()
    assert devices, "no audio devices found at all"
    for d in devices:
        assert d["name"], f"device {d['id']} has no name"
        assert d["uid"], f"device {d['name']!r} has no UID"


def test_exactly_one_device_is_the_default_output():
    devices = ad.list_devices()
    assert sum(1 for d in devices if d["is_default_output"]) == 1


def test_lookup_matches_exactly_before_falling_back_to_substring():
    """Otherwise 'BlackHole 2ch' could match a device merely containing it, and
    we would build the stack from the wrong hardware."""
    devices = ad.list_devices()
    first = devices[0]["name"]
    assert ad.find_device(first)["name"] == first
    assert ad.find_device(first.lower())["name"] == first
    assert ad.find_device("no such device anywhere") is None
    assert ad.find_device("") is None


# ── creating ────────────────────────────────────────────────────────────────

def _wait_until_gone(name: str, timeout: float = 5.0) -> bool:
    """The mirror of wait_for_device — removal is equally asynchronous."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ad.find_device(name) is None:
            return True
        time.sleep(0.2)
    return ad.find_device(name) is None


def _cleanup():
    dev = ad.find_device(_PROBE_NAME)
    if dev:
        ad.destroy_device(dev["id"])
        _wait_until_gone(_PROBE_NAME)


@pytest.fixture
def probe():
    _cleanup()
    yield
    _cleanup()


def test_a_multi_output_device_can_be_created_and_removed(probe):
    """The whole premise, end to end against real CoreAudio.

    Every check here WAITS rather than glances. coreaudiod republishes its
    device list asynchronously, so reading it in the same breath as a mutation
    is a race — which is the lesson production already learned the hard way,
    and which made these tests intermittent while a driver was installing on
    this very machine.
    """
    default = next(d for d in ad.list_devices() if d["is_default_output"])
    result = ad.create_multi_output(
        name=_PROBE_NAME, uid=_PROBE_UID,
        member_uids=[default["uid"]], master_uid=default["uid"])
    assert result["ok"], result
    assert ad.wait_for_device(_PROBE_NAME, timeout=5, interval=0.2) is not None, \
        "macOS reported success but the device never appeared"
    assert ad.destroy_device(result["device_id"]) is True
    assert _wait_until_gone(_PROBE_NAME), "the device was still listed after removal"


def test_creating_with_no_members_is_refused():
    result = ad.create_multi_output(name=_PROBE_NAME, uid=_PROBE_UID, member_uids=[])
    assert result["ok"] is False
    assert "no devices" in result["error"].lower()


def test_ensure_refuses_when_a_required_device_is_missing():
    """Before BlackHole exists there is nothing to build, and saying so beats
    creating a half-device that silently records nothing."""
    result = ad.ensure_multi_output(
        name=_PROBE_NAME, uid=_PROBE_UID,
        include_names=["A Device That Does Not Exist"])
    assert result["ok"] is False
    assert "A Device That Does Not Exist" in result["missing"]
    assert ad.find_device(_PROBE_NAME) is None, "a device was created despite missing members"


def test_ensure_is_idempotent(probe):
    """The user may have built 'Meeting Output' by hand following the upstream
    instructions. A second device with the same name would be worse than doing
    nothing — the recorder selects by name."""
    # include_names=[] keeps this about idempotency alone: naming the default
    # output would mark it "virtual" and drag hardware selection into a test
    # that is not about hardware selection.
    first = ad.ensure_multi_output(name=_PROBE_NAME, uid=_PROBE_UID,
                                   include_names=[])
    assert first["ok"] and first["created"] is True, first
    assert ad.wait_for_device(_PROBE_NAME, timeout=5, interval=0.2) is not None

    second = ad.ensure_multi_output(name=_PROBE_NAME, uid=_PROBE_UID,
                                    include_names=[])
    assert second["ok"] and second["created"] is False
    matches = [d for d in ad.list_devices() if d["name"] == _PROBE_NAME]
    assert len(matches) == 1, f"ended up with {len(matches)} devices of the same name"


def test_the_clock_master_is_real_hardware_not_the_virtual_device():
    """BlackHole has no physical clock to follow. Driving the stack from it
    invites drift, so the default output leads."""
    import inspect
    src = inspect.getsource(ad.ensure_multi_output)
    assert "master = dev[\"uid\"]" in src
    master_line = next(i for i, l in enumerate(src.splitlines()) if "master = dev" in l)
    include_line = next(i for i, l in enumerate(src.splitlines()) if "for want in include_names" in l)
    assert master_line < include_line, "the clock master must be set from the default output"


def test_the_description_marks_the_device_as_stacked():
    """`stacked` is what makes this a Multi-Output Device rather than an
    ordinary aggregate — without it, audio does not mirror to both."""
    assert ad._K_STACKED == "stacked"
    import inspect
    src = inspect.getsource(ad.create_multi_output)
    assert "_K_STACKED: 1" in src


# ── the race that broke a real install (2026-09-18) ─────────────────────────
#
# Field report: BlackHole installed correctly, and "Meeting Output" was still
# missing. The privileged step ends with `killall coreaudiod`; the daemon takes
# seconds to rescan plug-ins and republish. We looked for the new driver
# immediately, found nothing to combine, and skipped building the device — then
# a moment later the plan check saw BlackHole present, so the error named only
# the device that depended on it.

def test_waiting_returns_as_soon_as_a_device_appears(monkeypatch):
    calls = {"n": 0}

    def _find(name):
        calls["n"] += 1
        return {"name": name, "uid": "x"} if calls["n"] >= 3 else None
    monkeypatch.setattr(ad, "find_device", _find)

    found = ad.wait_for_device("BlackHole 2ch", timeout=5, interval=0.01)
    assert found is not None
    assert calls["n"] == 3, "it should stop polling the moment the device shows up"


def test_waiting_gives_up_rather_than_hanging(monkeypatch):
    monkeypatch.setattr(ad, "find_device", lambda name: None)
    assert ad.wait_for_device("Never Appears", timeout=0.05, interval=0.01) is None


def test_ensure_waits_for_a_driver_that_is_still_registering(monkeypatch):
    """The actual fix: a driver installed seconds ago is not visible yet."""
    seen = {"n": 0}
    default = next(d for d in ad.list_devices() if d["is_default_output"])

    def _find(name):
        if name == "SlowDriver":
            seen["n"] += 1
            return {"name": "SlowDriver", "uid": "slow-uid"} if seen["n"] >= 2 else None
        return default if name == default["name"] else None

    monkeypatch.setattr(ad, "find_device", _find)
    monkeypatch.setattr(ad, "create_multi_output",
                        lambda **kw: {"ok": True, "device_id": 1, "members": kw["member_uids"]})
    # find_device is patched, so the post-create existence check sees it too.
    result = ad.ensure_multi_output(name="SlowDriver", uid="u",
                                    include_names=["SlowDriver"], wait_seconds=2)
    assert seen["n"] >= 2, "it gave up before the driver had a chance to register"


def test_a_driver_that_never_appears_suggests_a_restart(monkeypatch):
    """BlackHole's own advice. Repeating "not installed" at someone who just
    watched it install is not help."""
    monkeypatch.setattr(ad, "find_device", lambda name: None)
    result = ad.ensure_multi_output(name="X", uid="u",
                                    include_names=["Ghost"], wait_seconds=0.05)
    assert result["ok"] is False
    assert "restart" in result["error"].lower()


def test_creation_is_retried_before_being_believed(monkeypatch):
    """coreaudiod can accept the call and still be settling; one refusal right
    after a driver install is not evidence that it cannot work."""
    import time as _time
    attempts = {"n": 0}
    default = next(d for d in ad.list_devices() if d["is_default_output"])
    created = {"yes": False}

    def _find(name):
        if name == "A Device We Are Building":
            return {"name": name, "uid": "new"} if created["yes"] else None
        return default if name == default["name"] else None

    def _create(**kw):
        attempts["n"] += 1
        if attempts["n"] >= 2:
            created["yes"] = True
            return {"ok": True, "device_id": 7}
        return {"ok": False, "error": "coreaudiod is still settling"}

    monkeypatch.setattr(ad, "find_device", _find)
    monkeypatch.setattr(ad, "create_multi_output", _create)
    monkeypatch.setattr(_time, "sleep", lambda s: None)

    result = ad.ensure_multi_output(name="A Device We Are Building", uid="u",
                                    include_names=[])
    assert result["ok"] and result["created"] is True
    assert attempts["n"] == 2, "a single refusal was treated as final"


def test_an_existing_aggregate_is_never_used_as_a_member():
    """Nesting aggregate devices is not reliably supported. On a machine that
    already has a Multi-Output Device, a fallback that ignored this could pick
    it — and whether it did depended on device enumeration order, which made a
    real test fail intermittently and would have failed on users' machines the
    same way.
    """
    aggregates = [d for d in ad.list_devices() if ad.device_members(d["id"])]
    if not aggregates:
        pytest.skip("no aggregate device on this machine to test against")

    default = next(d for d in ad.list_devices() if d["is_default_output"])
    chosen = {}
    monkey = ad.create_multi_output

    def _capture(**kw):
        chosen["members"] = kw["member_uids"]
        return {"ok": False, "error": "captured"}
    ad.create_multi_output = _capture
    try:
        # Force the fallback by naming the default output as a "virtual" member.
        ad.ensure_multi_output(name="LocalBook Nesting Probe", uid="u",
                               include_names=[default["name"]])
    finally:
        ad.create_multi_output = monkey

    by_uid = {d["uid"]: d for d in ad.list_devices()}
    for uid in chosen.get("members", []):
        dev = by_uid.get(uid)
        if dev:
            assert not ad.device_members(dev["id"]), \
                f"picked {dev['name']}, which is itself an aggregate"


def test_creation_waits_for_the_device_before_calling_it_missing():
    """The verification after creating a device has to wait like every other
    read of the device list.

    Checking instantly reported "macOS reported success but Meeting Output did
    not appear" on a build that had in fact worked — the worst kind of failure,
    since it tells the user something is broken AND leaves a working device
    behind for the next run to trip over.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(ad.ensure_multi_output).lstrip())
    calls = [n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "wait_for_device" in calls, \
        "the post-create check reads the device list without waiting for it"


# ── finding something that can actually hear the user ───────────────────────
#
# 2026-09-21 field report: recording failed with "Audio device not found" for a
# device named `Microphone`. The companion ships MIC_DEVICE="Microphone", and
# no Mac calls its input that — a MacBook's is "MacBook Pro Microphone". Its
# fallback names exactly that, and did not fire.
#
# So LocalBook writes the real device name. Which means knowing which input is
# a microphone and which is a loopback: BlackHole CAN record, and recording
# "you" from it would capture the far side of the call as your own voice.

def test_a_loopback_is_never_mistaken_for_a_microphone(monkeypatch):
    monkeypatch.setattr(ad, "list_devices", lambda: [
        {"id": 1, "name": "BlackHole 2ch", "uid": "bh", "can_input": True,
         "can_output": True, "is_default_output": False},
        {"id": 2, "name": "Meeting Output", "uid": "mo", "can_input": True,
         "can_output": True, "is_default_output": True},
    ])
    assert ad.microphones() == []
    assert ad.default_microphone() is None


def test_a_real_microphone_is_found_alongside_loopbacks(monkeypatch):
    monkeypatch.setattr(ad, "list_devices", lambda: [
        {"id": 1, "name": "BlackHole 2ch", "uid": "bh", "can_input": True,
         "can_output": True, "is_default_output": False},
        {"id": 2, "name": "MacBook Pro Microphone", "uid": "mbp", "can_input": True,
         "can_output": False, "is_default_output": False},
    ])
    assert [m["name"] for m in ad.microphones()] == ["MacBook Pro Microphone"]
    assert ad.default_microphone()["name"] == "MacBook Pro Microphone"


def test_a_built_in_microphone_is_preferred_over_others(monkeypatch):
    monkeypatch.setattr(ad, "list_devices", lambda: [
        {"id": 1, "name": "Scarlett Solo USB", "uid": "s", "can_input": True,
         "can_output": False, "is_default_output": False},
        {"id": 2, "name": "MacBook Pro Microphone", "uid": "m", "can_input": True,
         "can_output": False, "is_default_output": False},
    ])
    assert ad.default_microphone()["name"] == "MacBook Pro Microphone"


def test_an_external_interface_is_used_when_there_is_no_built_in(monkeypatch):
    """A Mac mini or Studio has no built-in microphone; an interface is not a
    second-best option there, it is the only one."""
    monkeypatch.setattr(ad, "list_devices", lambda: [
        {"id": 1, "name": "Scarlett Solo USB", "uid": "s", "can_input": True,
         "can_output": False, "is_default_output": False},
    ])
    assert ad.default_microphone()["name"] == "Scarlett Solo USB"


def test_output_only_devices_are_not_offered_as_microphones(monkeypatch):
    monkeypatch.setattr(ad, "list_devices", lambda: [
        {"id": 1, "name": "Mac mini Speakers", "uid": "s", "can_input": False,
         "can_output": True, "is_default_output": True},
    ])
    assert ad.microphones() == []


def test_coreaudio_names_match_what_ffmpeg_expects():
    """The name we write into MIC_DEVICE is passed straight to ffmpeg's
    AVFoundation input. If the two naming systems disagreed, writing a correct
    CoreAudio name would still produce "Audio device not found".

    Verified on this machine: CoreAudio "BlackHole 2ch" == AVFoundation
    "BlackHole 2ch".
    """
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True, timeout=30)
    listing = proc.stderr or ""
    inputs = [d["name"] for d in ad.list_devices() if d.get("can_input")]
    if not inputs:
        pytest.skip("no audio inputs on this machine")
    for name in inputs:
        assert name in listing, f"CoreAudio calls it {name!r}; ffmpeg does not"
