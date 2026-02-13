"""Memory system models for MemGPT-style persistent memory architecture"""
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict
from pydantic import BaseModel, Field
import uuid


class MemorySourceType(str, Enum):
    """Source of the memory"""
    USER_STATED = "user_stated"      # User explicitly said this
    AI_INFERRED = "ai_inferred"      # AI deduced from conversation
    DOCUMENT_EXTRACTED = "document_extracted"  # Extracted from uploaded documents
    SYSTEM = "system"                # System-generated (discovery, consolidation, etc.)
    WEB = "web"                      # Collected from web sources (RSS, scraping, etc.)
    MANUAL = "manual"                # Manually added content


class MemoryCategory(str, Enum):
    """Category of core memory"""
    USER_PREFERENCE = "user_preference"    # How user likes things done
    USER_FACT = "user_fact"                # Facts about the user
    PROJECT_CONTEXT = "project_context"    # Ongoing project/work context
    KEY_DECISION = "key_decision"          # Important decisions made
    RECURRING_THEME = "recurring_theme"    # Patterns across conversations
    IMPORTANT_DATE = "important_date"      # Dates/deadlines to remember
    RELATIONSHIP = "relationship"          # People/entities user mentions
    CUSTOM = "custom"                      # User-defined category


class MemoryImportance(str, Enum):
    """Importance level for prioritization"""
    CRITICAL = "critical"    # Always include in context
    HIGH = "high"            # Include when relevant
    MEDIUM = "medium"        # Include if space permits
    LOW = "low"              # Archive candidate


# =============================================================================
# Core Memory - Always in context (~2K tokens)
# =============================================================================

class CoreMemoryEntry(BaseModel):
    """
    A single entry in core memory.
    Core memory is structured key-value pairs always available to the LLM.
    Limited to ~2K tokens total, so entries must be concise.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    key: str                          # e.g., "user_name", "communication_style"
    value: str                        # e.g., "Patrick", "prefers concise responses"
    category: MemoryCategory = MemoryCategory.USER_FACT
    source_type: MemorySourceType = MemorySourceType.AI_INFERRED
    importance: MemoryImportance = MemoryImportance.MEDIUM
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)  # How confident AI is
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_accessed: datetime = Field(default_factory=datetime.utcnow)
    access_count: int = 0             # How often this memory is retrieved
    source_conversation_id: Optional[str] = None  # Which conversation created this
    supersedes: Optional[str] = None  # ID of memory this replaces (for conflicts)
    
    def to_prompt_string(self) -> str:
        """Format for injection into LLM prompt"""
        return f"- {self.key}: {self.value}"
    
    def token_estimate(self) -> int:
        """Rough token count (4 chars ≈ 1 token)"""
        return len(self.to_prompt_string()) // 4 + 1


class CoreMemory(BaseModel):
    """
    The complete core memory state.
    This is the "working memory" always available to the LLM.
    """
    entries: List[CoreMemoryEntry] = Field(default_factory=list)
    max_tokens: int = 2000
    last_compressed: Optional[datetime] = None
    version: int = 1  # Increment on major changes for conflict resolution
    
    def total_tokens(self) -> int:
        """Estimate total token usage"""
        return sum(e.token_estimate() for e in self.entries)
    
    def needs_compression(self) -> bool:
        """Check if we've exceeded token limit"""
        return self.total_tokens() > self.max_tokens
    
    def to_prompt_block(self) -> str:
        """Format entire core memory for LLM prompt injection"""
        if not self.entries:
            return ""
        
        lines = ["## What I Remember About You", ""]
        
        # Group by category
        by_category: Dict[MemoryCategory, List[CoreMemoryEntry]] = {}
        for entry in self.entries:
            if entry.category not in by_category:
                by_category[entry.category] = []
            by_category[entry.category].append(entry)
        
        # Format each category
        category_labels = {
            MemoryCategory.USER_PREFERENCE: "Your Preferences",
            MemoryCategory.USER_FACT: "About You",
            MemoryCategory.PROJECT_CONTEXT: "Current Projects",
            MemoryCategory.KEY_DECISION: "Key Decisions",
            MemoryCategory.RECURRING_THEME: "Recurring Themes",
            MemoryCategory.IMPORTANT_DATE: "Important Dates",
            MemoryCategory.RELATIONSHIP: "People & Relationships",
            MemoryCategory.CUSTOM: "Other",
        }
        
        for category, entries in by_category.items():
            label = category_labels.get(category, category.value)
            lines.append(f"### {label}")
            for entry in sorted(entries, key=lambda e: e.importance.value):
                lines.append(entry.to_prompt_string())
            lines.append("")
        
        return "\n".join(lines)


# =============================================================================
# Recall Memory - Recent conversations (SQLite)
# =============================================================================

class RecallMemoryEntry(BaseModel):
    """
    A conversation turn stored in recall memory.
    Searchable by text, retrievable by recency.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str              # Groups messages in same conversation
    notebook_id: Optional[str] = None # Which notebook this was in
    role: str                         # "user" or "assistant"
    content: str                      # The actual message
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    # Extracted metadata for search
    topics: List[str] = Field(default_factory=list)      # Key topics mentioned
    entities: List[str] = Field(default_factory=list)    # Named entities
    sentiment: Optional[str] = None   # positive/negative/neutral
    
    # For summarization
    is_summarized: bool = False       # Has this been compressed into archival?
    summary: Optional[str] = None     # Brief summary if compressed


class ConversationSummary(BaseModel):
    """
    Summary of a conversation for archival.
    Created when recall memory is compressed.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    notebook_id: Optional[str] = None
    summary: str                      # LLM-generated summary
    key_points: List[str] = Field(default_factory=list)
    decisions_made: List[str] = Field(default_factory=list)
    action_items: List[str] = Field(default_factory=list)
    start_time: datetime
    end_time: datetime
    message_count: int
    created_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# Archival Memory - Long-term storage (LanceDB vectors)
# =============================================================================

class ArchivalMemoryEntry(BaseModel):
    """
    Long-term memory stored as vectors in LanceDB.
    Unlimited storage, retrieved by semantic similarity.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str                      # The memory content
    content_type: str                 # "conversation_summary", "extracted_fact", "user_note"
    source_type: MemorySourceType = MemorySourceType.AI_INFERRED
    
    # Source tracking
    source_id: Optional[str] = None   # ID of source (conversation, document, etc.)
    source_notebook_id: Optional[str] = None
    
    # Metadata for retrieval
    topics: List[str] = Field(default_factory=list)
    entities: List[str] = Field(default_factory=list)
    importance: MemoryImportance = MemoryImportance.MEDIUM
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_accessed: datetime = Field(default_factory=datetime.utcnow)
    access_count: int = 0
    
    # Vector will be computed and stored separately in LanceDB
    # embedding: List[float] - stored in LanceDB table


# =============================================================================
# Memory Operations
# =============================================================================

class MemorySearchResult(BaseModel):
    """Result from searching archival memory"""
    entry: ArchivalMemoryEntry
    similarity_score: float
    recency_score: float
    combined_score: float


class MemoryConflict(BaseModel):
    """Detected conflict between memories"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    existing_memory_id: str
    new_memory_content: str
    conflict_type: str                # "contradiction", "update", "duplicate"
    resolution: Optional[str] = None  # "keep_existing", "use_new", "merge", "ask_user"
    resolved: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MemoryExtractionRequest(BaseModel):
    """Request to extract memories from a message"""
    message: str
    role: str                         # "user" or "assistant"
    conversation_id: str
    notebook_id: Optional[str] = None
    context: Optional[str] = None     # Previous messages for context


class MemoryExtractionResult(BaseModel):
    """Result of memory extraction"""
    core_memories: List[CoreMemoryEntry] = Field(default_factory=list)
    archival_memories: List[ArchivalMemoryEntry] = Field(default_factory=list)
    conflicts: List[MemoryConflict] = Field(default_factory=list)


class MemoryContext(BaseModel):
    """Memory context to inject into LLM prompt"""
    core_memory_block: str            # Formatted core memory
    retrieved_memories: List[str]     # Relevant archival memories
    recent_context: List[str]         # Recent conversation snippets
    total_tokens: int                 # Estimated token count


# =============================================================================
# Memory Tool Calls (for LLM to manage its own memory)
# =============================================================================

class MemoryToolCall(BaseModel):
    """Base for memory tool calls"""
    tool_name: str
    reasoning: str                    # Why the LLM is making this call


class CoreMemoryAppend(MemoryToolCall):
    """LLM requests to add to core memory"""
    tool_name: str = "core_memory_append"
    key: str
    value: str
    category: MemoryCategory = MemoryCategory.USER_FACT
    importance: MemoryImportance = MemoryImportance.MEDIUM


class CoreMemoryUpdate(MemoryToolCall):
    """LLM requests to update existing core memory"""
    tool_name: str = "core_memory_update"
    memory_id: str
    new_value: str
    reasoning: str


class CoreMemoryDelete(MemoryToolCall):
    """LLM requests to remove from core memory"""
    tool_name: str = "core_memory_delete"
    memory_id: str
    reasoning: str


class ArchivalMemoryInsert(MemoryToolCall):
    """LLM requests to store in archival memory"""
    tool_name: str = "archival_memory_insert"
    content: str
    topics: List[str] = Field(default_factory=list)
    importance: MemoryImportance = MemoryImportance.MEDIUM


class ArchivalMemorySearch(MemoryToolCall):
    """LLM requests to search archival memory"""
    tool_name: str = "archival_memory_search"
    query: str
    max_results: int = 5


class RecallMemorySearch(MemoryToolCall):
    """LLM requests to search recent conversations"""
    tool_name: str = "recall_memory_search"
    query: str
    max_results: int = 10
    time_range_days: Optional[int] = None  # Limit to recent N days
