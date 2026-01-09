"""Visual Summary API endpoints

Generates visual summaries including:
- Mermaid diagrams (flowcharts, mindmaps, timelines)
- Key point extraction
- Document structure visualization
"""
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.structured_llm import structured_llm, MermaidDiagram
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
    
    return VisualSummaryResponse(
        notebook_id=request.notebook_id,
        diagrams=diagrams,
        key_points=result.key_points
    )


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
