"""Quality Signals API — the "Rough edges" rollup.

Surfaces the recurrence-ranked ledger of silent near-misses (see services/quality_signals.py)
so the in-app Health panel can show "here's where the tool worked but not effectively lately."
Read-only, local-only.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.quality_signals import quality_signals, record_signal

router = APIRouter()

# Signal types the CLIENT may report. Bounded on purpose: the ledger drives promotion into Evaluator
# regression cases, so it must not become a free-form sink for arbitrary frontend strings.
_CLIENT_TYPES = {"render_failed", "degraded", "empty", "fallback"}
_MAX_DETAIL = 300


@router.get("/signals/recent")
async def recent_signals(days: int = Query(7, ge=1, le=90)):
    """Recurrence-ranked near-misses over the last `days`.

    Returns groups (most frequent first) of (type, component, key) with counts, severity,
    first/last seen, the latest detail, and a few sample trigger inputs. Empty list = clean run.
    """
    groups = quality_signals.get_recent(days=days)
    return {
        "days": days,
        "total": sum(g["count"] for g in groups),
        "groups": groups,
    }


class ClientSignal(BaseModel):
    """A near-miss the CLIENT observed. The renderer knows things the backend cannot."""
    type: str = Field(description="One of the client-reportable types")
    component: str = Field(max_length=64, description="e.g. 'chart_renderer'")
    detail: str = Field(max_length=_MAX_DETAIL)
    key: str = Field(default="", max_length=120, description="Recurrence key — groups repeats")
    severity: str = Field(default="warn")
    notebook_id: str = Field(default="", max_length=64)


@router.post("/signals/record")
async def record_client_signal(sig: ClientSignal):
    """Record a near-miss observed in the UI.

    Quality Signals only ever saw the BACKEND, which is how three chat charts stayed broken for
    months: they emitted a valid-looking `json-chart` fence whose payload the renderer could not
    read, so it drew "Unsupported chart type: undefined" and nothing anywhere logged a thing
    (found 2026-08-12). A render that silently degrades is precisely the near-miss this ledger
    exists to catch — but only the client can see it.

    Type-allowlisted and length-capped: these entries feed promotion into Evaluator regression
    cases, so the sink must stay bounded rather than accept arbitrary client strings.
    """
    if sig.type not in _CLIENT_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"type must be one of {sorted(_CLIENT_TYPES)}")
    # record_signal returns None and never raises (the sink must not break a caller).
    record_signal(
        sig.type,
        sig.component or "client",
        sig.detail,
        severity=sig.severity if sig.severity in ("info", "notable", "warn") else "warn",
        key=sig.key,
        notebook_id=sig.notebook_id,
    )
    return {"recorded": True}
