"""The encrypted volume, and the recovery surface when it will not open.

LB-11. These routes stay reachable while the app is LOCKED — they are how the
user gets back in, so they are on `volume_gate.UNLOCKED_PREFIXES`.

Everything here is worded on one principle: **locked is a holding state, not a
loss.** The natural reading of "LocalBook cannot open your data" is "LocalBook
has lost my data", and that is not what has happened. The data is intact inside
an image that did not mount.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)


class PhraseRequest(BaseModel):
    phrase: str


@router.get("/system/volume")
async def volume_status():
    """Everything the recovery screen needs, and what Data Health shows."""
    from services import volume_gate

    out = {"gate": volume_gate.current().as_dict(),
           "encryption_enabled": volume_gate.encryption_enabled()}
    try:
        from services import volume_service

        out["volume"] = volume_service.state().as_dict()
        out["has_key"] = volume_service.passphrase_exists()
    except Exception as exc:
        out["volume"] = {"error": str(exc)}
    return out


@router.post("/system/volume/unlock")
async def unlock_volume():
    """Try to mount with the key already in this Mac's Keychain.

    The ordinary case: the volume simply was not mounted yet. A restart is still
    needed afterwards, because the stores were never opened — and saying so is
    better than appearing to work and then failing on the first query.
    """
    import asyncio

    from services import volume_gate

    gate = await asyncio.to_thread(volume_gate.unlock)
    return {
        "unlocked": not gate.locked,
        "gate": gate.as_dict(),
        "restart_required": not gate.locked,
        "detail": (
            "Unlocked. Quit and reopen LocalBook to finish — nothing was opened "
            "while it was locked."
            if not gate.locked
            else gate.reason
        ),
    }


@router.post("/system/volume/recover")
async def recover_volume(req: PhraseRequest):
    """Restore the volume key from the 24-word phrase, then mount.

    For a wiped Keychain or a replacement Mac. The phrase never leaves this
    process and is not logged; it is used to unwrap the copy stored beside the
    image and put the key back where `hdiutil` can find it.
    """
    import asyncio

    from services import keyvault, volume_gate

    def _recover():
        keyvault.restore_from_phrase(req.phrase, "volume")
        return volume_gate.unlock()

    try:
        gate = await asyncio.to_thread(_recover)
    except keyvault.KeyVaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"could not recover: {exc}")

    return {
        "unlocked": not gate.locked,
        "gate": gate.as_dict(),
        "restart_required": not gate.locked,
    }


@router.post("/system/volume/create")
async def create_volume():
    """Make a new empty encrypted volume and mount it.

    Deliberately does NOT migrate anything: producing a working empty volume and
    moving the user's corpus into it are separate operations, and the second one
    takes a backup first.
    """
    import asyncio

    from services import volume_service

    try:
        state = await asyncio.to_thread(volume_service.initialise_new_volume)
    except volume_service.VolumeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "volume": state.as_dict()}


@router.post("/system/volume/compact")
async def compact_volume():
    """Reclaim space from deleted bands. Needs the volume detached."""
    import asyncio

    from services import volume_service

    return await asyncio.to_thread(volume_service.compact)
