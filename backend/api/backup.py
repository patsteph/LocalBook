"""Backup endpoints (LB-10 items 2-3).

Thin over `services/backup_service`. The one piece of judgement here is the
destination: it must be somewhere OUTSIDE the data directory — iCloud Drive, an
external disk, a NAS — because an archive stored inside what it backs up is not
a backup, and once LB-11 lands it would also be inside the encrypted volume it
is supposed to survive.

Time Machine is a bonus, never the backup (plan §5 LB-10 item 3).
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)

# 7 daily and 4 weekly, per the plan. Retention runs after a successful write,
# never before — pruning first would mean a failed backup costs an old one too.
KEEP_DAILY = 7
KEEP_WEEKLY = 4


class BackupRequest(BaseModel):
    destination: str
    include_blobs: bool = True


class VerifyRequest(BaseModel):
    archive: str
    phrase: Optional[str] = None


@router.post("/backup")
async def create_backup(req: BackupRequest):
    """Write one encrypted, verified archive.

    Runs off the event loop: it snapshots four databases, hashes every file and
    encrypts the lot, which on a real data directory is seconds to minutes.
    """
    import asyncio

    from services import backup_service

    destination = Path(req.destination).expanduser()
    if not destination.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"{destination} is not a folder. Choose one outside LocalBook's "
                   f"data directory — iCloud Drive, an external disk or a NAS.",
        )

    try:
        result = await asyncio.to_thread(
            backup_service.create_backup,
            destination,
            include_blobs=req.include_blobs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("[backup] failed")
        raise HTTPException(status_code=500, detail=f"backup failed: {exc}")

    manifest = result.manifest
    return {
        "ok": True,
        "path": str(result.path),
        "bytes": result.bytes_written,
        "seconds": round(result.seconds, 2),
        "file_count": len(manifest.get("files", {})),
        "schema": manifest.get("schema"),
        # Surfaced because an archive only this Mac can open is a materially
        # weaker promise than the UI otherwise implies.
        "recoverable_with_phrase": bool(manifest.get("recovery_public_key")),
    }


@router.post("/backup/verify")
async def verify_backup(req: VerifyRequest):
    """Open an archive and check every file against its recorded hash.

    This is what the nightly drill runs. "The file exists and is the right size"
    is not verification.
    """
    import asyncio

    from services import backup_service

    archive = Path(req.archive).expanduser()
    if not archive.is_file():
        raise HTTPException(status_code=404, detail=f"{archive} does not exist")

    try:
        report = await asyncio.to_thread(
            backup_service.verify_archive, archive, phrase=req.phrase
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"could not verify: {exc}")

    report.pop("manifest", None)      # the caller did not ask for the file list
    return report


@router.get("/backup/list")
async def list_backups(destination: str):
    """What archives are in a folder, newest first.

    Reads only the PUBLIC header of each — no decryption, so this stays cheap
    and reveals no file names.
    """
    from services import backup_service

    folder = Path(destination).expanduser()
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"{folder} is not a folder")

    out = []
    for path in sorted(folder.glob(f"*{backup_service.ARCHIVE_SUFFIX}"), reverse=True):
        entry = {"path": str(path), "bytes": path.stat().st_size}
        try:
            header = backup_service.read_header(path)
            entry.update({
                "created_at": header.get("created_at"),
                "device_id": header.get("device_id"),
                "openable_with_phrase": any(
                    r.get("kind") == "recovery" for r in header.get("recipients", [])
                ),
            })
        except Exception as exc:
            # Listed anyway, marked unreadable. Hiding it would make a corrupt
            # archive look like an archive that was never taken.
            entry["error"] = str(exc)
        out.append(entry)
    return {"backups": out}


# ── restore (LB-10 item 4) ──────────────────────────────────────────────────


class RestoreRequest(BaseModel):
    archive: str
    phrase: Optional[str] = None
    # Restore a damaged archive anyway. Sometimes worse-than-perfect beats
    # nothing — but the marker records that it was forced.
    force: bool = False


@router.post("/restore/check")
async def check_restore(req: RestoreRequest):
    """Everything a restore would do, except the part that changes anything.

    Opens the archive, checks every hash, opens every database and compares
    every row count — in a temp directory. Nothing on disk is touched.
    """
    import asyncio

    from services import restore_service

    archive = Path(req.archive).expanduser()
    if not archive.is_file():
        raise HTTPException(status_code=404, detail=f"{archive} does not exist")
    report = await asyncio.to_thread(restore_service.verify, archive, phrase=req.phrase)
    return report.as_dict()


@router.post("/restore")
async def restore(req: RestoreRequest):
    """Verify an archive and stage it for the next launch.

    **Restoring into a running app is refused**, and not as a policy: the
    backend has four databases open with live WALs and LanceDB holding file
    handles. The swap happens at startup, before any of that exists.
    """
    import asyncio

    from services import restore_service

    archive = Path(req.archive).expanduser()
    if not archive.is_file():
        raise HTTPException(status_code=404, detail=f"{archive} does not exist")

    report = await asyncio.to_thread(
        restore_service.stage_restore, archive, phrase=req.phrase, force=req.force
    )
    out = report.as_dict()
    out["detail"] = (
        "Staged. Quit and reopen LocalBook to apply it — the swap happens before "
        "anything opens a database. Your current data is kept, not replaced."
        if report.staged_to
        else "Not staged: the archive did not verify."
    )
    return out


@router.get("/restore/pending")
async def get_pending_restore():
    from services import restore_service

    return {"pending": restore_service.pending_restore()}


@router.delete("/restore/pending")
async def cancel_pending_restore():
    from services import restore_service

    return {"cancelled": restore_service.cancel_pending()}


@router.get("/restore/drills")
async def drill_history():
    """The nightly drill record. The plan's gate is seven consecutive greens
    before LB-11 migrates anything."""
    from services import restore_service

    return restore_service.drill_status()


@router.post("/restore/drill")
async def run_drill_now(destination: str):
    """Run the drill immediately, against the newest archive in `destination`."""
    import asyncio

    from services import restore_service

    return await asyncio.to_thread(restore_service.run_drill, Path(destination).expanduser())


# ── data health (LB-10 items 6, 8, 9) ───────────────────────────────────────


@router.get("/data-health")
async def data_health_status():
    """Everything the Data Health panel shows, in one call.

    Each probe degrades to its own error rather than raising — a panel that
    500s because one probe failed tells the user nothing about the other five.
    """
    import asyncio

    from services import data_health

    return await asyncio.to_thread(data_health.status)


@router.post("/data-health/integrity-check")
async def run_integrity_check():
    """Check every database now. Read-only; never repairs.

    Repairing automatically is the wrong instinct — SQLite's own guidance is to
    recover from a backup, and a well-meant in-place rebuild can turn a
    partially readable file into a confidently wrong one.
    """
    import asyncio

    from services import data_health

    return await asyncio.to_thread(data_health.check_integrity)


@router.delete("/data-health/dead-weight")
async def clear_dead_weight():
    """Remove the leftovers listed in the panel. Only ever on request."""
    import asyncio

    from services import data_health

    return await asyncio.to_thread(data_health.remove_dead_weight)
