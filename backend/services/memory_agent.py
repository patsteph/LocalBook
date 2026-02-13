"""
Memory Agent - Orchestrates memory extraction, retrieval, and management

This is the "brain" of the memory system that:
1. Extracts memorable information from conversations
2. Decides what to store in which memory tier
3. Retrieves relevant memories for context injection
4. Handles memory compression and conflict resolution
5. Provides tool calls for LLM to manage its own memory
"""
import json
import re
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import tiktoken
import httpx

from models.memory import (
    CoreMemory, CoreMemoryEntry, MemoryCategory, MemoryImportance, MemorySourceType,
    RecallMemoryEntry, ConversationSummary,
    ArchivalMemoryEntry, MemoryConflict, MemoryExtractionRequest,
    MemoryExtractionResult, MemoryContext
)
from storage.memory_store import memory_store
from config import settings


class MemoryAgent:
    """
    Orchestrates all memory operations.
    Singleton pattern for consistent state.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        # Token counter
        try:
            self._tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._tokenizer = None
        
        # Thresholds
        self.core_memory_max_tokens = 2000
        self.recall_compression_threshold = 100  # Compress after N conversations
        self.archival_retrieval_count = 5
        self.recall_retrieval_count = 10
        
        # LLM settings
        self.ollama_url = settings.ollama_base_url
        self.extraction_model = settings.ollama_fast_model  # Use fast model for extraction
        
        self._initialized = True
    
    # =========================================================================
    # Token Counting
    # =========================================================================
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in text"""
        if self._tokenizer:
            return len(self._tokenizer.encode(text))
        # Fallback: rough estimate
        return len(text) // 4
    
    # =========================================================================
    # Memory Extraction
    # =========================================================================
    
    async def extract_memories(self, request: MemoryExtractionRequest) -> MemoryExtractionResult:
        """
        Extract memorable information from a message.
        Uses LLM to identify facts, preferences, and important information.
        """
        result = MemoryExtractionResult()
        
        # Only extract from user messages (AI responses don't contain new user info)
        if request.role != "user":
            # Still store in recall memory
            await self._store_in_recall(request)
            return result
        
        # Store in recall memory first
        await self._store_in_recall(request)
        
        # Use LLM to extract memorable information
        extraction_prompt = self._build_extraction_prompt(request)
        
        try:
            extracted = await self._call_llm_for_extraction(extraction_prompt)
            
            if extracted:
                # Process extracted memories
                await self._process_extracted_memories(extracted, request, result)
        except Exception as e:
            print(f"Memory extraction error: {e}")
        
        return result
    
    def _build_extraction_prompt(self, request: MemoryExtractionRequest) -> str:
        """Build prompt for memory extraction.
        
        v1.1.0: Added example for better small model performance per PROMPT_AUDIT.md
        """
        return f"""Analyze this user message and extract any memorable information.

User message: "{request.message}"

{f'Context from conversation: {request.context}' if request.context else ''}

Extract the following if present (respond in JSON):
{{
    "user_facts": [
        {{"key": "name/preference/fact key", "value": "the information", "category": "user_fact|user_preference|project_context|key_decision|important_date|relationship", "importance": "critical|high|medium|low"}}
    ],
    "topics_mentioned": ["topic1", "topic2"],
    "entities_mentioned": ["person/place/thing"],
    "should_remember_long_term": "brief description of anything worth remembering long-term, or null"
}}

Rules:
- Only extract EXPLICIT information the user stated
- Don't infer or assume
- user_facts should be concise key-value pairs
- importance: critical=always remember, high=usually relevant, medium=sometimes useful, low=nice to know
- If nothing memorable, return empty arrays and null

EXAMPLE:
Input: "I'm working on the Q2 product launch with Sarah. The deadline is March 15th."
Output:
{{"user_facts": [{{"key": "current_project", "value": "Q2 product launch", "category": "project_context", "importance": "high"}}, {{"key": "deadline", "value": "March 15th", "category": "important_date", "importance": "critical"}}], "topics_mentioned": ["product launch", "Q2"], "entities_mentioned": ["Sarah"], "should_remember_long_term": "User is working on Q2 product launch with Sarah, deadline March 15th"}}

Respond ONLY with the JSON, no other text."""
    
    async def _call_llm_for_extraction(self, prompt: str) -> Optional[Dict]:
        """Call LLM to extract memories"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.extraction_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,  # Low temperature for consistent extraction
                        "num_predict": 500,
                    }
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                text = result.get("response", "")
                
                # Parse JSON from response
                try:
                    # Find JSON in response
                    json_match = re.search(r'\{[\s\S]*\}', text)
                    if json_match:
                        return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
        
        return None
    
    async def _process_extracted_memories(
        self, 
        extracted: Dict, 
        request: MemoryExtractionRequest,
        result: MemoryExtractionResult
    ) -> None:
        """Process extracted memories and store them"""
        
        # Process user facts -> Core Memory
        for fact in extracted.get("user_facts", []):
            entry = CoreMemoryEntry(
                key=fact.get("key", "unknown"),
                value=fact.get("value", ""),
                category=self._parse_category(fact.get("category", "user_fact")),
                importance=self._parse_importance(fact.get("importance", "medium")),
                source_type=MemorySourceType.USER_STATED,
                source_conversation_id=request.conversation_id,
            )
            
            # Check for conflicts
            existing = memory_store.find_similar_core_memory(f"{entry.key}: {entry.value}")
            if existing:
                conflict = MemoryConflict(
                    existing_memory_id=existing.id,
                    new_memory_content=entry.value,
                    conflict_type="update" if existing.key == entry.key else "similar"
                )
                result.conflicts.append(conflict)
                
                # Auto-resolve: update if same key, otherwise keep both
                if existing.key.lower() == entry.key.lower():
                    memory_store.update_core_memory(existing.id, entry.value)
                else:
                    # Different key but similar content - skip to avoid duplicates
                    continue
            else:
                success, conflict = memory_store.add_core_memory(entry)
                if success:
                    result.core_memories.append(entry)
                elif conflict:
                    result.conflicts.append(conflict)
        
        # Process long-term memories -> Archival Memory
        long_term = extracted.get("should_remember_long_term")
        if long_term:
            archival_entry = ArchivalMemoryEntry(
                content=long_term,
                content_type="extracted_fact",
                source_type=MemorySourceType.USER_STATED,
                source_id=request.conversation_id,
                source_notebook_id=request.notebook_id,
                topics=extracted.get("topics_mentioned", []),
                entities=extracted.get("entities_mentioned", []),
            )
            memory_store.add_archival_memory(archival_entry)
            result.archival_memories.append(archival_entry)
    
    async def _store_in_recall(self, request: MemoryExtractionRequest) -> None:
        """Store message in recall memory"""
        entry = RecallMemoryEntry(
            conversation_id=request.conversation_id,
            notebook_id=request.notebook_id,
            role=request.role,
            content=request.message,
        )
        memory_store.add_recall_entry(entry)
    
    def _parse_category(self, category_str: str) -> MemoryCategory:
        """Parse category string to enum"""
        mapping = {
            "user_fact": MemoryCategory.USER_FACT,
            "user_preference": MemoryCategory.USER_PREFERENCE,
            "project_context": MemoryCategory.PROJECT_CONTEXT,
            "key_decision": MemoryCategory.KEY_DECISION,
            "important_date": MemoryCategory.IMPORTANT_DATE,
            "relationship": MemoryCategory.RELATIONSHIP,
            "recurring_theme": MemoryCategory.RECURRING_THEME,
        }
        return mapping.get(category_str.lower(), MemoryCategory.USER_FACT)
    
    def _parse_importance(self, importance_str: str) -> MemoryImportance:
        """Parse importance string to enum"""
        mapping = {
            "critical": MemoryImportance.CRITICAL,
            "high": MemoryImportance.HIGH,
            "medium": MemoryImportance.MEDIUM,
            "low": MemoryImportance.LOW,
        }
        return mapping.get(importance_str.lower(), MemoryImportance.MEDIUM)
    
    # =========================================================================
    # Memory Retrieval
    # =========================================================================
    
    async def get_memory_context(
        self, 
        query: str,
        notebook_id: Optional[str] = None,
        max_tokens: int = 1500
    ) -> MemoryContext:
        """
        Get relevant memory context to inject into LLM prompt.
        Combines core memory, relevant archival memories, and recent context.
        """
        # 1. Core memory (always included)
        core_memory = memory_store.load_core_memory()
        core_block = core_memory.to_prompt_block()
        core_tokens = self.count_tokens(core_block)
        
        remaining_tokens = max_tokens - core_tokens
        
        # 2. Search archival memory for relevant context
        retrieved_memories = []
        if remaining_tokens > 200:
            archival_results = memory_store.search_archival_memory(
                query=query,
                limit=self.archival_retrieval_count,
                notebook_id=notebook_id
            )
            
            for result in archival_results:
                memory_text = f"[Memory] {result.entry.content}"
                tokens = self.count_tokens(memory_text)
                if tokens <= remaining_tokens:
                    retrieved_memories.append(memory_text)
                    remaining_tokens -= tokens
        
        # 3. Get recent conversation context
        recent_context = []
        if remaining_tokens > 100:
            recent_entries = memory_store.get_recent_conversations(
                limit=self.recall_retrieval_count,
                notebook_id=notebook_id,
                days=7  # Last week
            )
            
            for entry in recent_entries[:5]:  # Limit to 5 most recent
                context_text = f"[{entry.role}] {entry.content[:200]}..."
                tokens = self.count_tokens(context_text)
                if tokens <= remaining_tokens:
                    recent_context.append(context_text)
                    remaining_tokens -= tokens
        
        total_tokens = max_tokens - remaining_tokens
        
        return MemoryContext(
            core_memory_block=core_block,
            retrieved_memories=retrieved_memories,
            recent_context=recent_context,
            total_tokens=total_tokens
        )
    
    def build_memory_augmented_prompt(
        self, 
        system_prompt: str,
        memory_context: MemoryContext
    ) -> str:
        """
        Build a system prompt augmented with memory context.
        """
        parts = [system_prompt]
        
        # Add core memory
        if memory_context.core_memory_block:
            parts.append("\n" + memory_context.core_memory_block)
        
        # Add retrieved memories
        if memory_context.retrieved_memories:
            parts.append("\n## Relevant Past Context")
            parts.extend(memory_context.retrieved_memories)
        
        # Add recent context
        if memory_context.recent_context:
            parts.append("\n## Recent Conversation")
            parts.extend(memory_context.recent_context)
        
        return "\n".join(parts)
    
    # =========================================================================
    # Memory Compression
    # =========================================================================
    
    async def check_and_compress_memories(self) -> Dict[str, Any]:
        """
        Check if memory compression is needed and perform it.
        Returns stats about what was compressed.
        """
        stats = {
            "core_compressed": False,
            "recall_compressed": 0,
            "archival_added": 0,
        }
        
        # Check core memory
        core_memory = memory_store.load_core_memory()
        if core_memory.needs_compression():
            await self._compress_core_memory(core_memory)
            stats["core_compressed"] = True
        
        # Check recall memory
        recall_count = memory_store.get_recall_entry_count()
        if recall_count > self.recall_compression_threshold:
            compressed = await self._compress_recall_memory()
            stats["recall_compressed"] = compressed
            stats["archival_added"] = compressed
        
        return stats
    
    async def _compress_core_memory(self, memory: CoreMemory) -> None:
        """
        Compress core memory when it exceeds token limit.
        Strategy: Summarize low-importance entries, archive old entries.
        """
        # Sort by importance and recency
        entries = sorted(
            memory.entries,
            key=lambda e: (
                e.importance.value,  # Lower importance first
                e.last_accessed      # Older first
            )
        )
        
        # Remove entries until under limit
        while memory.needs_compression() and entries:
            entry_to_archive = entries.pop(0)
            
            # Archive to long-term memory
            archival_entry = ArchivalMemoryEntry(
                content=f"{entry_to_archive.key}: {entry_to_archive.value}",
                content_type="archived_core_memory",
                source_type=entry_to_archive.source_type,
                importance=entry_to_archive.importance,
            )
            memory_store.add_archival_memory(archival_entry)
            
            # Remove from core
            memory.entries = [e for e in memory.entries if e.id != entry_to_archive.id]
        
        memory.last_compressed = datetime.utcnow()
        memory_store.save_core_memory(memory)
    
    async def _compress_recall_memory(self) -> int:
        """
        Compress old recall memory into archival summaries.
        Returns number of conversations compressed.
        """
        # Get old conversations (older than 7 days)
        cutoff = datetime.utcnow() - timedelta(days=7)
        
        # Group by conversation
        recent = memory_store.get_recent_conversations(limit=1000)
        conversations: Dict[str, List[RecallMemoryEntry]] = {}
        
        for entry in recent:
            if entry.timestamp < cutoff and not entry.is_summarized:
                if entry.conversation_id not in conversations:
                    conversations[entry.conversation_id] = []
                conversations[entry.conversation_id].append(entry)
        
        compressed_count = 0
        
        for conv_id, entries in conversations.items():
            if len(entries) < 2:
                continue
            
            # Summarize conversation
            summary = await self._summarize_conversation(entries)
            if summary:
                # Save summary
                memory_store.save_conversation_summary(summary)
                
                # Archive key points
                archival_entry = ArchivalMemoryEntry(
                    content=summary.summary,
                    content_type="conversation_summary",
                    source_type=MemorySourceType.AI_INFERRED,
                    source_id=conv_id,
                    source_notebook_id=entries[0].notebook_id,
                    topics=entries[0].topics,
                )
                memory_store.add_archival_memory(archival_entry)
                
                # Mark as summarized
                memory_store.mark_entries_summarized(conv_id)
                compressed_count += 1
        
        return compressed_count
    
    async def _summarize_conversation(self, entries: List[RecallMemoryEntry]) -> Optional[ConversationSummary]:
        """Use LLM to summarize a conversation"""
        # Build conversation text
        conv_text = "\n".join([
            f"{e.role}: {e.content}" for e in sorted(entries, key=lambda x: x.timestamp)
        ])
        
        prompt = f"""Summarize this conversation concisely:

{conv_text[:3000]}  # Limit length

Respond in JSON:
{{
    "summary": "2-3 sentence summary",
    "key_points": ["point 1", "point 2"],
    "decisions_made": ["decision 1"] or [],
    "action_items": ["action 1"] or []
}}"""
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.extraction_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.1}
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    text = result.get("response", "")
                    
                    json_match = re.search(r'\{[\s\S]*\}', text)
                    if json_match:
                        data = json.loads(json_match.group())
                        
                        return ConversationSummary(
                            conversation_id=entries[0].conversation_id,
                            notebook_id=entries[0].notebook_id,
                            summary=data.get("summary", ""),
                            key_points=data.get("key_points", []),
                            decisions_made=data.get("decisions_made", []),
                            action_items=data.get("action_items", []),
                            start_time=min(e.timestamp for e in entries),
                            end_time=max(e.timestamp for e in entries),
                            message_count=len(entries),
                        )
        except Exception as e:
            print(f"Conversation summarization error: {e}")
        
        return None
    
    # =========================================================================
    # LLM Tool Calls (for LLM to manage its own memory)
    # =========================================================================
    
    def get_memory_tools_schema(self) -> List[Dict]:
        """Get tool schemas for LLM function calling"""
        return [
            {
                "name": "core_memory_append",
                "description": "Add a new fact or preference to remember about the user",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Short label for the memory"},
                        "value": {"type": "string", "description": "The information to remember"},
                        "category": {
                            "type": "string",
                            "enum": ["user_preference", "user_fact", "project_context", "key_decision", "important_date", "relationship"],
                            "description": "Category of memory"
                        },
                        "importance": {
                            "type": "string",
                            "enum": ["critical", "high", "medium", "low"],
                            "description": "How important is this to remember"
                        }
                    },
                    "required": ["key", "value"]
                }
            },
            {
                "name": "core_memory_update",
                "description": "Update an existing memory with new information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string", "description": "ID of memory to update"},
                        "new_value": {"type": "string", "description": "Updated value"}
                    },
                    "required": ["memory_id", "new_value"]
                }
            },
            {
                "name": "archival_memory_search",
                "description": "Search long-term memory for relevant past information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for"},
                        "max_results": {"type": "integer", "description": "Maximum results to return", "default": 5}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "recall_memory_search",
                "description": "Search recent conversations for specific information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for"},
                        "days": {"type": "integer", "description": "Limit to last N days", "default": 7}
                    },
                    "required": ["query"]
                }
            }
        ]
    
    async def execute_memory_tool(self, tool_name: str, params: Dict) -> Dict[str, Any]:
        """Execute a memory tool call from the LLM"""
        
        if tool_name == "core_memory_append":
            entry = CoreMemoryEntry(
                key=params["key"],
                value=params["value"],
                category=self._parse_category(params.get("category", "user_fact")),
                importance=self._parse_importance(params.get("importance", "medium")),
                source_type=MemorySourceType.AI_INFERRED,
            )
            success, conflict = memory_store.add_core_memory(entry)
            return {
                "success": success,
                "memory_id": entry.id if success else None,
                "conflict": conflict.model_dump() if conflict else None
            }
        
        elif tool_name == "core_memory_update":
            success = memory_store.update_core_memory(
                params["memory_id"],
                params["new_value"]
            )
            return {"success": success}
        
        elif tool_name == "archival_memory_search":
            results = memory_store.search_archival_memory(
                query=params["query"],
                limit=params.get("max_results", 5)
            )
            return {
                "results": [
                    {"content": r.entry.content, "score": r.combined_score}
                    for r in results
                ]
            }
        
        elif tool_name == "recall_memory_search":
            results = memory_store.search_recall_memory(
                query=params["query"]
            )
            return {
                "results": [
                    {"role": r.role, "content": r.content[:200], "timestamp": r.timestamp.isoformat()}
                    for r in results[:10]
                ]
            }
        
        return {"error": f"Unknown tool: {tool_name}"}
    
    # =========================================================================
    # Conflict Resolution
    # =========================================================================
    
    async def resolve_conflict(
        self, 
        conflict: MemoryConflict,
        resolution: str,
        new_value: Optional[str] = None
    ) -> bool:
        """
        Resolve a memory conflict.
        resolution: "keep_existing", "use_new", "merge"
        """
        if resolution == "keep_existing":
            conflict.resolution = "keep_existing"
            conflict.resolved = True
            return True
        
        elif resolution == "use_new":
            if new_value:
                memory_store.update_core_memory(conflict.existing_memory_id, new_value)
            conflict.resolution = "use_new"
            conflict.resolved = True
            return True
        
        elif resolution == "merge" and new_value:
            # Merge requires a new combined value
            memory_store.update_core_memory(conflict.existing_memory_id, new_value)
            conflict.resolution = "merge"
            conflict.resolved = True
            return True
        
        return False


# Singleton instance
memory_agent = MemoryAgent()
