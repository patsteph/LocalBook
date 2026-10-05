"""Recovery copies of each Mac's keys, held by every paired Mac (K-1 / LB-11).

A wrapped key (`keyvault.wrap_for_recovery`) is sealed to the recovery PUBLIC
key: without the 24-word phrase it is noise. So availability can come from
copies without costing confidentiality. They already sit beside the volume and
inside every backup; this puts each Mac's set in the synced `documents` table
too, so a dead disk plus the phrase plus ANY paired Mac recovers it.

Each Mac keeps its own phrase. A paired Mac stores the other's set verbatim and
can never open it — nothing here unwraps anything.

Restoring ANOTHER Mac's keys is guarded: a key this Mac already holds is kept
unless the caller explicitly replaces it, and the volume password and sync
identity of another Mac never overwrite this Mac's own — doing so would lock
this Mac out of its own volume, or make it impersonate the old one.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from services import keyvault
from services.keyvault import KeyVaultError

logger = logging.getLogger(__name__)

KIND = "key_escrow"
NEVER_REPLACE = ("volume", "device_identity")   # another Mac's copy never wins over ours
PHRASE_CHECK_DAYS = 90
_STAMP = "phrase_checked"


# ── publishing this Mac's set ───────────────────────────────────────────────


def _own_set() -> Dict[str, Any]:
    folder = keyvault._keys_dir() / keyvault.device_id()
    keys = {}
    for f in sorted(folder.glob("*.wrapped")) if folder.is_dir() else []:
        keys[f.stem] = f.read_text()
    return keys


def publish() -> bool:
    """Put this Mac's wrapped set in the synced documents. Never raises.
    Returns True when the document changed."""
    try:
        if not keyvault.has_recovery_key():
            return False
        keys = _own_set()
        if not keys:
            return False
        from storage import documents

        try:
            from services.sync.identity import device_name
            name = device_name()
        except Exception:
            name = None
        body = {"device_id": keyvault.device_id(), "name": name,
                "recovery_pub": keyvault._recovery_pub_path().read_text().strip(), "keys": keys}
        before = documents.get(KIND, keyvault.device_id())
        documents.put(KIND, keyvault.device_id(), body)
        return before != json.loads(json.dumps(body, sort_keys=True, default=str))
    except Exception as exc:
        logger.warning("[key-escrow] not published: %s", exc)
        return False


# ── what can be recovered here ──────────────────────────────────────────────


def _escrowed() -> Dict[str, Dict[str, Any]]:
    try:
        from storage import documents
        return {k: v for k, v in documents.items(KIND) if isinstance(v, dict)}
    except Exception as exc:
        logger.debug("[key-escrow] documents unavailable: %s", exc)
        return {}


def key_sets() -> List[Dict[str, Any]]:
    """Every Mac whose keys could be restored here: from the keys dir (this Mac,
    a restored backup) and from paired Macs' escrow."""
    me = keyvault.device_id()
    out: Dict[str, Dict[str, Any]] = {}
    root = keyvault._keys_dir()
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        purposes = sorted(f.stem for f in d.glob("*.wrapped")) if d.is_dir() else []
        if purposes:
            out[d.name] = {"device_id": d.name, "name": None, "purposes": purposes,
                           "sources": ["this Mac's disk"]}
    for dev, body in _escrowed().items():
        row = out.setdefault(dev, {"device_id": dev, "name": None, "purposes": [], "sources": []})
        row["name"] = row["name"] or body.get("name")
        row["purposes"] = sorted(set(row["purposes"]) | set((body.get("keys") or {}).keys()))
        row["sources"].append("a paired Mac")
    for row in out.values():
        row["this_mac"] = row["device_id"] == me
    return sorted(out.values(), key=lambda r: (not r["this_mac"], r.get("name") or r["device_id"]))


def materialise(device: str) -> List[str]:
    """Write an escrowed set into the keys dir so keyvault can unwrap it.
    Never overwrites a file already there. Returns the purposes written."""
    body = _escrowed().get(device) or {}
    folder = keyvault._keys_dir() / device
    written = []
    for purpose, envelope in (body.get("keys") or {}).items():
        if purpose not in keyvault.PURPOSES:
            continue
        path = folder / f"{purpose}.wrapped"
        if path.exists():
            continue
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(envelope)
        written.append(purpose)
    return written


def restore(phrase: str, device: Optional[str] = None,
            purposes: Optional[Iterable[str]] = None, replace: bool = False) -> Dict[str, Any]:
    """Restore keys from the phrase. For this Mac's own set it is the plain
    restore. For another Mac's set, keys this Mac already holds are kept unless
    `replace` — and the volume password / sync identity are never replaced."""
    me = keyvault.device_id()
    device = device or me
    if device != me:
        materialise(device)
    wanted = list(purposes) if purposes else list(keyvault.PURPOSES)
    restored, kept, failed = [], {}, {}
    for purpose in wanted:
        if device != me:
            have = keyvault._keychain_read(purpose) is not None
            if have and (purpose in NEVER_REPLACE or not replace):
                kept[purpose] = ("this Mac's own key is kept — another Mac's never replaces it"
                                 if purpose in NEVER_REPLACE else
                                 "this Mac already has one — choose Replace to use the other Mac's")
                continue
        try:
            keyvault.restore_from_phrase(phrase, purpose, device=device)
            restored.append(purpose)
        except KeyVaultError as exc:
            failed[purpose] = str(exc)
    return {"restored": restored, "kept": kept, "failed": failed}


# ── "do you still have it?" ─────────────────────────────────────────────────


def mark_phrase_checked() -> None:
    try:
        (keyvault._keys_dir() / _STAMP).write_text(datetime.now(timezone.utc).isoformat() + "\n")
    except OSError as exc:
        logger.warning("[key-escrow] could not stamp the phrase check: %s", exc)


def phrase_check() -> Dict[str, Any]:
    """When the phrase was last proven, and whether it is time to ask again.
    Setting it up counts as proving it."""
    if not keyvault.has_recovery_key():
        return {"configured": False, "due": False, "days": None}
    stamp = keyvault._keys_dir() / _STAMP
    src = stamp if stamp.exists() else keyvault._recovery_pub_path()
    days = int((time.time() - src.stat().st_mtime) / 86400)
    return {"configured": True, "days": days, "due": days >= PHRASE_CHECK_DAYS}
