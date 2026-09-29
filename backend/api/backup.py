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
