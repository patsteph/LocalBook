"""This Mac's sync identity (LB-12a): an Ed25519 key, a certificate, TLS contexts.

The key is the 32-byte `device_identity` seed keyvault has reserved since K-1 —
in the Keychain, wrapped to the recovery key, never on disk. The certificate is
self-signed (CA:TRUE with key identifiers, so OpenSSL accepts it as its own
trust anchor) and is what a peer PINS at pairing: trust is "this exact
certificate", never a CA.

Two quirks this module absorbs:
  * `truststore` replaces `ssl.SSLContext` process-wide, and even the ORIGINAL
    class's property setters then recurse (they look the patched name up). The
    contexts here are the original class, configured through the C-level
    descriptors — verified 2026-09-30: pinned mTLS 1.3 works, an unpinned
    client and a wrong server are both refused.
  * `load_cert_chain` only reads files. The private key is written 0600 into
    the data dir (inside the encrypted volume when there is one) and unlinked
    the moment it is loaded.
"""

from __future__ import annotations

import _ssl
import base64
import datetime
import hashlib
import os
import ssl
import time
from pathlib import Path
from typing import Iterable, Optional

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

PROTOCOL = 1


def _ctx_class():
    try:
        from truststore._api import _original_SSLContext
        return _original_SSLContext
    except Exception:
        return ssl.SSLContext


def _set(ctx, name: str, value) -> None:
    getattr(_ssl._SSLContext, name).__set__(ctx, value)


def _dir() -> Path:
    from config import settings

    d = Path(settings.data_dir) / "sync"
    d.mkdir(parents=True, exist_ok=True)
    return d


def device_id() -> str:
    from services import keyvault

    return keyvault.device_id()


def device_name() -> str:
    try:
        import subprocess
        out = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    import socket
    return socket.gethostname()


def private_key() -> ed25519.Ed25519PrivateKey:
    from services import keyvault

    return ed25519.Ed25519PrivateKey.from_private_bytes(keyvault.get_or_create("device_identity"))


def _pub_raw(key) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def cert_pem() -> str:
    """This Mac's certificate, made once and reused while the key is unchanged."""
    key = private_key()
    p = _dir() / "device.crt"
    if p.exists():
        cert = x509.load_pem_x509_certificate(p.read_bytes())
        if _pub_raw(cert.public_key()) == _pub_raw(key.public_key()):
            return p.read_text()
    pub = key.public_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"LocalBook {device_id()}")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(pub)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365 * 20))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(True, False, False, False, False, True, False, False, False),
                           critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(pub), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(pub), critical=False)
            .sign(key, None))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    p.write_text(pem)
    return pem


def fingerprint(pem: str) -> str:
    der = x509.load_pem_x509_certificate(pem.encode()).public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


def fingerprint_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def sas(fp_a: str, fp_b: str) -> str:
    """The 6-digit code both Macs show at pairing. A man in the middle presents
    a different certificate to each side, so the two screens would disagree."""
    lo, hi = sorted((fp_a, fp_b))
    return f"{int(hashlib.sha256((lo + hi).encode()).hexdigest(), 16) % 1_000_000:06d}"


# ── request signing: binds each request to one pinned device ────────────────


def _message(method: str, path: str, ts: str, body: bytes) -> bytes:
    return b"\n".join([method.upper().encode(), path.encode(), ts.encode(),
                       hashlib.sha256(body or b"").hexdigest().encode()])


def sign_headers(method: str, path: str, body: bytes) -> dict:
    ts = str(int(time.time()))
    sig = private_key().sign(_message(method, path, ts, body))
    return {"X-LB-Device": device_id(), "X-LB-Time": ts,
            "X-LB-Sig": base64.b64encode(sig).decode()}


def verify_signature(cert: str, method: str, path: str, ts: str, body: bytes, sig_b64: str,
                     max_skew: int = 300) -> bool:
    try:
        if abs(int(time.time()) - int(ts)) > max_skew:
            return False
        pub = x509.load_pem_x509_certificate(cert.encode()).public_key()
        pub.verify(base64.b64decode(sig_b64), _message(method, path, ts, body))
        return True
    except Exception:
        return False


# ── TLS contexts ─────────────────────────────────────────────────────────────


def _load_own(ctx) -> None:
    key_path = _dir() / f".key-{os.getpid()}-{time.time_ns()}.pem"
    cert_path = _dir() / "device.crt"
    cert_pem()
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, private_key().private_bytes(serialization.Encoding.PEM,
                                                 serialization.PrivateFormat.PKCS8,
                                                 serialization.NoEncryption()))
    finally:
        os.close(fd)
    try:
        ctx.load_cert_chain(str(cert_path), str(key_path))
    finally:
        key_path.unlink(missing_ok=True)


def server_context(peer_pems: Iterable[str], require_client: bool = True):
    ctx = _ctx_class()(ssl.PROTOCOL_TLS_SERVER)
    _set(ctx, "minimum_version", ssl.TLSVersion.TLSv1_3)
    _load_own(ctx)
    pems = "".join(peer_pems)
    if require_client:
        _set(ctx, "verify_mode", ssl.CERT_REQUIRED)
        if pems:
            ctx.load_verify_locations(cadata=pems)
    else:
        _set(ctx, "verify_mode", ssl.CERT_NONE)
    return ctx


def client_context(peer_pem: Optional[str]):
    """Pinned to exactly `peer_pem`; `None` only for the first pairing hello,
    where the SAS the user compares is what authenticates the certificate."""
    ctx = _ctx_class()(ssl.PROTOCOL_TLS_CLIENT)
    _set(ctx, "minimum_version", ssl.TLSVersion.TLSv1_3)
    _set(ctx, "check_hostname", False)
    _load_own(ctx)
    if peer_pem:
        ctx.load_verify_locations(cadata=peer_pem)
    else:
        _set(ctx, "verify_mode", ssl.CERT_NONE)
    return ctx
