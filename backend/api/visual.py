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

from services.structured_llm import structured_llm, MermaidDiagram
from services.visual_analyzer import visual_analyzer
from services.visual_router import visual_router, VISUAL_TEMPLATES
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


@router.post("/smart")
async def smart_visual(request: SmartVisualRequest):
    """Generate 3 visual options using smart template routing.
    
    Analyzes the topic/content and generates 3 different visual styles
    for the user to choose from.
    """
    import asyncio
    
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
        # First try regex pattern analysis
        template, analysis = visual_router.route(content)
        
        # If regex patterns have LOW confidence, use LLM to semantically understand the content
        # This handles cases where user just provides facts without explicit visual instructions
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
    async def generate_for_template(tmpl):
        try:
            result = await structured_llm.generate_visual_summary(
                content=f"{tmpl.prompt_enhancement}\n\nContent:\n{content}",
                diagram_types=[tmpl.mermaid_type]
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
    
    # Ensure we have diverse mermaid types - GUARANTEE 3 different types
    seen_types = set()
    diverse_templates = []
    for t in templates_to_use:
        if t.mermaid_type not in seen_types:
            diverse_templates.append(t)
            seen_types.add(t.mermaid_type)
    
    # If we still don't have 3 diverse templates, add guaranteed fallbacks
    guaranteed_diverse = ["mindmap", "timeline", "flowchart"]
    for fallback_type in guaranteed_diverse:
        if len(diverse_templates) >= 3:
            break
        for tmpl in visual_router.templates.values():
            if tmpl.mermaid_type == fallback_type and tmpl.mermaid_type not in seen_types:
                diverse_templates.append(tmpl)
                seen_types.add(tmpl.mermaid_type)
                break
    
    print(f"[Visual] Generating {len(diverse_templates[:3])} diverse visual options: {[t.mermaid_type for t in diverse_templates[:3]]}")
    
    # Generate all in parallel for speed (ALWAYS 3 diverse options)
    results = await asyncio.gather(*[generate_for_template(t) for t in diverse_templates[:3]])
    diagrams = [r for r in results if r is not None]
    
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
