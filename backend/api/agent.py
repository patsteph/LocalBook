"""Agent API endpoints

Exposes the LangGraph agent system via HTTP endpoints.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRequest(BaseModel):
    """Request to run the agent system."""
    query: str
    notebook_id: Optional[str] = None
    

class CaptureRequest(BaseModel):
    """Browser capture request."""
    url: str
    title: str
    content: str
    notebook_id: str
    html_content: Optional[str] = None
    selected_text: Optional[str] = None


class BatchCaptureRequest(BaseModel):
    """Batch browser capture request."""
    captures: List[CaptureRequest]


class AgentResponse(BaseModel):
    """Response from agent system."""
    response: str
    intent: Optional[str] = None
    agent: Optional[str] = None
    generated_content: Optional[dict] = None
    citations: List[dict] = []


@router.post("/run", response_model=AgentResponse)
async def run_agent_endpoint(request: AgentRequest):
    """Run the agent system with a query."""
    try:
        from agents.supervisor import run_agent
        
        result = await run_agent(
            query=request.query,
            notebook_id=request.notebook_id
        )
        
        return AgentResponse(
            response=result.get("response", ""),
            intent=result.get("intent"),
            agent=result.get("agent"),
            generated_content=result.get("generated_content"),
            citations=result.get("citations", [])
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/classify")
async def classify_intent(request: AgentRequest):
    """Classify the intent of a query without executing."""
    try:
        from agents.supervisor import classify_intent
        
        result = await classify_intent(request.query)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
