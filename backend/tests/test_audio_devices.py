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

def _cleanup():
    dev = ad.find_device(_PROBE_NAME)
    if dev:
        ad.destroy_device(dev["id"])


@pytest.fixture
def probe():
    _cleanup()
    yield
    _cleanup()


def test_a_multi_output_device_can_be_created_and_removed(probe):
    """The whole premise, end to end against real CoreAudio."""
    default = next(d for d in ad.list_devices() if d["is_default_output"])
    result = ad.create_multi_output(
        name=_PROBE_NAME, uid=_PROBE_UID,
        member_uids=[default["uid"]], master_uid=default["uid"])
    assert result["ok"], result
    assert ad.find_device(_PROBE_NAME) is not None, "macOS reported success but nothing appeared"
    assert ad.destroy_device(result["device_id"]) is True
    assert ad.find_device(_PROBE_NAME) is None


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
    default = next(d for d in ad.list_devices() if d["is_default_output"])
    first = ad.ensure_multi_output(name=_PROBE_NAME, uid=_PROBE_UID,
                                   include_names=[default["name"]])
    assert first["ok"] and first["created"] is True

    second = ad.ensure_multi_output(name=_PROBE_NAME, uid=_PROBE_UID,
                                    include_names=[default["name"]])
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
