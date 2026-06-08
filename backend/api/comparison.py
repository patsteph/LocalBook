"""Comparison API — Phase 4 of v2-information-cortex.

Single endpoint: POST /comparison/generate
Body: { notebook_id, source_a_id, source_b_id, focus? }
Returns: an Artifact envelope (type='json:comparison').
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from services.comparison_service import comparison_service

router = APIRouter()


class GenerateComparisonRequest(BaseModel):
    notebook_id: str
    source_a_id: str
    source_b_id: str
    focus: Optional[str] = Field(default=None, description="Optional axis of comparison")


@router.post("/generate")
async def generate_comparison(request: GenerateComparisonRequest):
    try:
        artifact = await comparison_service.compare(
            notebook_id=request.notebook_id,
            source_a_id=request.source_a_id,
            source_b_id=request.source_b_id,
            focus=request.focus,
        )
        return {"artifact": artifact}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Comparison failed: {e}")
