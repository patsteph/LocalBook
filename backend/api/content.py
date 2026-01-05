"""Content Generation API endpoints - Text-based skill outputs"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import json

from storage.skills_store import skills_store
from storage.source_store import source_store
from storage.content_store import content_store
from services.rag_engine import rag_engine

router = APIRouter()


class ContentGenerateRequest(BaseModel):
    """Request model for content generation"""
    notebook_id: str
    skill_id: str
    topic: Optional[str] = None


class ContentGenerateResponse(BaseModel):
    """Response model for content generation"""
    notebook_id: str
    skill_id: str
    skill_name: str
    content: str
    sources_used: int


class ContentExportRequest(BaseModel):
    """Request for exporting content"""
    content: str
    title: str
    format: str = "markdown"  # markdown or text


@router.post("/generate", response_model=ContentGenerateResponse)
async def generate_content(request: ContentGenerateRequest):
    """Generate text content using a skill with RAG context"""
    try:
        # Get skill
        skill = await skills_store.get(request.skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        
        # Get sources content
        sources = await source_store.list(request.notebook_id)
        if not sources:
            raise HTTPException(status_code=400, detail="No sources in notebook")
        
        content_parts = []
        for source in sources[:10]:  # Use up to 10 sources
            source_content = await source_store.get_content(request.notebook_id, source["id"])
            if source_content and source_content.get("content"):
                content_parts.append(
                    f"## Source: {source.get('filename', 'Unknown')}\n{source_content['content'][:4000]}"
                )
        
        context = "\n\n---\n\n".join(content_parts)
        
        # Build prompt based on skill
        skill_prompt = skill.get("system_prompt", "")
        skill_name = skill.get("name", "Content")
        topic_focus = request.topic or "the main topics and insights"
        
        # Different formatting based on skill type
        format_instructions = _get_format_instructions(request.skill_id)
        
        system_prompt = f"""{skill_prompt}

{format_instructions}

Focus on: {topic_focus}

Use ONLY the provided source content. Do not make up information."""

        user_prompt = f"""Based on the following research content, create a {skill_name}:

{context[:12000]}

Generate the {skill_name}:"""

        # Generate content
        content = await rag_engine._call_ollama(system_prompt, user_prompt)
        
        # Save to content store for persistence
        await content_store.create(
            notebook_id=request.notebook_id,
            skill_id=request.skill_id,
            skill_name=skill_name,
            content=content,
            topic=request.topic,
            sources_used=len(content_parts)
        )
        
        return ContentGenerateResponse(
            notebook_id=request.notebook_id,
            skill_id=request.skill_id,
            skill_name=skill_name,
            content=content,
            sources_used=len(content_parts)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate/stream")
async def generate_content_stream(request: ContentGenerateRequest):
    """Stream content generation for real-time display"""
    try:
        # Get skill
        skill = await skills_store.get(request.skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        
        # Get sources content
        sources = await source_store.list(request.notebook_id)
        if not sources:
            raise HTTPException(status_code=400, detail="No sources in notebook")
        
        content_parts = []
        for source in sources[:10]:
            source_content = await source_store.get_content(request.notebook_id, source["id"])
            if source_content and source_content.get("content"):
                content_parts.append(
                    f"## Source: {source.get('filename', 'Unknown')}\n{source_content['content'][:4000]}"
                )
        
        context = "\n\n---\n\n".join(content_parts)
        
        skill_prompt = skill.get("system_prompt", "")
        skill_name = skill.get("name", "Content")
        topic_focus = request.topic or "the main topics and insights"
        format_instructions = _get_format_instructions(request.skill_id)
        
        system_prompt = f"""{skill_prompt}

{format_instructions}

Focus on: {topic_focus}

Use ONLY the provided source content. Do not make up information."""

        user_prompt = f"""Based on the following research content, create a {skill_name}:

{context[:12000]}

Generate the {skill_name}:"""

        async def stream_generator():
            async for chunk in rag_engine._stream_ollama(system_prompt, user_prompt):
                yield f"data: {json.dumps({'content': chunk})}\n\n"
            yield "data: [DONE]\n\n"
        
        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list/{notebook_id}")
async def list_content_generations(notebook_id: str):
    """List all content generations for a notebook"""
    try:
        generations = await content_store.list(notebook_id)
        return {"generations": generations}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{content_id}")
async def get_content_generation(content_id: str):
    """Get a specific content generation"""
    try:
        generation = await content_store.get(content_id)
        if not generation:
            raise HTTPException(status_code=404, detail="Content not found")
        return generation
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{content_id}")
async def delete_content_generation(content_id: str):
    """Delete a content generation"""
    try:
        deleted = await content_store.delete(content_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Content not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _get_format_instructions(skill_id: str) -> str:
    """Get formatting instructions based on skill type"""
    formats = {
        "study_guide": """Format as a structured study guide with:
- Clear section headings (use ## for main sections)
- Key concepts with definitions
- Important facts and details
- Review questions at the end
Use markdown formatting.""",
        
        "summary": """Format as a clear, well-organized summary with:
- Executive summary paragraph at the top
- Key points organized by theme
- Concise bullet points for main takeaways
Use markdown formatting.""",
        
        "faq": """Format as a FAQ document with:
- Questions in bold (use **)
- Clear, detailed answers
- Mix of basic and advanced questions
- Organized by topic
Use markdown formatting.""",
        
        "briefing": """Format as an executive briefing with:
- Executive Summary section
- Key Findings section with bullet points
- Implications section
- Recommended Actions section
Use professional, concise language. Use markdown formatting.""",
        
        "deep_dive": """Format as an in-depth analysis with:
- Introduction and context
- Detailed exploration of key themes
- Connections between ideas
- Nuances and implications
- Conclusion
Use markdown formatting with clear section headings.""",
        
        "explain": """Format as a simple explanation:
- Use everyday language
- Include helpful analogies
- Break complex ideas into simple parts
- Use examples the average person would understand
Avoid jargon and technical terms.""",
    }
    
    return formats.get(skill_id, "Format clearly with appropriate sections and markdown formatting.")
