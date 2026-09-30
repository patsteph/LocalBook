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


@router.post("/system/volume/migrate")
async def migrate_to_encrypted(req: MigrateRequest):
    """Start preparing an encrypted copy of the data directory, in the background.

    Returns at once; poll `/system/volume/setup` for progress. It takes a backup,
    creates the volume, copies ~690 MB in and verifies every database and every
    file. Nothing live is touched — the plaintext directory is only read, and a
    failure leaves no marker, so an abandoned attempt costs nothing.
    """
    import asyncio

    from services import encryption_setup

    check = await asyncio.to_thread(encryption_setup.preflight)
    blockers = [name for name, c in check["checks"].items() if not c["ok"]
                and not (req.skip_backup and name == "backup_destination")]
    if blockers:
        raise HTTPException(status_code=409,
                            detail=f"not ready to encrypt: {', '.join(blockers)}")
    if not encryption_setup.start_prepare_job(skip_backup=req.skip_backup):
        raise HTTPException(status_code=409, detail="a migration is already running")
    return {"started": True}


@router.get("/system/volume/setup")
async def encryption_setup_state():
    """Everything the encryption setup screen needs, in one poll."""
    import asyncio

    from services import (encryption_migration, encryption_rollback, encryption_setup,
                          volume_gate, volume_service)

    check = await asyncio.to_thread(encryption_setup.preflight)
    return {
        "encryption_enabled": volume_gate.encryption_enabled(),
        "mounted": volume_service.is_mounted(),
        "preflight": check,
        "job": encryption_setup.job_status(),
        "pending": encryption_migration.pending(),
        "last_apply": encryption_migration.last_apply(),
        "plaintext_copies": await asyncio.to_thread(encryption_migration.plaintext_copies),
        "decrypt_pending": encryption_rollback.pending(),
        "last_decrypt": encryption_rollback.last_apply(),
        "leftover_image": await asyncio.to_thread(encryption_rollback.leftover_image),
    }


# ── the startup prompt ──────────────────────────────────────────────────────


class PromptAction(BaseModel):
    action: str        # snooze | dismiss | acknowledge_failure


@router.get("/system/volume/prompt")
async def encryption_prompt():
    """What the app should offer at startup, if anything. Never migrates."""
    import asyncio

    from services import encryption_setup

    return await asyncio.to_thread(encryption_setup.prompt)


@router.post("/system/volume/prompt")
async def respond_to_encryption_prompt(req: PromptAction):
    import asyncio

    from services import encryption_setup

    try:
        return await asyncio.to_thread(encryption_setup.respond_to_prompt, req.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── the escape hatch ────────────────────────────────────────────────────────


class ExportRequest(BaseModel):
    destination: str


@router.post("/system/volume/export")
async def export_decrypted(req: ExportRequest):
    """A decrypted, verified copy in a folder of the user's choosing. Encryption
    stays on. Background job — poll `/system/volume/setup`."""
    from pathlib import Path

    from services import encryption_setup

    if not encryption_setup.start_export_job(Path(req.destination)):
        raise HTTPException(status_code=409, detail="another volume operation is running")
    return {"started": True}


@router.post("/system/volume/decrypt")
async def decrypt_volume():
    """Stage turning encryption off. The switch happens on the next launch, and
    the encrypted image is kept until the user deletes it."""
    from services import encryption_setup

    if not encryption_setup.start_decrypt_job():
        raise HTTPException(status_code=409, detail="another volume operation is running")
    return {"started": True}


@router.delete("/system/volume/decrypt/pending")
async def cancel_decrypt():
    from services import encryption_rollback

    return {"cancelled": encryption_rollback.cancel_pending()}


@router.delete("/system/volume/image")
async def discard_encrypted_image():
    """Delete the encrypted image once encryption is off. Refused otherwise."""
    import asyncio

    from services import encryption_rollback

    result = await asyncio.to_thread(encryption_rollback.discard_image)
    if not result.get("deleted"):
        raise HTTPException(status_code=400, detail=result.get("error", "could not delete"))
    return result


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
async def discard_plaintext(path: str):
    """Delete a kept plaintext copy. The only destructive call in LB-11.

    Refused unless the encrypted volume is currently mounted — deleting it while
    the volume is unavailable turns a recoverable situation into a total loss,
    and that is exactly the moment a frustrated user is most likely to try.
    """
    import asyncio

    from services import encryption_migration

    result = await asyncio.to_thread(encryption_migration.discard_plaintext, path)
    if not result.get("deleted"):
        raise HTTPException(status_code=400, detail=result.get("error", "could not delete"))
    return result
