"""HuggingFace transport configuration — ONE place, for the installer and the runtime.

Why this exists (2026-08-19): `huggingface_hub` 1.x **removed `configure_http_backend`** and
replaced it with `set_client_factory`, because the library migrated from `requests` to `httpx`.
It is not a rename — the factory must now return an `httpx.Client`, so every call site's body was
wrong too, not just its import.

We had SIX copies of that now-broken block: five in `install.sh` and one at
`services/audio_llm.py:543` (the Kokoro-TTS SSL helper). All six raised `ImportError` on the
shipped `huggingface_hub==1.23.0`, which is the most likely reason the arctic embedding model
could never be downloaded on a machine needing the SSL bypass — and it silently broke the TTS
download path in exactly the same way.

⚠️ Setting the client factory is NOT sufficient on its own. The Xet transfer path does its own
downloading and does not go through this client, so an SSL bypass configured here never reaches
the model bytes. `HF_HUB_DISABLE_XET=1` forces transfers back onto the httpx client this
configures — which is why the two are set together here rather than left to each caller.

Never raises: a transport-config failure must degrade to library defaults, never break a download.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_INSTALLED = False
_TRUST_INJECTED = False

CONNECT_TIMEOUT = 30.0
READ_TIMEOUT = 120.0
RETRIES = 3


def ssl_noverify_requested() -> bool:
    """The installer sets this when it had to fall back to an unverified handshake."""
    return os.environ.get("LOCALBOOK_SSL_NOVERIFY") == "1"


def install_system_trust(force: bool = False) -> bool:
    """Verify TLS against the macOS keychain instead of certifi. Idempotent; never raises.

    Why (2026-09-14): on a machine whose network inspects HTTPS, the middlebox re-signs every
    connection with a private root. macOS trusts it — `curl`, Safari and the browser extension
    all work — but Python does not, because certifi ships PUBLIC roots only and has no way to
    learn about a locally-installed one. Every httpx call then dies with
    `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`, which httpx surfaces as
    a bare `ConnectError` — indistinguishable from an unplugged cable.

    The symptom was "the model browser can't reach Hugging Face", but the blast radius is every
    Python-side HTTPS call: model downloads, the embedding checkpoint, FlashRank, article
    fetching. Kokoro TTS was the only survivor, and only because `audio_llm` shells out to
    `curl -k` when frozen — an accident, not a design.

    `truststore` routes verification through the platform verifier, which is exactly what curl
    already does. It does NOT weaken anything: a self-signed or expired certificate still fails
    (verified against badssl.com — "certificate is not trusted" / "certificate is expired").
    That distinction matters, because the pre-existing escape hatch (`LOCALBOOK_SSL_NOVERIFY`)
    turns verification OFF, and this must not quietly become that.

    Injection is global — it swaps `ssl.SSLContext` — so it has to run before the clients that
    depend on it are constructed. `main.py` calls it during startup, ahead of every service
    import, and `install_hf_transport()` calls it again for any path that reaches the network
    without going through startup (the installer's standalone python blocks).
    """
    global _TRUST_INJECTED
    if _TRUST_INJECTED and not force:
        return True
    try:
        import truststore

        truststore.inject_into_ssl()
        _TRUST_INJECTED = True
        logger.info("[hf-transport] TLS verifies against the system trust store")
        return True
    except Exception as e:
        # certifi remains in place; this is a downgrade, not a failure. Warn rather than
        # info: on an intercepted network it is the line that explains everything after it.
        logger.warning(f"[hf-transport] system trust store unavailable ({type(e).__name__}: {e}); "
                       "falling back to certifi — HTTPS will fail if this network inspects TLS")
        return False


def install_hf_transport(force: bool = False) -> bool:
    """Configure huggingface_hub's HTTP transport. Idempotent; returns True if applied.

    Call before ANY `snapshot_download` / `hf_hub_download` / model load that may hit the network.
    """
    global _INSTALLED
    if _INSTALLED and not force:
        return True

    no_verify = ssl_noverify_requested()

    # Trust first: the client factory below is built with `verify=True` in the normal case, so
    # it inherits whatever `ssl` resolves to at construction time.
    if not no_verify:
        install_system_trust()

    # Xet bypasses the configured client entirely, so an SSL bypass would not reach the bytes.
    # Only disabled when we actually need the bypass — Xet is faster when it works.
    if no_verify:
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    # Bound Xet's concurrency even when it IS in use: `max_workers=1` at the download call site
    # does not constrain Xet's internal range-GETs, which is how a "serialised" download still
    # saturates a constrained connection.
    os.environ.setdefault("HF_XET_NUM_CONCURRENT_RANGE_GETS", "2")

    try:
        import huggingface_hub as hf

        if hasattr(hf, "set_client_factory"):
            # huggingface_hub >= 1.0 — httpx
            import httpx

            def _factory() -> "httpx.Client":
                return httpx.Client(
                    verify=not no_verify,
                    timeout=httpx.Timeout(
                        connect=CONNECT_TIMEOUT, read=READ_TIMEOUT,
                        write=READ_TIMEOUT, pool=CONNECT_TIMEOUT,
                    ),
                    transport=httpx.HTTPTransport(retries=RETRIES, verify=not no_verify),
                    follow_redirects=True,
                )

            hf.set_client_factory(_factory)
        elif hasattr(hf, "configure_http_backend"):
            # huggingface_hub < 1.0 — requests. Kept so the installer still works if it runs
            # against an older env on another machine.
            import requests
            from requests.adapters import HTTPAdapter

            class _TimeoutAdapter(HTTPAdapter):
                def send(self, *a, **kw):
                    kw.setdefault("timeout", (CONNECT_TIMEOUT, READ_TIMEOUT))
                    return super().send(*a, **kw)

            def _legacy_factory() -> "requests.Session":
                s = requests.Session()
                s.mount("http://", _TimeoutAdapter(max_retries=RETRIES))
                s.mount("https://", _TimeoutAdapter(max_retries=RETRIES))
                if no_verify:
                    s.verify = False
                return s

            hf.configure_http_backend(backend_factory=_legacy_factory)
        else:
            logger.warning("[hf-transport] neither set_client_factory nor configure_http_backend "
                           "is available; using library defaults")
            return False

        if no_verify:
            # These override a client's own verify setting, so they must go.
            for key in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
                os.environ.pop(key, None)
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass

        _INSTALLED = True
        logger.info(f"[hf-transport] configured (ssl_verify={not no_verify}, "
                    f"xet_disabled={os.environ.get('HF_HUB_DISABLE_XET') == '1'})")
        return True
    except Exception as e:
        logger.warning(f"[hf-transport] could not configure transport, using defaults: {e}")
        return False


def bootstrap_snippet(backend_dir: Optional[str] = None) -> str:
    """The two lines an `install.sh` python block needs to reuse this module.

    The installer runs standalone `python -c` blocks with no package context, so they put the
    backend dir on `sys.path` and import this. One implementation, not six copies.
    """
    d = backend_dir or "os.path.dirname(os.path.abspath(__file__))"
    return (f"import sys, os; sys.path.insert(0, {d!r})\n"
            "from services.hf_transport import install_hf_transport; install_hf_transport()")
