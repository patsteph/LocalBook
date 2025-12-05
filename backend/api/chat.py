"""Chat API endpoints"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
from services.rag_engine import rag_service
import json

router = APIRouter()


class ChatQuery(BaseModel):
    """Chat query request - matches frontend ChatQuery interface"""
    notebook_id: str
    question: str  # Frontend uses 'question', not 'query'
    source_ids: Optional[List[str]] = None
    top_k: Optional[int] = 5
    enable_web_search: Optional[bool] = False
    llm_provider: Optional[str] = None


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


@router.post("/query", response_model=ChatResponse)
async def query(chat_query: ChatQuery):
    """Query the RAG system"""
    try:
        result = await rag_service.query(
            notebook_id=chat_query.notebook_id,
            question=chat_query.question,
            source_ids=chat_query.source_ids,
            top_k=chat_query.top_k or 5,
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
    
    async def generate():
        try:
            async for chunk in rag_service.query_stream(
                notebook_id=chat_query.notebook_id,
                question=chat_query.question,
                source_ids=chat_query.source_ids,
                top_k=chat_query.top_k or 5,
                llm_provider=chat_query.llm_provider
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


@router.get("/suggested-questions/{notebook_id}")
async def get_suggested_questions(notebook_id: str):
    """Get suggested questions for a notebook"""
    try:
        questions = await rag_service.get_suggested_questions(notebook_id)
        return {"questions": questions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
