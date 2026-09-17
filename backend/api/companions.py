"""Companions API — install, connect, and wire up local companion tools.

Thin over `services/companions.py`. The one piece of real orchestration is
`/connect`, which does the three things that turn a standalone tool into part
of LocalBook: point it at our engine, watch the folder it writes into, and
(optionally) create the notebook its output belongs in.
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import companions as svc

router = APIRouter()
logger = logging.getLogger(__name__)


class ConnectRequest(BaseModel):
    # Where its output should land. None => connect the engine only, and leave
    # the folder unlinked; the user may prefer to file it themselves.
    notebook_id: Optional[str] = None
    # "all" ingests what is already in the output folder; "new_only" baselines
    # it. Same vocabulary as linking any other folder.
    backfill: str = "new_only"
    frequency: str = "every_2_hours"


def _manifest_or_404(companion_id: str):
    m = svc.get_manifest(companion_id)
    if not m:
        raise HTTPException(status_code=404, detail="Unknown companion")
    return m


@router.get("/companions")
async def list_companions():
    items = svc.all_status()
    titles = {}
    try:
        from storage.notebook_store import notebook_store
        titles = {n["id"]: n.get("title") or "Untitled"
                  for n in (await notebook_store.list() or [])}
    except Exception:
        pass
    for it in items:
        it["linked_notebook_title"] = titles.get(it.get("linked_notebook_id") or "")
    return {"companions": items,
            "notebooks": [{"id": k, "title": v} for k, v in
                          sorted(titles.items(), key=lambda kv: kv[1].lower())]}


@router.get("/companions/{companion_id}")
async def get_companion(companion_id: str):
    return svc.status(_manifest_or_404(companion_id))


@router.get("/companions/{companion_id}/verify")
async def verify(companion_id: str):
    """Did the install actually produce a working tool?

    Its exit code is not evidence. Meeting Notes swallows a failed audio-driver
    install with `2>/dev/null || true`, and the user would not discover it until
    the far side of their first call came back silent.
    """
    return svc.verify_install(_manifest_or_404(companion_id))


@router.get("/companions/{companion_id}/install-script")
async def install_script(companion_id: str):
    """Download the PINNED installer and check it against the recorded hash.

    Called before offering to run anything, so a mismatch stops the flow rather
    than being discovered afterwards.
    """
    manifest = _manifest_or_404(companion_id)
    result = svc.fetch_and_verify_script(manifest)
    return {"source": svc.install_source(manifest), "verification": result}


@router.post("/companions/{companion_id}/connect")
async def connect(companion_id: str, req: ConnectRequest):
    """Point the tool at LocalBook's engine and watch what it writes.

    Ordering matters. The engine is connected first because it is the part that
    can fail for a reason the user must see (the tool isn't installed, or its
    config is missing). Folder linking is best-effort on top — a failure there
    leaves a working, connected tool rather than a half-configured one.
    """
    manifest = _manifest_or_404(companion_id)
    if not svc.is_installed(manifest):
        raise HTTPException(
            status_code=409,
            detail=f"{manifest.get('name')} is not installed yet. Install it first.")

    result = svc.write_config(manifest)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Could not connect"))

    linked = None
    link_error = None
    produces = manifest.get("produces") or {}
    out_dir = Path(produces["dir"]).expanduser() if produces.get("dir") else None

    if req.notebook_id and out_dir:
        try:
            from storage.folder_link_store import folder_link_store, FolderLinkPathError
            # The tool creates its output folder on first run; create it now so
            # the link can be made before the user's first recording rather than
            # after it, which would silently miss that recording.
            out_dir.mkdir(parents=True, exist_ok=True)
            existing = folder_link_store.find_by_path(str(out_dir), req.notebook_id)
            if existing:
                linked = existing
            else:
                linked = folder_link_store.create_link(
                    path=str(out_dir), notebook_id=req.notebook_id,
                    frequency=req.frequency,
                )
                if req.backfill == "new_only":
                    from services.folder_watcher import folder_watcher
                    folder_watcher.mark_baseline(linked)
        except FolderLinkPathError as e:
            link_error = str(e)
        except Exception as e:
            link_error = f"{type(e).__name__}: {e}"
        if link_error:
            logger.warning(f"[companions] engine connected but folder link failed: {link_error}")

    return {"ok": True, "config": result.get("config"),
            "folder_link_id": (linked or {}).get("id"),
            "link_error": link_error,
            "status": svc.status(manifest)}


@router.post("/companions/{companion_id}/disconnect")
async def disconnect(companion_id: str, remove_link: bool = False):
    """Restore the tool's own config. Its ingested notes are never touched."""
    manifest = _manifest_or_404(companion_id)
    result = svc.disconnect(manifest)
    if remove_link:
        try:
            from storage.folder_link_store import folder_link_store
            st = svc.status(manifest)
            if st.get("folder_link_id"):
                folder_link_store.delete_link(st["folder_link_id"])
        except Exception as e:
            logger.warning(f"[companions] could not unlink: {e}")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Could not disconnect"))
    return {"ok": True, "status": svc.status(manifest)}


@router.post("/companions/{companion_id}/extras/{extra_id}")
async def add_extra(companion_id: str, extra_id: str):
    """Install one optional add-on — e.g. the menu bar control.

    Needs no privilege: SwiftBar is an app cask and the plugin is a shell script
    in a folder the user already owns. This is the one part of the flow that
    genuinely is one click.
    """
    manifest = _manifest_or_404(companion_id)
    result = svc.install_extra(manifest, extra_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Could not install"))
    return {"ok": True, "status": svc.status(manifest)}


@router.delete("/companions/{companion_id}/extras/{extra_id}")
async def drop_extra(companion_id: str, extra_id: str):
    """Remove the add-on, leaving its host app alone — other plugins may use it."""
    manifest = _manifest_or_404(companion_id)
    result = svc.remove_extra(manifest, extra_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Could not remove"))
    return {"ok": True, "status": svc.status(manifest)}


@router.post("/companions/{companion_id}/control/{action}")
async def control(companion_id: str, action: str):
    manifest = _manifest_or_404(companion_id)
    if action not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="action must be start or stop")
    result = svc.run_control(manifest, action)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Command failed"))
    return {"ok": True, "output": result.get("output"), "status": svc.status(manifest)}


@router.post("/companions/key/revoke")
async def revoke_key():
    """Cut off every connected companion at once. They can be reconnected."""
    svc.revoke_companion_key()
    return {"ok": True}
