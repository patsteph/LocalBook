"""Content Generation API endpoints - Text-based skill outputs

Uses professional-grade templates from output_templates.py to ensure
world-class document quality across all output types.
"""
import logging
import traceback
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import json

logger = logging.getLogger(__name__)

from storage.skills_store import skills_store
from storage.source_store import source_store
from storage.content_store import content_store
from services.rag_engine import rag_engine
from services.output_templates import build_document_prompt, DOCUMENT_TEMPLATES

router = APIRouter()


class ContentGenerateRequest(BaseModel):
    """Request model for content generation"""
    notebook_id: str
    skill_id: str
    topic: Optional[str] = None
    style: Optional[str] = "professional"  # Output style: professional, casual, academic, etc.


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
        
        # Build prompt based on skill using professional templates
        skill_name = skill.get("name", "Content")
        topic_focus = request.topic or "the main topics and insights"
        
        # Use professional template if available, otherwise fall back to skill's own prompt
        if request.skill_id in DOCUMENT_TEMPLATES:
            template_system, template_format = build_document_prompt(
                request.skill_id, 
                topic_focus, 
                request.style or "professional",
                len(content_parts)
            )
            system_prompt = f"""{template_system}

{template_format}

FOCUS: {topic_focus}

CRITICAL: Use ONLY the provided source content. Synthesize across multiple sources.
Do not make up information. Attribute insights to specific sources where possible."""
        else:
            # Fallback to skill's own prompt for custom skills
            skill_prompt = skill.get("system_prompt", "")
            format_instructions = _get_format_instructions(request.skill_id)
            style_instructions = _get_style_instructions(request.style)
            
            system_prompt = f"""{skill_prompt}

{format_instructions}

{style_instructions}

Focus on: {topic_focus}

Use ONLY the provided source content. Do not make up information."""

        user_prompt = f"""Based on the following {len(content_parts)} source document(s), create a world-class {skill_name}:

{context[:12000]}

Generate the {skill_name} now, ensuring you synthesize insights across ALL sources:"""

        # Use template-specific token limit for thorough generation
        template = DOCUMENT_TEMPLATES.get(request.skill_id)
        doc_num_predict = template.recommended_tokens if template else 2000
        
        # Generate content
        content = await rag_engine._call_ollama(system_prompt, user_prompt, num_predict=doc_num_predict)
        
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
        logger.error(f"[STUDIO] Content generation failed for skill={request.skill_id}, notebook={request.notebook_id}")
        logger.error(f"[STUDIO] Error: {type(e).__name__}: {str(e)}")
        logger.error(f"[STUDIO] Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Content generation failed: {str(e)}")


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
        
        skill_name = skill.get("name", "Content")
        topic_focus = request.topic or "the main topics and insights"
        
        # Use professional template if available
        if request.skill_id in DOCUMENT_TEMPLATES:
            template_system, template_format = build_document_prompt(
                request.skill_id, 
                topic_focus, 
                request.style or "professional",
                len(content_parts)
            )
            system_prompt = f"""{template_system}

{template_format}

FOCUS: {topic_focus}

CRITICAL: Use ONLY the provided source content. Synthesize across multiple sources.
Do not make up information. Attribute insights to specific sources where possible."""
        else:
            skill_prompt = skill.get("system_prompt", "")
            format_instructions = _get_format_instructions(request.skill_id)
            style_instructions = _get_style_instructions(request.style)
            
            system_prompt = f"""{skill_prompt}

{format_instructions}

{style_instructions}

Focus on: {topic_focus}

Use ONLY the provided source content. Do not make up information."""

        user_prompt = f"""Based on the following {len(content_parts)} source document(s), create a world-class {skill_name}:

{context[:12000]}

Generate the {skill_name} now, ensuring you synthesize insights across ALL sources:"""

        # Use template-specific token limit for thorough generation
        template = DOCUMENT_TEMPLATES.get(request.skill_id)
        doc_num_predict = template.recommended_tokens if template else 2000

        async def stream_generator():
            async for chunk in rag_engine._stream_ollama(system_prompt, user_prompt, num_predict=doc_num_predict):
                yield f"data: {json.dumps({'content': chunk})}\n\n"
            yield "data: [DONE]\n\n"
        
        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[STUDIO] Content streaming failed for skill={request.skill_id}, notebook={request.notebook_id}")
        logger.error(f"[STUDIO] Error: {type(e).__name__}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Content streaming failed: {str(e)}")


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


def _get_style_instructions(style: str) -> str:
    """Get writing style instructions"""
    styles = {
        "professional": "Write in a professional, business-appropriate tone. Be clear, concise, and authoritative.",
        "casual": "Write in a friendly, conversational tone. Be approachable and easy to read.",
        "academic": "Write in a formal academic style. Be precise, well-structured, and cite sources appropriately.",
        "technical": "Write in a technical style for expert audiences. Include specific details and use domain terminology.",
        "creative": "Write in an engaging, creative style. Use vivid language and compelling narratives.",
        "concise": "Write in an extremely concise style. Minimize words while maximizing information density.",
    }
    
    return styles.get(style, styles["professional"])
