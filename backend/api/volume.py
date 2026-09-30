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


# ── the migration (LB-11 measure 5) ─────────────────────────────────────────


class MigrateRequest(BaseModel):
    # Only for a machine that genuinely has no destination and accepts the risk.
    # Defaults to taking one, because the backup is the thing standing behind
    # the whole operation.
    skip_backup: bool = False


class DiscardRequest(BaseModel):
    path: str


@router.post("/system/volume/migrate")
async def migrate_to_encrypted(req: MigrateRequest):
    """Prepare an encrypted copy of the data directory and stage the swap.

    Long-running: it takes a backup, creates the volume, copies ~690 MB in and
    verifies every database and every file. Nothing live is touched — the
    plaintext directory is only read, and a failure leaves no marker, so an
    abandoned attempt costs nothing.
    """
    import asyncio

    from services import encryption_migration

    report = await asyncio.to_thread(
        encryption_migration.prepare, skip_backup=req.skip_backup
    )
    out = report.as_dict()
    out["detail"] = (
        "Prepared and verified. Quit and reopen LocalBook to switch over — your "
        "current data is kept, not replaced."
        if report.ok
        else "Not staged. Your data has not been touched."
    )
    return out


@router.get("/system/volume/migrate/pending")
async def pending_migration():
    from services import encryption_migration

    return {"pending": encryption_migration.pending()}


@router.delete("/system/volume/migrate/pending")
async def cancel_migration():
    """Abandon a staged migration. The prepared volume is left in place, unused."""
    from services import encryption_migration

    return {"cancelled": encryption_migration.cancel_pending()}


@router.get("/system/volume/plaintext-copies")
async def list_plaintext_copies():
    """What the migration kept. Shown until the user says it can go."""
    from services import encryption_migration

    return {"copies": encryption_migration.plaintext_copies()}


@router.delete("/system/volume/plaintext-copies")
async def discard_plaintext(req: DiscardRequest):
    """Delete a kept plaintext copy. The only destructive call in LB-11.

    Refused unless the encrypted volume is currently mounted — deleting it while
    the volume is unavailable turns a recoverable situation into a total loss,
    and that is exactly the moment a frustrated user is most likely to try.
    """
    import asyncio

    from services import encryption_migration

    result = await asyncio.to_thread(encryption_migration.discard_plaintext, req.path)
    if not result.get("deleted"):
        raise HTTPException(status_code=400, detail=result.get("error", "could not delete"))
    return result
