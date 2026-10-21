"""Settings › Sync (LB-12): the app-token API behind the screen.

The Mac-to-Mac protocol is NOT here — it runs on its own TLS listeners
(services/sync/peer.py). These routes are this Mac's own UI talking to its own
backend: turn sync on/off, pair, preview, apply, sync now, resolve conflicts.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/sync", tags=["sync"])


def _svc():
    from services.sync import service

    return service


async def _call(coro):
    try:
        return await coro
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"{type(exc).__name__}: {exc}"[:400])


@router.get("/status")
async def status():
    import asyncio

    return await asyncio.to_thread(_svc().status)


@router.post("/enable")
async def enable():
    return await _call(_svc().enable())


@router.post("/disable")
async def disable():
    return await _call(_svc().disable())


@router.post("/pairing/open")
async def open_pairing():
    return await _call(_svc().open_pairing())


class PairRequest(BaseModel):
    host: str
    port: Optional[int] = None


@router.post("/pairing/connect")
async def pair_with(req: PairRequest):
    out = await _call(_svc().pair_with(req.host, req.port))
    return {k: out[k] for k in ("id", "name", "sas", "device_id")}


@router.post("/pairing/{pairing_id}/confirm")
async def confirm(pairing_id: str):
    return await _call(_svc().confirm_pairing(pairing_id))


@router.post("/pairing/{pairing_id}/reject")
async def reject(pairing_id: str):
    _svc().reject_pairing(pairing_id)
    return {"ok": True}


@router.post("/devices/{device_id}/preview")
async def preview(device_id: str):
    return await _call(_svc().preview(device_id))


@router.post("/devices/{device_id}/apply")
async def apply(device_id: str):
    """Start sync (first time): returns at once with a run id; follow /sync/progress."""
    try:
        return _svc().start_run(device_id, first=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/devices/{device_id}/sync")
async def sync_now(device_id: str):
    try:
        return _svc().start_run(device_id, first=False)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/progress")
async def get_progress():
    from services.sync import progress

    return progress.snapshot()


@router.post("/progress/cancel")
async def cancel_progress():
    """Stop safely: what already arrived is kept; the next sync continues."""
    from services.sync import progress

    return {"stopping": progress.cancel()}


@router.get("/discover")
async def discover():
    """LocalBook Macs answering on this network (Bonjour), minus this one and the
    ones already paired. Empty on networks that block multicast — type the address."""
    import asyncio

    from services.sync import discovery, identity, store
    if not store.enabled():
        return {"macs": []}
    paired = {d["device_id"] for d in store.devices()}
    found = await asyncio.to_thread(discovery.browse, identity.device_id())
    return {"macs": [m for m in found if m.get("device_id") not in paired]}


class PhoneLibraryRequest(BaseModel):
    excluded: list[str]


@router.get("/phones/library")
async def phone_library_settings():
    """Every notebook and whether paired phones may read it."""
    from services.sync import phone_library
    return {"notebooks": phone_library.settings_view()}


@router.post("/phones/library")
async def set_phone_library(req: PhoneLibraryRequest):
    """Older endpoint: sets the device-only list (the notebooks that sync with no other device)."""
    from services.sync import phone_library
    phone_library.set_excluded(req.excluded)
    return {"notebooks": phone_library.settings_view()}


def _notebook_view():
    from services.sync import phone_library, scope
    local, legacy = set(scope.local_notebooks()), set(scope.legacy_phone_only())
    return {"notebooks": [{"id": i, "title": t, "syncs": i not in local, "phones_only_off": i in legacy and i not in local}
                          for i, t in sorted(phone_library.notebook_titles().items(), key=lambda kv: kv[1].lower())]}


@router.get("/notebooks")
async def sync_notebooks():
    """Every notebook and whether it syncs with other devices (off = kept on this device only).
    `phones_only_off` marks a notebook from the older setting that kept it off phones but let Macs sync it."""
    return _notebook_view()


class SyncNotebooksRequest(BaseModel):
    keep_local: list[str]
    share_with_phones: list[str] = []


@router.post("/notebooks")
async def set_sync_notebooks(req: SyncNotebooksRequest):
    """Set which notebooks stay on this device only (they sync with no other device). `share_with_phones` lifts an
    older phones-only mark without changing how the notebook syncs between Macs."""
    from services.sync import scope
    scope.set_local(req.keep_local)
    if req.share_with_phones:
        scope.clear_phones_only(req.share_with_phones)
    return _notebook_view()


@router.post("/phones/{device_id}/revoke")
async def revoke_phone(device_id: str):
    return await _call(_svc().revoke_phone(device_id))


@router.post("/devices/{device_id}/revoke")
async def revoke(device_id: str):
    return await _call(_svc().revoke(device_id))


class Collector(BaseModel):
    device_id: str


@router.post("/collector")
async def set_collector(req: Collector):
    """Which Mac runs scheduled collections — one synced setting for every Mac."""
    from services.sync import identity, roles, store
    if req.device_id == identity.device_id():
        name = identity.device_name()
    else:
        d = store.device(req.device_id)
        if not d or d.get("revoked_at"):
            raise HTTPException(400, "that Mac is not paired")
        name = d.get("name")
    roles.set_collector(req.device_id, name)
    return _svc().status()


@router.get("/conflicts")
async def conflicts(status: str = "open"):
    from services.sync import runtime

    r = runtime.replica("main")
    try:
        rows = r.conn.execute(
            "SELECT id, tbl, pk, field, kind, kept_value, other_value, kept_clock, other_clock, "
            "status, created_at FROM sync_conflicts WHERE status=? ORDER BY created_at DESC LIMIT 200",
            (status,)).fetchall()
    finally:
        r.conn.close()
    cols = ["id", "tbl", "pk", "field", "kind", "kept_value", "other_value", "kept_clock",
            "other_clock", "status", "created_at"]
    out = []
    for row in rows:
        d = dict(zip(cols, row))
        for k in ("kept_value", "other_value"):
            try:
                d[k] = json.loads(d[k]) if d[k] is not None else None
            except Exception:
                pass
        out.append(d)
    return {"conflicts": out}


class Resolve(BaseModel):
    keep: str            # "kept" | "other"


@router.post("/conflicts/{conflict_id}/resolve")
async def resolve(conflict_id: str, req: Resolve):
    """Keep what sync kept, or restore the other value. Restoring is an ordinary
    local edit — it ships, and the resolution reaches every Mac."""
    if req.keep not in ("kept", "other"):
        raise HTTPException(400, "keep must be 'kept' or 'other'")
    from storage.database import get_db

    conn = get_db().get_connection()
    row = conn.execute("SELECT tbl, pk, field, other_value, kind FROM sync_conflicts WHERE id=?",
                       (conflict_id,)).fetchone()
    if not row:
        raise HTTPException(404, "no such conflict")
    tbl, pk, field, other, kind = row
    if req.keep == "other":
        from services.sync import merge, registry

        try:
            t = registry.table("main", tbl)
        except KeyError:
            raise HTTPException(400, "that conflict's table no longer syncs")
        value = merge.decode(json.loads(other)) if other is not None else None
        if tbl == "documents" and kind in ("concurrent-edit",) and field == "body_json":
            # Through the documents store: it journals (ships), bumps updated_at, and the
            # in-memory copies of settings / core memory are dropped so this Mac stops
            # serving the old value until a restart (2026-10-03).
            from services.sync.peer import drop_document_caches
            from storage import documents
            doc_kind, doc_key = json.loads(pk)
            documents.put(doc_kind, doc_key, json.loads(value) if isinstance(value, str) else value)
            drop_document_caches()
        elif kind in ("concurrent-edit",) and field in [c for c in t.content]:
            where = " AND ".join(f'"{c}" = ?' for c in t.pk)
            conn.execute(f'UPDATE "{tbl}" SET "{field}" = ? WHERE {where}', [value, *json.loads(pk)])
        else:
            raise HTTPException(400, "this kind of conflict can only be acknowledged; copy the "
                                     "text you want to keep from the conflict first")
    conn.execute("UPDATE sync_conflicts SET status='resolved', resolution=?, "
                 "resolved_at=CURRENT_TIMESTAMP WHERE id=?", (req.keep, conflict_id))
    conn.commit()
    return {"ok": True}
