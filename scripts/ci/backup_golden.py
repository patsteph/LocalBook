#!/usr/bin/env python3
"""Writes swift/LocalBookCore/Tests/LocalBookCoreTests/Fixtures/backup_golden.json: what the old app's keyvault and
backup_service make of a fixed recovery phrase and a tiny data folder, so the Swift port is held to the real bytes.

Reimplements the few lines of `services/keyvault.py` and `services/backup_service.py` that fix the format (BIP-39 seed,
X25519 recovery key, the HKDF wrap, the LBBK1 envelope) with fixed randomness, so the fixture is reproducible. Needs
only `cryptography` (and the BIP-39 English list beside this script or in the mnemonic package).
"""
import base64, hashlib, hmac, io, json, os, sys, tarfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PHRASE = ("legal winner thank year wave sausage worth useful legal winner thank year wave sausage worth useful "
          "legal winner thank year wave sausage worth title")          # the BIP-39 test vector for 0x7f*32
WRAP_INFO = b"LocalBook/LB-10/backup-wrap/v1"
MAGIC = b"LBBK1\n"


def to_seed(phrase: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", phrase.encode(), b"mnemonic", 2048)


def main() -> None:
    out = Path(__file__).resolve().parents[2] / "swift/LocalBookCore/Tests/LocalBookCoreTests/Fixtures/backup_golden.json"
    seed = to_seed(PHRASE)
    priv = X25519PrivateKey.from_private_bytes(seed[:32])
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    # A tiny data folder, as `_stage` lays it out, with MANIFEST.json inside the tar under data/.
    files = {"Library/notes.txt": b"hello from the old app\n", "settings.json": b'{"answerLength": "short"}\n'}
    manifest = {
        "format_version": 1, "created_at": "2026-11-27T12:00:00+00:00", "device_id": "golden-device",
        "include_blobs": False, "schema": {"version": "golden"}, "row_counts": {}, "skipped": [],
        "files": {k: {"sha256": hashlib.sha256(v).hexdigest(), "bytes": len(v)} for k, v in files.items()},
        "recovery_public_key": base64.b64encode(pub).decode(),
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in sorted({**files, "MANIFEST.json": json.dumps(manifest, indent=2).encode()}.items()):
            info = tarfile.TarInfo("data/" + name); info.size = len(data); info.mtime = 1_764_000_000; info.mode = 0o600
            tar.addfile(info, io.BytesIO(data))
    tar_bytes = buf.getvalue()

    rng = hashlib.sha256(b"fixed randomness").digest()
    content_key = rng
    eph = X25519PrivateKey.from_private_bytes(hashlib.sha256(b"ephemeral").digest())
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    wrap_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=WRAP_INFO + eph_pub + pub).derive(eph.exchange(priv.public_key()))
    nonce_wrap = hashlib.sha256(b"nonce-wrap").digest()[:12]
    recipient = {"kind": "recovery", "ephemeral_pub": base64.b64encode(eph_pub).decode(), "nonce": base64.b64encode(nonce_wrap).decode(),
                 "wrapped": base64.b64encode(AESGCM(wrap_key).encrypt(nonce_wrap, content_key, b"recovery")).decode()}
    device_key = hashlib.sha256(b"device key").digest()
    nonce_dev = hashlib.sha256(b"nonce-dev").digest()[:12]
    device = {"kind": "device", "device_id": "golden-device", "nonce": base64.b64encode(nonce_dev).decode(),
              "wrapped": base64.b64encode(AESGCM(device_key).encrypt(nonce_dev, content_key, b"device")).decode()}
    header = {"format_version": 1, "created_at": manifest["created_at"], "device_id": "golden-device", "recipients": [recipient, device]}
    header_bytes = json.dumps(header, sort_keys=True).encode()
    nonce = hashlib.sha256(b"nonce-body").digest()[:12]
    ciphertext = AESGCM(content_key).encrypt(nonce, tar_bytes, header_bytes)
    archive = MAGIC + len(header_bytes).to_bytes(4, "big") + header_bytes + nonce + ciphertext

    golden = {
        "phrase": PHRASE, "seed_hex": seed.hex(), "private_hex": seed[:32].hex(), "public_b64": base64.b64encode(pub).decode(),
        "device_key_hex": device_key.hex(), "content_key_hex": content_key.hex(),
        "files": {k: v.decode() for k, v in files.items()}, "manifest": manifest,
        "archive_b64": base64.b64encode(archive).decode(), "tar_sha256": hashlib.sha256(tar_bytes).hexdigest(),
        # One wrong word fails the checksum; the Swift side must refuse it as the old app did.
        "bad_phrase": PHRASE.replace("title", "abandon"),
    }
    out.write_text(json.dumps(golden, indent=2) + "\n")
    print(f"wrote {out} ({len(archive)} archive bytes)")


if __name__ == "__main__":
    main()
