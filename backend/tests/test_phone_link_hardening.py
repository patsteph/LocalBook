"""The phone link authenticates by signature alone, so the signature has to be replay-proof,
addressed to one Mac, fresh, and rate-limited."""
import base64
import datetime
import hashlib
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from services.sync import identity, peer, store


@pytest.fixture()
def phone(tmp_path, monkeypatch):
    from config import settings
    from services import keyvault
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    monkeypatch.setattr(keyvault, "get_or_create", lambda purpose: bytes(range(32)))
    monkeypatch.setattr(keyvault, "device_id", lambda: "mac-test")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "phone")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    store.put("enabled", True)
    store.pin_phone({"device_id": "phone-1", "name": "iPhone", "cert_pem": pem,
                     "fingerprint": identity.fingerprint(pem)})
    monkeypatch.setattr(peer, "_phone_failures", peer._FailureLimiter())
    monkeypatch.setattr(peer, "_phone_nonces", peer._NonceCache())
    return key


def _post(key, *, nonce="a" * 32, ts=None, mac_id="mac-test", mac_fp=None, client=None, path="/phone/hello"):
    client = client or TestClient(peer.phone_sync_app)
    ts = str(int(time.time()) if ts is None else ts)
    fp = mac_fp or identity.fingerprint(identity.phone_cert_pem())
    msg = identity.phone_message("POST", path, ts, nonce, mac_id, fp, b"{}")
    sig = base64.b64encode(key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
    return client.post(path, content=b"{}", headers={
        "X-LB-Device": "phone-1", "X-LB-Time": ts, "X-LB-Nonce": nonce, "X-LB-Sig": sig})


def test_a_correct_request_is_accepted(phone):
    assert _post(phone).status_code == 200


def test_replaying_a_request_is_refused(phone):
    assert _post(phone, nonce="b" * 32).status_code == 200
    assert _post(phone, nonce="b" * 32).status_code == 403


def test_a_request_signed_for_another_mac_is_refused(phone):
    assert _post(phone, nonce="c" * 32, mac_id="some-other-mac").status_code == 403
    assert _post(phone, nonce="d" * 32, mac_fp="0" * 64).status_code == 403


def test_a_stale_request_is_refused(phone):
    assert _post(phone, nonce="e" * 32, ts=int(time.time()) - 600).status_code == 403


def test_the_old_unsigned_nonce_format_is_refused(phone):
    ts = str(int(time.time()))
    old = b"\n".join([b"POST", b"/phone/hello", ts.encode(), hashlib.sha256(b"{}").hexdigest().encode()])
    sig = base64.b64encode(phone.sign(old, ec.ECDSA(hashes.SHA256()))).decode()
    r = TestClient(peer.phone_sync_app).post("/phone/hello", content=b"{}", headers={
        "X-LB-Device": "phone-1", "X-LB-Time": ts, "X-LB-Sig": sig})
    assert r.status_code == 403


def test_repeated_failures_lock_the_address_out(phone):
    client = TestClient(peer.phone_sync_app)
    for i in range(6):
        assert _post(phone, nonce=f"{i:032x}", mac_id="wrong", client=client).status_code == 403
    assert _post(phone, nonce="f" * 32, client=client).status_code == 429     # even a valid one now


def test_limiter_and_nonce_cache_expire():
    lim = peer._FailureLimiter(max_failures=3, window=10, block=20)
    for _ in range(3):
        lim.failure("ip", now=100)
    assert lim.blocked("ip", now=110) and not lim.blocked("ip", now=121)
    cache = peer._NonceCache(ttl=10, max_items=3)
    assert cache.first_use("a", now=0) and not cache.first_use("a", now=5) and cache.first_use("a", now=11)
