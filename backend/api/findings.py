"""
Findings API - Endpoints for saving, retrieving, and managing user findings.

Part of the Canvas architecture - enables bookmarking visuals, highlights,
answers, and notes from research.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from services.findings_store import get_findings_store

router = APIRouter(prefix="/findings", tags=["findings"])


class CreateFindingRequest(BaseModel):
    notebook_id: str
    type: str  # 'visual' | 'answer' | 'highlight' | 'source' | 'note'
    title: str
    content: Dict[str, Any]
    tags: Optional[List[str]] = None
    starred: bool = False


class UpdateFindingRequest(BaseModel):
    title: Optional[str] = None
    tags: Optional[List[str]] = None
    starred: Optional[bool] = None
    content: Optional[Dict[str, Any]] = None


class FindingResponse(BaseModel):
    id: str
    notebook_id: str
    type: str
    title: str
    created_at: str
    updated_at: str
    content: Dict[str, Any]
    tags: List[str]
    starred: bool


@router.post("", response_model=FindingResponse)
async def create_finding(request: CreateFindingRequest):
    """Create a new finding (bookmark, saved visual, highlight, etc.)."""
    store = get_findings_store()
    
    # Validate type
    valid_types = ['visual', 'answer', 'highlight', 'source', 'note']
    if request.type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid finding type. Must be one of: {valid_types}"
        )
    
    finding = await store.create_finding(
        notebook_id=request.notebook_id,
        finding_type=request.type,
        title=request.title,
        content=request.content,
        tags=request.tags,
        starred=request.starred,
    )
    
    return FindingResponse(**finding.to_dict())


@router.get("/{notebook_id}/stats/summary")
async def get_findings_stats(notebook_id: str):
    """Get statistics about findings for a notebook.
    
    NOTE: This route must be defined BEFORE /{notebook_id}/{finding_id}
    to avoid route conflict in FastAPI.
    """
    store = get_findings_store()
    
    stats = await store.get_stats(notebook_id)
    return stats


@router.get("/{notebook_id}")
async def get_findings(
    notebook_id: str,
    type: Optional[str] = None,
    starred: bool = False,
    tag: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Get findings for a notebook with optional filters."""
    store = get_findings_store()
    
    findings = await store.get_findings(
        notebook_id=notebook_id,
        type_filter=type,
        starred_only=starred,
        tag_filter=tag,
        limit=limit,
        offset=offset,
    )
    
    return {
        "findings": [f.to_dict() for f in findings],
        "count": len(findings),
    }


@router.get("/{notebook_id}/{finding_id}", response_model=FindingResponse)
async def get_finding(notebook_id: str, finding_id: str):
    """Get a specific finding."""
    store = get_findings_store()
    
    finding = await store.get_finding(notebook_id, finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    
    return FindingResponse(**finding.to_dict())


@router.patch("/{notebook_id}/{finding_id}", response_model=FindingResponse)
async def update_finding(
    notebook_id: str,
    finding_id: str,
    request: UpdateFindingRequest,
):
    """Update a finding's title, tags, starred status, or content."""
    store = get_findings_store()
    
    updates = {}
    if request.title is not None:
        updates['title'] = request.title
    if request.tags is not None:
        updates['tags'] = request.tags
    if request.starred is not None:
        updates['starred'] = request.starred
    if request.content is not None:
        updates['content'] = request.content
    
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    
    finding = await store.update_finding(notebook_id, finding_id, updates)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    
    return FindingResponse(**finding.to_dict())


@router.delete("/{notebook_id}/{finding_id}")
async def delete_finding(notebook_id: str, finding_id: str):
    """Delete a finding."""
    store = get_findings_store()
    
    success = await store.delete_finding(notebook_id, finding_id)
    if not success:
        raise HTTPException(status_code=404, detail="Finding not found")
    
    return {"success": True, "message": "Finding deleted"}
