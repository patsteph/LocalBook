"""Per-companion API keys: one key each, hashed at rest, scoped, revocable.

LB-0 of the v2.5.0 plan. Split out of `services/companions.py` rather than added
to it — that file is already ~1500 lines, and key custody is a distinct
responsibility from installing and configuring a tool.

What this replaces. There used to be ONE key, in one file, in plaintext, shared
by every companion. Revoking it disconnected all of them, any companion could
use any endpoint, and the file on disk was the key itself. With a second
companion arriving (Jocasta) and MCP about to expose notebooks, memory and the
web, "one key, all doors" stopped being acceptable.

Three properties this file exists to hold:

  * **Hashed at rest.** The store keeps `sha256(salt + token)`. A key is shown
    to the user exactly once, when it is issued. Nothing can read it back —
    not this module, not a leaked backup of the data dir.
  * **Scoped.** `llm`, `mcp`, `audio`, `memory`, `events`. The meeting recorder
    needs `llm` and nothing else; handing it `mcp` would hand it every notebook.
  * **Independently revocable.** Revoking Jocasta must leave the recorder
    working, which the old single key could not do.

Per-machine, never synced (LB-12h). A key authorises a companion on *this* Mac;
the same tool on another Mac gets its own.

Reissuing rotates: `issue()` for a companion that already has a key replaces it,
because the only honest way to hand back a key we cannot read is to make a new
one. That is why `desired_config` in `companions.py` does NOT issue — a status
poll must never rotate a working key. Only `write_config` does.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STORE_FILE = "companion_keys.json"
LEGACY_KEY_FILE = "companion_key"
STORE_VERSION = 1

KEY_PREFIX = "lb-"
_TOKEN_BYTES = 32

# The full set. A scope not listed here is refused at issue time, so a typo in a
# manifest cannot silently grant nothing (or, worse, be treated as a wildcard).
SCOPES = ("llm", "mcp", "audio", "memory", "events")

# What the legacy single key becomes. It was only ever used against /v1, so
# widening it during migration would grant the recorder access it never had.
LEGACY_COMPANION_ID = "meeting-notes"
LEGACY_SCOPES = ("llm",)


@dataclass
class CompanionIdentity:
    """Who is calling, and what they are allowed to do."""

    companion_id: str
    scopes: tuple = field(default_factory=tuple)

    def has(self, scope: str) -> bool:
        return scope in self.scopes


# ── store plumbing ──────────────────────────────────────────────────────────


def _store_path() -> Path:
    """Resolved per call, never captured at import: `backend/.venv` points
    `settings.data_dir` at the real production data dir, so a module-level
    capture would make every test write there."""
    from config import settings

    return Path(settings.data_dir) / STORE_FILE


def _legacy_path() -> Path:
    from config import settings

    return Path(settings.data_dir) / LEGACY_KEY_FILE


def _load() -> Dict[str, Any]:
    p = _store_path()
    if not p.exists():
        return {"version": STORE_VERSION, "companions": {}}
    try:
        data = json.loads(p.read_text())
        if not isinstance(data.get("companions"), dict):
            raise ValueError("no companions map")
        return data
    except Exception as exc:
        # Deliberately loud and non-destructive. Returning an empty store would
        # silently revoke every companion; leaving the file alone means the user
        # can see what happened and a bad write can be recovered.
        logger.error("[companion-keys] store at %s is unreadable: %s", p, exc)
        raise RuntimeError(f"companion key store is unreadable: {exc}") from exc


def _save(data: Dict[str, Any]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(data, indent=2).encode("utf-8"))
    finally:
        os.close(fd)
    tmp.replace(p)
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 600
    except OSError:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(salt: str, token: str) -> str:
    return hashlib.sha256((salt + token).encode("utf-8")).hexdigest()


# ── issuing, verifying, revoking ────────────────────────────────────────────


def issue(companion_id: str, scopes: Optional[List[str]] = None) -> str:
    """Mint a key for `companion_id` and return it ONCE, in plaintext.

    Replaces any existing key for that companion — reissuing is a rotation, and
    the caller is responsible for writing the new value where the companion will
    read it.
    """
    if not companion_id or not str(companion_id).strip():
        raise ValueError("companion_id is required")

    wanted = tuple(scopes) if scopes else LEGACY_SCOPES
    unknown = [s for s in wanted if s not in SCOPES]
    if unknown:
        raise ValueError(
            f"unknown scope(s) {unknown}; expected some of {', '.join(SCOPES)}"
        )

    token = KEY_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    salt = secrets.token_hex(16)

    data = _load()
    existing = data["companions"].get(companion_id) or {}
    data["companions"][companion_id] = {
        "salt": salt,
        "key_hash": _hash(salt, token),
        "scopes": list(wanted),
        "created_at": _now(),
        "last_used_at": None,
        # Kept across a rotation so "has this ever been used?" survives.
        "first_issued_at": existing.get("first_issued_at") or _now(),
    }
    _save(data)
    logger.info(
        "[companion-keys] issued a key for %s with scopes %s",
        companion_id,
        ",".join(wanted),
    )
    return token


def verify(token: str) -> Optional[CompanionIdentity]:
    """Return who holds this key, or None.

    Compared with `hmac.compare_digest` against every record, with no early exit
    on a miss, so the time taken does not reveal which companions exist.
    """
    if not token or not isinstance(token, str):
        return None

    migrate_legacy_key()

    try:
        data = _load()
    except RuntimeError:
        return None  # unreadable store denies access; it does not grant it

    matched: Optional[str] = None
    for companion_id, rec in data["companions"].items():
        salt = rec.get("salt") or ""
        stored = rec.get("key_hash") or ""
        if not stored:
            continue
        if hmac.compare_digest(stored, _hash(salt, token)) and matched is None:
            matched = companion_id

    if matched is None:
        return None

    rec = data["companions"][matched]
    rec["last_used_at"] = _now()
    try:
        _save(data)
    except Exception as exc:
        # A failed last-used write must not fail the request it is describing.
        logger.warning("[companion-keys] could not record last_used_at: %s", exc)

    return CompanionIdentity(companion_id=matched, scopes=tuple(rec.get("scopes") or ()))


def revoke(companion_id: str) -> bool:
    """Drop one companion's key. Every other companion keeps working."""
    data = _load()
    if companion_id not in data["companions"]:
        return False
    del data["companions"][companion_id]
    _save(data)
    logger.info("[companion-keys] revoked %s", companion_id)
    return True


def revoke_all() -> int:
    data = _load()
    n = len(data["companions"])
    data["companions"] = {}
    _save(data)
    try:
        _legacy_path().unlink(missing_ok=True)
    except OSError:
        pass
    logger.info("[companion-keys] revoked all (%d)", n)
    return n


def list_keys() -> List[Dict[str, Any]]:
    """Everything the Settings screen needs, and nothing secret."""
    try:
        data = _load()
    except RuntimeError:
        return []
    return [
        {
            "companion_id": cid,
            "scopes": rec.get("scopes") or [],
            "created_at": rec.get("created_at"),
            "last_used_at": rec.get("last_used_at"),
        }
        for cid, rec in sorted(data["companions"].items())
    ]


def has_key(companion_id: str) -> bool:
    try:
        return companion_id in _load()["companions"]
    except RuntimeError:
        return False


# ── legacy migration ────────────────────────────────────────────────────────


def migrate_legacy_key() -> bool:
    """Adopt the old shared plaintext key as `meeting-notes` with scope `llm`.

    The existing key is HASHED IN PLACE rather than replaced: the meeting
    recorder already has that value written into its own config file, and
    issuing a new one would disconnect a working tool at upgrade time for no
    reason. The plaintext file is removed once the hash is stored.
    """
    legacy = _legacy_path()
    if not legacy.exists():
        return False

    try:
        token = legacy.read_text().strip()
    except OSError as exc:
        logger.warning("[companion-keys] could not read the legacy key: %s", exc)
        return False

    if not token:
        legacy.unlink(missing_ok=True)
        return False

    data = _load()
    if LEGACY_COMPANION_ID in data["companions"]:
        # Already migrated, and something re-created the file. The store wins.
        legacy.unlink(missing_ok=True)
        return False

    salt = secrets.token_hex(16)
    data["companions"][LEGACY_COMPANION_ID] = {
        "salt": salt,
        "key_hash": _hash(salt, token),
        "scopes": list(LEGACY_SCOPES),
        "created_at": _now(),
        "last_used_at": None,
        "first_issued_at": _now(),
        "migrated_from_legacy": True,
    }
    _save(data)
    legacy.unlink(missing_ok=True)
    logger.info(
        "[companion-keys] migrated the legacy shared key to %s with scope %s; "
        "the plaintext file is gone and the tool keeps working",
        LEGACY_COMPANION_ID,
        ",".join(LEGACY_SCOPES),
    )
    return True
