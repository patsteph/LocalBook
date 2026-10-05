"""Key custody for the keys LocalBook cannot afford to lose silently.

K-1 of the v2.5.0 plan. Read `READFIRST/ArchitectureDocs/KEY_CUSTODY.md` before changing
anything here.

Three purposes live in this vault:

    credentials      the Fernet key for credentials.enc and auth/*.enc
    backup           the key LB-10's encrypted backups are written under
    device_identity  this machine's Ed25519 seed, used by LB-12 pairing

    volume           the passphrase for LB-11's encrypted sparsebundle

**On `volume`, and the plan's apparent contradiction.** K-1 says the volume
password is "held by macOS, not by us" and moves it out of this module; LB-11
says it "comes from `keyvault("volume")`". Both describe what is implemented
here, because this module's storage IS D20's mechanism:
`security add-generic-password -A` writes an ordinary login-keychain item with a
permissive ACL, so no app identity is consulted and an ad-hoc `./build.sh
--rebuild` reads the same item. What K-1 was ruling out was `SecItemAdd` and the
data-protection keychain, which an ad-hoc build cannot use at all.

Routing it through here also satisfies K-1(c) — "a copy of every key, **including
the volume password**, is wrapped to the recovery public key" — with no extra
machinery. Reconciled 2026-09-29 while building LB-11.

Two things about this module are asymmetric with `keychain_manager`, on purpose:

  * `keychain_manager` FAILS OPEN. Its keys unlock search and YouTube; a missing
    one degrades a feature, and a 4-hour biometric TTL is a fair trade.
  * `keyvault` FAILS CLOSED. Its keys gate the credential store, the backups and
    this device's identity. Handing back a freshly generated key instead of the
    real one does not degrade anything — it silently orphans the data the key was
    protecting, and the failure only surfaces later, as corruption. So a read that
    cannot be completed raises; it never invents a key.

  Do not "fix" that asymmetry. It is the whole design.

Recovery (D6'). A 24-word BIP-39 phrase deterministically produces one X25519 key
pair. Only the PUBLIC half is ever stored on a device. Each machine generates its
own random keys and keeps a copy of each wrapped to that public key, beside — not
inside — the encrypted volume, so a wiped Keychain or a replaced Mac is
recoverable from the phrase alone. The private half exists only while the user is
typing the phrase, which is also why nothing secret is ever typed on the
work-managed Mac.

Why this shells out to `security(1)` instead of calling SecItemAdd:
    Items must carry a PERMISSIVE ACL, so that an ad-hoc `./build.sh --rebuild`,
    an `install.sh` source build and the notarized bundle all read the same item
    with no prompt and no app identity consulted (D20). `SecItemAdd` without an
    explicit `kSecAttrAccess` produces the opposite — an ACL trusting only the
    creating binary — and building a null ACL needs `SecAccessCreate`, which the
    pyobjc Security bindings expose awkwardly. `security -A` produces exactly the
    item the K-1 gate verified end to end, including from a launchd context with
    no GUI session (gate item 8, 2026-09-29). One mechanism, already proven,
    beats two code paths where only one was tested.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Dict, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

# ── constants ───────────────────────────────────────────────────────────────

SERVICE_NAME = "LocalBook-keyvault"


def service_name() -> str:
    """The Keychain service for THIS data dir's keys.

    The production data dir keeps `SERVICE_NAME` exactly — its items already
    exist, lib.rs reads the volume key under that name, and renaming it would
    orphan every key on every machine. Any other data dir (the dev sandbox, a
    test, a throwaway end-to-end run) gets a name derived from its resolved
    path. Before this, a second install on the same Mac read and WROTE the
    production items: a test run created the real `volume` item, and a recovery
    setup there would have wrapped production's credential key to a phrase that
    belonged to the test. The keys dir is already named after the data dir for
    the same reason (`_keys_dir`).
    """
    from config import PRODUCTION_DATA_DIR

    d = _data_dir()
    try:
        resolved = d.resolve()
        if resolved == PRODUCTION_DATA_DIR.resolve():
            return SERVICE_NAME
    except OSError:
        resolved = d
        if d == PRODUCTION_DATA_DIR:
            return SERVICE_NAME
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:12]
    return f"{SERVICE_NAME}.{digest}"
PURPOSES = ("credentials", "backup", "device_identity", "volume")
KEY_BYTES = 32

_WRAP_INFO = b"LocalBook/K-1/recovery-wrap/v1"
_ENVELOPE_VERSION = 1

# Timeout for every `security(1)` call. A keychain read is local and immediate;
# if one blocks, it is prompting, and a prompt here means the permissive ACL was
# lost. Failing fast is better than hanging a startup path forever.
_SECURITY_TIMEOUT = 15


class KeyVaultError(RuntimeError):
    """A key could not be read, written or unwrapped.

    Always raised rather than returning a fresh key — see the module docstring.
    """


# ── data-dir plumbing ───────────────────────────────────────────────────────


def _data_dir() -> Path:
    """Resolve the data dir through settings, never by hardcoding a path.

    Imported at call time so tests can monkeypatch `settings.data_dir` to a temp
    directory. `backend/.venv` resolves it to the REAL production data dir, so a
    module-level capture here would point every test at the user's own data.
    """
    from config import settings

    return Path(settings.data_dir)


# The legacy location, inside the data dir. Migrated out on first access.
LEGACY_KEYS_DIRNAME = "LocalBook.keys"


def _keys_dir() -> Path:
    """Where wrapped copies live: BESIDE the data dir, never inside it.

    This is D6′, and LB-11 is why it matters. LB-11 makes the data dir a mount
    point for an encrypted sparsebundle — so wrapped keys stored inside it would
    be sealed in exactly the volume they exist to unlock. A Keychain reset would
    then be unrecoverable at precisely the moment recovery is needed, while the
    UI cheerfully reported "protected".

    Named after the data dir rather than fixed, so the dev sandbox and production
    do not share one keys directory:

        ~/Library/Application Support/LocalBook       →  LocalBook.keys
        ~/Library/Application Support/LocalBook-dev   →  LocalBook-dev.keys

    A fixed `LocalBook.keys` would have had a dev run reading and wrapping
    against the real machine's keys.
    """
    data_dir = _data_dir()
    current = data_dir.parent / f"{data_dir.name}.keys"
    _migrate_keys_dir(data_dir, current)
    return current


def adopt_restored_keys(data_dir: Path) -> list:
    """Hand a restored archive's wrapped key sets to the keys dir.

    A restore puts the archive's `LocalBook.keys` inside the data dir — the legacy
    spot, which `_migrate_keys_dir` ignores once this Mac has its own keys dir.
    Merge each device's set in, never overwriting: this Mac's own keys and its
    recovery.pub stay as they are, and the old Mac's set becomes reachable for
    `restore_from_phrase(device=…)`. Returns the device ids adopted.
    """
    restored = Path(data_dir) / LEGACY_KEYS_DIRNAME
    if not restored.is_dir():
        return []
    target = _keys_dir()
    target.mkdir(parents=True, exist_ok=True)
    adopted = []
    for device in restored.iterdir():
        if not device.is_dir():
            continue
        dest = target / device.name
        for f in device.iterdir():
            if f.is_file() and not (dest / f.name).exists():
                dest.mkdir(mode=0o700, exist_ok=True)
                shutil.copy2(f, dest / f.name)
                if device.name not in adopted:
                    adopted.append(device.name)
    if adopted:
        logger.warning("[keyvault] adopted wrapped keys from a restored archive: %s", adopted)
    return adopted


def _migrate_keys_dir(data_dir: Path, target: Path) -> None:
    """Move a pre-LB-11 keys directory out of the data dir, once.

    Copies rather than moves, and only when the target does not exist — the
    wrapped keys are the last line of recovery, and a half-finished move of them
    is the one failure with no fallback behind it. The original is left in place
    for a release; LB-11's migration removes it after the volume is proven.
    """
    legacy = data_dir / LEGACY_KEYS_DIRNAME
    if target.exists() or not legacy.is_dir():
        return
    try:
        shutil.copytree(legacy, target)
        os.chmod(target, stat.S_IRWXU)
        logger.warning(
            "[keyvault] wrapped keys copied out of the data dir to %s (D6' — they "
            "must not live inside the volume they unlock). The original is kept.",
            target,
        )
    except OSError as exc:
        # Not fatal, and deliberately not raising: failing here would make the
        # app unable to read keys it can still perfectly well read in the old
        # place. The next launch tries again.
        logger.error("[keyvault] could not relocate the keys dir: %s", exc)


def _recovery_pub_path() -> Path:
    return _keys_dir() / "recovery.pub"


def device_id() -> str:
    """A stable, non-secret identifier for this machine's key set.

    Generated once and stored beside the wrapped keys. Deliberately not the
    hostname: hostnames change, and `credential_locker`'s old key derivation is a
    standing lesson in what that costs.
    """
    path = _keys_dir() / "device_id"
    try:
        if path.exists():
            existing = path.read_text().strip()
            if existing:
                return existing
    except OSError as exc:
        raise KeyVaultError(f"could not read device id at {path}: {exc}") from exc

    new_id = secrets.token_hex(8)
    _ensure_keys_dir()
    path.write_text(new_id + "\n")
    return new_id


def _ensure_keys_dir() -> Path:
    d = _keys_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, stat.S_IRWXU)  # 700 — owner only
    except OSError as exc:
        logger.warning("[keyvault] could not tighten permissions on %s: %s", d, exc)
    return d


def _validate_purpose(purpose: str) -> None:
    if purpose not in PURPOSES:
        raise KeyVaultError(
            f"unknown purpose {purpose!r}; expected one of {', '.join(PURPOSES)}"
        )


# ── keychain access (permissive ACL, no app identity) ───────────────────────


def _run_security(args: list[str]) -> subprocess.CompletedProcess:
    """Run `security`, giving up after a timeout WITHOUT killing it.

    A timeout means `security` is waiting on a SecurityAgent dialog. Killing a
    client while its dialog is pending makes `securityd` abort (observed twice on
    2026-09-30, `/Library/Logs/DiagnosticReports/securityd-*.ips`), and every
    restart of `securityd` forgets the login keychain was unlocked — so macOS
    services all over the system start asking for the password. So on timeout
    the process is left to finish when the user answers, and we raise.
    """
    try:
        proc = subprocess.Popen(
            ["security", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover — macOS always has it
        raise KeyVaultError("`security` not found; this module is macOS-only") from exc
    try:
        out, err = proc.communicate(timeout=_SECURITY_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        logger.error("[keyvault] `security %s` is waiting on a macOS dialog; leaving it "
                     "running (killing it crashes securityd)", args[0] if args else "")
        raise KeyVaultError(
            "`security` timed out, which means it is showing a prompt. The item's "
            "permissive ACL has been lost — see READFIRST/ArchitectureDocs/KEY_CUSTODY.md."
        ) from exc
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _keychain_read(account: str) -> Optional[bytes]:
    """Return the stored key, or None if no item exists. Raises on a real error."""
    proc = _run_security(
        ["find-generic-password", "-a", account, "-s", service_name(), "-w"]
    )
    if proc.returncode == 0:
        raw = proc.stdout.strip()
        try:
            return base64.b64decode(raw, validate=True)
        except Exception as exc:
            raise KeyVaultError(
                f"keychain item {account!r} is present but unreadable: {exc}"
            ) from exc

    # 44 = errSecItemNotFound. Anything else is a genuine failure, and a failure
    # is NOT the same as "no key yet" — conflating them is how a vault quietly
    # mints a second key and orphans everything the first one encrypted.
    stderr = (proc.stderr or "").strip()
    if proc.returncode == 44 or "could not be found" in stderr:
        return None
    raise KeyVaultError(f"keychain read for {account!r} failed ({proc.returncode}): {stderr}")


def _keychain_write(account: str, key: bytes) -> None:
    # Never UPDATE an existing item: `add-generic-password -U` over an item that
    # already exists raises a SecurityAgent confirmation dialog even with -A,
    # and `security` blocks on it (found by the LB-11 matrix, 2026-09-30). That
    # is exactly the recovery-screen case — restoring the phrase's key over a
    # wrong one — where a hang reads as "recovery is broken". Delete (silent)
    # and add fresh instead. The caller holds the key in memory throughout, so
    # nothing is lost if this is interrupted between the two.
    existing = _keychain_read(account)
    if existing == key:
        return
    if existing is not None:
        _keychain_delete(account)
    encoded = base64.b64encode(key).decode()
    proc = _run_security(
        [
            "add-generic-password",
            "-a", account,
            "-s", service_name(),
            "-w", encoded,
            "-A",   # permissive ACL: no trusted-application list (D20)
            "-D", "LocalBook key",
            "-j", "Managed by LocalBook. Deleting this needs the recovery phrase to undo.",
        ]
    )
    if proc.returncode != 0:
        raise KeyVaultError(
            f"keychain write for {account!r} failed ({proc.returncode}): "
            f"{(proc.stderr or '').strip()}"
        )


def _keychain_delete(account: str) -> bool:
    proc = _run_security(["delete-generic-password", "-a", account, "-s", service_name()])
    return proc.returncode == 0


# ── the public key API ──────────────────────────────────────────────────────


def get_or_create(purpose: str) -> bytes:
    """Return this device's key for `purpose`, creating it on first use.

    Raises `KeyVaultError` rather than returning a new key when an existing one
    cannot be read — including when the Keychain item is gone but a wrapped copy
    exists on disk, which means the key is recoverable and minting a fresh one
    would strand the data. That case routes to the recovery flow.
    """
    _validate_purpose(purpose)

    existing = _keychain_read(purpose)
    if existing is not None:
        if len(existing) != KEY_BYTES:
            raise KeyVaultError(
                f"key for {purpose!r} is {len(existing)} bytes, expected {KEY_BYTES}"
            )
        return existing

    if wrapped_path(purpose).exists():
        raise KeyVaultError(
            f"the Keychain item for {purpose!r} is missing, but a wrapped copy exists "
            f"at {wrapped_path(purpose)}. This key is RECOVERABLE — unwrap it with the "
            f"recovery phrase rather than generating a new one, which would orphan "
            f"everything it protects."
        )

    key = secrets.token_bytes(KEY_BYTES)
    _keychain_write(purpose, key)
    logger.info("[keyvault] created a new %s key for device %s", purpose, device_id())

    # Wrap immediately when a recovery key is configured, so a key is never
    # unrecoverable for a window. When it is not, setup does it later.
    if has_recovery_key():
        try:
            wrap_for_recovery(purpose)
        except KeyVaultError as exc:
            logger.error("[keyvault] created %s but could not wrap it: %s", purpose, exc)
    return key


def delete(purpose: str) -> bool:
    """Remove this device's Keychain item for `purpose`. Used by tests and by the
    'reset this device' path. The wrapped copy on disk is left alone — that is
    what makes the reset recoverable."""
    _validate_purpose(purpose)
    return _keychain_delete(purpose)


# ── recovery phrase ─────────────────────────────────────────────────────────


def generate_recovery_phrase() -> str:
    """A fresh 24-word BIP-39 phrase. Shown to the user exactly once."""
    from mnemonic import Mnemonic

    return Mnemonic("english").generate(strength=256)


def _recovery_private_key(phrase: str) -> X25519PrivateKey:
    from mnemonic import Mnemonic

    normalized = " ".join(phrase.lower().split())
    mnemo = Mnemonic("english")
    if not mnemo.check(normalized):
        raise KeyVaultError("that is not a valid 24-word recovery phrase")

    # BIP-39 seed, then the first 32 bytes clamped into an X25519 scalar. The
    # phrase is the only input, so the same phrase always yields the same key on
    # any machine — which is what makes recovery work from a replacement Mac.
    seed = Mnemonic.to_seed(normalized)
    return X25519PrivateKey.from_private_bytes(seed[:32])


def recovery_public_key_from_phrase(phrase: str) -> bytes:
    priv = _recovery_private_key(phrase)
    return priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def set_recovery_key(phrase: str) -> bytes:
    """Store the PUBLIC half of the recovery key for this install.

    The private half is derived, used to compute the public half, and dropped. It
    is never written anywhere, which is the point of D6'.
    """
    pub = recovery_public_key_from_phrase(phrase)
    _ensure_keys_dir()
    _recovery_pub_path().write_text(base64.b64encode(pub).decode() + "\n")
    logger.info("[keyvault] recovery public key stored for device %s", device_id())
    return pub


def has_recovery_key() -> bool:
    return _recovery_pub_path().exists()


def _load_recovery_public_key() -> X25519PublicKey:
    path = _recovery_pub_path()
    if not path.exists():
        raise KeyVaultError(
            "no recovery key is configured; run the recovery-phrase setup before wrapping"
        )
    try:
        raw = base64.b64decode(path.read_text().strip(), validate=True)
        return X25519PublicKey.from_public_bytes(raw)
    except Exception as exc:
        raise KeyVaultError(f"recovery public key at {path} is unreadable: {exc}") from exc


# ── wrapping / unwrapping ───────────────────────────────────────────────────


def wrapped_path(purpose: str) -> Path:
    _validate_purpose(purpose)
    return _keys_dir() / device_id() / f"{purpose}.wrapped"


def _derive_wrap_key(shared: bytes, ephemeral_pub: bytes, recipient_pub: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_WRAP_INFO + ephemeral_pub + recipient_pub,
    ).derive(shared)


def wrap_for_recovery(purpose: str) -> Path:
    """Write a copy of this device's `purpose` key, encrypted to the recovery key.

    An ephemeral X25519 key pair is generated per wrap, agreed with the recovery
    public key, and run through HKDF into an AES-GCM key. Only the ephemeral
    public half is stored, so the envelope reveals nothing without the phrase.
    """
    _validate_purpose(purpose)
    key = _keychain_read(purpose)
    if key is None:
        raise KeyVaultError(f"no {purpose!r} key on this device to wrap")

    recipient = _load_recovery_public_key()
    recipient_raw = recipient.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )

    ephemeral = X25519PrivateKey.generate()
    ephemeral_pub = ephemeral.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    wrap_key = _derive_wrap_key(ephemeral.exchange(recipient), ephemeral_pub, recipient_raw)

    nonce = secrets.token_bytes(12)
    # The purpose is authenticated, so an envelope cannot be renamed to pass off
    # the backup key as the credentials key.
    ciphertext = AESGCM(wrap_key).encrypt(nonce, key, purpose.encode())

    envelope = {
        "version": _ENVELOPE_VERSION,
        "purpose": purpose,
        "device_id": device_id(),
        "ephemeral_pub": base64.b64encode(ephemeral_pub).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }

    path = wrapped_path(purpose)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, stat.S_IRWXU)
    except OSError:
        pass
    path.write_text(json.dumps(envelope, indent=2) + "\n")
    return path


def unwrap_with_phrase(phrase: str, purpose: str, *, device: Optional[str] = None) -> bytes:
    """Recover a key from its wrapped copy using the recovery phrase.

    `device` names whose key set to recover; it defaults to this machine's. A
    replacement Mac recovers another device's keys by passing its id — the
    envelopes are on disk beside the volume precisely so this works when the
    volume cannot mount.
    """
    _validate_purpose(purpose)

    # Check the phrase before touching the filesystem. A mistyped phrase is the
    # common case by far, and reporting it as a missing file sends the user
    # looking for the wrong problem.
    priv = _recovery_private_key(phrase)

    target = device or device_id()
    path = _keys_dir() / target / f"{purpose}.wrapped"
    if not path.exists():
        raise KeyVaultError(f"no wrapped {purpose!r} key for device {target} at {path}")

    try:
        envelope = json.loads(path.read_text())
    except Exception as exc:
        raise KeyVaultError(f"wrapped key at {path} is not readable: {exc}") from exc

    if envelope.get("version") != _ENVELOPE_VERSION:
        raise KeyVaultError(
            f"wrapped key at {path} is version {envelope.get('version')!r}, "
            f"this build understands {_ENVELOPE_VERSION}"
        )
    if envelope.get("purpose") != purpose:
        raise KeyVaultError(
            f"wrapped key at {path} is for {envelope.get('purpose')!r}, not {purpose!r}"
        )

    recipient_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    try:
        ephemeral_pub = base64.b64decode(envelope["ephemeral_pub"], validate=True)
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
    except Exception as exc:
        raise KeyVaultError(f"wrapped key at {path} is malformed: {exc}") from exc

    wrap_key = _derive_wrap_key(
        priv.exchange(X25519PublicKey.from_public_bytes(ephemeral_pub)),
        ephemeral_pub,
        recipient_raw,
    )
    try:
        return AESGCM(wrap_key).decrypt(nonce, ciphertext, purpose.encode())
    except Exception as exc:
        # The usual cause is a valid phrase that is simply the wrong one. Say so
        # plainly; "decryption failed" sends people looking for a corrupt file.
        raise KeyVaultError(
            "that recovery phrase does not match this device's wrapped key"
        ) from exc


def restore_from_phrase(phrase: str, purpose: str, *, device: Optional[str] = None) -> bytes:
    """Unwrap a key and put it back in this machine's Keychain."""
    key = unwrap_with_phrase(phrase, purpose, device=device)
    _keychain_write(purpose, key)
    logger.info("[keyvault] restored %s from the recovery phrase", purpose)
    return key


def wrap_all() -> Dict[str, object]:
    """Wrap every key this device already holds, for the recovery key on file.

    Run right after a recovery key is configured. Keys created BEFORE setup have
    no wrapped copy, so without this backfill they stay unrecoverable forever
    while the UI happily reports "recovery configured" — which is worse than no
    recovery at all, because it is a false assurance.
    """
    if not has_recovery_key():
        raise KeyVaultError("no recovery key is configured")

    wrapped: list = []
    skipped: Dict[str, str] = {}
    for purpose in PURPOSES:
        try:
            if _keychain_read(purpose) is None:
                skipped[purpose] = "no key on this device"
                continue
            wrap_for_recovery(purpose)
            wrapped.append(purpose)
        except KeyVaultError as exc:
            skipped[purpose] = str(exc)
    return {"wrapped": wrapped, "skipped": skipped}


def unprotected_purposes() -> list:
    """Keys that exist on this device but have NO wrapped copy.

    Anything in this list is lost for good if the Keychain is wiped. Data Health
    reads it; it is the honest answer to "am I actually covered?".
    """
    out = []
    for purpose in PURPOSES:
        try:
            if _keychain_read(purpose) is not None and not wrapped_path(purpose).exists():
                out.append(purpose)
        except KeyVaultError:
            continue
    return out


def status() -> Dict[str, object]:
    """A non-secret summary, for Data Health and the settings screen."""
    unprotected = unprotected_purposes()
    out: Dict[str, object] = {
        "device_id": device_id(),
        "recovery_key_configured": has_recovery_key(),
        # The honest headline. "Recovery configured" alone is not the same as
        # "every key is recoverable" — a key created before setup has no
        # wrapped copy until wrap_all() runs.
        "unprotected_purposes": unprotected,
        "fully_protected": has_recovery_key() and not unprotected,
        "purposes": {},
    }
    for purpose in PURPOSES:
        try:
            present = _keychain_read(purpose) is not None
            error = None
        except KeyVaultError as exc:
            present, error = False, str(exc)
        out["purposes"][purpose] = {  # type: ignore[index]
            "in_keychain": present,
            "wrapped": wrapped_path(purpose).exists(),
            "error": error,
        }
    return out
