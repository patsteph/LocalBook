"""Writing Assistant API endpoints

Provides AI-powered writing assistance with format prompting:
- Content improvement
- Format conversion (academic, blog, email, etc.)
- Summarization with style
- Expansion and elaboration
"""
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.structured_llm import structured_llm
from storage.source_store import source_store


router = APIRouter(prefix="/writing", tags=["writing"])


# =============================================================================
# Request/Response Models
# =============================================================================

class WriteRequest(BaseModel):
    content: str = Field(description="Content to work with")
    task: str = Field(
        default="improve",
        description="Task: improve, summarize, expand, rewrite, proofread"
    )
    format_style: str = Field(
        default="professional",
        description="Style: professional, academic, casual, technical, blog, email"
    )
    additional_instructions: Optional[str] = None


class WriteFromSourcesRequest(BaseModel):
    notebook_id: str
    source_ids: Optional[List[str]] = None
    task: str = Field(default="summarize")
    format_style: str = Field(default="professional")
    focus_topic: Optional[str] = None
    max_words: Optional[int] = Field(default=500, ge=50, le=2000)


class WritingResponse(BaseModel):
    content: str
    format_used: str
    word_count: int
    suggestions: List[str]


class FormatOption(BaseModel):
    value: str
    label: str
    description: str


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/formats")
async def get_available_formats() -> List[FormatOption]:
    """Get available writing format styles."""
    return [
        FormatOption(
            value="professional",
            label="Professional",
            description="Clear, formal, suitable for business communication"
        ),
        FormatOption(
            value="academic",
            label="Academic",
            description="Scholarly, with proper structure and formal tone"
        ),
        FormatOption(
            value="casual",
            label="Casual",
            description="Friendly, conversational, relaxed tone"
        ),
        FormatOption(
            value="technical",
            label="Technical",
            description="Precise, detailed, for technical audiences"
        ),
        FormatOption(
            value="blog",
            label="Blog Post",
            description="Engaging, readable, with good flow and hooks"
        ),
        FormatOption(
            value="email",
            label="Email",
            description="Concise, clear, action-oriented"
        ),
        FormatOption(
            value="bullet_points",
            label="Bullet Points",
            description="Key points in list format"
        ),
        FormatOption(
            value="executive_summary",
            label="Executive Summary",
            description="High-level overview for decision makers"
        )
    ]


@router.get("/tasks")
async def get_available_tasks():
    """Get available writing tasks."""
    return [
        {"value": "improve", "label": "Improve", "description": "Enhance clarity and flow"},
        {"value": "summarize", "label": "Summarize", "description": "Condense to key points"},
        {"value": "expand", "label": "Expand", "description": "Add detail and elaboration"},
        {"value": "rewrite", "label": "Rewrite", "description": "Complete rewrite in new style"},
        {"value": "proofread", "label": "Proofread", "description": "Fix grammar and spelling"},
        {"value": "simplify", "label": "Simplify", "description": "Make easier to understand"},
    ]


@router.post("/assist", response_model=WritingResponse)
async def assist_writing(request: WriteRequest):
    """Get AI writing assistance for provided content."""
    
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="Content is required")
    
    # Add additional instructions if provided
    content = request.content
    if request.additional_instructions:
        content = f"Additional instructions: {request.additional_instructions}\n\nContent:\n{content}"
    
    result = await structured_llm.assist_writing(
        content=content,
        task=request.task,
        format_style=request.format_style
    )
    
    return WritingResponse(
        content=result.content,
        format_used=result.format_used,
        word_count=result.word_count,
        suggestions=result.suggestions
    )


@router.post("/from-sources", response_model=WritingResponse)
async def write_from_sources(request: WriteFromSourcesRequest):
    """Generate writing based on notebook sources."""
    
    sources = await source_store.list(request.notebook_id)
    if not sources:
        raise HTTPException(status_code=404, detail="No sources found in notebook")
    
    if request.source_ids:
        sources = [s for s in sources if s.get("id") in request.source_ids]
    
    # Collect content from sources
    content_parts = []
    for source in sources[:5]:
        source_content = source.get("content", "")[:3000]
        if source_content:
            content_parts.append(f"Source: {source.get('filename', 'Unknown')}\n{source_content}")
    
    combined_content = "\n\n---\n\n".join(content_parts)
    
    if request.focus_topic:
        combined_content = f"Focus on: {request.focus_topic}\n\n{combined_content}"
    
    if request.max_words:
        combined_content = f"Target length: approximately {request.max_words} words\n\n{combined_content}"
    
    result = await structured_llm.assist_writing(
        content=combined_content,
        task=request.task,
        format_style=request.format_style
    )
    
    return WritingResponse(
        content=result.content,
        format_used=result.format_used,
        word_count=result.word_count,
        suggestions=result.suggestions
    )


@router.post("/quick-summary")
async def quick_summary(notebook_id: str, max_sentences: int = 3):
    """Generate a quick summary of notebook content."""
    
    sources = await source_store.list(notebook_id)
    if not sources:
        raise HTTPException(status_code=404, detail="No sources found")
    
    content = "\n\n".join([s.get("content", "")[:2000] for s in sources[:3]])
    
    result = await structured_llm.assist_writing(
        content=f"Summarize in {max_sentences} sentences:\n\n{content}",
        task="summarize",
        format_style="professional"
    )
    
    return {
        "notebook_id": notebook_id,
        "summary": result.content,
        "word_count": result.word_count
    }
