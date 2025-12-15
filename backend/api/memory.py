"""Memory API endpoints for viewing and managing persistent memory"""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models.memory import (
    CoreMemory, CoreMemoryEntry, MemoryCategory, MemoryImportance,
    ArchivalMemoryEntry, MemorySearchResult, MemoryConflict
)
from storage.memory_store import memory_store
from services.memory_agent import memory_agent


router = APIRouter(prefix="/memory", tags=["memory"])


# =============================================================================
# Request/Response Models
# =============================================================================

class CoreMemoryCreateRequest(BaseModel):
    key: str
    value: str
    category: str = "user_fact"
    importance: str = "medium"


class CoreMemoryUpdateRequest(BaseModel):
    value: str


class MemorySearchRequest(BaseModel):
    query: str
    max_results: int = 10
    notebook_id: Optional[str] = None


class ConflictResolutionRequest(BaseModel):
    resolution: str  # "keep_existing", "use_new", "merge"
    new_value: Optional[str] = None


class MemoryStatsResponse(BaseModel):
    core_memory: Dict[str, Any]
    recall_memory: Dict[str, Any]
    archival_memory: Dict[str, Any]


# =============================================================================
# Core Memory Endpoints
# =============================================================================

@router.get("/core", response_model=CoreMemory)
async def get_core_memory():
    """Get all core memory entries"""
    return memory_store.load_core_memory()


@router.get("/core/{memory_id}")
async def get_core_memory_entry(memory_id: str):
    """Get a specific core memory entry"""
    memory = memory_store.load_core_memory()
    for entry in memory.entries:
        if entry.id == memory_id:
            return entry
    raise HTTPException(status_code=404, detail="Memory not found")


@router.post("/core", response_model=Dict[str, Any])
async def create_core_memory(request: CoreMemoryCreateRequest):
    """Create a new core memory entry"""
    entry = CoreMemoryEntry(
        key=request.key,
        value=request.value,
        category=MemoryCategory(request.category) if request.category in [e.value for e in MemoryCategory] else MemoryCategory.USER_FACT,
        importance=MemoryImportance(request.importance) if request.importance in [e.value for e in MemoryImportance] else MemoryImportance.MEDIUM,
    )
    
    success, conflict = memory_store.add_core_memory(entry)
    
    return {
        "success": success,
        "memory_id": entry.id if success else None,
        "conflict": conflict.model_dump() if conflict else None
    }


@router.put("/core/{memory_id}")
async def update_core_memory(memory_id: str, request: CoreMemoryUpdateRequest):
    """Update an existing core memory entry"""
    success = memory_store.update_core_memory(memory_id, request.value)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True}


@router.delete("/core/{memory_id}")
async def delete_core_memory(memory_id: str):
    """Delete a core memory entry"""
    success = memory_store.delete_core_memory(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True}


@router.get("/core/category/{category}")
async def get_core_memory_by_category(category: str):
    """Get core memories by category"""
    try:
        cat = MemoryCategory(category)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid category: {category}")
    
    entries = memory_store.get_core_memory_by_category(cat)
    return {"entries": [e.model_dump() for e in entries]}


# =============================================================================
# Archival Memory Endpoints
# =============================================================================

@router.post("/archival/search", response_model=List[Dict[str, Any]])
async def search_archival_memory(request: MemorySearchRequest):
    """Search archival memory by semantic similarity"""
    results = memory_store.search_archival_memory(
        query=request.query,
        limit=request.max_results,
        notebook_id=request.notebook_id
    )
    
    return [
        {
            "id": r.entry.id,
            "content": r.entry.content,
            "content_type": r.entry.content_type,
            "topics": r.entry.topics,
            "similarity_score": r.similarity_score,
            "recency_score": r.recency_score,
            "combined_score": r.combined_score,
            "created_at": r.entry.created_at.isoformat()
        }
        for r in results
    ]


@router.post("/archival")
async def create_archival_memory(
    content: str,
    content_type: str = "user_note",
    topics: List[str] = [],
    notebook_id: Optional[str] = None
):
    """Manually add an archival memory"""
    entry = ArchivalMemoryEntry(
        content=content,
        content_type=content_type,
        topics=topics,
        source_notebook_id=notebook_id,
    )
    memory_store.add_archival_memory(entry)
    return {"success": True, "memory_id": entry.id}


# =============================================================================
# Recall Memory Endpoints
# =============================================================================

@router.get("/recall/recent")
async def get_recent_conversations(
    limit: int = 50,
    notebook_id: Optional[str] = None,
    days: Optional[int] = None
):
    """Get recent conversation entries"""
    entries = memory_store.get_recent_conversations(
        limit=limit,
        notebook_id=notebook_id,
        days=days
    )
    
    return {
        "entries": [
            {
                "id": e.id,
                "conversation_id": e.conversation_id,
                "notebook_id": e.notebook_id,
                "role": e.role,
                "content": e.content[:500],  # Truncate for list view
                "timestamp": e.timestamp.isoformat(),
                "topics": e.topics,
            }
            for e in entries
        ]
    }


@router.post("/recall/search")
async def search_recall_memory(request: MemorySearchRequest):
    """Search recall memory by text"""
    entries = memory_store.search_recall_memory(
        query=request.query,
        limit=request.max_results,
        notebook_id=request.notebook_id
    )
    
    return {
        "entries": [
            {
                "id": e.id,
                "conversation_id": e.conversation_id,
                "role": e.role,
                "content": e.content,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in entries
        ]
    }


@router.get("/recall/conversation/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Get all entries for a specific conversation"""
    entries = memory_store.get_conversation(conversation_id)
    return {
        "entries": [
            {
                "id": e.id,
                "role": e.role,
                "content": e.content,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in entries
        ]
    }


# =============================================================================
# Memory Management Endpoints
# =============================================================================

@router.get("/stats", response_model=MemoryStatsResponse)
async def get_memory_stats():
    """Get statistics about all memory tiers"""
    stats = memory_store.get_memory_stats()
    return MemoryStatsResponse(**stats)


@router.post("/compress")
async def compress_memories():
    """Trigger memory compression"""
    stats = await memory_agent.check_and_compress_memories()
    return {
        "success": True,
        "compressed": stats
    }


@router.post("/conflict/resolve")
async def resolve_memory_conflict(
    conflict_id: str,
    request: ConflictResolutionRequest
):
    """Resolve a memory conflict"""
    # In a real implementation, we'd look up the conflict by ID
    # For now, this is a placeholder
    return {
        "success": True,
        "resolution": request.resolution
    }


# =============================================================================
# Memory Context Endpoint (for debugging/inspection)
# =============================================================================

@router.post("/context")
async def get_memory_context(
    query: str,
    notebook_id: Optional[str] = None,
    max_tokens: int = 1500
):
    """Get the memory context that would be injected for a query"""
    context = await memory_agent.get_memory_context(
        query=query,
        notebook_id=notebook_id,
        max_tokens=max_tokens
    )
    
    return {
        "core_memory_block": context.core_memory_block,
        "retrieved_memories": context.retrieved_memories,
        "recent_context": context.recent_context,
        "total_tokens": context.total_tokens
    }


# =============================================================================
# Memory Tools Schema (for LLM integration)
# =============================================================================

@router.get("/tools/schema")
async def get_memory_tools_schema():
    """Get the tool schemas for LLM function calling"""
    return {"tools": memory_agent.get_memory_tools_schema()}


@router.post("/tools/execute")
async def execute_memory_tool(tool_name: str, params: Dict[str, Any]):
    """Execute a memory tool call"""
    result = await memory_agent.execute_memory_tool(tool_name, params)
    return result
