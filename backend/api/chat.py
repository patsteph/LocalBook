"""Chat API endpoints"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
from services.rag_engine import rag_engine
from services.query_orchestrator import get_orchestrator
import json

router = APIRouter()


class ChatQuery(BaseModel):
    """Chat query request - matches frontend ChatQuery interface"""
    notebook_id: str
    question: str  # Frontend uses 'question', not 'query'
    source_ids: Optional[List[str]] = None
    top_k: Optional[int] = 4  # Reduced from 5 for faster LLM response
    enable_web_search: Optional[bool] = False
    llm_provider: Optional[str] = None
    deep_think: Optional[bool] = False  # Enable Deep Think mode with chain-of-thought reasoning
    use_orchestrator: Optional[bool] = True  # v0.60: Auto-detect complex queries and decompose


class WebSource(BaseModel):
    """Web search result source"""
    title: str
    snippet: str
    url: str


class Citation(BaseModel):
    """Citation model - matches frontend Citation interface"""
    number: int
    source_id: str
    filename: str  # Frontend expects 'filename', not 'source_title'
    chunk_index: int
    text: str
    snippet: str  # Short preview of the text
    page: Optional[int] = None
    confidence: float = 0.0
    confidence_level: str = "medium"  # 'high', 'medium', 'low'


class ChatResponse(BaseModel):
    """Chat response - matches frontend ChatResponse interface"""
    answer: str
    citations: List[Citation]
    sources: List[str]
    web_sources: Optional[List[WebSource]] = None
    follow_up_questions: Optional[List[str]] = None
    low_confidence: Optional[bool] = False  # True when < 3 citations found
    memory_used: Optional[List[str]] = None  # Types of memory used: "core_context", "retrieved_memories"
    memory_context_summary: Optional[str] = None  # Brief summary of memory context used


@router.post("/query", response_model=ChatResponse)
async def query(chat_query: ChatQuery):
    """Query the RAG system
    
    v0.60: Automatically detects complex queries and uses orchestrator for decomposition.
    """
    # Clear visual cache when new question is asked
    from services.visual_cache import visual_cache
    await visual_cache.clear_notebook(chat_query.notebook_id)
    
    try:
        # v0.60: Use orchestrator for complex query detection and decomposition
        if chat_query.use_orchestrator:
            orchestrator = get_orchestrator(rag_engine)
            complexity = orchestrator.classify_complexity(chat_query.question)
            
            if complexity == 'complex':
                # Use full orchestration for complex queries
                result = await orchestrator.process(
                    query=chat_query.question,
                    notebook_id=chat_query.notebook_id,
                    llm_provider=chat_query.llm_provider or "ollama"
                )
                return result
        
        # Standard path for simple/moderate queries
        result = await rag_engine.query(
            notebook_id=chat_query.notebook_id,
            question=chat_query.question,
            source_ids=chat_query.source_ids,
            top_k=chat_query.top_k or 4,
            enable_web_search=chat_query.enable_web_search,
            llm_provider=chat_query.llm_provider
        )
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query/stream")
async def query_stream(chat_query: ChatQuery):
    """Query the RAG system with streaming response"""
    
    # CRITICAL: Clear visual cache for this notebook when new question is asked
    # This prevents stale visuals from a previous question being shown
    from services.visual_cache import visual_cache
    cleared = await visual_cache.clear_notebook(chat_query.notebook_id)
    if cleared > 0:
        print(f"[Chat] Cleared {cleared} stale visual cache entries for notebook {chat_query.notebook_id}")
    
    async def generate():
        try:
            async for chunk in rag_engine.query_stream(
                notebook_id=chat_query.notebook_id,
                question=chat_query.question,
                source_ids=chat_query.source_ids,
                top_k=chat_query.top_k or 4,
                llm_provider=chat_query.llm_provider,
                deep_think=chat_query.deep_think or False
            ):
                yield f"data: {json.dumps(chunk)}\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


class ChatHistoryMessage(BaseModel):
    """Single message in chat history"""
    role: str  # "user" or "assistant"
    content: str


class ContextChatQuery(BaseModel):
    """Chat query with injected page context - for extension use"""
    notebook_id: str
    question: str
    page_context: Optional[dict] = None  # {title, summary, key_points, key_concepts}
    chat_history: Optional[List[ChatHistoryMessage]] = None  # Previous messages for context
    enable_web_search: Optional[bool] = False


@router.post("/query-with-context")
async def query_with_context(request: ContextChatQuery):
    """Chat endpoint that accepts injected page context.
    
    Best practices implemented:
    1. Context injection - page summary/content injected directly
    2. Conversation history - previous messages included for continuity
    3. Fallback handling - graceful responses when context is limited
    4. Query understanding - LLM understands the browsing context
    """
    try:
        # Build context-enriched prompt
        context_parts = []
        
        # Add page context if provided
        if request.page_context:
            pc = request.page_context
            context_parts.append(f"[PAGE TITLE: {pc.get('title', 'Unknown')}]")
            
            # Include raw content if available (for detailed Q&A)
            if pc.get('raw_content'):
                context_parts.append(f"\n[FULL ARTICLE CONTENT]\n{pc['raw_content']}\n[END ARTICLE CONTENT]")
            elif pc.get('summary'):
                # Fallback to summary if no raw content
                context_parts.append(f"\n[ARTICLE SUMMARY]\n{pc['summary']}\n[END SUMMARY]")
            
            if pc.get('key_points'):
                points = pc['key_points']
                if isinstance(points, list) and points:
                    context_parts.append("\n[KEY POINTS]")
                    for p in points:
                        context_parts.append(f"• {p}")
                    context_parts.append("[END KEY POINTS]")
            
            if pc.get('key_concepts'):
                concepts = pc['key_concepts']
                if isinstance(concepts, list) and concepts:
                    context_parts.append(f"\n[KEY CONCEPTS: {', '.join(concepts)}]")
        
        page_context_text = "\n".join(context_parts) if context_parts else ""
        
        # Build conversation history (keep last 6 messages to stay in context window)
        history_text = ""
        if request.chat_history and len(request.chat_history) > 0:
            recent_history = request.chat_history[-6:]  # Last 3 exchanges
            history_parts = []
            for msg in recent_history:
                role_label = "User" if msg.role == "user" else "Assistant"
                history_parts.append(f"{role_label}: {msg.content}")
            if history_parts:
                history_text = "\n\n=== CONVERSATION HISTORY ===\n" + "\n".join(history_parts) + "\n" + "="*50
        
        # Combine all context
        full_context = page_context_text + history_text
        
        # Determine response strategy based on available context
        has_page_context = bool(request.page_context and (request.page_context.get('raw_content') or request.page_context.get('summary')))
        
        # Use LLM to answer with full context
        if has_page_context:
            system_prompt = """You are a helpful research assistant. The user is reading a web article and asking questions about it.

INSTRUCTIONS:
1. Answer ONLY based on the article content provided below
2. Be specific and cite details from the article
3. If the information isn't in the article, say so briefly - don't speculate
4. Give direct, focused answers without preamble
5. NEVER repeat the question or include any markup from the context in your response"""
        else:
            system_prompt = """You are a helpful research assistant. The user is browsing the web and has a question.

Since there's no page content available, provide a helpful general response.
If the question seems to be about specific page content, suggest the user first summarize the page."""

        # Clean prompt structure - question is clearly separated
        user_prompt = f"""ARTICLE CONTEXT:
{full_context}

USER QUESTION: {request.question}

Provide a direct answer based on the article content above."""
        
        answer = await rag_engine._call_ollama(system_prompt, user_prompt)
        
        # Generate follow-up questions based on context
        follow_ups = None
        if has_page_context and request.page_context.get('key_concepts'):
            concepts = request.page_context['key_concepts'][:3]
            follow_ups = [f"Tell me more about {c}" for c in concepts]
        
        return ChatResponse(
            answer=answer,
            citations=[],
            sources=[],
            web_sources=None,
            follow_up_questions=follow_ups,
            low_confidence=not has_page_context
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/suggested-questions/{notebook_id}")
async def get_suggested_questions(notebook_id: str):
    """Get suggested questions for a notebook"""
    try:
        questions = await rag_engine.get_suggested_questions(notebook_id)
        return {"questions": questions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/insights/{notebook_id}")
async def get_proactive_insights(notebook_id: str, limit: int = 3):
    """Phase 4.1: Get proactive insights for a notebook.
    
    Analyzes document content to suggest interesting questions
    or observations the user might want to explore.
    """
    try:
        insights = await rag_engine.generate_proactive_insights(notebook_id, limit=limit)
        return {"insights": insights}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
