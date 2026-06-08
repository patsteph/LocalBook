"""Cross-source synthesis API — Phase 12 of v2-information-cortex.

Endpoints for the perspectives view (topic-across-sources diff +
light consensus/contested aggregation).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class PerspectivesRequest(BaseModel):
    query: str
    notebook_id: Optional[str] = None
    cross_notebook: bool = False
    max_sources: int = 8


class DeepDiveRequest(BaseModel):
    entity: str
    notebook_id: Optional[str] = None
    cross_notebook: bool = True
    max_sources: int = 8


@router.post("/deep-dive")
async def deep_dive(request: DeepDiveRequest):
    if not request.entity.strip():
        raise HTTPException(status_code=400, detail="entity is required")
    from services.topic_perspectives import find_deep_dive, deep_dive_to_html
    try:
        result = await find_deep_dive(
            request.entity,
            request.notebook_id,
            max_sources=max(1, min(16, request.max_sources)),
            cross_notebook=request.cross_notebook,
        )
    except Exception as e:
        logger.exception("[synthesis.deep_dive] failed")
        raise HTTPException(status_code=500, detail=f"Deep-dive failed: {e}")
    html = deep_dive_to_html(result)
    return {"html": html, "perspectives": result.model_dump()}


@router.post("/perspectives")
async def perspectives(request: PerspectivesRequest):
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="query is required")
    if not request.cross_notebook and not request.notebook_id:
        raise HTTPException(
            status_code=400,
            detail="notebook_id required when cross_notebook is false",
        )
    from services.topic_perspectives import find_perspectives, perspectives_to_html
    try:
        result = await find_perspectives(
            request.query,
            request.notebook_id,
            max_sources=max(1, min(16, request.max_sources)),
            cross_notebook=request.cross_notebook,
        )
    except Exception as e:
        logger.exception("[synthesis.perspectives] failed")
        raise HTTPException(status_code=500, detail=f"Perspectives generation failed: {e}")
    html = perspectives_to_html(result)
    return {"html": html, "perspectives": result.model_dump()}
