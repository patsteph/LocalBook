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


@router.post("/incidents/scan")
async def scan_signals(days: int = 7):
    """Walk recent signal groups and escalate the ones that clear the promotion bar.

    THE MISSING LINK (added 2026-08-12). Phase 2 shipped the sink, the promoter, the incident
    queue, and the filing path — but nothing ever walked the ledger, so `escalate_to_incident` and
    `promote_recent` had no production caller and the queue stayed permanently empty. The pipeline
    existed end to end and was never connected in the middle.

    One eligible group produces up to two artifacts, sharing `promotion_verdict` as the single
    definition of "worth escalating":
      - a **queued incident** (scrubbed, local) for the user to review and optionally file, and
      - an **Evaluator regression case**, when the group is of a promotable shape.

    Local + idempotent + never sends: re-scanning does not duplicate, and filing stays a separate
    explicit step behind the default-OFF flag.
    """
    # Reuse the promoter's day-span helper rather than re-deriving it — the incident path and the
    # eval-case path must agree on what "recurring" means, or one escalates while the other doesn't.
    from services.field_edge_promoter import _distinct_days, promote_signal_to_eval_case
    from services.quality_signals import promotion_verdict, quality_signals

    escalated, promoted, considered = [], [], 0
    for group in quality_signals.get_recent(days):
        considered += 1
        distinct_days = _distinct_days(group)
        verdict = promotion_verdict(
            group.get("count", 0), distinct_days, str(group.get("severity", "notable")))
        if not verdict.get("eligible"):
            continue
        inc = quality_signals.escalate_to_incident(
            group,
            metrics={"count": group.get("count", 0), "distinct_days": distinct_days,
                     "window_days": days},
            promotion=verdict,
        )
        if inc:
            escalated.append(inc.get("incident_id") or inc.get("id"))
        case = promote_signal_to_eval_case(group)
        if case:
            promoted.append(case.get("name"))

    return {"days": days, "considered": considered,
            "escalated": len(escalated), "incident_ids": escalated,
            "promoted_cases": promoted}


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
