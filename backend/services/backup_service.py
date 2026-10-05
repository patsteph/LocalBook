"""One encrypted, verified archive of everything that cannot be rebuilt.

LB-10 item 2 of the v2.5.0 plan. Until this exists there is **no backup or
restore anywhere** in LocalBook, which is the single largest risk in front of
LB-11 (moving the whole data directory onto an encrypted volume) and LB-12
(rewriting ids for sync).

**What goes in is decided by one question: can it be rebuilt?**

  included    the four SQLite databases, `memory/` (core, recall, archival,
              events), `curator_brain/`, the notebook and source JSON, the YAML
              configs, `audio/` and `quizzes/`
  excluded    `lancedb/` — **D19**. It is fully derived from `sources.content`,
              restore rebuilds it with `/reindex/all?drop_tables=true`, and
              leaving it out is what makes a HOT backup possible: no maintenance
              lock, no writer pause, and a much smaller archive.
  excluded    caches, logs, `models/`, `eval_results/`, and the orphaned
              `lancedb_backup_*` directories — all reproducible or disposable.

⚠️ **`memory/archival_memory/` is LanceDB and is NOT excluded.** It is the one
LanceDB table that is not derived from anything else: its text exists nowhere
but there until LB-12d moves it into SQLite. Excluding it with the rest of
LanceDB would silently discard the user's archival memory, which is exactly the
kind of "it looked like a backup" failure this module exists to prevent.

**The SQLite databases are copied with the `sqlite3` backup API**, not `cp`. A
file copy of a live database with a WAL beside it is not a database — it is a
torn page and whatever the WAL happened to hold. The backup API takes a
consistent snapshot while writers continue.

**Two independent ways back in.** The content key is random per archive and is
sealed twice: once to the recovery public key (X25519, so the 24-word phrase
opens it on any machine, including a replacement Mac) and once under this
device's `backup` key from the Keychain. Losing the Keychain costs nothing;
losing the phrase costs nothing; losing both is what the user was warned about.

**The manifest travels inside the ciphertext.** File names alone say a great
deal — `imap:person@example.com` would be sitting in the clear — so only the
format version, the creation time and the sealed keys are outside, and those are
authenticated as AAD so the header cannot be edited without breaking decryption.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

MAGIC = b"LBBK1\n"
FORMAT_VERSION = 1
ARCHIVE_SUFFIX = ".lbbackup"

_WRAP_INFO = b"LocalBook/LB-10/backup-wrap/v1"

# The four databases, relative to the data dir. Copied with the backup API.
SQLITE_DBS = (
    "localbook.db",
    "tabular.db",
    "memory/recall_memory.db",
    "curator_brain/brain.db",
)

# Directory trees that are copied wholesale.
INCLUDED_TREES = (
    "memory",          # core + archival + events; recall_memory.db is replaced below
    "curator_brain",   # brain.db is replaced below
    "notebooks",
    "quizzes",
    "auth",            # encrypted session state — small, and not reproducible
    "LocalBook.keys",  # wrapped key copies; useless without the phrase
)

# Big, and worth their own switch: 500 MB+ of generated audio dominates every
# archive, and it is regenerable from the notebooks it came from.
BLOB_TREES = ("audio", "audio_output")

# Never included. Each is derived, reproducible, or disposable.
EXCLUDED_TREES = (
    "lancedb",              # D19 — derived from sources.content
    "models",               # re-downloadable
    "eval_results",
    "signals",
    "diagnostics",
    "backups",
    "__pycache__",
)
EXCLUDED_PREFIXES = ("lancedb_backup_",)   # orphans from a 2026 one-off
EXCLUDED_SUFFIXES = (".log", ".log.1", ".log.2", "-wal", "-shm", ".tmp")

# Files that are pure cache and would otherwise bloat every archive.
EXCLUDED_FILES = ("answer_cache.json", "rag_metrics.json", ".app_token")


@dataclass
class BackupResult:
    path: Path
    bytes_written: int
    manifest: Dict[str, object]
    seconds: float


# ── selection ───────────────────────────────────────────────────────────────


def _is_excluded(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return True
    top = parts[0]
    if top in EXCLUDED_TREES:
        return True
    if any(top.startswith(p) for p in EXCLUDED_PREFIXES):
        return True
    # `lancedb` nested anywhere (curator_brain/lancedb) is still derived —
    # EXCEPT memory/archival_memory, which is handled by not listing it here.
    if "lancedb" in parts:
        return True
    name = parts[-1]
    if name in EXCLUDED_FILES:
        return True
    if any(name.endswith(s) for s in EXCLUDED_SUFFIXES):
        return True
    return False


def _top_level_files(data_dir: Path) -> List[Path]:
    """Loose JSON and YAML at the root of the data dir — the irreplaceable ones."""
    out = []
    for entry in sorted(data_dir.glob("*")):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in (".json", ".yaml", ".yml", ".txt"):
            continue
        if _is_excluded(entry.relative_to(data_dir)):
            continue
        out.append(entry)
    return out


# ── SQLite snapshots ────────────────────────────────────────────────────────


def snapshot_sqlite(source: Path, target: Path) -> Dict[str, int]:
    """Consistent copy of a live database, plus per-table row counts.

    Uses the `sqlite3` backup API rather than copying the file. A live database
    has a WAL beside it; a file copy captures a torn page and whatever the WAL
    happened to hold at that instant, and the result opens without complaint and
    is wrong. The row counts go in the manifest so a restore can prove it got
    everything rather than merely getting a file.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
            counts: Dict[str, int] = {}
            for (name,) in dst.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall():
                try:
                    counts[name] = dst.execute(
                        f'SELECT COUNT(*) FROM "{name}"'
                    ).fetchone()[0]
                except sqlite3.Error:
                    counts[name] = -1     # unreadable is not the same as empty
            return counts
        finally:
            dst.close()
    finally:
        src.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ── staging ─────────────────────────────────────────────────────────────────


def _copy_if_still_there(source: Path, dest: Path) -> bool:
    """Copy a file, tolerating it vanishing underneath us.

    A backup is a HOT backup (D19 removed the need for a maintenance lock), so
    the enrichment worker, the collector, the folder watcher and audio
    generation are all free to write while it runs. Between `rglob` listing a
    path and `copy2` reading it, that path can be deleted or rotated — a log
    roll, a temp file, a cache eviction — and an unguarded copy propagates
    FileNotFoundError and fails the ENTIRE backup.

    Failing a whole backup because one disposable file moved is the wrong
    trade. Skipped files are RECORDED in the manifest rather than swallowed, so
    a restore can see exactly what was not captured.
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("[backup] skipped %s: %s", source, exc)
        return False


def _stage(data_dir: Path, staging: Path, include_blobs: bool) -> Dict[str, object]:
    """Assemble everything to be archived and describe it."""
    row_counts: Dict[str, Dict[str, int]] = {}
    skipped: List[str] = []

    for rel in SQLITE_DBS:
        source = data_dir / rel
        if not source.exists():
            continue
        row_counts[rel] = snapshot_sqlite(source, staging / rel)

    trees = list(INCLUDED_TREES) + (list(BLOB_TREES) if include_blobs else [])
    for tree in trees:
        source = _tree_source(data_dir, tree)
        if source is None:
            continue
        for entry in source.rglob("*"):
            if not entry.is_file():
                continue
            rel = Path(tree) / entry.relative_to(source)
            if _is_excluded(rel):
                continue
            if str(rel) in SQLITE_DBS:
                continue                  # already snapshotted, do not overwrite
            if not _copy_if_still_there(entry, staging / rel):
                skipped.append(str(rel))

    for entry in _top_level_files(data_dir):
        if not _copy_if_still_there(entry, staging / entry.name):
            skipped.append(entry.name)

    files: Dict[str, Dict[str, object]] = {}
    for entry in sorted(staging.rglob("*")):
        if entry.is_file():
            rel = str(entry.relative_to(staging))
            files[rel] = {"sha256": _sha256(entry), "bytes": entry.stat().st_size}

    return {"files": files, "row_counts": row_counts, "skipped": skipped}


def build_manifest(data_dir: Path, staged: Dict[str, object], include_blobs: bool) -> Dict:
    from services import keyvault

    try:
        from services import migration_ledger

        schema = {
            "version": migration_ledger.schema_version(),
            "ledger_head": migration_ledger.head(),
        }
    except Exception as exc:
        # Recorded, not guessed. A restore has to be able to see that the head
        # was unknown rather than assume it matched.
        schema = {"version": None, "ledger_head": None, "error": str(exc)}

    recovery_pub = None
    try:
        if keyvault.has_recovery_key():
            recovery_pub = keyvault._recovery_pub_path().read_text().strip()
    except Exception:
        pass

    return {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "device_id": keyvault.device_id(),
        "schema": schema,
        "recovery_public_key": recovery_pub,
        "includes_blobs": include_blobs,
        "excluded": {
            "lancedb": "derived from sources.content; rebuilt by /reindex/all (D19)",
            "trees": list(EXCLUDED_TREES),
            "blobs": [] if include_blobs else list(BLOB_TREES),
        },
        "row_counts": staged["row_counts"],
        # Files that moved or vanished between being listed and being copied.
        # Recorded rather than swallowed: a restore has to be able to see what
        # was not captured.
        "skipped": staged.get("skipped", []),
        "files": staged["files"],
    }


# ── sealing ─────────────────────────────────────────────────────────────────


def _seal_to_recovery(content_key: bytes, recovery_pub_b64: str) -> Dict[str, str]:
    import base64

    recipient = X25519PublicKey.from_public_bytes(
        base64.b64decode(recovery_pub_b64, validate=True)
    )
    recipient_raw = recipient.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    ephemeral = X25519PrivateKey.generate()
    ephemeral_pub = ephemeral.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    wrap_key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=_WRAP_INFO + ephemeral_pub + recipient_raw,
    ).derive(ephemeral.exchange(recipient))
    nonce = os.urandom(12)
    return {
        "kind": "recovery",
        "ephemeral_pub": base64.b64encode(ephemeral_pub).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "wrapped": base64.b64encode(
            AESGCM(wrap_key).encrypt(nonce, content_key, b"recovery")
        ).decode(),
    }


def _seal_to_device(content_key: bytes) -> Dict[str, str]:
    import base64

    from services import keyvault

    device_key = keyvault.get_or_create("backup")
    nonce = os.urandom(12)
    return {
        "kind": "device",
        "device_id": keyvault.device_id(),
        "nonce": base64.b64encode(nonce).decode(),
        "wrapped": base64.b64encode(
            AESGCM(device_key).encrypt(nonce, content_key, b"device")
        ).decode(),
    }


def _write_archive(tar_bytes: bytes, destination: Path, manifest: Dict) -> int:
    import base64

    from services import keyvault

    content_key = os.urandom(32)
    recipients: List[Dict[str, str]] = []

    recovery_pub = manifest.get("recovery_public_key")
    if recovery_pub:
        recipients.append(_seal_to_recovery(content_key, recovery_pub))
    recipients.append(_seal_to_device(content_key))

    if not any(r["kind"] == "recovery" for r in recipients):
        # Not fatal, but the archive is then only openable by THIS Mac's
        # Keychain — which is precisely the failure the recovery phrase exists
        # to prevent. Say so loudly rather than writing a false assurance.
        logger.warning(
            "[backup] no recovery key configured — this archive can only be "
            "opened by this Mac's Keychain. Set up a recovery phrase."
        )

    header = {
        "format_version": FORMAT_VERSION,
        "created_at": manifest["created_at"],
        "device_id": manifest["device_id"],
        "recipients": recipients,
    }
    header_bytes = json.dumps(header, sort_keys=True).encode()

    nonce = os.urandom(12)
    # The header is AAD, so editing it — swapping recipients, back-dating the
    # archive — breaks decryption instead of going unnoticed.
    ciphertext = AESGCM(content_key).encrypt(nonce, tar_bytes, header_bytes)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as fh:
        fh.write(MAGIC)
        fh.write(len(header_bytes).to_bytes(4, "big"))
        fh.write(header_bytes)
        fh.write(nonce)
        fh.write(ciphertext)
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    return destination.stat().st_size


# ── the entry point ─────────────────────────────────────────────────────────


def _tree_source(data_dir: Path, tree: str) -> Optional[Path]:
    """Where a backed-up tree actually lives on this Mac.

    The wrapped keys moved OUT of the data dir in LB-11 (`keyvault._keys_dir`, beside
    it — they must not be sealed in the volume they unlock), so reading
    `data_dir/LocalBook.keys` backed up a stale legacy copy, or nothing. Archived
    under the same name either way; restore hands them back to keyvault.
    """
    if tree == "LocalBook.keys":
        try:
            from services import keyvault
            current = keyvault._keys_dir()
            if current.is_dir():
                return current
        except Exception as exc:
            logger.warning("[backup] keys dir unavailable, using the legacy copy: %s", exc)
    source = data_dir / tree
    return source if source.is_dir() else None


def create_backup(
    destination_dir: Path,
    *,
    data_dir: Optional[Path] = None,
    include_blobs: bool = True,
) -> BackupResult:
    """Write one encrypted, verified archive into `destination_dir`.

    The destination must be OUTSIDE the data directory — an archive stored
    inside what it is backing up is not a backup, and once LB-11 lands it would
    also be inside the encrypted volume it is supposed to survive.
    """
    started = time.perf_counter()
    if data_dir is None:
        from config import settings

        data_dir = Path(settings.data_dir)
    data_dir = Path(data_dir)
    destination_dir = Path(destination_dir)

    resolved_dest = destination_dir.expanduser().resolve()
    resolved_data = data_dir.expanduser().resolve()
    if resolved_dest == resolved_data or resolved_data in resolved_dest.parents:
        raise ValueError(
            "the backup destination must be outside the data directory — "
            "an archive stored inside what it backs up is not a backup"
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = resolved_dest / f"localbook-{stamp}{ARCHIVE_SUFFIX}"

    with tempfile.TemporaryDirectory(prefix="lb-backup-") as tmp:
        staging = Path(tmp) / "data"
        staging.mkdir(parents=True)

        staged = _stage(data_dir, staging, include_blobs)
        manifest = build_manifest(data_dir, staged, include_blobs)
        (staging / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))

        tar_path = Path(tmp) / "payload.tar"
        with tarfile.open(tar_path, "w") as tar:
            tar.add(staging, arcname="data")
        tar_bytes = tar_path.read_bytes()

        written = _write_archive(tar_bytes, destination, manifest)

    elapsed = time.perf_counter() - started
    logger.info(
        "[backup] wrote %s (%.1f MB, %d files) in %.1fs",
        destination.name, written / 1024 ** 2, len(staged["files"]), elapsed,
    )
    return BackupResult(
        path=destination, bytes_written=written, manifest=manifest, seconds=elapsed
    )


def read_header(archive: Path) -> Dict:
    """The public header, without decrypting anything.

    Enough to answer "can this machine open it, and when was it made?" without
    revealing a single file name.
    """
    with Path(archive).open("rb") as fh:
        magic = fh.read(len(MAGIC))
        if magic != MAGIC:
            raise ValueError("not a LocalBook backup archive")
        length = int.from_bytes(fh.read(4), "big")
        if length <= 0 or length > 1024 * 1024:
            raise ValueError("backup header is malformed")
        return json.loads(fh.read(length).decode())


# ── opening ─────────────────────────────────────────────────────────────────


def _unseal_recovery(recipient: Dict[str, str], phrase: str) -> bytes:
    import base64

    from services import keyvault

    priv = keyvault._recovery_private_key(phrase)
    recipient_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    ephemeral_pub = base64.b64decode(recipient["ephemeral_pub"], validate=True)
    wrap_key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=_WRAP_INFO + ephemeral_pub + recipient_raw,
    ).derive(priv.exchange(X25519PublicKey.from_public_bytes(ephemeral_pub)))
    return AESGCM(wrap_key).decrypt(
        base64.b64decode(recipient["nonce"], validate=True),
        base64.b64decode(recipient["wrapped"], validate=True),
        b"recovery",
    )


def _unseal_device(recipient: Dict[str, str]) -> bytes:
    import base64

    from services import keyvault

    device_key = keyvault.get_or_create("backup")
    return AESGCM(device_key).decrypt(
        base64.b64decode(recipient["nonce"], validate=True),
        base64.b64decode(recipient["wrapped"], validate=True),
        b"device",
    )


def open_archive(archive: Path, *, phrase: Optional[str] = None) -> Dict[str, object]:
    """Decrypt an archive and return its manifest and payload.

    Tries this device's Keychain first and the recovery phrase second, because
    the common case is restoring onto the machine that made it. Either path
    alone is sufficient — that is the whole point of sealing it twice.
    """
    archive = Path(archive)
    raw = archive.read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError("not a LocalBook backup archive")

    offset = len(MAGIC)
    header_len = int.from_bytes(raw[offset:offset + 4], "big")
    offset += 4
    header_bytes = raw[offset:offset + header_len]
    offset += header_len
    nonce = raw[offset:offset + 12]
    ciphertext = raw[offset + 12:]

    header = json.loads(header_bytes.decode())

    content_key = None
    attempts: List[str] = []
    for recipient in header.get("recipients", []):
        try:
            if recipient["kind"] == "device":
                content_key = _unseal_device(recipient)
            elif recipient["kind"] == "recovery" and phrase:
                content_key = _unseal_recovery(recipient, phrase)
            else:
                continue
            break
        except Exception as exc:
            attempts.append(f"{recipient.get('kind')}: {type(exc).__name__}")

    if content_key is None:
        raise ValueError(
            "could not unlock this archive with this Mac's key"
            + (" or that recovery phrase" if phrase else "; try the recovery phrase")
            + (f" ({'; '.join(attempts)})" if attempts else "")
        )

    # The header is the AAD, so a tampered header fails here rather than
    # quietly producing a plausible archive.
    payload = AESGCM(content_key).decrypt(nonce, ciphertext, header_bytes)

    with tempfile.TemporaryDirectory(prefix="lb-open-") as tmp:
        tar_path = Path(tmp) / "payload.tar"
        tar_path.write_bytes(payload)
        with tarfile.open(tar_path, "r") as tar:
            member = tar.extractfile("data/MANIFEST.json")
            if member is None:
                raise ValueError("archive has no manifest")
            manifest = json.loads(member.read().decode())

    return {"header": header, "manifest": manifest, "payload": payload}


def verify_archive(archive: Path, *, phrase: Optional[str] = None) -> Dict[str, object]:
    """Open an archive and check every file against its recorded hash.

    This is what the nightly drill runs. "The file exists and is the right size"
    is not verification — a truncated or silently corrupted archive passes that
    and fails when it is finally needed.
    """
    opened = open_archive(archive, phrase=phrase)
    manifest = opened["manifest"]
    expected = manifest.get("files", {})

    mismatched: List[str] = []
    missing: List[str] = []
    checked = 0

    with tempfile.TemporaryDirectory(prefix="lb-verify-") as tmp:
        tar_path = Path(tmp) / "payload.tar"
        tar_path.write_bytes(opened["payload"])
        extracted = Path(tmp) / "out"
        with tarfile.open(tar_path, "r") as tar:
            tar.extractall(extracted, filter="data")
        root = extracted / "data"

        for rel, meta in expected.items():
            if rel == "MANIFEST.json":
                continue
            path = root / rel
            if not path.is_file():
                missing.append(rel)
                continue
            checked += 1
            if _sha256(path) != meta.get("sha256"):
                mismatched.append(rel)

    return {
        "ok": not mismatched and not missing,
        "checked": checked,
        "missing": missing,
        "mismatched": mismatched,
        "manifest": manifest,
    }


# ── retention (LB-10 item 3) ────────────────────────────────────────────────

# How many archives to keep. Two, by decision (2026-09-29): at ~550 MB each,
# eleven archives was ~6 GB for a corpus that grows slowly, and the machine is
# not short of copies — three Macs hold the data once LB-12 lands.
#
# ⚠️ **Retention depth IS the "how long until you notice" window.** With two
# archives on a daily cadence, a corruption that goes unnoticed for three days
# is in both of them. That is the cost, it is accepted, and it is the reason
# this is a setting rather than a constant.
DEFAULT_KEEP = 2

# The tiered 7-daily/4-weekly scheme is gone with it. Weekly thinning is
# meaningless below about five archives — there is nothing to thin.


def keep_count() -> int:
    """How many archives to retain, read per call so a change takes effect at
    the next prune rather than the next restart."""
    try:
        from config import settings

        return max(1, int(getattr(settings, "backup_keep", DEFAULT_KEEP)))
    except Exception:
        return DEFAULT_KEEP


def prune(destination_dir: Path, *, keep: Optional[int] = None) -> Dict[str, object]:
    """Keep the newest `keep` archives; delete the rest.

    Runs only AFTER a successful backup, never before. Pruning first would mean
    a failed backup costs an old archive too — the exact moment you can least
    afford to lose one.

    Never deletes the last archive, whatever `keep` says: `keep_count()` floors
    at 1, so a mistyped 0 cannot leave the user with no backups at all.

    An archive whose header cannot be read is KEPT, not deleted. It may be
    corrupt, but "I could not understand this file" is not grounds for removing
    the only copy of something; a corrupt archive is still evidence, and the
    drill will say so.
    """
    keep = keep_count() if keep is None else max(1, int(keep))
    destination_dir = Path(destination_dir)
    if not destination_dir.is_dir():
        return {"kept": [], "deleted": [], "unreadable": []}

    dated: List = []
    unreadable: List[str] = []
    for path in destination_dir.glob(f"*{ARCHIVE_SUFFIX}"):
        try:
            created = read_header(path).get("created_at")
            stamp = datetime.fromisoformat(str(created))
        except Exception:
            unreadable.append(path.name)
            continue
        dated.append((stamp, path))

    dated.sort(key=lambda pair: pair[0], reverse=True)

    keeping: set = {path for _, path in dated[:keep]}

    deleted: List[str] = []
    for _, path in dated:
        if path in keeping:
            continue
        try:
            path.unlink()
            deleted.append(path.name)
        except OSError as exc:
            logger.warning("[backup] could not prune %s: %s", path.name, exc)

    return {
        "keep": keep,
        "kept": sorted(p.name for p in keeping),
        "deleted": sorted(deleted),
        "unreadable": sorted(unreadable),
    }
