"""Agent Browser API Endpoints

v1.1.0: API for AI-native browser automation.
Used by browser extension for semantic element finding and action execution.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from services.agent_browser import (
    agent_browser,
    PageContext,
    BrowserAction,
    SemanticElement
)


router = APIRouter(prefix="/agent-browser", tags=["agent-browser"])


# =============================================================================
# Request/Response Models
# =============================================================================

class ElementInfo(BaseModel):
    """Element information from browser."""
    tag: str
    text: Optional[str] = None
    selector: Optional[str] = None
    xpath: Optional[str] = None
    attributes: Dict[str, str] = {}
    position: Optional[Dict[str, int]] = None


class PageContextRequest(BaseModel):
    """Page context from browser extension."""
    url: str
    title: str
    elements: List[Dict[str, Any]] = []
    text_content: Optional[str] = None


class FindElementRequest(BaseModel):
    """Request to find an element by description."""
    description: str
    page_context: PageContextRequest


class FindElementResponse(BaseModel):
    """Found element response."""
    found: bool
    element: Optional[Dict[str, Any]] = None
    confidence: float = 0.0
    fallback_used: bool = False


class PlanActionsRequest(BaseModel):
    """Request to plan actions for a goal."""
    goal: str
    page_context: PageContextRequest


class PlanActionsResponse(BaseModel):
    """Planned actions response."""
    actions: List[Dict[str, Any]]
    step_count: int


class ExecuteActionRequest(BaseModel):
    """Request to prepare action execution."""
    action: str  # BrowserAction value
    element_description: str
    element_selector: Optional[str] = None
    element_xpath: Optional[str] = None
    value: Optional[str] = None


class ExecuteActionResponse(BaseModel):
    """Action execution result."""
    success: bool
    action_payload: Dict[str, Any]
    error: Optional[str] = None


# =============================================================================
# API Endpoints
# =============================================================================

@router.post("/find-element", response_model=FindElementResponse)
async def find_element(request: FindElementRequest):
    """Find an element on the page using natural language description.
    
    The browser extension sends the page context and element description,
    and this endpoint uses LLM to identify the matching element.
    
    Example descriptions:
    - "the blue submit button"
    - "login link in the navigation"
    - "search input field"
    - "the main article heading"
    """
    page_context = PageContext(
        url=request.page_context.url,
        title=request.page_context.title,
        elements=request.page_context.elements,
        text_content=request.page_context.text_content or ""
    )
    
    element = await agent_browser.find_element(
        description=request.description,
        page_context=page_context
    )
    
    if element:
        return FindElementResponse(
            found=True,
            element=element.to_dict(),
            confidence=element.confidence,
            fallback_used=element.confidence < 0.8
        )
    
    return FindElementResponse(
        found=False,
        element=None,
        confidence=0.0,
        fallback_used=True
    )


@router.post("/plan-actions", response_model=PlanActionsResponse)
async def plan_actions(request: PlanActionsRequest):
    """Plan a sequence of browser actions to achieve a goal.
    
    Given a user goal and current page context, returns a sequence
    of actions to perform.
    
    Example goals:
    - "Log into the website"
    - "Search for 'machine learning'"
    - "Add item to cart and checkout"
    - "Extract all article titles"
    """
    page_context = PageContext(
        url=request.page_context.url,
        title=request.page_context.title,
        elements=request.page_context.elements,
        text_content=request.page_context.text_content or ""
    )
    
    actions = await agent_browser.plan_actions(
        goal=request.goal,
        page_context=page_context
    )
    
    return PlanActionsResponse(
        actions=actions,
        step_count=len(actions)
    )


@router.post("/prepare-action", response_model=ExecuteActionResponse)
async def prepare_action(request: ExecuteActionRequest):
    """Prepare an action for execution by the browser extension.
    
    This creates the action payload that the extension will use
    to actually perform the action in the browser.
    """
    try:
        action = BrowserAction(request.action)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action: {request.action}. Valid: {[a.value for a in BrowserAction]}"
        )
    
    element = SemanticElement(
        description=request.element_description,
        element_type=agent_browser._infer_element_type({"tag": "unknown"}),
        selector=request.element_selector,
        xpath=request.element_xpath
    )
    
    result = await agent_browser.execute_action(
        action=action,
        element=element,
        value=request.value
    )
    
    return ExecuteActionResponse(
        success=result.success,
        action_payload=result.result_data,
        error=result.error
    )


@router.get("/actions")
async def list_available_actions():
    """List all available browser actions."""
    return {
        "actions": [
            {"name": a.value, "description": _get_action_description(a)}
            for a in BrowserAction
        ]
    }


def _get_action_description(action: BrowserAction) -> str:
    """Get description for a browser action."""
    descriptions = {
        BrowserAction.CLICK: "Click on an element",
        BrowserAction.TYPE: "Type text into an input field",
        BrowserAction.SELECT: "Select an option from a dropdown",
        BrowserAction.SCROLL_TO: "Scroll the element into view",
        BrowserAction.HOVER: "Hover over an element",
        BrowserAction.EXTRACT_TEXT: "Extract text content from element",
        BrowserAction.EXTRACT_ATTRIBUTE: "Extract an attribute value",
        BrowserAction.WAIT_FOR: "Wait for element to appear",
        BrowserAction.SCREENSHOT: "Take screenshot of element"
    }
    return descriptions.get(action, "Unknown action")
