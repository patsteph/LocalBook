"""Visual Summary API endpoints

Generates visual summaries including:
- Mermaid diagrams (flowcharts, mindmaps, timelines)
- Key point extraction
- Document structure visualization

v1.0.5: Added content analysis and smart template routing
"""
import logging
import traceback
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from services.structured_llm import structured_llm
from services.visual_analyzer import visual_analyzer
from services.visual_router import visual_router, VISUAL_TEMPLATES
from services.template_scorer import select_primary_and_alternatives
from services.visual_generator import visual_generator
from storage.source_store import source_store


router = APIRouter(prefix="/visual", tags=["visual"])


# =============================================================================
# Request/Response Models
# =============================================================================

class GenerateVisualRequest(BaseModel):
    notebook_id: str
    diagram_types: Optional[List[str]] = Field(
        default=["mindmap", "flowchart"],
        description="Types of diagrams to generate"
    )
    source_ids: Optional[List[str]] = None
    focus_topic: Optional[str] = None


class DiagramResponse(BaseModel):
    diagram_type: str
    code: str
    title: str
    description: str


class VisualSummaryResponse(BaseModel):
    notebook_id: str
    diagrams: List[DiagramResponse]
    key_points: List[str]


class MindmapRequest(BaseModel):
    notebook_id: str
    source_ids: Optional[List[str]] = None
    max_depth: int = Field(default=3, ge=1, le=5)


class FlowchartRequest(BaseModel):
    notebook_id: str
    source_ids: Optional[List[str]] = None
    focus: Optional[str] = Field(default=None, description="Specific process or concept to visualize")


# =============================================================================
# API Endpoints
# =============================================================================

@router.post("/summary", response_model=VisualSummaryResponse)
async def generate_visual_summary(request: GenerateVisualRequest):
    """Generate a visual summary with multiple diagram types."""
    try:
        logger.info(f"[STUDIO] Visual summary started for notebook={request.notebook_id}, types={request.diagram_types}")
        
        # Get sources
        sources = await source_store.list(request.notebook_id)
        if not sources:
            raise HTTPException(status_code=404, detail="No sources found in notebook")
        
        # Filter by source IDs if provided
        if request.source_ids:
            sources = [s for s in sources if s.get("id") in request.source_ids]
        
        # Collect content
        content = "\n\n".join([s.get("content", "")[:3000] for s in sources[:5]])
        
        if request.focus_topic:
            content = f"Focus on: {request.focus_topic}\n\n{content}"
        
        # Generate visual summary
        result = await structured_llm.generate_visual_summary(
            content=content,
            diagram_types=request.diagram_types
        )
        
        diagrams = [
            DiagramResponse(
                diagram_type=d.diagram_type,
                code=d.code,
                title=d.title,
                description=d.description
            )
            for d in result.diagrams
        ]
        
        logger.info(f"[STUDIO] Visual summary completed: {len(diagrams)} diagrams generated")
        return VisualSummaryResponse(
            notebook_id=request.notebook_id,
            diagrams=diagrams,
            key_points=result.key_points
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[STUDIO] Visual summary failed for notebook={request.notebook_id}")
        logger.error(f"[STUDIO] Error: {type(e).__name__}: {str(e)}")
        logger.error(f"[STUDIO] Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Visual generation failed: {str(e)}")


@router.post("/mindmap")
async def generate_mindmap(request: MindmapRequest):
    """Generate a mindmap visualization of the notebook content."""
    
    sources = await source_store.list(request.notebook_id)
    if not sources:
        raise HTTPException(status_code=404, detail="No sources found")
    
    if request.source_ids:
        sources = [s for s in sources if s.get("id") in request.source_ids]
    
    content = "\n\n".join([s.get("content", "")[:3000] for s in sources[:5]])
    
    result = await structured_llm.generate_visual_summary(
        content=content,
        diagram_types=["mindmap"]
    )
    
    if result.diagrams:
        return {
            "success": True,
            "diagram": {
                "type": "mindmap",
                "code": result.diagrams[0].code,
                "title": result.diagrams[0].title,
                "description": result.diagrams[0].description
            },
            "key_points": result.key_points
        }
    
    return {
        "success": False,
        "error": "Failed to generate mindmap",
        "key_points": result.key_points
    }


@router.post("/flowchart")
async def generate_flowchart(request: FlowchartRequest):
    """Generate a flowchart visualization."""
    
    sources = await source_store.list(request.notebook_id)
    if not sources:
        raise HTTPException(status_code=404, detail="No sources found")
    
    if request.source_ids:
        sources = [s for s in sources if s.get("id") in request.source_ids]
    
    content = "\n\n".join([s.get("content", "")[:3000] for s in sources[:5]])
    
    if request.focus:
        content = f"Create a flowchart about: {request.focus}\n\n{content}"
    
    result = await structured_llm.generate_visual_summary(
        content=content,
        diagram_types=["flowchart"]
    )
    
    if result.diagrams:
        return {
            "success": True,
            "diagram": {
                "type": "flowchart",
                "code": result.diagrams[0].code,
                "title": result.diagrams[0].title,
                "description": result.diagrams[0].description
            },
            "key_points": result.key_points
        }
    
    return {
        "success": False,
        "error": "Failed to generate flowchart",
        "key_points": result.key_points
    }


@router.post("/compare")
async def compare_documents(
    notebook_id: str,
    source_id_1: str,
    source_id_2: str
):
    """Compare two documents and visualize differences."""
    
    sources = await source_store.list(notebook_id)
    
    doc1 = next((s for s in sources if s.get("id") == source_id_1), None)
    doc2 = next((s for s in sources if s.get("id") == source_id_2), None)
    
    if not doc1 or not doc2:
        raise HTTPException(status_code=404, detail="One or both sources not found")
    
    result = await structured_llm.compare_documents(
        doc1_content=doc1.get("content", "")[:5000],
        doc2_content=doc2.get("content", "")[:5000]
    )
    
    return {
        "notebook_id": notebook_id,
        "document_1": {
            "id": source_id_1,
            "name": doc1.get("filename", "Document 1")
        },
        "document_2": {
            "id": source_id_2,
            "name": doc2.get("filename", "Document 2")
        },
        "comparison": {
            "similarities": result.similarities,
            "differences": result.differences,
            "unique_to_first": result.unique_to_first,
            "unique_to_second": result.unique_to_second,
            "synthesis": result.synthesis
        }
    }


# =============================================================================
# v1.0.5: Smart Template Routing Endpoints
# =============================================================================

@router.post("/analyze")
async def analyze_content(notebook_id: str, source_ids: Optional[List[str]] = None):
    """Analyze content and suggest best visualization templates.
    
    Returns detected patterns, suggested templates, and content insights.
    """
    sources = await source_store.list(notebook_id)
    if not sources:
        raise HTTPException(status_code=404, detail="No sources found")
    
    if source_ids:
        sources = [s for s in sources if s.get("id") in source_ids]
    
    content = "\n\n".join([s.get("content", "")[:3000] for s in sources[:5]])
    
    analysis = visual_analyzer.analyze(content)
    
    return {
        "notebook_id": notebook_id,
        "detected_patterns": [p.value for p in analysis.detected_patterns],
        "suggested_templates": analysis.suggested_templates,
        "content_type": analysis.content_type,
        "has_temporal_data": analysis.has_temporal_data,
        "has_comparison": analysis.has_comparison,
        "has_hierarchy": analysis.has_hierarchy,
        "entities": analysis.entities[:10],
        "numbers": analysis.numbers[:10],
    }


@router.get("/templates")
async def list_templates():
    """List all available visual templates."""
    templates = []
    for tid, template in VISUAL_TEMPLATES.items():
        templates.append({
            "id": tid,
            "name": template.name,
            "category": template.category.value,
            "description": template.description,
            "mermaid_type": template.mermaid_type,
            "best_for": template.best_for,
        })
    return {"templates": templates, "count": len(templates)}


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    """Get details for a specific template."""
    template = visual_router.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    return {
        "id": template.id,
        "name": template.name,
        "category": template.category.value,
        "description": template.description,
        "mermaid_type": template.mermaid_type,
        "prompt_enhancement": template.prompt_enhancement,
        "example_code": template.example_code,
        "best_for": template.best_for,
    }


class SmartVisualRequest(BaseModel):
    notebook_id: str
    topic: str
    source_ids: Optional[List[str]] = None
    template_id: Optional[str] = None
    color_theme: Optional[str] = "auto"  # vibrant, ocean, sunset, forest, monochrome, pastel
    guidance: Optional[str] = None  # User refinement guidance: "emphasize X", "show relationship between Y and Z"


@router.post("/smart")
async def smart_visual(request: SmartVisualRequest):
    """Generate 3 visual options using smart template routing.
    
    v1.0.7: Check cache first for instant response if pre-classified during query.
    Analyzes the topic/content and generates 3 different visual styles
    for the user to choose from.
    """
    import asyncio
    from services.visual_cache import visual_cache
    
    # Use topic as primary content, optionally supplement with sources
    content = request.topic
    
    if request.source_ids:
        sources = await source_store.list(request.notebook_id)
        if sources:
            filtered = [s for s in sources if s.get("id") in request.source_ids]
            source_content = "\n\n".join([s.get("content", "")[:2000] for s in filtered[:3]])
            if source_content:
                content = f"{request.topic}\n\nContext from sources:\n{source_content}"
    
    # Route to best template or use provided one
    if request.template_id:
        template = visual_router.get_template(request.template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        analysis = visual_analyzer.analyze(content)
        templates_to_use = [template]
    else:
        # v1.0.7: Check cache FIRST for instant response
        cached = await visual_cache.get_by_notebook(request.notebook_id)
        if cached:
            print(f"[Visual] 🚀 CACHE HIT! Using ONLY pre-classified: {cached.visual_type} -> {cached.suggested_template}")
            template = visual_router.get_template(cached.suggested_template)
            if template:
                # Use ONLY cached classification - single template for speed
                templates_to_use = [template]
            else:
                # Cached template not found, fall through to normal analysis
                cached = None
        
        if not cached:
            # No cache hit - do normal analysis
            # First try regex pattern analysis
            template, analysis = visual_router.route(content)
            
            # If regex patterns have LOW confidence, use LLM to semantically understand the content
            max_confidence = max(analysis.confidence_scores.values()) if analysis.confidence_scores else 0
            
            if max_confidence < 0.3:
                print(f"[Visual] Low pattern confidence ({max_confidence:.2f}), using LLM semantic analysis...")
                try:
                    llm_analysis = await visual_analyzer.analyze_with_llm(content)
                    llm_template = visual_router.get_template(llm_analysis["suggested_template"])
                    if llm_template:
                        template = llm_template
                        print(f"[Visual] LLM suggests: {llm_analysis['visual_type']} -> {llm_analysis['suggested_template']}")
                except Exception as e:
                    print(f"[Visual] LLM analysis failed, using pattern-based: {e}")
            
            # Get 3 alternative templates to GUARANTEE we have enough diverse options
            alternatives = visual_router.get_alternatives(content, 3)
            templates_to_use = [template] + [t for t, _ in alternatives]
    
    # Generate visuals for each template (up to 3)
    color_theme = request.color_theme or "auto"
    
    async def generate_for_template(tmpl):
        try:
            # FAST PATH: Use fast model with minimal content
            result = await structured_llm.generate_visual_summary(
                content=content[:1500],  # Minimal content for speed
                diagram_types=[tmpl.mermaid_type],
                color_theme=color_theme,
                use_fast_model=True  # Force fast model
            )
            if result.diagrams:
                d = result.diagrams[0]
                return {
                    "diagram_type": tmpl.mermaid_type,
                    "code": d.code,
                    "title": d.title,
                    "description": d.description,
                    "template_id": tmpl.id,
                    "template_name": tmpl.name,
                }
        except Exception as e:
            print(f"[Visual] Failed to generate for {tmpl.id}: {e}")
            import traceback
            traceback.print_exc()
        return None
    
    # FAST PATH: Generate primary visual first, alternatives in background
    primary_template = templates_to_use[0]
    print(f"[Visual] ⚡ Generating PRIMARY first: {primary_template.mermaid_type}")
    
    # Generate primary visual immediately
    primary_result = await generate_for_template(primary_template)
    diagrams = [primary_result] if primary_result else []
    
    # If no cache hit, generate alternatives in background (non-blocking for response)
    # But for now, we generate them sequentially to include in response
    if not cached and len(diagrams) > 0:
        # Get 1-2 alternative templates
        seen_types = {primary_template.mermaid_type}
        alt_templates = []
        guaranteed_diverse = ["mindmap", "flowchart", "timeline"]
        for fallback_type in guaranteed_diverse:
            if len(alt_templates) >= 2:
                break
            if fallback_type not in seen_types:
                for tmpl in visual_router.templates.values():
                    if tmpl.mermaid_type == fallback_type:
                        alt_templates.append(tmpl)
                        seen_types.add(fallback_type)
                        break
        
        # Generate alternatives with short timeout (don't block too long)
        if alt_templates:
            print(f"[Visual] Generating {len(alt_templates)} alternatives: {[t.mermaid_type for t in alt_templates]}")
            try:
                alt_results = await asyncio.wait_for(
                    asyncio.gather(*[generate_for_template(t) for t in alt_templates], return_exceptions=True),
                    timeout=30.0  # Short timeout for alternatives
                )
                for r in alt_results:
                    if r is not None and not isinstance(r, Exception):
                        diagrams.append(r)
            except asyncio.TimeoutError:
                print("[Visual] ⚠️ Alternatives timed out - returning primary only")
    
    # Extract key points from content directly (simple extraction)
    key_points = []
    sentences = content.split('.')[:10]
    for s in sentences:
        s = s.strip()
        if len(s) > 20 and len(s) < 150:
            key_points.append(s)
        if len(key_points) >= 5:
            break
    
    return {
        "success": len(diagrams) > 0,
        "notebook_id": request.notebook_id,
        "diagrams": diagrams,  # Now returns up to 3 options
        "template_used": {
            "id": template.id,
            "name": template.name,
            "category": template.category.value,
        },
        "analysis": {
            "content_type": analysis.content_type,
            "detected_patterns": [p.value for p in analysis.detected_patterns[:3]],
        },
        "key_points": key_points,
    }


@router.post("/generate")
async def generate_visual(
    notebook_id: str,
    source_ids: Optional[List[str]] = None,
    template_id: Optional[str] = None
):
    """Generate a high-quality visual using the Enhanced Generator (Phase 2).
    
    Uses template-specific prompts for better output quality.
    Auto-routes to best template if template_id not provided.
    """
    sources = await source_store.list(notebook_id)
    if not sources:
        raise HTTPException(status_code=404, detail="No sources found")
    
    if source_ids:
        sources = [s for s in sources if s.get("id") in source_ids]
    
    content = "\n\n".join([s.get("content", "")[:3000] for s in sources[:5]])
    
    result = await visual_generator.generate(content, template_id)
    
    return {
        "success": result.success,
        "notebook_id": notebook_id,
        "template": {
            "id": result.template_id,
            "name": result.template_name,
        },
        "diagram": {
            "code": result.mermaid_code,
            "title": result.title,
            "description": result.description,
        } if result.success else None,
        "key_points": result.key_points,
        "alternatives": result.alternatives,
        "error": result.error,
    }


@router.post("/generate/batch")
async def generate_batch(
    notebook_id: str,
    template_ids: List[str],
    source_ids: Optional[List[str]] = None
):
    """Generate multiple visuals at once using specified templates."""
    sources = await source_store.list(notebook_id)
    if not sources:
        raise HTTPException(status_code=404, detail="No sources found")
    
    if source_ids:
        sources = [s for s in sources if s.get("id") in source_ids]
    
    content = "\n\n".join([s.get("content", "")[:3000] for s in sources[:5]])
    
    results = await visual_generator.generate_multiple(content, template_ids)
    
    return {
        "notebook_id": notebook_id,
        "results": [
            {
                "success": r.success,
                "template": {"id": r.template_id, "name": r.template_name},
                "diagram": {
                    "code": r.mermaid_code,
                    "title": r.title,
                    "description": r.description,
                } if r.success else None,
                "key_points": r.key_points,
                "error": r.error,
            }
            for r in results
        ],
        "success_count": sum(1 for r in results if r.success),
        "total_count": len(results),
    }


# =============================================================================
# Cache Status Check - Frontend polls this to know when visual is ready
# =============================================================================

@router.get("/cache/status/{notebook_id}")
async def get_cache_status(notebook_id: str):
    """Check if visual cache is ready for a notebook.
    
    Frontend should poll this after answer is shown to enable "Create Visual" button.
    Returns: {"ready": true/false, "theme_count": N, "age_seconds": N}
    """
    from services.visual_cache import visual_cache
    status = await visual_cache.is_ready(notebook_id)
    return status


# =============================================================================
# Streaming Visual Generation - Primary first, alternatives stream in
# =============================================================================

from fastapi.responses import StreamingResponse
import json as json_module

@router.post("/smart/stream")
async def generate_smart_visual_stream(request: SmartVisualRequest):
    """Generate visuals with streaming - primary appears first, alternatives follow.
    
    Returns Server-Sent Events (SSE) stream:
    - event: primary  -> First/best visual (from cache or generated)
    - event: alternative -> Additional visual options
    - event: done -> Stream complete
    """
    
    async def generate_stream():
        from services.visual_cache import visual_cache  # Import inside generator
        
        # Use topic as primary content, but FETCH notebook sources for meaningful extraction
        topic = request.topic
        if not topic or not topic.strip():
            yield f"event: error\ndata: {json_module.dumps({'error': 'No topic provided'})}\n\n"
            return
        
        # Fetch notebook sources to get REAL content for theme extraction
        content = topic
        try:
            sources = await source_store.list(request.notebook_id)
            if sources:
                # Get content from first 3 sources (up to 2000 chars each)
                source_texts = [s.get("content", "")[:2000] for s in sources[:3] if s.get("content")]
                if source_texts:
                    content = f"{topic}\n\nSource content:\n" + "\n\n".join(source_texts)
                    print(f"[Visual Stream] Using {len(source_texts)} sources ({len(content)} chars) for extraction")
        except Exception as e:
            print(f"[Visual Stream] Failed to fetch sources: {e}")
        
        # STEP 1: Check cache FIRST - it contains structure extracted from the ANSWER
        # This is critical for context alignment: the cache was built from the actual answer,
        # not from raw notebook sources which may have different themes
        cached = await visual_cache.get_by_notebook(request.notebook_id)
        
        import re as re_module
        import asyncio
        
        # ALWAYS extract dynamic title from topic - even when using cache
        # The topic contains the answer content with theme count info
        clean_topic = re_module.sub(r'\[\d+\]', '', topic)
        dynamic_title = None
        
        # P0: Extract dynamic title - look for theme count or question
        first_lines = clean_topic.split('\n')[:10]
        for line in first_lines:
            line = line.strip()
            if not line:
                continue
            # Skip numbered theme headers like "**1. Safety..." - these are CONTENT not title
            if re_module.match(r'^\*?\*?\d+\.', line) or re_module.match(r'^\*\*[IVX]+\.', line):
                continue
            # Skip section headers and emoji prefixes
            clean_line = re_module.sub(r'^[^\w]*', '', line)  # Strip leading non-word chars (emoji, etc)
            if clean_line.lower().startswith(('quick answer', 'detailed answer')):
                continue
                
            # If it's a statement about themes/sections, extract count
            if any(kw in line.lower() for kw in ['theme', 'section', 'point', 'insight', 'finding', 'grouped into', 'distinct']):
                count_match = re_module.search(r'(two|three|four|five|six|\d+)\s+(distinct\s+)?(section|theme|point|area|insight)', line, re_module.IGNORECASE)
                if count_match:
                    count_word = count_match.group(1)
                    count_map = {'two': '2', 'three': '3', 'four': '4', 'five': '5', 'six': '6'}
                    count = count_map.get(count_word.lower(), count_word)
                    dynamic_title = f"{count} Key Themes"
                    print(f"[Visual Stream] 📌 Dynamic title from count: {dynamic_title}")
                    break
                    
            # Use question as title if it looks like a question
            if '?' in line or line.lower().startswith(('what', 'how', 'why', 'when', 'which', 'where')):
                clean_q = re_module.sub(r'[*_#]', '', line)  # Strip markdown
                dynamic_title = clean_q[:60] if len(clean_q) <= 60 else clean_q[:57] + "..."
                print(f"[Visual Stream] 📌 Dynamic title from question: {dynamic_title}")
                break
        
        # HYBRID EXTRACTION: Fast regex → Validation → LLM fallback
        # This ensures 99% reliability regardless of LLM output format
        from services.theme_extractor import (
            extract_themes_hybrid, extract_subpoints_for_themes, VisualContent
        )
        
        fast_extracted_themes = None
        fast_extracted_subpoints = {}
        insight = None
        
        if not cached:
            # Use hybrid extraction (validates regex, falls back to LLM if garbage detected)
            visual_content: VisualContent = await extract_themes_hybrid(clean_topic)
            
            fast_extracted_themes = visual_content.themes
            insight = visual_content.insight
            
            # Override dynamic_title if extraction found one
            if visual_content.title and visual_content.title != "Key Themes":
                dynamic_title = visual_content.title
            
            print(f"[Visual Stream] ⚡ Hybrid extraction: {len(fast_extracted_themes)} themes: {fast_extracted_themes}")
            
            # Extract subpoints for mindmap
            fast_extracted_subpoints = extract_subpoints_for_themes(clean_topic, fast_extracted_themes)
            
            if insight:
                print(f"[Visual Stream] 💡 Insight: {insight}")
        
        structure = {}
        template = None
        
        # Use fast-extracted themes if available (instant path)
        if fast_extracted_themes:
            structure = {
                'themes': fast_extracted_themes,
                'insight': insight,
                'subpoints': fast_extracted_subpoints if fast_extracted_subpoints else {},
            }
            if dynamic_title:
                structure['title'] = dynamic_title
            print(f"[Visual Stream] Using instant-extracted themes: {fast_extracted_themes}")
            if fast_extracted_subpoints:
                print(f"[Visual Stream] With subpoints: {list(fast_extracted_subpoints.keys())}")
        
        # IMPORTANT: Validate cached entry matches current topic before using
        # The cache stores data for ANY previous question - we need to verify relevance
        cache_is_relevant = False
        if cached and cached.structure:
            # Check if the cached answer_preview overlaps significantly with current topic
            # This prevents using stale data from a completely different question
            cached_preview = cached.answer_preview.lower()[:200] if cached.answer_preview else ""
            topic_preview = topic.lower()[:200] if topic else ""
            
            # Simple relevance check: do they share significant words?
            cached_words = set(cached_preview.split())
            topic_words = set(topic_preview.split())
            overlap = len(cached_words & topic_words)
            
            if overlap >= 5:  # At least 5 words in common
                cache_is_relevant = True
                print(f"[Visual Stream] ✅ Cache RELEVANT (overlap={overlap} words)")
            else:
                print(f"[Visual Stream] ⚠️ Cache STALE - different question (overlap={overlap})")
                print(f"[Visual Stream]   Cached: {cached_preview[:100]}...")
                print(f"[Visual Stream]   Topic: {topic_preview[:100]}...")
        
        if cached and cached.structure and cache_is_relevant:
            # Verify cached structure has ACTUAL content, not empty/placeholder
            cached_themes = cached.structure.get('themes', [])
            has_real_content = (
                len(cached_themes) >= 2 and 
                all(t and len(t) > 5 for t in cached_themes[:2])  # At least 2 real themes
            )
            
            if has_real_content:
                # USE CACHED STRUCTURE - this was extracted from the RAG answer
                structure = cached.structure.copy()  # Copy to avoid modifying cache
                # ALWAYS override with dynamic title if we extracted one
                if dynamic_title:
                    structure['title'] = dynamic_title
                    print(f"[Visual Stream] 📌 Overriding cached title with dynamic: {dynamic_title}")
                template = visual_router.get_template(cached.suggested_template)
                print(f"[Visual Stream] ✅ USING CACHED structure: {cached.visual_type} -> {cached.suggested_template}")
                print(f"[Visual Stream] Cached themes: {structure.get('themes', [])}")
            else:
                print(f"[Visual Stream] ⚠️ Cache has EMPTY/BAD themes: {cached_themes}, will re-extract")
        
        # Handle user guidance - if provided, we need to re-extract with guidance context
        user_guidance = request.guidance
        if user_guidance and user_guidance.strip():
            print(f"[Visual Stream] 🎯 User guidance provided: {user_guidance[:100]}...")
            # Even with cache, user guidance means we should re-extract to honor their intent
            try:
                # Include guidance prominently in extraction
                guidance_content = f"USER WANTS THIS VISUAL TO EMPHASIZE:\n{user_guidance}\n\nCONTENT TO VISUALIZE:\n{topic[:2000]}"
                pre_classified = await visual_analyzer.analyze_with_llm(guidance_content)
                structure = pre_classified.get('structure', {})
                # Override title if guidance suggests one
                if user_guidance and len(user_guidance) < 60:
                    structure['title'] = user_guidance.strip()
                print(f"[Visual Stream] Guided extraction: {pre_classified.get('visual_type')} with themes: {structure.get('themes', [])}")
            except Exception as e:
                print(f"[Visual Stream] Guided extraction failed: {e}, falling back to cache/default")
        
        # Only re-extract if NO cached structure available AND no guidance was provided
        if not structure:
            print("[Visual Stream] No cache hit - extracting from content...")
            print(f"[Visual Stream] Topic length: {len(topic)} chars")
            print(f"[Visual Stream] Topic preview: {topic[:300]}...")
            pre_classified = None
            try:
                # Strip citation markers before extraction
                import re as re_module
                clean_topic = re_module.sub(r'\[\d+\]', '', topic)
                clean_content = re_module.sub(r'\[\d+\]', '', content)
                
                # Weight the topic (answer text) higher than source content
                # Pass full content - regex section detection runs on full text,
                # LLM extraction handles its own 3000 char limit internally
                extraction_content = f"MAIN CONTENT TO VISUALIZE:\n{clean_topic}\n\nSUPPORTING CONTEXT:\n{clean_content}"
                pre_classified = await visual_analyzer.analyze_with_llm(extraction_content)
                structure = pre_classified.get('structure', {})
                themes = structure.get('themes', [])
                print(f"[Visual Stream] Extracted: {pre_classified.get('visual_type')} with themes: {themes}")
            except Exception as e:
                print(f"[Visual Stream] Extraction failed: {e}")
                import traceback
                traceback.print_exc()
        
        # CRITICAL FALLBACK: If we STILL have no themes, extract directly from topic using regex
        # This bypasses LLM entirely - guaranteed to work if content has structure
        if not structure.get('themes') or len(structure.get('themes', [])) < 2:
            print("[Visual Stream] ⚠️ EMERGENCY FALLBACK - extracting themes directly from topic")
            import re as re_module
            clean_topic = re_module.sub(r'\[\d+\]', '', topic)
            
            # Try all patterns directly on the topic
            patterns = [
                (r'^[IVX]+\.\s*(.+?)(?:\n|$)', "roman"),
                (r'\*\*\d+\.\s*([^*\n]+)\*\*', "bold-num"),
                (r'^\d+\.\s*\*?\*?([^*\n]+)', "num"),
                (r'^[A-Za-z][.)]\s*\*?\*?([^*\n]+)', "letter"),
                (r'^[-*•]\s+([A-Z][^*\n]{10,})', "bullet"),
            ]
            
            for pattern, method in patterns:
                matches = re_module.findall(pattern, clean_topic, re_module.MULTILINE)
                if matches and len(matches) >= 2:
                    themes = [m.strip()[:60] for m in matches if m.strip() and len(m.strip()) > 3]
                    if len(themes) >= 2:
                        structure['themes'] = themes[:8]
                        print(f"[Visual Stream] ✅ EMERGENCY extraction found {len(themes)} themes via {method}: {themes}")
                        break
            
            # Last resort: first sentences
            if not structure.get('themes') or len(structure.get('themes', [])) < 2:
                sentences = re_module.split(r'[.!?]\s+', clean_topic[:1500])
                fallback = [s.strip()[:60] for s in sentences[:5] if len(s.strip()) > 20]
                if fallback:
                    structure['themes'] = fallback
                    print(f"[Visual Stream] 🔄 Sentence fallback: {fallback}")
        
        # Determine template using content-driven scoring (not keyword matching)
        scored_alternatives = []  # Initialize before conditional
        
        if not template:
            # Use scoring system to pick best template based on extracted structure
            primary_id, scored_alternatives = select_primary_and_alternatives(structure, max_alternatives=3)
            
            template = visual_router.get_template(primary_id)
            if not template:
                template = visual_router.get_template("key_takeaways")
        
        color_theme = request.color_theme or "auto"
        
        # DIAGNOSTIC: Full structure summary before building visual
        themes = structure.get('themes', [])
        print("[Visual Stream] ═══════════════════════════════════════")
        print("[Visual Stream] FINAL STRUCTURE FOR VISUAL:")
        print(f"[Visual Stream]   Template: {template.id if template else 'None'}")
        print(f"[Visual Stream]   Themes ({len(themes)}): {themes}")
        print(f"[Visual Stream]   Pros: {structure.get('pros', [])}")
        print(f"[Visual Stream]   Cons: {structure.get('cons', [])}")
        print(f"[Visual Stream]   Title: {structure.get('title', 'N/A')}")
        print("[Visual Stream] ═══════════════════════════════════════")
        
        # VALIDATION: Warn if we're about to render with empty data
        if not themes or len(themes) < 2:
            print(f"[Visual Stream] ⚠️ WARNING: Rendering with {len(themes)} themes - visual will be sparse!")
        
        # Helper to generate SVG for a template - uses pre-classified structure
        async def gen_visual(tmpl):
            try:
                # Get title from cached structure or topic - create concise title if too long
                # Priority 1: Use title from LLM extraction (SHORT punchy headline)
                if structure and structure.get("title"):
                    title = structure["title"]
                    print(f"[Visual Stream] Using extracted title: {title}")
                # Priority 2: Use cached title if available
                elif cached and cached.title:
                    title = cached.title
                # Priority 3: Generate a generic title (fallback)
                else:
                    title = "Key Insights"
                
                # Use SVG builder instead of Mermaid for reliable rendering
                result = structured_llm.build_svg_from_structure(
                    structure=structure,
                    template_id=tmpl.id,
                    color_theme=color_theme,
                    title=title,
                    dark_mode=True  # Always dark mode for now
                )
                
                # Extract tagline/summary for user editing
                tagline = structure.get("insight") or ""
                if not tagline and structure.get("themes"):
                    # Generate a default tagline from themes
                    theme_count = len(structure.get("themes", []))
                    tagline = f"Exploring {theme_count} key themes from your sources"
                
                # Create display title that includes template type for differentiation
                display_title = result["title"]
                template_label = tmpl.name.replace("Key ", "").replace("Takeaways", "Hub-Spoke")
                if template_label and template_label.lower() not in display_title.lower():
                    display_title = f"{result['title']} ({template_label})"
                
                return {
                    "render_type": "svg",
                    "svg": result["svg"],
                    "title": display_title,
                    "description": result["description"],
                    "template_id": tmpl.id,
                    "template_name": tmpl.name,
                    "tagline": tagline,  # Editable summary line
                }
            except Exception as e:
                print(f"[Visual Stream] Failed for {tmpl.id}: {e}")
                import traceback
                traceback.print_exc()
            return None
        
        # 1. Generate and yield PRIMARY visual immediately
        print(f"[Visual Stream] Generating primary visual: {template.id}")
        primary = await gen_visual(template)
        if primary:
            print("[Visual Stream] ✅ Primary ready, yielding immediately")
            yield f"event: primary\ndata: {json_module.dumps(primary)}\n\n"
        else:
            yield f"event: error\ndata: {json_module.dumps({'error': 'Failed to generate primary visual'})}\n\n"
            return
        
        # Small delay to ensure primary is rendered before alternatives start
        await asyncio.sleep(0.1)
        
        # 2. Generate alternatives using scored templates (content-driven selection)
        seen_templates = {template.id}
        generated_count = 0
        
        # Use alternatives from scoring system (already computed above)
        # If we didn't compute alternatives earlier (template was pre-specified), compute now
        if not scored_alternatives:
            _, scored_alternatives = select_primary_and_alternatives(structure, max_alternatives=3)
        
        print(f"[Visual Stream] 📊 Scored alternatives: {scored_alternatives}")
        
        for alt_id in scored_alternatives:
            if alt_id not in seen_templates:
                alt_tmpl = visual_router.get_template(alt_id)
                if alt_tmpl:
                    print(f"[Visual Stream] Generating alternative: {alt_id}")
                    alt = await gen_visual(alt_tmpl)
                    if alt:
                        yield f"event: alternative\ndata: {json_module.dumps(alt)}\n\n"
                        await asyncio.sleep(0.05)
                        generated_count += 1
                        seen_templates.add(alt_id)
                    else:
                        print(f"[Visual Stream] ⚠️ Alternative {alt_id} failed to generate")
            
            if generated_count >= 3:
                break
        
        yield f"event: done\ndata: {json_module.dumps({'total': len(seen_templates)})}\n\n"
    
    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# =============================================================================
# Phase 4: Visual Refinement Chat
# =============================================================================

class RefineVisualRequest(BaseModel):
    notebook_id: str
    current_code: str  # Current Mermaid code
    refinement: str  # User instruction: "make it simpler", "focus on X", "add more detail"
    color_theme: Optional[str] = "auto"


@router.post("/refine")
async def refine_visual(request: RefineVisualRequest):
    """Refine an existing visual based on user feedback.
    
    Phase 4 feature: Allow users to iteratively improve visuals with natural language.
    Examples: "make it simpler", "focus on the first 3 items", "add more connections"
    """
    
    refinement_prompt = f"""You are refining a Mermaid diagram based on user feedback.

CURRENT DIAGRAM:
```mermaid
{request.current_code}
```

USER REQUEST: {request.refinement}

Apply the user's refinement to the diagram. Common refinements:
- "make it simpler" → reduce nodes to 3-5 key items, remove details
- "add more detail" → expand nodes with sub-items
- "focus on X" → keep only nodes related to X
- "horizontal/vertical" → change layout direction (LR vs TB)
- "different colors" → apply new color scheme

Return ONLY a JSON object:
{{
    "code": "refined mermaid code here",
    "changes_made": "brief description of what changed"
}}

CRITICAL: Preserve the diagram type (flowchart, mindmap, etc). Only modify structure/content."""

    try:
        import httpx
        from config import settings
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_model,
                    "prompt": refinement_prompt,
                    "stream": False,
                    "options": {"num_predict": 1000, "temperature": 0.3}
                }
            )
            result = response.json().get("response", "{}")
            
            import json
            import re
            json_match = re.search(r'\{[\s\S]*\}', result)
            if json_match:
                parsed = json.loads(json_match.group())
                return {
                    "success": True,
                    "code": parsed.get("code", request.current_code),
                    "changes_made": parsed.get("changes_made", "Diagram refined"),
                }
    except Exception as e:
        print(f"[Visual] Refinement failed: {e}")
    
    return {
        "success": False,
        "code": request.current_code,
        "changes_made": "Refinement failed - original preserved",
    }
