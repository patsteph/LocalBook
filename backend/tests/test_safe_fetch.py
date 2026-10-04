"""Companion fetches: the address is checked at EVERY connection, not once.

2026-10-03: `check_url` resolved and checked the host, then the HTTP client resolved
again at connect (DNS rebinding) and followed redirects unchecked — a public page
answering 302 → 127.0.0.1 reached LocalBook's own API. 100.64/10 (Tailscale) passed.
And fetch_page read a key the scraper never set, so it always returned "".
Offline: a local server stands in for the internet; fake DNS maps names to it.
"""
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from httpcore._backends.auto import AutoBackend

from utils import safe_fetch, url_guard
from tests.test_mcp_server import caller, store, tools  # noqa: F401  (fixtures)

PUBLIC = "93.184.216.34"
served = []


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        served.append(self.path)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://inside.test:{self.server.server_port}/secret")
            self.end_headers()
            return
        body = (b"<html><head><title>Field notes</title></head><body><article><p>"
                + b"Servant leadership puts the team first. " * 30 + b"</p></article></body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def web(monkeypatch):
    served.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dns = {"public.test": [PUBLIC], "inside.test": ["127.0.0.1"]}
    monkeypatch.setattr(url_guard, "_resolve", lambda h: dns.get(h, []))
    monkeypatch.setattr(safe_fetch, "_resolve", lambda h: dns.get(h, []))
    real = AutoBackend.connect_tcp

    async def to_local(self, host, port, **kw):        # "the internet" is the local server
        return await real(self, "127.0.0.1" if host == PUBLIC else host, port, **kw)
    monkeypatch.setattr(AutoBackend, "connect_tcp", to_local)
    yield srv, dns
    srv.shutdown()


def test_a_public_page_comes_back_with_its_text(web):
    srv, _ = web
    out = asyncio.run(safe_fetch.fetch(f"http://public.test:{srv.server_port}/page"))
    assert "Servant leadership" in out["text"] and out["title"] == "Field notes"


def test_a_redirect_into_loopback_is_refused_at_connect(web):
    srv, _ = web
    out = asyncio.run(safe_fetch.fetch(f"http://public.test:{srv.server_port}/redirect"))
    assert out["error"].startswith("refused") and "inside.test resolves to 127.0.0.1" in out["error"]
    assert "/secret" not in served                      # never reached


def test_dns_rebinding_is_refused_at_connect(web, monkeypatch):
    """Public for the check, loopback for the connect."""
    srv, dns = web
    answers = iter([[PUBLIC], ["127.0.0.1"]])
    monkeypatch.setattr(url_guard, "_resolve", lambda h: next(answers))
    monkeypatch.setattr(safe_fetch, "_resolve", lambda h: next(answers))
    out = asyncio.run(safe_fetch.fetch(f"http://rebind.test:{srv.server_port}/page"))
    assert out["error"].startswith("refused") and "127.0.0.1" in out["error"]
    assert served == []


@pytest.mark.parametrize("addr", ["100.64.1.1", "100.100.100.100", "192.168.1.1", "127.0.0.1", "169.254.169.254"])
def test_non_public_addresses_are_refused(addr):
    assert not url_guard.check_url(f"http://{addr}/", resolve=False)
    with pytest.raises(safe_fetch.BlockedAddress):
        safe_fetch.vet_addresses("h", [addr])


def test_one_inward_answer_among_public_ones_is_enough_to_refuse():
    with pytest.raises(safe_fetch.BlockedAddress):
        safe_fetch.vet_addresses("h", [PUBLIC, "10.0.0.5"])


def test_fetch_page_returns_the_text(web, store, caller, tools):
    from tests.test_mcp_server import _call
    srv, _ = web
    out = _call(tools, "fetch_page", url=f"http://public.test:{srv.server_port}/page")
    assert "Servant leadership" in out["text"]           # it used to be "" every time
