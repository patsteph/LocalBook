"""LocalBook Supervisor Agent

The main orchestrator that routes requests to specialized agents.
Uses LangGraph to manage state and flow between agents.
"""

from typing import Literal, Optional
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from agents.state import LocalBookState
from agents.tools import (
    rag_search_tool,
    web_search_tool,
    generate_document_tool,
    generate_quiz_tool,
    generate_visual_tool,
    capture_page_tool,
    summarize_page_tool,
    extract_page_metadata_tool,
)

import httpx
import json
from config import settings


INTENT_CLASSIFICATION_PROMPT = """You are an intent classifier for LocalBook, a research and learning assistant.

Classify the user's request into ONE of these intents:
- **research**: Questions about notebook content, searching sources, finding information
- **studio**: Creating documents, quizzes, visuals, audio content, summaries
- **browser**: Capturing web pages, importing content, page summarization
- **memory**: Recalling past conversations, user preferences, stored memories
- **chat**: General conversation, greetings, clarifications, off-topic

Also identify which tools might be needed.

Respond with JSON only:
{
    "intent": "research|studio|browser|memory|chat",
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation",
    "suggested_tools": ["tool1", "tool2"]
}

User query: {query}"""


async def classify_intent(query: str) -> dict:
    """Classify the intent of a user query using LLM."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.ollama_fast_model,
                "prompt": INTENT_CLASSIFICATION_PROMPT.format(query=query),
                "stream": False,
                "options": {"temperature": 0.1}
            }
        )
        
        if response.status_code == 200:
            result = response.json()
            text = result.get("response", "")
            
            # Extract JSON from response
            try:
                # Find JSON in response
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    
    # Default fallback
    return {
        "intent": "chat",
        "confidence": 0.5,
        "reasoning": "Could not classify, defaulting to chat",
        "suggested_tools": []
    }


async def supervisor_node(state: LocalBookState) -> LocalBookState:
    """Main supervisor that classifies intent and routes to appropriate agent."""
    
    # Get the latest user message
    user_query = state.get("user_query", "")
    if not user_query and state.get("messages"):
        for msg in reversed(state["messages"]):
            if isinstance(msg, HumanMessage):
                user_query = msg.content
                break
    
    # Classify intent
    classification = await classify_intent(user_query)
    
    return {
        **state,
        "intent": classification.get("intent", "chat"),
        "user_query": user_query,
    }


async def research_node(state: LocalBookState) -> LocalBookState:
    """Handle research queries using RAG and web search."""
    from services.rag_engine import rag_engine
    
    query = state.get("user_query", "")
    notebook_id = state.get("notebook_id")
    
    rag_results = []
    sources_used = []
    
    if notebook_id:
        # Search notebook sources
        results = await rag_engine.search(
            notebook_id=notebook_id,
            query=query,
            top_k=5
        )
        rag_results = results
        sources_used = list(set(r.get("source_id", "") for r in results if r.get("source_id")))
    
    # Generate response using RAG context
    context = "\n\n".join([
        f"Source: {r.get('filename', 'Unknown')}\n{r.get('content', '')}"
        for r in rag_results[:5]
    ])
    
    system_prompt = """You are a research assistant. Answer the user's question based on the provided sources.
Always cite which source your information comes from. If sources don't contain relevant info, say so."""
    
    user_prompt = f"Question: {query}\n\nSources:\n{context}" if context else f"Question: {query}"
    
    response = await rag_engine._call_ollama(system_prompt, user_prompt)
    
    return {
        **state,
        "rag_results": rag_results,
        "rag_sources_used": sources_used,
        "final_response": response,
        "current_agent": "research"
    }


async def studio_node(state: LocalBookState) -> LocalBookState:
    """Handle content generation requests."""
    from services.rag_engine import rag_engine
    
    query = state.get("user_query", "").lower()
    notebook_id = state.get("notebook_id")
    
    # Determine content type from query
    content_type = "document"
    doc_type = "summary"
    
    if "quiz" in query:
        content_type = "quiz"
    elif "visual" in query or "diagram" in query or "mindmap" in query or "flowchart" in query:
        content_type = "visual"
    elif "briefing" in query or "executive" in query:
        doc_type = "briefing"
    elif "study guide" in query or "study" in query:
        doc_type = "study_guide"
    elif "faq" in query or "questions" in query:
        doc_type = "faq"
    elif "deep dive" in query or "analysis" in query:
        doc_type = "deep_dive"
    elif "explain" in query or "simple" in query:
        doc_type = "explain"
    elif "podcast" in query or "audio" in query:
        doc_type = "podcast_script"
    
    generated_content = None
    
    if content_type == "quiz":
        result = await generate_quiz_tool.ainvoke({
            "notebook_id": notebook_id,
            "num_questions": 5,
            "difficulty": "medium"
        })
        generated_content = result
    elif content_type == "visual":
        diagram_types = ["mindmap"]
        if "flowchart" in query:
            diagram_types = ["flowchart"]
        elif "timeline" in query:
            diagram_types = ["timeline"]
        result = await generate_visual_tool.ainvoke({
            "notebook_id": notebook_id,
            "diagram_types": diagram_types
        })
        generated_content = result
    else:
        result = await generate_document_tool.ainvoke({
            "notebook_id": notebook_id,
            "document_type": doc_type,
            "topic": None,
            "style": "professional"
        })
        generated_content = result
    
    response = f"Generated {content_type}: {doc_type if content_type == 'document' else content_type}"
    if generated_content and generated_content.get("content"):
        response = generated_content["content"]
    elif generated_content and generated_content.get("questions"):
        response = f"Generated {len(generated_content['questions'])} quiz questions"
    elif generated_content and generated_content.get("diagrams"):
        response = f"Generated {len(generated_content['diagrams'])} diagrams"
    
    return {
        **state,
        "generated_content": generated_content,
        "content_type": content_type,
        "final_response": response,
        "current_agent": "studio"
    }


async def browser_node(state: LocalBookState) -> LocalBookState:
    """Handle browser capture and summarization requests."""
    
    pending = state.get("pending_captures", [])
    
    if pending:
        # Process pending captures
        results = []
        for capture in pending:
            result = await capture_page_tool.ainvoke({
                "url": capture.get("url", ""),
                "title": capture.get("title", ""),
                "content": capture.get("content", ""),
                "notebook_id": capture.get("notebook_id", state.get("notebook_id", "")),
                "meta_tags": capture.get("meta_tags")
            })
            results.append(result)
        
        response = f"Captured {len(results)} pages to notebook"
    else:
        response = "No pending captures to process"
    
    return {
        **state,
        "pending_captures": [],
        "final_response": response,
        "current_agent": "browser"
    }


async def chat_node(state: LocalBookState) -> LocalBookState:
    """Handle general chat/conversation."""
    from services.rag_engine import rag_engine
    
    query = state.get("user_query", "")
    
    system_prompt = """You are LocalBook, a helpful research and learning assistant. 
You help users organize their research, create study materials, and learn effectively.
Be friendly, concise, and helpful."""
    
    response = await rag_engine._call_ollama(system_prompt, query)
    
    return {
        **state,
        "final_response": response,
        "current_agent": "chat"
    }


def route_by_intent(state: LocalBookState) -> str:
    """Route to the appropriate agent based on classified intent."""
    intent = state.get("intent", "chat")
    
    routing = {
        "research": "research",
        "studio": "studio", 
        "browser": "browser",
        "memory": "chat",  # Memory handled by chat for now
        "chat": "chat"
    }
    
    return routing.get(intent, "chat")


def create_supervisor_graph() -> StateGraph:
    """Create the main supervisor graph with all agents."""
    
    # Build the graph
    workflow = StateGraph(LocalBookState)
    
    # Add nodes
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("research", research_node)
    workflow.add_node("studio", studio_node)
    workflow.add_node("browser", browser_node)
    workflow.add_node("chat", chat_node)
    
    # Set entry point
    workflow.set_entry_point("supervisor")
    
    # Add conditional routing from supervisor
    workflow.add_conditional_edges(
        "supervisor",
        route_by_intent,
        {
            "research": "research",
            "studio": "studio",
            "browser": "browser",
            "chat": "chat"
        }
    )
    
    # All agents go to END
    workflow.add_edge("research", END)
    workflow.add_edge("studio", END)
    workflow.add_edge("browser", END)
    workflow.add_edge("chat", END)
    
    return workflow.compile()


# Create singleton instance
supervisor_graph = None

def get_supervisor():
    """Get or create the supervisor graph."""
    global supervisor_graph
    if supervisor_graph is None:
        supervisor_graph = create_supervisor_graph()
    return supervisor_graph


async def run_agent(
    query: str,
    notebook_id: Optional[str] = None,
    pending_captures: Optional[list] = None
) -> dict:
    """Run the agent system with a query.
    
    Args:
        query: User's query/request
        notebook_id: Optional notebook context
        pending_captures: Optional list of pages to capture
        
    Returns:
        Dictionary with response and metadata
    """
    graph = get_supervisor()
    
    initial_state: LocalBookState = {
        "messages": [HumanMessage(content=query)],
        "notebook_id": notebook_id,
        "user_query": query,
        "intent": None,
        "current_agent": None,
        "rag_results": [],
        "rag_sources_used": [],
        "web_results": [],
        "generated_content": None,
        "content_type": None,
        "pending_captures": pending_captures or [],
        "page_summary": None,
        "page_metadata": None,
        "relevant_memories": [],
        "should_remember": False,
        "final_response": None,
        "citations": []
    }
    
    result = await graph.ainvoke(initial_state)
    
    return {
        "response": result.get("final_response", ""),
        "intent": result.get("intent"),
        "agent": result.get("current_agent"),
        "generated_content": result.get("generated_content"),
        "rag_results": result.get("rag_results", []),
        "citations": result.get("citations", [])
    }
