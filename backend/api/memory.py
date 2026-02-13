"""Memory API endpoints for viewing and managing persistent memory"""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models.memory import (
    CoreMemory, CoreMemoryEntry, MemoryCategory, MemoryImportance,
    ArchivalMemoryEntry
)
from storage.memory_store import memory_store, AgentNamespace
from services.memory_agent import memory_agent
from services.memory_manager import memory_manager


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
    namespace: Optional[str] = "system"
    cross_notebook: bool = False


class UserSignalRequest(BaseModel):
    notebook_id: str
    signal_type: str  # view, click, ignore, search_miss, manual_add
    item_id: Optional[str] = None
    query: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


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
    """Search archival memory by semantic similarity with namespace isolation"""
    # Parse namespace
    try:
        namespace = AgentNamespace(request.namespace) if request.namespace else AgentNamespace.SYSTEM
    except ValueError:
        namespace = AgentNamespace.SYSTEM
    
    results = memory_store.search_archival_memory(
        query=request.query,
        limit=request.max_results,
        namespace=namespace,
        notebook_id=request.notebook_id,
        cross_notebook=request.cross_notebook
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
    notebook_id: Optional[str] = None,
    namespace: str = "system"
):
    """Manually add an archival memory with namespace"""
    entry = ArchivalMemoryEntry(
        content=content,
        content_type=content_type,
        topics=topics,
        source_notebook_id=notebook_id,
    )
    
    # Parse namespace
    try:
        ns = AgentNamespace(namespace)
    except ValueError:
        ns = AgentNamespace.SYSTEM
    
    memory_store.add_archival_memory(entry, namespace=ns, notebook_id=notebook_id)
    return {"success": True, "memory_id": entry.id, "namespace": ns.value}


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


@router.post("/consolidate")
async def trigger_consolidation():
    """Manually trigger full memory consolidation cycle (Tier 3)"""
    result = await memory_manager.run_consolidation()
    return result


@router.post("/consolidate/compact")
async def trigger_compact():
    """Manually trigger Tier 1: Hourly event compaction"""
    result = await memory_manager.run_compact()
    return result


@router.post("/consolidate/patterns")
async def trigger_pattern_analysis():
    """Manually trigger Tier 2: 3-hour pattern analysis"""
    result = await memory_manager.run_pattern_analysis()
    return result


@router.post("/consolidate/deep")
async def trigger_deep_consolidation():
    """Manually trigger Tier 3: 6-hour deep consolidation"""
    result = await memory_manager.run_consolidation()
    return result


@router.post("/consolidate/daily")
async def trigger_daily_summary():
    """Manually trigger Tier 4: Daily summary"""
    result = await memory_manager.run_daily_summary()
    return result


@router.get("/consolidation/status")
async def get_consolidation_status():
    """Get current consolidation scheduler status"""
    return memory_manager.get_consolidation_status()


# =============================================================================
# User Signals (Negative Signal Learning)
# =============================================================================

@router.post("/signals")
async def record_user_signal(request: UserSignalRequest):
    """Record a user signal for learning (view, click, ignore, search_miss, manual_add)"""
    valid_types = ["view", "click", "ignore", "search_miss", "manual_add"]
    if request.signal_type not in valid_types:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid signal_type. Must be one of: {valid_types}"
        )
    
    memory_store.record_user_signal(
        notebook_id=request.notebook_id,
        signal_type=request.signal_type,
        item_id=request.item_id,
        query=request.query,
        metadata=request.metadata
    )
    return {"success": True}


@router.get("/signals/{notebook_id}")
async def get_user_signals(
    notebook_id: str,
    signal_type: Optional[str] = None,
    since_days: int = 30,
    limit: int = 100
):
    """Get user signals for a notebook"""
    signals = memory_store.get_user_signals(
        notebook_id=notebook_id,
        signal_type=signal_type,
        since_days=since_days,
        limit=limit
    )
    return {"signals": signals}


@router.get("/signals/{notebook_id}/ignored")
async def get_ignored_items(notebook_id: str, days_threshold: int = 7):
    """Get items that were shown but never clicked (negative signal)"""
    items = memory_store.get_ignored_items(notebook_id, days_threshold)
    return {"ignored_items": items, "count": len(items)}


@router.get("/signals/{notebook_id}/search-misses")
async def get_search_misses(notebook_id: str, since_days: int = 30):
    """Get queries where user searched but Collector had no results"""
    queries = memory_store.get_search_misses(notebook_id, since_days)
    return {"search_misses": queries, "count": len(queries)}


# =============================================================================
# Namespace-aware Stats
# =============================================================================

@router.get("/namespaces/{notebook_id}")
async def get_notebook_memory_stats(notebook_id: str):
    """Get memory statistics for a specific notebook's collector namespace"""
    collector_count = memory_store.get_archival_memory_count(
        namespace=AgentNamespace.COLLECTOR,
        notebook_id=notebook_id
    )
    
    signals = memory_store.get_user_signals(notebook_id, limit=10)
    ignored = memory_store.get_ignored_items(notebook_id)
    
    return {
        "notebook_id": notebook_id,
        "collector_memories": collector_count,
        "recent_signals": len(signals),
        "ignored_items": len(ignored),
        "system_memories": memory_store.get_archival_memory_count(AgentNamespace.SYSTEM)
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
