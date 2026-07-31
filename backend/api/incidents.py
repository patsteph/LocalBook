"""Incidents API — preview + (gated) file for Quality-Signals Phase 2 (slice 2b).

Two endpoints on top of `services/github_incident.py`:
  - `GET  /incidents/preview` — the SAFE view: what WOULD be filed for each queued incident
    (already-scrubbed title + body). Zero network, zero subprocess.
  - `POST /incidents/file`     — the ONLY send path. Refuses with 409 when the default-OFF flag
    is unset, and requires an explicit `confirm: true` in the body (Decision #5 = explicit click).

Local-only, read-mostly. The emitter never raises; the endpoints translate its structured
result / disabled state into HTTP.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.github_incident import github_incident

router = APIRouter()


@router.get("/incidents/preview")
async def preview_incidents():
    """What WOULD be filed for each queued incident. SAFE — never sends.

    Returns the already-scrubbed title+body verbatim from the local queue, plus each
    incident's `already_filed` state (local idempotency map). Never raises.
    """
    incidents = github_incident.preview_incidents()
    return {
        "enabled": github_incident.is_enabled(),
        "auth": github_incident.auth_status(),
        "count": len(incidents),
        "incidents": incidents,
    }


class FileRequest(BaseModel):
    confirm: bool = False


@router.post("/incidents/file")
async def file_incidents(request: FileRequest):
    """File every not-yet-filed queued incident — gated OFF + explicit confirm.

    - Refuses with **409** when the default-OFF `incidents_enabled` flag is unset (no auth
      or network is touched).
    - Requires `confirm: true` in the body; **400** otherwise.
    Idempotent: an incident already in the local filed-map is skipped.
    """
    if not github_incident.is_enabled():
        raise HTTPException(
            status_code=409,
            detail="Incident filing is disabled (default-OFF). Enable it before filing.",
        )
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Explicit confirm=true is required to file incidents.",
        )
    return github_incident.file_incidents(confirm=True)
