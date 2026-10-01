"""Find the other Macs without typing an address (LB-12, Bonjour).

macOS's own `dns-sd` does it — no new dependency. While sync is on, this Mac
advertises `_localbook._tcp` with its sync port and device id; "Pair a Mac"
browses for a couple of seconds and lists what answered.

Discovery only says WHO is out there. Trust is unchanged: pairing still needs
the 6-digit code confirmed on both Macs, and every sync is pinned mutual TLS.
Networks that block multicast (work / MDM) simply list nothing; typing the
address still works.
"""

from __future__ import annotations

import logging
import re
import signal
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DNS_SD = "/usr/bin/dns-sd"
SERVICE = "_localbook._tcp"
_adv: Optional[subprocess.Popen] = None


def _env() -> Dict[str, str]:
    try:
        from utils.subprocess_env import clean_child_env
        return clean_child_env()
    except Exception:
        return {"PATH": "/usr/bin:/bin"}


def advertise(name: str, port: int, device_id: str) -> bool:
    """Announce this Mac until `stop()`. Idempotent; never raises."""
    global _adv
    if _adv and _adv.poll() is None:
        return True
    try:
        _adv = subprocess.Popen([DNS_SD, "-R", name, SERVICE, "local", str(int(port)), f"id={device_id}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, env=_env())
        return True
    except Exception as exc:
        logger.info("[sync-discovery] not advertising: %s", exc)
        _adv = None
        return False


def stop() -> None:
    global _adv
    if _adv and _adv.poll() is None:
        _adv.terminate()
        try:
            _adv.wait(3)
        except Exception:
            _adv.kill()
    _adv = None


def _run_for(args: List[str], seconds: float) -> str:
    """dns-sd never exits on its own; SIGINT flushes what it found."""
    p = subprocess.Popen([DNS_SD, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, text=True, env=_env())
    time.sleep(seconds)
    p.send_signal(signal.SIGINT)
    try:
        return p.communicate(timeout=3)[0] or ""
    except subprocess.TimeoutExpired:
        p.kill()
        return p.communicate()[0] or ""


_ADD = re.compile(r"\sAdd\s+\d+\s+\d+\s+\S+\s+" + re.escape(SERVICE) + r"\.\s+(.+?)\s*$")
_AT = re.compile(r"can be reached at (\S+?)\.?:(\d+)")
_ID = re.compile(r"\bid=([0-9a-fA-F]+)")


def parse_browse(out: str) -> List[str]:
    names: List[str] = []
    for line in out.splitlines():
        m = _ADD.search(line)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def parse_lookup(out: str) -> Optional[Dict[str, Any]]:
    at, dev = _AT.search(out), _ID.search(out)
    if not at:
        return None
    return {"hostname": at.group(1), "port": int(at.group(2)), "device_id": dev.group(1) if dev else None}


def _ipv4(hostname: str) -> Optional[str]:
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return None


def browse(own_device_id: Optional[str] = None, seconds: float = 2.0) -> List[Dict[str, Any]]:
    """The LocalBook Macs answering on this network (not this one). Blocking —
    call it in a thread. Never raises; an empty list is a normal answer."""
    try:
        names = parse_browse(_run_for(["-B", SERVICE, "local"], seconds))
    except Exception as exc:
        logger.info("[sync-discovery] browse failed: %s", exc)
        return []
    found = []
    for name in names:
        try:
            info = parse_lookup(_run_for(["-L", name, SERVICE, "local"], 1.0))
        except Exception:
            info = None
        if not info or (own_device_id and info.get("device_id") == own_device_id):
            continue
        info["name"] = name
        info["host"] = _ipv4(info["hostname"]) or info["hostname"]
        found.append(info)
    return found
