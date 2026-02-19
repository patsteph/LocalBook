"""Content Generation API endpoints - Text-based skill outputs

Uses professional-grade templates from output_templates.py to ensure
world-class document quality across all output types.
"""
import logging
import re
import traceback
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import json


def _clean_llm_output(text: str) -> str:
    """Post-process LLM output: detect repetition loops and ensure clean ending.
    
    Addresses three failure modes:
    1. Sentence-level loops (same sentence repeats 3+ times)
    2. Paragraph-level loops (same paragraph block repeats)
    3. Mid-sentence cutoff (output ends abruptly)
    """
    if not text or len(text) < 100:
        return text
    
    original_len = len(text)
    
    # --- 1. Detect sentence-level repetition ---
    # Split into sentences and find repeating patterns
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) > 6:
        # Look for a repeating sentence (appears 3+ times)
        seen_count = {}
        first_repeat_idx = None
        for i, s in enumerate(sentences):
            # Normalize for comparison (strip whitespace, lowercase)
            key = s.strip().lower()[:200]
            if len(key) < 20:
                continue
            seen_count[key] = seen_count.get(key, 0) + 1
            if seen_count[key] >= 3 and first_repeat_idx is None:
                # Find where this sentence first appeared after unique content
                # Keep the first two occurrences, cut at third
                count = 0
                for j, s2 in enumerate(sentences):
                    if s2.strip().lower()[:200] == key:
                        count += 1
                        if count == 3:
                            first_repeat_idx = j
                            break
        
        if first_repeat_idx is not None and first_repeat_idx > 3:
            # Truncate at the point repetition starts (3rd occurrence)
            text = ' '.join(sentences[:first_repeat_idx]).strip()
            logger.warning(f"[PostProcess] Truncated repetitive output: "
                          f"{original_len} → {len(text)} chars "
                          f"(cut at sentence {first_repeat_idx}/{len(sentences)})")
    
    # --- 2. Detect paragraph-level loops ---
    # Split into paragraphs and check for repeated blocks
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if len(paragraphs) > 4:
        seen_paras = {}
        cut_idx = None
        for i, p in enumerate(paragraphs):
            key = p[:300].lower()
            if len(key) < 50:
                continue
            if key in seen_paras:
                # This paragraph is a repeat — if it repeats 2+ times, cut
                seen_paras[key] += 1
                if seen_paras[key] >= 2 and cut_idx is None:
                    cut_idx = i
            else:
                seen_paras[key] = 1
        
        if cut_idx is not None and cut_idx > 2:
            text = '\n\n'.join(paragraphs[:cut_idx]).strip()
            logger.warning(f"[PostProcess] Truncated paragraph loop: "
                          f"cut at paragraph {cut_idx}/{len(paragraphs)}")
    
    # --- 3. Ensure clean sentence ending ---
    text = text.rstrip()
    if text and text[-1] not in '.!?:*':
        # Find the last sentence-ending punctuation
        last_period = max(text.rfind('. '), text.rfind('.\n'), 
                         text.rfind('! '), text.rfind('!\n'),
                         text.rfind('? '), text.rfind('?\n'))
        # Also check if text ends with period right at the end
        if text.endswith('.') or text.endswith('!') or text.endswith('?'):
            pass  # Already ends cleanly
        elif last_period > len(text) * 0.5:
            # Only truncate if we keep at least 50% of content
            text = text[:last_period + 1]
            logger.warning(f"[PostProcess] Trimmed to last complete sentence: "
                          f"{original_len} → {len(text)} chars")
        else:
            # Can't find a good cut point — append ellipsis
            text = text.rstrip(',; ') + '...'
    
    return text

logger = logging.getLogger(__name__)

from storage.skills_store import skills_store
from storage.content_store import content_store
from services.rag_engine import rag_engine
from services.output_templates import build_document_prompt, DOCUMENT_TEMPLATES
from services.context_builder import context_builder

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
        
        # Build adaptive context using the centralized context builder
        built = await context_builder.build_context(
            notebook_id=request.notebook_id,
            skill_id=request.skill_id,
            topic=request.topic,
        )
        
        if built.sources_used == 0:
            raise HTTPException(status_code=400, detail="No sources in notebook")
        
        # Build prompt based on skill using professional templates
        skill_name = skill.get("name", "Content")
        topic_focus = request.topic or "the main topics and insights"
        
        # Use professional template if available, otherwise fall back to skill's own prompt
        if request.skill_id in DOCUMENT_TEMPLATES:
            template_system, template_format = build_document_prompt(
                request.skill_id, 
                topic_focus, 
                request.style or "professional",
                built.sources_used
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

        user_prompt = f"""Based on the following {built.sources_used} source document(s), create a world-class {skill_name}:

{built.context}

Generate the {skill_name} now, ensuring you synthesize insights across ALL sources:"""

        # Use template-specific token limit for thorough generation
        template = DOCUMENT_TEMPLATES.get(request.skill_id)
        doc_num_predict = template.recommended_tokens if template else 2000
        
        logger.info(f"[STUDIO] Context: {built.total_chars} chars from {built.sources_used} sources "
                    f"(strategy={built.strategy_used}, profile={built.profile_used}, "
                    f"build_time={built.build_time_ms}ms)")
        
        # Get adaptive temperature from context profile
        from services.context_builder import CONTEXT_PROFILES
        skill_temp = CONTEXT_PROFILES.get(request.skill_id, CONTEXT_PROFILES["default"]).temperature
        
        # Generate content
        raw_content = await rag_engine._call_ollama(system_prompt, user_prompt, num_predict=doc_num_predict, temperature=skill_temp)
        
        # Post-process: detect loops, ensure clean ending
        content = _clean_llm_output(raw_content)
        if len(content) < len(raw_content) * 0.8:
            logger.warning(f"[STUDIO] Post-processing removed {len(raw_content) - len(content)} chars "
                          f"({len(raw_content)} → {len(content)})")
        
        # Save to content store for persistence
        await content_store.create(
            notebook_id=request.notebook_id,
            skill_id=request.skill_id,
            skill_name=skill_name,
            content=content,
            topic=request.topic,
            sources_used=built.sources_used
        )
        
        return ContentGenerateResponse(
            notebook_id=request.notebook_id,
            skill_id=request.skill_id,
            skill_name=skill_name,
            content=content,
            sources_used=built.sources_used
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
        
        # Build adaptive context using the centralized context builder
        built = await context_builder.build_context(
            notebook_id=request.notebook_id,
            skill_id=request.skill_id,
            topic=request.topic,
        )
        
        if built.sources_used == 0:
            raise HTTPException(status_code=400, detail="No sources in notebook")
        
        skill_name = skill.get("name", "Content")
        topic_focus = request.topic or "the main topics and insights"
        
        # Use professional template if available
        if request.skill_id in DOCUMENT_TEMPLATES:
            template_system, template_format = build_document_prompt(
                request.skill_id, 
                topic_focus, 
                request.style or "professional",
                built.sources_used
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

        user_prompt = f"""Based on the following {built.sources_used} source document(s), create a world-class {skill_name}:

{built.context}

Generate the {skill_name} now, ensuring you synthesize insights across ALL sources:"""

        # Use template-specific token limit for thorough generation
        template = DOCUMENT_TEMPLATES.get(request.skill_id)
        doc_num_predict = template.recommended_tokens if template else 2000
        
        logger.info(f"[STUDIO] Streaming context: {built.total_chars} chars from {built.sources_used} sources "
                    f"(strategy={built.strategy_used}, build_time={built.build_time_ms}ms)")

        # Get adaptive temperature from context profile
        from services.context_builder import CONTEXT_PROFILES
        skill_temp = CONTEXT_PROFILES.get(request.skill_id, CONTEXT_PROFILES["default"]).temperature

        async def stream_generator():
            async for chunk in rag_engine._stream_ollama(system_prompt, user_prompt, num_predict=doc_num_predict, temperature_override=skill_temp):
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
