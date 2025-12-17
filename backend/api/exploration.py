"""Exploration API endpoints - Track user's learning journey"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from storage.exploration_store import exploration_store

router = APIRouter(prefix="/exploration", tags=["exploration"])


class RecordQueryRequest(BaseModel):
    """Request to record a query in exploration history"""
    notebook_id: str
    query: str
    topics: List[str] = []
    sources_used: List[str] = []
    confidence: float = 0.5
    answer_preview: str = ""


@router.post("/record")
async def record_query(request: RecordQueryRequest):
    """Record a query as part of the user's exploration journey"""
    result = await exploration_store.record_query(
        notebook_id=request.notebook_id,
        query=request.query,
        topics=request.topics,
        sources_used=request.sources_used,
        confidence=request.confidence,
        answer_preview=request.answer_preview
    )
    return {"status": "recorded", "query_id": result["id"]}


@router.get("/journey/{notebook_id}")
async def get_journey(notebook_id: str, limit: int = 50):
    """Get the exploration journey for a notebook"""
    return await exploration_store.get_journey(notebook_id, limit)


@router.get("/suggestions/{notebook_id}")
async def get_suggestions(notebook_id: str):
    """Get suggestions for continuing exploration"""
    return await exploration_store.get_suggestions(notebook_id)


@router.delete("/clear/{notebook_id}")
async def clear_exploration(notebook_id: str):
    """Clear exploration history for a notebook"""
    await exploration_store.clear_notebook(notebook_id)
    return {"status": "cleared"}
