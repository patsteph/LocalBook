"""Quality Signals API — the "Rough edges" rollup.

Surfaces the recurrence-ranked ledger of silent near-misses (see services/quality_signals.py)
so the in-app Health panel can show "here's where the tool worked but not effectively lately."
Read-only, local-only.
"""
from fastapi import APIRouter, Query

from services.quality_signals import quality_signals

router = APIRouter()


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
