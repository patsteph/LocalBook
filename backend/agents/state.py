"""LocalBook Agent State Schema

Defines the state that flows through all agents in the LangGraph system.
"""

from typing import TypedDict, Annotated, Sequence, Optional, Literal
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class LocalBookState(TypedDict):
    """State schema for LocalBook agent system.
    
    This state is passed through all nodes in the agent graph and accumulates
    results from each agent's work.
    """
    
    # Core conversation state
    messages: Annotated[Sequence[BaseMessage], add_messages]
    
    # Context
    notebook_id: Optional[str]
    user_query: str
    
    # Routing
    intent: Optional[str]  # research, studio, memory, browser, chat
    current_agent: Optional[str]
    
    # RAG Results
    rag_results: list[dict]
    rag_sources_used: list[str]
    
    # Web Search Results  
    web_results: list[dict]
    
    # Generated Content
    generated_content: Optional[dict]
    content_type: Optional[str]  # document, quiz, visual, audio
    
    # Browser Extension Data
    pending_captures: list[dict]
    page_summary: Optional[str]
    page_metadata: Optional[dict]
    
    # Memory
    relevant_memories: list[dict]
    should_remember: bool
    
    # Final Response
    final_response: Optional[str]
    citations: list[dict]


class IntentClassification(TypedDict):
    """Result of intent classification."""
    intent: Literal["research", "studio", "browser", "memory", "chat"]
    confidence: float
    reasoning: str
    suggested_tools: list[str]


class CaptureRequest(TypedDict):
    """Browser capture request from extension."""
    url: str
    title: str
    content: Optional[str]
    selected_text: Optional[str]
    meta_tags: dict
    capture_type: Literal["page", "selection", "youtube", "pdf"]
    notebook_id: str


class PageMetadata(TypedDict):
    """Extracted metadata from a web page."""
    title: str
    description: Optional[str]
    author: Optional[str]
    publish_date: Optional[str]
    reading_time_minutes: int
    word_count: int
    og_image: Optional[str]
    keywords: list[str]
    key_concepts: list[str]
