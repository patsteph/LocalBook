"""CoreAudio device inspection and Multi-Output Device creation.

Exists for one step that otherwise cannot be automated. Meeting Notes needs a
Multi-Output Device — one that plays to your speakers AND to BlackHole at the
same time, so you can hear a call while it is being captured. Its installer
opens Audio MIDI Setup and shows a dialog explaining how to build one by hand;
the comment above that line reads "can't be safely scripted".

It can. A Multi-Output Device is an aggregate device with the `stacked` flag,
and `AudioHardwareCreateAggregateDevice` creates one. Doing it here turns that
dialog into a rubber stamp — the user clicks Done on instructions for something
already true.

**ctypes rather than Swift**, deliberately. A Swift helper would need Xcode
Command Line Tools present at install time, which is exactly the assumption that
makes the upstream helper-app step degrade to a warning. CoreAudio is a C API
and Python can call it directly, so this works on a machine with no developer
tooling at all.

**No privilege required.** Creating an aggregate device is a user-level
operation, so this runs after the one password prompt without needing another.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import plistlib
from ctypes import POINTER, Structure, byref, c_char_p, c_long, c_uint32, c_void_p
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CA = None
_CF = None


def _frameworks():
    """Load CoreAudio + CoreFoundation once. Returns (None, None) off macOS."""
    global _CA, _CF
    if _CA is None or _CF is None:
        try:
            ca_path = ctypes.util.find_library("CoreAudio")
            cf_path = ctypes.util.find_library("CoreFoundation")
            if not ca_path or not cf_path:
                return None, None
            ca, cf = ctypes.CDLL(ca_path), ctypes.CDLL(cf_path)

            cf.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_long, c_uint32]
            cf.CFStringGetCString.restype = ctypes.c_bool
            cf.CFDataCreate.argtypes = [c_void_p, c_char_p, c_long]
            cf.CFDataCreate.restype = c_void_p
            cf.CFPropertyListCreateWithData.argtypes = [
                c_void_p, c_void_p, c_uint32, POINTER(c_uint32), POINTER(c_void_p)]
            cf.CFPropertyListCreateWithData.restype = c_void_p
            cf.CFRelease.argtypes = [c_void_p]
            ca.AudioHardwareCreateAggregateDevice.argtypes = [c_void_p, POINTER(c_uint32)]
            ca.AudioHardwareDestroyAggregateDevice.argtypes = [c_uint32]
            _CA, _CF = ca, cf
        except Exception as e:                       # pragma: no cover - non-macOS
            logger.debug(f"[audio] CoreAudio unavailable: {e}")
            return None, None
    return _CA, _CF


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


class _Addr(Structure):
    _fields_ = [("mSelector", c_uint32), ("mScope", c_uint32), ("mElement", c_uint32)]


_SYSTEM = 1
_GLOBAL = _fourcc("glob")
_OUTPUT = _fourcc("outp")
_MAIN = 0
_P_DEVICES = _fourcc("dev#")
_P_DEFAULT_OUT = _fourcc("dOut")
_P_UID = _fourcc("uid ")
_P_NAME = _fourcc("lnam")
_P_STREAMS = _fourcc("stm#")

_UTF8 = 0x08000100

# Aggregate-description keys. These are literal CFString values from
# AudioHardware.h — "stacked" is the one that makes it a Multi-Output Device
# rather than an ordinary aggregate, which is the whole point.
_K_NAME, _K_UID, _K_SUBS = "name", "uid", "subdevices"
_K_MASTER, _K_STACKED, _K_PRIVATE = "master", "stacked", "private"


def _cfstr(ref) -> str:
    _, cf = _frameworks()
    if not cf or not ref:
        return ""
    buf = ctypes.create_string_buffer(1024)
    if cf.CFStringGetCString(ref, buf, 1024, _UTF8):
        return buf.value.decode("utf-8", errors="replace")
    return ""


def _prop(obj: int, selector: int, scope: int = _GLOBAL):
    ca, _ = _frameworks()
    if not ca:
        return None
    addr = _Addr(selector, scope, _MAIN)
    size = c_uint32(0)
    if ca.AudioObjectGetPropertyDataSize(obj, byref(addr), 0, None, byref(size)) != 0:
        return None
    if size.value == 0:
        return None
    buf = (ctypes.c_byte * size.value)()
    if ca.AudioObjectGetPropertyData(obj, byref(addr), 0, None, byref(size), buf) != 0:
        return None
    return buf, size.value


def _prop_string(obj: int, selector: int) -> str:
    got = _prop(obj, selector)
    if not got:
        return ""
    return _cfstr(ctypes.cast(got[0], POINTER(c_void_p))[0])


def default_output_id() -> Optional[int]:
    got = _prop(_SYSTEM, _P_DEFAULT_OUT)
    if not got:
        return None
    return int(ctypes.cast(got[0], POINTER(c_uint32))[0])


def list_devices() -> List[Dict[str, Any]]:
    """Every audio device CoreAudio knows about, with the UIDs we need."""
    got = _prop(_SYSTEM, _P_DEVICES)
    if not got:
        return []
    buf, size = got
    ids = ctypes.cast(buf, POINTER(c_uint32))
    default_out = default_output_id()
    out = []
    for i in range(size // ctypes.sizeof(c_uint32)):
        did = int(ids[i])
        streams = _prop(did, _P_STREAMS, _OUTPUT)
        out.append({
            "id": did,
            "uid": _prop_string(did, _P_UID),
            "name": _prop_string(did, _P_NAME),
            "can_output": bool(streams and streams[1] > 0),
            "is_default_output": did == default_out,
        })
    return out


def find_device(name: str) -> Optional[Dict[str, Any]]:
    """Match on name, exactly first and then by substring — the user's speakers
    may be 'MacBook Pro Speakers' while a manifest says 'BlackHole 2ch'."""
    want = (name or "").strip().lower()
    if not want:
        return None
    devices = list_devices()
    for d in devices:
        if d["name"].strip().lower() == want:
            return d
    for d in devices:
        if want in d["name"].strip().lower():
            return d
    return None


# CoreAudio status codes are four-character codes read as an int. These are the
# ones aggregate-device creation actually returns, translated — a bare number is
# not something a user can act on, and this runs on machines we cannot inspect.
_STATUS_HINTS = {
    0: "success",
    560947818: "the device list is busy — usually means Core Audio is still restarting",
    1852797029: "unsupported operation on this device",
    561211770: "bad property size",
    2003332927: "unknown property",
    560226676: "a device in the list is not valid or has gone away",
    1886547824: "not permitted",
}


def _status_hint(status: int) -> str:
    if status in _STATUS_HINTS:
        return _STATUS_HINTS[status]
    try:
        as_chars = status.to_bytes(4, "big").decode("ascii")
        if as_chars.isprintable():
            return f"code '{as_chars}'"
    except Exception:
        pass
    return "unrecognised error"


def create_multi_output(*, name: str, uid: str, member_uids: List[str],
                        master_uid: Optional[str] = None) -> Dict[str, Any]:
    """Create a Multi-Output Device that plays to every member at once.

    `stacked` is what distinguishes this from a plain aggregate device: an
    aggregate combines devices into one interface, a stacked one mirrors output
    to all members — which is what lets the user hear a call that is
    simultaneously being routed into BlackHole for capture.

    The clock master should be real hardware, not the virtual device: BlackHole
    has no physical clock to follow, and driving the stack from it invites drift.
    """
    ca, cf = _frameworks()
    if not ca or not cf:
        return {"ok": False, "error": "CoreAudio is not available on this system."}
    if not member_uids:
        return {"ok": False, "error": "No devices to combine."}

    description = {
        _K_NAME: name,
        _K_UID: uid,
        _K_STACKED: 1,
        _K_PRIVATE: 0,      # persists across reboots and is visible in Sound
        _K_SUBS: [{_K_UID: u} for u in member_uids],
    }
    if master_uid:
        description[_K_MASTER] = master_uid

    cfdata = cfdict = None
    try:
        raw = plistlib.dumps(description, fmt=plistlib.FMT_XML)
        cfdata = cf.CFDataCreate(None, raw, len(raw))
        fmt, err = c_uint32(0), c_void_p()
        cfdict = cf.CFPropertyListCreateWithData(None, cfdata, 0, byref(fmt), byref(err))
        if not cfdict:
            return {"ok": False, "error": "Could not build the device description."}

        device = c_uint32(0)
        status = ca.AudioHardwareCreateAggregateDevice(cfdict, byref(device))
        if status != 0 or not device.value:
            # Carry the status code AND what we handed it. "macOS refused" alone
            # is unactionable, and this runs on someone else's machine where the
            # device list is the missing half of the picture.
            return {"ok": False, "status": status, "members": list(member_uids),
                    "error": f"CoreAudio refused to create {name} "
                             f"(status {status}, {_status_hint(status)}) from "
                             f"{len(member_uids)} device(s)."}
        logger.info(f"[audio] created multi-output device {name!r} "
                    f"from {len(member_uids)} device(s)")
        return {"ok": True, "device_id": int(device.value), "name": name, "uid": uid}
    except Exception as e:
        return {"ok": False, "error": f"Could not create the device: {e}"}
    finally:
        for ref in (cfdict, cfdata):
            if ref:
                try:
                    cf.CFRelease(ref)
                except Exception:
                    pass


def destroy_device(device_id: int) -> bool:
    ca, _ = _frameworks()
    if not ca:
        return False
    return ca.AudioHardwareDestroyAggregateDevice(int(device_id)) == 0


def wait_for_device(name: str, timeout: float = 25.0,
                    interval: float = 1.0) -> Optional[Dict[str, Any]]:
    """Block until a device shows up, or give up.

    A freshly installed audio driver is not visible the instant its installer
    finishes. `killall coreaudiod` restarts the daemon, and it takes a few
    seconds to rescan plug-ins and republish the device list. Building the
    Multi-Output Device inside that window silently finds nothing to combine
    (field report, 2026-09-18: BlackHole installed correctly, and the device
    that depends on it was skipped a moment too early).
    """
    import time
    deadline = time.monotonic() + timeout
    while True:
        found = find_device(name)
        if found:
            return found
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def ensure_multi_output(*, name: str, uid: str, include_names: List[str],
                        include_default_output: bool = True,
                        wait_seconds: float = 0.0,
                        attempts: int = 3) -> Dict[str, Any]:
    """Make sure the device exists, building it only if it does not.

    Idempotent on purpose. The user may already have built "Meeting Output" by
    hand following the upstream instructions, and creating a second device with
    the same name would be worse than doing nothing — the tool selects by name.
    """
    existing = find_device(name)
    if existing:
        return {"ok": True, "created": False, "device": existing,
                "message": f"{name} already exists."}

    members: List[str] = []
    master: Optional[str] = None
    missing: List[str] = []
    virtual = {n.strip().lower() for n in include_names}

    if include_default_output:
        did = default_output_id()
        devices = list_devices()
        dev = next((d for d in devices if d["id"] == did), None)
        # If the user's output is already the virtual device we are adding — or
        # it reports no UID, which cannot be referenced in an aggregate — pick a
        # real output instead. A multi-output built only from BlackHole would be
        # created successfully and play to nothing the user can hear.
        if not dev or not dev["uid"] or dev["name"].strip().lower() in virtual:
            dev = next((d for d in devices
                        if d["can_output"] and d["uid"]
                        and d["name"].strip().lower() not in virtual), None)
        if dev and dev["uid"]:
            members.append(dev["uid"])
            master = dev["uid"]          # real hardware drives the clock
        else:
            missing.append("a speaker or headphone output")

    for want in include_names:
        # Wait rather than glance: a driver installed seconds ago may still be
        # invisible while coreaudiod restarts.
        dev = wait_for_device(want, timeout=wait_seconds) if wait_seconds else find_device(want)
        if dev and dev["uid"]:
            if dev["uid"] not in members:
                members.append(dev["uid"])
        else:
            missing.append(want)

    # Record what we are about to combine. Two real cases break a naive build:
    # the default output may BE one of the devices we are adding (a user who set
    # BlackHole as output hears nothing), and a device may report no UID at all,
    # which cannot be referenced in an aggregate description.
    chosen = [{"uid": u, "name": next((d["name"] for d in list_devices()
                                       if d["uid"] == u), u)} for u in members]

    if missing:
        # A driver that never appears after a wait usually needs a reboot — which
        # is the advice BlackHole itself gives. Say that, rather than repeating
        # "not installed" at someone who just watched it install.
        hint = (" It may need a restart before macOS publishes it."
                if wait_seconds else "")
        return {"ok": False, "created": False, "missing": missing,
                "error": f"Cannot build {name} yet — {', '.join(missing)} "
                         f"{'is' if len(missing) == 1 else 'are'} not available."
                         f"{hint}"}

    # Retry: coreaudiod may accept the call and still be settling, and a single
    # refusal right after a driver install is not evidence that it cannot work.
    import time
    result = {}
    for attempt in range(1, max(1, attempts) + 1):
        result = create_multi_output(name=name, uid=uid, member_uids=members,
                                     master_uid=master)
        if result.get("ok"):
            break
        if attempt < attempts:
            logger.info(f"[audio] {name} creation attempt {attempt} failed, retrying")
            time.sleep(1.5)
    if not result.get("ok"):
        return {"ok": False, "created": False, "error": result.get("error"),
                "status": result.get("status"), "tried": chosen}

    # Verify it actually appeared rather than trusting the status code.
    appeared = find_device(name)
    if not appeared:
        return {"ok": False, "created": False,
                "error": f"macOS reported success but {name} did not appear."}
    return {"ok": True, "created": True, "device": appeared,
            "message": f"Created {name}."}
