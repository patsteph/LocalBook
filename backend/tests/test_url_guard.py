"""LB-2 SSRF guard.

`fetch_page` lets a companion name a URL and have LocalBook fetch it from
inside the user's LAN, next to LocalBook's own API on 127.0.0.1:8000. These
tests are the deny list.

`resolve=False` where a test must not depend on DNS; the resolution behaviour
has its own tests that monkeypatch the resolver rather than hit the network.
"""

import pytest

from utils.url_guard import check_url


# ── schemes ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "file://localhost/etc/shadow",
        "gopher://example.com/",
        "ftp://example.com/x",
        "data:text/html,<script>",
        "javascript:alert(1)",
        "ssh://example.com",
    ],
)
def test_only_http_and_https_are_fetchable(url):
    v = check_url(url, resolve=False)
    assert not v
    assert "http" in v.reason


def test_a_plain_hostname_with_no_scheme_is_refused():
    assert not check_url("example.com/page", resolve=False)


# ── loopback: LocalBook's own API ───────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/notebooks",
        "http://127.0.0.1/",
        "http://localhost:8000/v1/models",
        "https://LOCALHOST/",
        "http://[::1]:8000/",
        "http://127.0.0.2/",
    ],
)
def test_loopback_is_refused(url):
    """This is the one that matters most: /mcp is exempt from the app token,
    so a companion able to fetch 127.0.0.1 could reach every route it was
    never granted."""
    v = check_url(url, resolve=False)
    assert not v
    assert "loopback" in v.reason


# ── the LAN ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5/admin",
        "http://172.16.4.1/",
        "http://172.31.255.255/",
        "http://192.168.1.1/",
        "https://192.168.0.254:8443/",
    ],
)
def test_private_ranges_are_refused(url):
    v = check_url(url, resolve=False)
    assert not v
    assert "private" in v.reason


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",   # cloud metadata
        "http://169.254.1.1/",
        "http://[fe80::1]/",
    ],
)
def test_link_local_is_refused(url):
    v = check_url(url, resolve=False)
    assert not v
    assert "link-local" in v.reason


@pytest.mark.parametrize(
    "url",
    ["http://mac-mini.local/", "http://printer.local:631/", "http://x.internal/",
     "http://y.home.arpa/"],
)
def test_mdns_and_internal_names_are_refused(url):
    v = check_url(url, resolve=False)
    assert not v
    assert "local" in v.reason


def test_a_trailing_dot_does_not_slip_a_local_name_through():
    assert not check_url("http://mac-mini.local./", resolve=False)


def test_credentials_in_the_url_are_refused():
    v = check_url("http://user:pass@example.com/", resolve=False)
    assert not v
    assert "credentials" in v.reason


# ── the bypass this guard exists for ────────────────────────────────────────


def test_a_public_name_resolving_inward_is_refused(monkeypatch):
    """The standard bypass: a guard that only reads the string sees
    `evil.example.com` and allows it."""
    import utils.url_guard as guard

    monkeypatch.setattr(guard, "_resolve", lambda host: ["127.0.0.1"])
    v = guard.check_url("http://evil.example.com/")
    assert not v
    assert "evil.example.com" in v.reason and "127.0.0.1" in v.reason


def test_every_resolved_address_is_checked_not_just_the_first(monkeypatch):
    """A host answering with one public and one private address must be
    refused — taking the first would be a coin flip."""
    import utils.url_guard as guard

    monkeypatch.setattr(guard, "_resolve", lambda host: ["93.184.216.34", "10.1.2.3"])
    v = guard.check_url("http://mixed.example.com/")
    assert not v
    assert "10.1.2.3" in v.reason


def test_a_host_that_does_not_resolve_is_refused(monkeypatch):
    import utils.url_guard as guard

    monkeypatch.setattr(guard, "_resolve", lambda host: [])
    v = guard.check_url("http://nowhere.example.com/")
    assert not v
    assert "does not resolve" in v.reason


# ── what must still work ────────────────────────────────────────────────────


def test_ordinary_public_urls_are_allowed(monkeypatch):
    import utils.url_guard as guard

    monkeypatch.setattr(guard, "_resolve", lambda host: ["93.184.216.34"])
    for url in (
        "https://example.com/article",
        "http://example.com:8080/a/b?c=d#e",
        "https://en.wikipedia.org/wiki/Thing",
    ):
        assert guard.check_url(url), url


def test_a_public_literal_address_is_allowed():
    assert check_url("https://93.184.216.34/", resolve=False)


def test_empty_and_nonsense_input_is_refused():
    assert not check_url("")
    assert not check_url(None)
    assert not check_url("   ")
