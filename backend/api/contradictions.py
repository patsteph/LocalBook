"""Contradiction Detection API endpoints

Provides endpoints for scanning notebooks for conflicting information
and managing detected contradictions.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from services.contradiction_detector import (
    contradiction_detector,
    ContradictionReport,
    Contradiction
)


router = APIRouter(prefix="/contradictions", tags=["contradictions"])


class ScanRequest(BaseModel):
    force_rescan: bool = False


class DismissRequest(BaseModel):
    reason: Optional[str] = None


# Background scan status
_scan_status = {}  # notebook_id -> {"status": "scanning"|"complete", "progress": 0-100}


@router.post("/scan/{notebook_id}", response_model=ContradictionReport)
async def scan_for_contradictions(
    notebook_id: str,
    request: ScanRequest = ScanRequest()
):
    """
    Scan a notebook for contradictions between sources.
    
    This analyzes claims across sources and identifies conflicts.
    Results are cached - use force_rescan=true to refresh.
    """
    try:
        report = await contradiction_detector.scan_notebook(
            notebook_id, 
            force_rescan=request.force_rescan
        )
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{notebook_id}", response_model=ContradictionReport)
async def get_contradictions(notebook_id: str):
    """
    Get cached contradiction report for a notebook.
    
    Returns empty report if no scan has been run.
    """
    report = await contradiction_detector.get_cached_report(notebook_id)
    
    if not report:
        # Return empty report with suggestion to scan
        from datetime import datetime
        return ContradictionReport(
            notebook_id=notebook_id,
            generated_at=datetime.utcnow().isoformat(),
            contradictions=[],
            claims_analyzed=0,
            sources_analyzed=0
        )
    
    return report


@router.get("/{notebook_id}/count")
async def get_contradiction_count(notebook_id: str):
    """Get quick count of contradictions without full report."""
    report = await contradiction_detector.get_cached_report(notebook_id)
    
    if not report:
        return {"count": 0, "has_scanned": False}
    
    # Count non-dismissed contradictions
    active = [c for c in report.contradictions if not c.dismissed]
    
    return {
        "count": len(active),
        "total": len(report.contradictions),
        "has_scanned": True,
        "scanned_at": report.generated_at
    }


@router.post("/{notebook_id}/scan-background")
async def scan_background(
    notebook_id: str,
    background_tasks: BackgroundTasks
):
    """Start a background scan for contradictions."""
    
    async def do_scan():
        _scan_status[notebook_id] = {"status": "scanning", "progress": 0}
        try:
            await contradiction_detector.scan_notebook(notebook_id, force_rescan=True)
            _scan_status[notebook_id] = {"status": "complete", "progress": 100}
        except Exception as e:
            _scan_status[notebook_id] = {"status": "error", "error": str(e)}
    
    background_tasks.add_task(do_scan)
    
    return {"message": "Scan started", "notebook_id": notebook_id}


@router.get("/{notebook_id}/scan-status")
async def get_scan_status(notebook_id: str):
    """Get the status of a background scan."""
    return _scan_status.get(notebook_id, {"status": "idle", "progress": 0})


@router.post("/{notebook_id}/dismiss/{contradiction_id}")
async def dismiss_contradiction(
    notebook_id: str,
    contradiction_id: str,
    request: DismissRequest = DismissRequest()
):
    """Mark a contradiction as dismissed/reviewed."""
    success = await contradiction_detector.dismiss_contradiction(
        notebook_id, 
        contradiction_id
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="Contradiction not found")
    
    return {"message": "Contradiction dismissed", "id": contradiction_id}


@router.delete("/{notebook_id}/cache")
async def clear_cache(notebook_id: str):
    """Clear cached contradiction report to force fresh scan."""
    await contradiction_detector.clear_cache(notebook_id)
    return {"message": "Cache cleared", "notebook_id": notebook_id}
