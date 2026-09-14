"""TLS trust: the system keychain, not certifi's public-roots-only bundle.

The failure this guards (2026-09-14): on a network that inspects HTTPS, every connection is
re-signed by a private root. macOS trusts it, so `curl`, Safari and the browser extension all
work. certifi has never heard of it, so every Python HTTPS call dies with
`CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`. It presented as "the model
browser can't reach Hugging Face", but it takes out model downloads, the embedding checkpoint,
FlashRank and article fetching too.

The line these tests walk: routing verification through the platform verifier must NOT become a
second `LOCALBOOK_SSL_NOVERIFY`. Trusting what the machine's administrator trusts is not the
same as trusting everything, and an expired or self-signed certificate still has to fail.
"""
import logging
import ssl
import sys

import pytest

from services import hf_transport


@pytest.fixture(autouse=True)
def _restore_ssl():
    """Injection is global — it swaps `ssl.SSLContext`. Leaving it in place would change TLS
    for every test that runs after this file."""
    original = ssl.SSLContext
    prev_flag = hf_transport._TRUST_INJECTED
    hf_transport._TRUST_INJECTED = False
    try:
        yield
    finally:
        try:
            import truststore
            truststore.extract_from_ssl()
        except Exception:
            pass
        ssl.SSLContext = original
        hf_transport._TRUST_INJECTED = prev_flag


def test_verification_moves_to_the_platform_verifier():
    assert hf_transport.install_system_trust() is True
    import truststore
    assert ssl.SSLContext is truststore.SSLContext


def test_injection_is_idempotent():
    assert hf_transport.install_system_trust() is True
    assert hf_transport.install_system_trust() is True, "a second call must be a no-op, not a re-inject"


def test_it_still_rejects_a_certificate_the_machine_does_not_trust():
    """The whole point. If this ever passes without raising, trust has been turned OFF rather
    than redirected, and the fix has become the bug it replaced."""
    hf_transport.install_system_trust()
    import truststore
    ctx = ssl.create_default_context()
    assert isinstance(ctx, truststore.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED, "verification must remain required"
    assert ctx.check_hostname is True, "hostname checking must remain on"


def test_a_missing_truststore_degrades_to_certifi_rather_than_raising(monkeypatch, caplog):
    """`hf_transport` never raises, by contract — a transport-config failure must not break a
    download. A machine without truststore is back to certifi, which is where we started."""
    monkeypatch.setitem(sys.modules, "truststore", None)
    with caplog.at_level(logging.WARNING, logger=hf_transport.logger.name):
        assert hf_transport.install_system_trust(force=True) is False
    assert any("certifi" in r.message for r in caplog.records), \
        "the fallback has to be visible — on an intercepted network it explains every later failure"


def test_configuring_the_hub_transport_also_fixes_trust(monkeypatch):
    """`install_hf_transport` builds its client with verify=True, so it inherits whatever `ssl`
    resolves to when the client is constructed. Trust has to be in place first."""
    calls = []
    monkeypatch.setattr(hf_transport, "install_system_trust",
                        lambda *a, **k: calls.append(True) or True)
    hf_transport.install_hf_transport(force=True)
    assert calls, "the Hub transport must not be configured on top of a broken trust store"


def test_the_bypass_flag_is_still_the_only_way_to_disable_verification(monkeypatch):
    """When the installer has already decided verification must be off, injecting a VERIFYING
    trust store on top would fight it. The two paths stay separate."""
    calls = []
    monkeypatch.setenv("LOCALBOOK_SSL_NOVERIFY", "1")
    monkeypatch.setattr(hf_transport, "install_system_trust",
                        lambda *a, **k: calls.append(True) or True)
    hf_transport.install_hf_transport(force=True)
    assert not calls


# ── The artifact, not the diff (the lesson of 2026-09-02) ───────────────────────
#
# Removing an import did not remove a dependency then; adding one here does not add it to the
# bundle either. truststore is imported lazily inside a function, so PyInstaller's static
# analysis cannot see it — without the hidden-import the built app silently falls back to
# certifi and the bug returns in exactly the form that is hardest to spot: only on the
# machines that need the fix.

def _uncommented(path) -> str:
    return "\n".join(l for l in path.read_text().splitlines() if not l.strip().startswith("#"))


def test_the_bundle_is_told_to_include_truststore():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert "truststore" in _uncommented(root / "build_backend.sh"), \
        "lazily imported — PyInstaller needs --hidden-import=truststore or the built app loses this fix"
    assert "truststore" in _uncommented(root / "requirements.in"), \
        "a hidden-import for a package that is not installed fails the build"
