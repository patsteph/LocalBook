"""Agent Browser Service

v1.1.0: AI-native browser automation using semantic element references.
Based on Vercel's Agent Browser concept - uses natural language to identify
and interact with page elements instead of fragile CSS/XPath selectors.

Key Features:
- Semantic element identification (e.g., "the login button", "search input")
- Auto-adaptation to page changes
- LLM-powered element matching
- Integration with browser extension
"""

import re
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple
from enum import Enum
import httpx

from config import settings


class ElementType(str, Enum):
    """Types of interactive elements."""
    BUTTON = "button"
    LINK = "link"
    INPUT = "input"
    SELECT = "select"
    CHECKBOX = "checkbox"
    TEXTAREA = "textarea"
    IMAGE = "image"
    TEXT = "text"
    CONTAINER = "container"
    UNKNOWN = "unknown"


@dataclass
class SemanticElement:
    """A page element identified by semantic description."""
    description: str  # Natural language description
    element_type: ElementType
    selector: Optional[str] = None  # CSS selector (fallback)
    xpath: Optional[str] = None  # XPath (fallback)
    text_content: Optional[str] = None
    attributes: Dict[str, str] = field(default_factory=dict)
    position: Optional[Dict[str, int]] = None  # {x, y, width, height}
    confidence: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "element_type": self.element_type.value,
            "selector": self.selector,
            "xpath": self.xpath,
            "text_content": self.text_content,
            "attributes": self.attributes,
            "position": self.position,
            "confidence": self.confidence
        }


@dataclass
class PageContext:
    """Context about the current page for element matching."""
    url: str
    title: str
    elements: List[Dict[str, Any]] = field(default_factory=list)  # Raw DOM elements
    text_content: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "element_count": len(self.elements),
            "text_preview": self.text_content[:500] if self.text_content else ""
        }


class BrowserAction(str, Enum):
    """Actions that can be performed on elements."""
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    SCROLL_TO = "scroll_to"
    HOVER = "hover"
    EXTRACT_TEXT = "extract_text"
    EXTRACT_ATTRIBUTE = "extract_attribute"
    WAIT_FOR = "wait_for"
    SCREENSHOT = "screenshot"


@dataclass
class ActionResult:
    """Result of a browser action."""
    success: bool
    action: BrowserAction
    element: Optional[SemanticElement] = None
    result_data: Optional[Any] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action.value,
            "element": self.element.to_dict() if self.element else None,
            "result_data": self.result_data,
            "error": self.error
        }


class AgentBrowser:
    """AI-native browser automation service."""
    
    ELEMENT_MATCH_PROMPT = """You are an expert at identifying web page elements from natural language descriptions.

Given a page context and a natural language description of an element, identify the most likely matching element.

Page Context:
- URL: {url}
- Title: {title}
- Available Elements (sample):
{elements_sample}

User wants to find: "{description}"

Analyze the elements and return JSON with:
{{
    "matched_index": <index of best matching element, or -1 if none>,
    "confidence": <0.0-1.0>,
    "reasoning": "<brief explanation>",
    "alternative_indices": [<other possible matches>]
}}

Only return the JSON, no other text."""

    ACTION_PLAN_PROMPT = """You are an expert at planning browser automation actions.

Given a user's goal and the current page context, plan a sequence of actions.

Page Context:
- URL: {url}
- Title: {title}

User Goal: "{goal}"

Available actions: click, type, select, scroll_to, hover, extract_text, wait_for

Return a JSON array of action steps:
[
    {{"action": "click", "target": "natural language description of element"}},
    {{"action": "type", "target": "input field description", "value": "text to type"}},
    ...
]

Only return the JSON array, no other text."""

    def __init__(self):
        self._ollama_url = settings.ollama_base_url
        self._model = settings.ollama_fast_model  # Use fast model for element matching
    
    async def find_element(
        self,
        description: str,
        page_context: PageContext
    ) -> Optional[SemanticElement]:
        """Find an element on the page using natural language description.
        
        Args:
            description: Natural language description like "the blue submit button"
            page_context: Context about the current page including DOM elements
            
        Returns:
            SemanticElement if found, None otherwise
        """
        if not page_context.elements:
            return None
        
        # Prepare elements sample for LLM (limit to avoid context overflow)
        elements_sample = self._format_elements_for_prompt(page_context.elements[:50])
        
        prompt = self.ELEMENT_MATCH_PROMPT.format(
            url=page_context.url,
            title=page_context.title,
            elements_sample=elements_sample,
            description=description
        )
        
        try:
            response = await self._call_llm(prompt)
            result = self._parse_json_response(response)
            
            if result and result.get("matched_index", -1) >= 0:
                idx = result["matched_index"]
                if idx < len(page_context.elements):
                    elem = page_context.elements[idx]
                    return SemanticElement(
                        description=description,
                        element_type=self._infer_element_type(elem),
                        selector=elem.get("selector"),
                        xpath=elem.get("xpath"),
                        text_content=elem.get("text", "")[:200],
                        attributes=elem.get("attributes", {}),
                        position=elem.get("position"),
                        confidence=result.get("confidence", 0.5)
                    )
        except Exception as e:
            print(f"[AgentBrowser] Element matching failed: {e}")
        
        # Fallback: simple text matching
        return self._fallback_find_element(description, page_context)
    
    def _fallback_find_element(
        self,
        description: str,
        page_context: PageContext
    ) -> Optional[SemanticElement]:
        """Simple fallback element finding using text matching."""
        desc_lower = description.lower()
        keywords = desc_lower.split()
        
        best_match = None
        best_score = 0
        
        for elem in page_context.elements:
            score = 0
            elem_text = (elem.get("text", "") + " " + elem.get("aria-label", "")).lower()
            elem_type = elem.get("tag", "").lower()
            
            # Check keyword matches
            for keyword in keywords:
                if keyword in elem_text:
                    score += 2
                if keyword in elem_type:
                    score += 1
            
            # Bonus for interactive elements when looking for buttons/links
            if any(w in desc_lower for w in ["button", "click", "submit"]):
                if elem_type in ["button", "a", "input"]:
                    score += 2
            
            if any(w in desc_lower for w in ["input", "field", "text", "enter"]):
                if elem_type in ["input", "textarea"]:
                    score += 2
            
            if score > best_score:
                best_score = score
                best_match = elem
        
        if best_match and best_score >= 2:
            return SemanticElement(
                description=description,
                element_type=self._infer_element_type(best_match),
                selector=best_match.get("selector"),
                xpath=best_match.get("xpath"),
                text_content=best_match.get("text", "")[:200],
                attributes=best_match.get("attributes", {}),
                position=best_match.get("position"),
                confidence=min(best_score / 10, 0.8)  # Cap confidence for fallback
            )
        
        return None
    
    async def plan_actions(
        self,
        goal: str,
        page_context: PageContext
    ) -> List[Dict[str, Any]]:
        """Plan a sequence of actions to achieve a goal.
        
        Args:
            goal: User's goal in natural language
            page_context: Current page context
            
        Returns:
            List of action steps
        """
        prompt = self.ACTION_PLAN_PROMPT.format(
            url=page_context.url,
            title=page_context.title,
            goal=goal
        )
        
        try:
            response = await self._call_llm(prompt)
            actions = self._parse_json_response(response)
            if isinstance(actions, list):
                return actions
        except Exception as e:
            print(f"[AgentBrowser] Action planning failed: {e}")
        
        return []
    
    async def execute_action(
        self,
        action: BrowserAction,
        element: SemanticElement,
        value: Optional[str] = None,
        page_context: Optional[PageContext] = None
    ) -> ActionResult:
        """Execute an action on an element.
        
        Note: Actual execution happens in the browser extension.
        This method prepares the action for the extension.
        
        Args:
            action: Action to perform
            element: Target element
            value: Optional value for type/select actions
            page_context: Current page context for re-finding element if needed
            
        Returns:
            ActionResult with prepared action data
        """
        # Prepare action payload for browser extension
        action_payload = {
            "action": action.value,
            "selector": element.selector,
            "xpath": element.xpath,
            "description": element.description,
            "value": value
        }
        
        return ActionResult(
            success=True,
            action=action,
            element=element,
            result_data=action_payload
        )
    
    def _format_elements_for_prompt(self, elements: List[Dict]) -> str:
        """Format elements for LLM prompt."""
        lines = []
        for i, elem in enumerate(elements):
            tag = elem.get("tag", "?")
            text = (elem.get("text", "") or "")[:50]
            attrs = elem.get("attributes", {})
            
            attr_str = ", ".join(f'{k}="{v}"' for k, v in list(attrs.items())[:3])
            
            line = f"[{i}] <{tag}"
            if attr_str:
                line += f" {attr_str}"
            line += f">"
            if text:
                line += f" \"{text}\""
            
            lines.append(line)
        
        return "\n".join(lines)
    
    def _infer_element_type(self, elem: Dict) -> ElementType:
        """Infer element type from tag and attributes."""
        tag = elem.get("tag", "").lower()
        input_type = elem.get("attributes", {}).get("type", "").lower()
        
        if tag == "button" or (tag == "input" and input_type == "button"):
            return ElementType.BUTTON
        elif tag == "a":
            return ElementType.LINK
        elif tag == "input":
            if input_type == "checkbox":
                return ElementType.CHECKBOX
            return ElementType.INPUT
        elif tag == "select":
            return ElementType.SELECT
        elif tag == "textarea":
            return ElementType.TEXTAREA
        elif tag == "img":
            return ElementType.IMAGE
        elif tag in ["p", "span", "h1", "h2", "h3", "h4", "h5", "h6", "label"]:
            return ElementType.TEXT
        elif tag in ["div", "section", "article", "main", "nav", "header", "footer"]:
            return ElementType.CONTAINER
        
        return ElementType.UNKNOWN
    
    async def _call_llm(self, prompt: str) -> str:
        """Call Ollama LLM."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._ollama_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1}
                }
            )
            
            if response.status_code == 200:
                return response.json().get("response", "")
            return ""
    
    def _parse_json_response(self, response: str) -> Optional[Any]:
        """Parse JSON from LLM response."""
        try:
            # Try to find JSON in response
            response = response.strip()
            
            # Handle markdown code blocks
            if "```json" in response:
                start = response.find("```json") + 7
                end = response.find("```", start)
                response = response[start:end].strip()
            elif "```" in response:
                start = response.find("```") + 3
                end = response.find("```", start)
                response = response[start:end].strip()
            
            # Find JSON object or array
            if response.startswith("["):
                end = response.rfind("]") + 1
                response = response[:end]
            elif response.startswith("{"):
                end = response.rfind("}") + 1
                response = response[:end]
            
            return json.loads(response)
        except json.JSONDecodeError:
            return None


# Singleton instance
agent_browser = AgentBrowser()
