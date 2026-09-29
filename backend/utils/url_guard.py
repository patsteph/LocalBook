"""SSRF guard for URLs an agent hands us.

LB-2 of the v2.5.0 plan. `fetch_page` lets a companion name a URL and have
LocalBook fetch it. LocalBook runs on the user's machine, inside their LAN,
alongside its own API on 127.0.0.1:8000 — so "fetch this URL for me" is a
request to make LocalBook act as a proxy into a network the caller cannot
otherwise reach. That is the whole shape of SSRF.

What is refused, and why each one matters here:

  * **Anything that is not http/https.** `file:///etc/passwd` and `gopher://`
    are the classic reads; `data:` and `blob:` are pointless to fetch.
  * **Loopback.** `http://127.0.0.1:8000/notebooks` is LocalBook's own API, and
    it is exempt from the app token on some prefixes. A companion scoped to
    `mcp` could otherwise reach every route it was never granted.
  * **Private ranges** (10/8, 172.16/12, 192.168/16) and **link-local**
    (169.254/16, fe80::/10). The last one includes the cloud metadata address
    169.254.169.254 — not a threat on a Mac mini, but this code is not the
    place to bet on where it runs.
  * **`.local` and other mDNS names**, which resolve onto the LAN.
  * **Credentials in the URL** (`http://user:pass@host`), which are a redirect
    trick more often than an intention.

**Resolution, not just parsing.** A hostname is resolved and EVERY address it
returns is checked, because `evil.example.com` resolving to 127.0.0.1 is the
standard bypass for a guard that only reads the string. This still leaves a
DNS-rebind window between the check and the fetch; closing that properly means
pinning the resolved address into the connection, which is noted in LB-2's next
steps rather than pretended away here.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")

# Suffixes that resolve on the local network rather than the internet.
BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".home.arpa")

BLOCKED_HOSTNAMES = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})


@dataclass
class UrlVerdict:
    allowed: bool
    reason: Optional[str] = None
    resolved: Tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.allowed


def _address_is_blocked(ip: ipaddress._BaseAddress) -> Optional[str]:
    if ip.is_loopback:
        return "loopback addresses are not fetchable — that is LocalBook itself"
    if ip.is_link_local:
        return "link-local addresses are not fetchable"
    if ip.is_private:
        return "private network addresses are not fetchable"
    if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return "reserved, multicast and unspecified addresses are not fetchable"
    return None


def _resolve(host: str) -> List[str]:
    """Every address this hostname answers to. Empty when it does not resolve."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return []
    out: List[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in out:
            out.append(addr)
    return out


def check_url(url: str, *, resolve: bool = True) -> UrlVerdict:
    """Decide whether an agent-supplied URL may be fetched.

    `resolve=False` skips DNS — used only by tests that must stay offline, and
    by callers that have already pinned an address.
    """
    if not url or not isinstance(url, str):
        return UrlVerdict(False, "no URL given")

    try:
        parsed = urlparse(url.strip())
    except ValueError as exc:
        return UrlVerdict(False, f"unparseable URL: {exc}")

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return UrlVerdict(
            False,
            f"only {' and '.join(ALLOWED_SCHEMES)} URLs can be fetched, not "
            f"{parsed.scheme or 'a scheme-less URL'!r}",
        )

    if parsed.username or parsed.password:
        return UrlVerdict(False, "URLs carrying credentials are not fetchable")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return UrlVerdict(False, "the URL has no host")

    if host in BLOCKED_HOSTNAMES:
        return UrlVerdict(False, "loopback addresses are not fetchable — that is LocalBook itself")

    if any(host.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
        return UrlVerdict(False, f"{host} is a local-network name, not an internet host")

    # A literal address needs no DNS, and must still be checked.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        blocked = _address_is_blocked(literal)
        return UrlVerdict(blocked is None, blocked, (host,))

    if not resolve:
        return UrlVerdict(True, None, ())

    addresses = _resolve(host)
    if not addresses:
        return UrlVerdict(False, f"{host} does not resolve")

    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        blocked = _address_is_blocked(ip)
        if blocked:
            # Named explicitly: a public-looking host resolving inward is the
            # bypass this function exists for, and a vague error hides it.
            return UrlVerdict(False, f"{host} resolves to {addr}, and {blocked}", tuple(addresses))

    return UrlVerdict(True, None, tuple(addresses))
