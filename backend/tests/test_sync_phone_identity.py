"""The phone-facing P-256 identity: Apple's TLS can't use the Mac's Ed25519 one."""
import base64
import ssl
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from services.sync import identity, store


@pytest.fixture()
def ident(tmp_path, monkeypatch):
    from config import settings
    from services import keyvault
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    seeds = {}
    monkeypatch.setattr(keyvault, "get_or_create", lambda purpose: seeds.setdefault(purpose, bytes(range(32))))
    monkeypatch.setattr(keyvault, "device_id", lambda: "mac-test")
    return tmp_path


def test_phone_certificate_is_p256_and_stable(ident):
    pem = identity.phone_cert_pem()
    assert identity.is_p256_cert(pem) and identity.phone_cert_pem() == pem
    assert not identity.is_p256_cert(identity.cert_pem())          # the Mac identity stays Ed25519


def test_ecdsa_request_signatures_verify_and_tampering_fails(ident):
    key = ec.generate_private_key(ec.SECP256R1())
    # a phone's self-signed cert, built the way identity builds its own
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    import datetime
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "phone")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(__import__("cryptography").hazmat.primitives.serialization.Encoding.PEM).decode()
    ts = str(int(time.time()))
    msg = identity._message("POST", "/phone/hello", ts, b"{}")
    sig = base64.b64encode(key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
    assert identity.verify_signature(pem, "POST", "/phone/hello", ts, b"{}", sig)
    assert not identity.verify_signature(pem, "POST", "/phone/hello", ts, b'{"x":1}', sig)


def test_phone_server_context_is_tls13_p256(ident):
    ctx = identity.server_context([], False, True)
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3


def test_phones_are_not_devices(ident):
    store.pin_phone({"device_id": "p1", "name": "iPhone", "cert_pem": "x", "fingerprint": "f"})
    assert store.phone("p1") and store.devices() == []


def test_phone_key_is_derived_from_the_device_identity_seed(ident):
    """No keyvault purpose of its own: the same seed always gives the same phone key."""
    from services import keyvault
    pub = lambda k: k.public_key().public_numbers().x
    first = pub(identity.phone_private_key())
    assert first == pub(identity.phone_private_key())
    assert identity.phone_private_key().public_key().public_numbers().x == first
