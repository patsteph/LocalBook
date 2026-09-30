"""Whether the app may serve at all, and what to say when it may not.

LB-11 measure 1 — fail closed. This is the difference between encryption being a
safety feature and encryption being a way to lose everything quietly.

**The failure it exists to prevent.** The data directory is a mount point. If the
volume does not attach — wrong key, image missing, an OS upgrade that changed
something — then the mount point is an ordinary empty directory. Nothing above
that layer can tell the difference between "the volume failed to mount" and
"this is a brand-new install", so LocalBook would:

  1. come up completely blank, and
  2. start writing a fresh corpus into the directory,

while the real data sat perfectly safe and perfectly unreachable in an image
nobody was looking at. The second step is the unrecoverable one: it puts a new
`localbook.db` where the mount belongs, so the next successful attach finds the
mount point non-empty and refuses.

So when encryption is on and the volume is not mounted, the app enters **LOCKED**:
it boots far enough to serve a recovery screen and nothing else. Every data route
returns 503 with an explanation. No store is opened, no directory is created, no
migration runs.

**Locked is not an error state, it is a holding state.** The user's data is fine.
The wording everywhere says so, because the natural reading of "LocalBook cannot
open your data" is "LocalBook has lost my data", and that is not what happened.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class GateState(str, Enum):
    OPEN = "open"          # encryption off, or on and mounted — serve normally
    LOCKED = "locked"      # encryption on, volume not available — recovery only
    UNKNOWN = "unknown"    # the check itself failed


# Paths that keep working while LOCKED. Everything else is refused.
#
# Deliberately tight: the recovery screen needs the frontend, the health probes,
# the app token and the volume endpoints, and nothing else. A generous allowlist
# is how a data route slips through and writes into an unmounted mount point.
UNLOCKED_PREFIXES = (
    "/health",
    "/system/volume",
    "/keyvault",          # recovery phrase → restore the volume key
    "/auth/",
    "/updates/startup-status",
    "/favicon.ico",
    "/assets/",           # the bundled frontend
)
UNLOCKED_EXACT = ("/", "/docs", "/openapi.json")


@dataclass
class Gate:
    state: GateState
    reason: Optional[str] = None
    detail: Optional[str] = None
    volume: Optional[Dict[str, object]] = None

    @property
    def locked(self) -> bool:
        return self.state is GateState.LOCKED

    def as_dict(self) -> Dict[str, object]:
        return {
            "state": self.state.value,
            "locked": self.locked,
            "reason": self.reason,
            "detail": self.detail,
            "volume": self.volume,
        }


# The single source of truth for the running process. Set once at boot, before
# anything opens a database, and updated by a successful unlock.
_gate = Gate(state=GateState.OPEN)


def current() -> Gate:
    return _gate


def encryption_enabled() -> bool:
    """Per-machine flag (D11). Off means none of this applies.

    Never synced: the plan rolls encryption out one Mac at a time, and a synced
    flag would switch it on for a machine that has no volume.
    """
    try:
        from config import encryption_flag_path, settings

        if bool(getattr(settings, "encryption_enabled", False)):
            return True
        # Read live, not only at import: the flag file sits outside the volume
        # precisely so it is still visible when the volume is not.
        if encryption_flag_path(settings.data_dir).exists():
            return True
        return _image_beside_empty_data_dir(Path(settings.data_dir))
    except Exception:
        return False


def _image_beside_empty_data_dir(data_dir: Path) -> bool:
    """The flag was lost, but the evidence was not.

    An encrypted image next to a missing or empty data dir means "the volume is
    not mounted", never "new install". Without this, losing one flag file would
    reopen exactly the hole measure 1 closed. An image beside a POPULATED data
    dir is an abandoned prepare — the plaintext is live — and stays OPEN.
    """
    try:
        from services import volume_service

        if not volume_service.image_path().exists():
            return False
        return not data_dir.is_dir() or not any(data_dir.iterdir())
    except Exception:
        return False


def evaluate() -> Gate:
    """Decide whether to serve. Called once, at boot, before any store opens."""
    global _gate

    if not encryption_enabled():
        _gate = Gate(state=GateState.OPEN, reason="encryption is not enabled on this Mac")
        return _gate

    try:
        from services import volume_service

        state = volume_service.state()
        if state.mounted:
            _gate = Gate(state=GateState.OPEN, reason="volume mounted",
                         volume=state.as_dict())
            return _gate

        if not state.exists:
            detail = (
                "The encrypted volume is missing. Your notebooks are not lost — "
                "restore them from a backup, or recover the volume with your "
                "24-word phrase."
            )
            reason = "the encrypted volume is missing"
        elif not volume_service.passphrase_exists():
            detail = (
                "This Mac cannot find the key for your encrypted volume. Your "
                "notebooks are safe inside it — unlock it with your 24-word "
                "recovery phrase."
            )
            reason = "the volume key is not in this Mac's Keychain"
        else:
            detail = (
                "The encrypted volume did not unlock. Your notebooks are still "
                "inside it. Try unlocking again, or use your recovery phrase."
            )
            reason = "the volume is present but not mounted"

        _gate = Gate(state=GateState.LOCKED, reason=reason, detail=detail,
                     volume=state.as_dict())
        logger.error("[volume-gate] LOCKED — %s", reason)
        return _gate

    except Exception as exc:
        # An unknown state is treated as LOCKED, never as OPEN. Guessing "open"
        # here is the one mistake that lets the app write into an unmounted
        # mount point, which is the unrecoverable outcome.
        _gate = Gate(
            state=GateState.LOCKED,
            reason=f"could not determine the volume state: {exc}",
            detail=(
                "LocalBook could not check its encrypted volume, so it has not "
                "opened anything. Your notebooks have not been touched."
            ),
        )
        logger.error("[volume-gate] LOCKED — check failed: %s", exc)
        return _gate


def unlock() -> Gate:
    """Try to mount and, if it works, open the gate.

    Used by the recovery screen. Idempotent, like `attach` itself.
    """
    global _gate

    from services import volume_service

    try:
        state = volume_service.attach()
    except Exception as exc:
        _gate = Gate(state=GateState.LOCKED, reason=str(exc),
                     detail=_gate.detail, volume=_gate.volume)
        return _gate

    _gate = Gate(state=GateState.OPEN, reason="unlocked", volume=state.as_dict())
    ensure_subdirs()
    logger.warning("[volume-gate] unlocked — a restart is needed to open the stores")
    return _gate


def ensure_subdirs() -> None:
    """Create the directories inside the volume that config.py no longer makes.

    `config.py` used to `mkdir` the data dir at import. That is exactly what made
    a failed mount indistinguishable from a new install, so it now only does so
    when encryption is off. Once the volume IS mounted, the subdirectories still
    have to exist — that happens here, after the mount, not before it.
    """
    try:
        from config import settings

        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        Path(settings.db_path).mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("[volume-gate] could not create data subdirectories: %s", exc)


def path_is_allowed(path: str) -> bool:
    """Whether a request may proceed while LOCKED."""
    if path in UNLOCKED_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in UNLOCKED_PREFIXES)


class LockedGateMiddleware:
    """Pure-ASGI refusal for everything but the recovery surface.

    ASGI rather than BaseHTTPMiddleware so it sits outside every other
    middleware and cannot be bypassed by one, and so it adds nothing to the
    request path once the gate is open.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _gate.locked:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path_is_allowed(path):
            await self.app(scope, receive, send)
            return

        import json

        body = json.dumps({
            "detail": _gate.detail or "LocalBook's encrypted volume is not open.",
            "locked": True,
            "reason": _gate.reason,
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                # So a client can tell this apart from an ordinary outage.
                (b"x-localbook-locked", b"1"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
