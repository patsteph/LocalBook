"""
Memory Store - Persistent storage for multi-tiered memory architecture

Three-tier storage:
1. Core Memory: JSON file (~2K tokens, always in context)
2. Recall Memory: SQLite (recent conversations, searchable by text)
3. Archival Memory: LanceDB (unlimited long-term, vector search)

Namespace Isolation:
- SYSTEM: App-wide memories, user preferences
- CURATOR: Cross-notebook synthesis, global insights
- COLLECTOR: Per-notebook isolated memories
"""
import json
import sqlite3
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import threading
import lancedb
import requests


class AgentNamespace(str, Enum):
    """Memory namespace for agent isolation"""
    SYSTEM = "system"        # App-wide, user preferences, global context
    CURATOR = "curator"      # Cross-notebook synthesis, global insights
    COLLECTOR = "collector"  # Per-notebook isolation (requires notebook_id)


from models.memory import (
    CoreMemory, CoreMemoryEntry, MemoryCategory, MemoryImportance, MemorySourceType,
    RecallMemoryEntry, ConversationSummary,
    ArchivalMemoryEntry, MemorySearchResult, MemoryConflict
)
from config import settings


class MemoryStore:
    """
    Unified interface for all memory tiers.
    Thread-safe with connection pooling for SQLite.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.data_dir = Path(settings.data_dir)
        self.memory_dir = self.data_dir / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        
        # Core memory file
        self.core_memory_path = self.memory_dir / "core_memory.json"
        
        # SQLite for recall memory
        self.recall_db_path = self.memory_dir / "recall_memory.db"
        self._init_recall_db()
        
        # LanceDB for archival memory
        self.archival_db_path = self.memory_dir / "archival_memory"
        self._init_archival_db()
        
        # Embedding model (lazy loaded)
        self._embedding_model = None
        
        # Cache for core memory
        self._core_memory_cache: Optional[CoreMemory] = None
        self._core_memory_lock = threading.Lock()
        
        self._initialized = True
    
    # =========================================================================
    # Embedding Model - Uses Ollama (same as RAG engine for consistency)
    # =========================================================================
    
    def get_embedding(self, text: str) -> List[float]:
        """Generate embedding using Ollama API (matches RAG engine)"""
        try:
            response = requests.post(
                f"{settings.ollama_base_url}/api/embeddings",
                json={
                    "model": settings.embedding_model,  # snowflake-arctic-embed2
                    "prompt": text
                },
                timeout=60
            )
            result = response.json()
            embedding = result.get("embedding", [])
            if embedding:
                return embedding
            # Fallback to zero vector if empty
            return [0.0] * settings.embedding_dim
        except Exception as e:
            print(f"[MemoryStore] Embedding error: {e}")
            return [0.0] * settings.embedding_dim
    
    # =========================================================================
    # Core Memory (JSON file)
    # =========================================================================
    
    def load_core_memory(self) -> CoreMemory:
        """Load core memory from disk"""
        with self._core_memory_lock:
            if self._core_memory_cache is not None:
                return self._core_memory_cache
            
            if self.core_memory_path.exists():
                try:
                    data = json.loads(self.core_memory_path.read_text())
                    self._core_memory_cache = CoreMemory(**data)
                except Exception as e:
                    print(f"Error loading core memory: {e}")
                    self._core_memory_cache = CoreMemory()
            else:
                self._core_memory_cache = CoreMemory()
            
            return self._core_memory_cache
    
    def save_core_memory(self, memory: CoreMemory) -> None:
        """Save core memory to disk"""
        with self._core_memory_lock:
            self._core_memory_cache = memory
            self.core_memory_path.write_text(
                memory.model_dump_json(indent=2)
            )
    
    def add_core_memory(self, entry: CoreMemoryEntry) -> Tuple[bool, Optional[MemoryConflict]]:
        """
        Add entry to core memory.
        Returns (success, conflict) - conflict if similar entry exists.
        """
        memory = self.load_core_memory()
        
        # Check for conflicts (same key or very similar content)
        for existing in memory.entries:
            if existing.key.lower() == entry.key.lower():
                # Same key - this is an update
                conflict = MemoryConflict(
                    existing_memory_id=existing.id,
                    new_memory_content=entry.value,
                    conflict_type="update"
                )
                return False, conflict
        
        # Check token limit
        memory.entries.append(entry)
        if memory.needs_compression():
            # Remove the entry we just added
            memory.entries.pop()
            return False, None  # Need compression first
        
        self.save_core_memory(memory)
        return True, None
    
    def update_core_memory(self, memory_id: str, new_value: str) -> bool:
        """Update existing core memory entry"""
        memory = self.load_core_memory()
        
        for entry in memory.entries:
            if entry.id == memory_id:
                entry.value = new_value
                entry.updated_at = datetime.utcnow()
                entry.access_count += 1
                self.save_core_memory(memory)
                return True
        
        return False
    
    def delete_core_memory(self, memory_id: str) -> bool:
        """Delete core memory entry"""
        memory = self.load_core_memory()
        original_count = len(memory.entries)
        memory.entries = [e for e in memory.entries if e.id != memory_id]
        
        if len(memory.entries) < original_count:
            self.save_core_memory(memory)
            return True
        return False
    
    def get_core_memory_by_category(self, category: MemoryCategory) -> List[CoreMemoryEntry]:
        """Get all core memories of a specific category"""
        memory = self.load_core_memory()
        return [e for e in memory.entries if e.category == category]
    
    def find_similar_core_memory(self, content: str, threshold: float = 0.85) -> Optional[CoreMemoryEntry]:
        """Find core memory with similar content using embeddings"""
        memory = self.load_core_memory()
        if not memory.entries:
            return None
        
        query_embedding = self.get_embedding(content)
        
        best_match = None
        best_score = 0.0
        
        for entry in memory.entries:
            entry_embedding = self.get_embedding(f"{entry.key}: {entry.value}")
            # Cosine similarity
            score = sum(a * b for a, b in zip(query_embedding, entry_embedding))
            score /= (sum(a**2 for a in query_embedding) ** 0.5)
            score /= (sum(b**2 for b in entry_embedding) ** 0.5)
            
            if score > best_score and score >= threshold:
                best_score = score
                best_match = entry
        
        return best_match
    
    # =========================================================================
    # Recall Memory (SQLite)
    # =========================================================================
    
    def _init_recall_db(self) -> None:
        """Initialize SQLite database for recall memory"""
        conn = sqlite3.connect(str(self.recall_db_path))
        cursor = conn.cursor()

        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        
        # Conversation turns
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recall_entries (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                notebook_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                topics TEXT,
                entities TEXT,
                sentiment TEXT,
                is_summarized INTEGER DEFAULT 0,
                summary TEXT
            )
        """)
        
        # Conversation summaries
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                notebook_id TEXT,
                summary TEXT NOT NULL,
                key_points TEXT,
                decisions_made TEXT,
                action_items TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        
        # Indexes for fast retrieval
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recall_conversation ON recall_entries(conversation_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recall_timestamp ON recall_entries(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recall_notebook ON recall_entries(notebook_id)")
        
        # Access tracking for archival memories (LanceDB doesn't support updates)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS archival_access (
                memory_id TEXT PRIMARY KEY,
                access_count INTEGER DEFAULT 0,
                last_accessed TEXT,
                usefulness_score REAL DEFAULT 0.5,
                decay_rate REAL DEFAULT 0.1
            )
        """)
        
        # User signals for negative signal learning (Enhancement #3)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_signals (
                id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                item_id TEXT,
                query TEXT,
                timestamp TEXT NOT NULL,
                metadata TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_notebook ON user_signals(notebook_id, signal_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON user_signals(timestamp)")
        
        conn.commit()
        conn.close()
    
    def _get_recall_connection(self) -> sqlite3.Connection:
        """Get SQLite connection (thread-local)"""
        conn = sqlite3.connect(str(self.recall_db_path))
        conn.execute("PRAGMA busy_timeout=5000")
        return conn
    
    def add_recall_entry(self, entry: RecallMemoryEntry) -> None:
        """Add conversation turn to recall memory"""
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO recall_entries 
            (id, conversation_id, notebook_id, role, content, timestamp, topics, entities, sentiment, is_summarized, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.id,
            entry.conversation_id,
            entry.notebook_id,
            entry.role,
            entry.content,
            entry.timestamp.isoformat(),
            json.dumps(entry.topics),
            json.dumps(entry.entities),
            entry.sentiment,
            1 if entry.is_summarized else 0,
            entry.summary
        ))
        
        conn.commit()
        conn.close()
    
    def get_recent_conversations(
        self, 
        limit: int = 50,
        notebook_id: Optional[str] = None,
        days: Optional[int] = None
    ) -> List[RecallMemoryEntry]:
        """Get recent conversation entries"""
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        
        query = "SELECT * FROM recall_entries WHERE 1=1"
        params = []
        
        if notebook_id:
            query += " AND notebook_id = ?"
            params.append(notebook_id)
        
        if days:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            query += " AND timestamp > ?"
            params.append(cutoff)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        entries = []
        for row in rows:
            entries.append(RecallMemoryEntry(
                id=row[0],
                conversation_id=row[1],
                notebook_id=row[2],
                role=row[3],
                content=row[4],
                timestamp=datetime.fromisoformat(row[5]),
                topics=json.loads(row[6]) if row[6] else [],
                entities=json.loads(row[7]) if row[7] else [],
                sentiment=row[8],
                is_summarized=bool(row[9]),
                summary=row[10]
            ))
        
        return entries
    
    def search_recall_memory(
        self, 
        query: str, 
        limit: int = 20,
        notebook_id: Optional[str] = None
    ) -> List[RecallMemoryEntry]:
        """Search recall memory by text content"""
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        
        sql = """
            SELECT * FROM recall_entries 
            WHERE content LIKE ?
        """
        params = [f"%{query}%"]
        
        if notebook_id:
            sql += " AND notebook_id = ?"
            params.append(notebook_id)
        
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()
        
        entries = []
        for row in rows:
            entries.append(RecallMemoryEntry(
                id=row[0],
                conversation_id=row[1],
                notebook_id=row[2],
                role=row[3],
                content=row[4],
                timestamp=datetime.fromisoformat(row[5]),
                topics=json.loads(row[6]) if row[6] else [],
                entities=json.loads(row[7]) if row[7] else [],
                sentiment=row[8],
                is_summarized=bool(row[9]),
                summary=row[10]
            ))
        
        return entries
    
    def get_conversation(self, conversation_id: str) -> List[RecallMemoryEntry]:
        """Get all entries for a specific conversation"""
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM recall_entries WHERE conversation_id = ? ORDER BY timestamp",
            (conversation_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        
        entries = []
        for row in rows:
            entries.append(RecallMemoryEntry(
                id=row[0],
                conversation_id=row[1],
                notebook_id=row[2],
                role=row[3],
                content=row[4],
                timestamp=datetime.fromisoformat(row[5]),
                topics=json.loads(row[6]) if row[6] else [],
                entities=json.loads(row[7]) if row[7] else [],
                sentiment=row[8],
                is_summarized=bool(row[9]),
                summary=row[10]
            ))
        
        return entries
    
    def get_recall_entry_count(self) -> int:
        """Get total number of recall entries"""
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM recall_entries WHERE is_summarized = 0")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    def mark_entries_summarized(self, conversation_id: str) -> None:
        """Mark all entries in a conversation as summarized"""
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE recall_entries SET is_summarized = 1 WHERE conversation_id = ?",
            (conversation_id,)
        )
        conn.commit()
        conn.close()
    
    def save_conversation_summary(self, summary: ConversationSummary) -> None:
        """Save a conversation summary"""
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO conversation_summaries
            (id, conversation_id, notebook_id, summary, key_points, decisions_made, action_items, start_time, end_time, message_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            summary.id,
            summary.conversation_id,
            summary.notebook_id,
            summary.summary,
            json.dumps(summary.key_points),
            json.dumps(summary.decisions_made),
            json.dumps(summary.action_items),
            summary.start_time.isoformat(),
            summary.end_time.isoformat(),
            summary.message_count,
            summary.created_at.isoformat()
        ))
        
        conn.commit()
        conn.close()
    
    # =========================================================================
    # Archival Memory (LanceDB)
    # =========================================================================
    
    def _init_archival_db(self) -> None:
        """Initialize LanceDB for archival memory with namespace support"""
        self.archival_db = lancedb.connect(str(self.archival_db_path))
        
        import pyarrow as pa
        required_schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("namespace", pa.string()),  # SYSTEM, CURATOR, or COLLECTOR
            pa.field("content", pa.string()),
            pa.field("content_type", pa.string()),
            pa.field("source_type", pa.string()),
            pa.field("source_id", pa.string()),
            pa.field("source_notebook_id", pa.string()),
            pa.field("topics", pa.string()),  # JSON array
            pa.field("entities", pa.string()),  # JSON array
            pa.field("importance", pa.string()),
            pa.field("created_at", pa.string()),
            pa.field("last_accessed", pa.string()),
            pa.field("access_count", pa.int32()),
            pa.field("vector", pa.list_(pa.float32(), 1024)),  # snowflake-arctic-embed2 dimensions
        ])
        
        if "archival_memories" not in self.archival_db.table_names():
            self.archival_db.create_table("archival_memories", schema=required_schema)
        else:
            # Migrate existing table if schema is outdated (e.g. missing 'namespace' column)
            try:
                table = self.archival_db.open_table("archival_memories")
                existing_names = set(table.schema.names)
                required_names = set(required_schema.names)
                missing = required_names - existing_names
                if missing:
                    print(f"[MEMORY] Archival table missing columns {missing}, recreating with new schema")
                    self.archival_db.drop_table("archival_memories")
                    self.archival_db.create_table("archival_memories", schema=required_schema)
            except Exception as e:
                print(f"[MEMORY] Archival table migration check failed ({e}), recreating")
                try:
                    self.archival_db.drop_table("archival_memories")
                except Exception:
                    pass
                self.archival_db.create_table("archival_memories", schema=required_schema)
    
    def add_archival_memory(
        self, 
        entry: ArchivalMemoryEntry,
        namespace: AgentNamespace = AgentNamespace.SYSTEM,
        notebook_id: Optional[str] = None
    ) -> None:
        """
        Add entry to archival memory with embedding and namespace isolation.
        
        Args:
            entry: The memory entry to store
            namespace: Which namespace to store in (SYSTEM, CURATOR, COLLECTOR)
            notebook_id: Required for COLLECTOR namespace, ignored otherwise
        
        Raises:
            ValueError: If COLLECTOR namespace used without notebook_id
        """
        if namespace == AgentNamespace.COLLECTOR and not notebook_id:
            raise ValueError("notebook_id required for COLLECTOR namespace")
        
        table = self.archival_db.open_table("archival_memories")
        
        # Generate embedding
        embedding = self.get_embedding(entry.content)
        
        # Use provided notebook_id or fall back to entry's source_notebook_id
        effective_notebook_id = notebook_id or entry.source_notebook_id or ""
        
        # Prepare record with namespace
        record = {
            "id": entry.id,
            "namespace": namespace.value,
            "content": entry.content,
            "content_type": entry.content_type,
            "source_type": entry.source_type.value,
            "source_id": entry.source_id or "",
            "source_notebook_id": effective_notebook_id,
            "topics": json.dumps(entry.topics),
            "entities": json.dumps(entry.entities),
            "importance": entry.importance.value,
            "created_at": entry.created_at.isoformat(),
            "last_accessed": entry.last_accessed.isoformat(),
            "access_count": entry.access_count,
            "vector": embedding,
        }
        
        table.add([record])
    
    def search_archival_memory(
        self, 
        query: str, 
        limit: int = 10,
        namespace: AgentNamespace = AgentNamespace.SYSTEM,
        notebook_id: Optional[str] = None,
        cross_notebook: bool = False,
        recency_weight: float = 0.2
    ) -> List[MemorySearchResult]:
        """
        Search archival memory by semantic similarity with namespace isolation.
        
        Access Rules:
        - SYSTEM namespace: Only searches SYSTEM memories
        - CURATOR namespace: Can search ALL namespaces (cross_notebook=True allowed)
        - COLLECTOR namespace: Only searches own notebook's COLLECTOR memories + SYSTEM
        
        Args:
            query: Search query text
            limit: Max results to return
            namespace: Caller's namespace (determines access permissions)
            notebook_id: Required for COLLECTOR namespace searches
            cross_notebook: Only CURATOR can set True to search across notebooks
            recency_weight: Weight for recency in scoring (0-1)
        
        Returns:
            List of MemorySearchResult sorted by combined score
        """
        table = self.archival_db.open_table("archival_memories")
        
        # Generate query embedding
        query_embedding = self.get_embedding(query)
        
        # Vector search - get more results to filter
        results = table.search(query_embedding).limit(limit * 3).to_list()
        
        # Apply namespace access control
        filtered_results = []
        for r in results:
            r_namespace = r.get("namespace", "system")
            r_notebook = r.get("source_notebook_id", "")
            
            if namespace == AgentNamespace.CURATOR and cross_notebook:
                # Curator with cross_notebook can access everything
                filtered_results.append(r)
            elif namespace == AgentNamespace.CURATOR:
                # Curator without cross_notebook: CURATOR + SYSTEM namespaces
                if r_namespace in [AgentNamespace.CURATOR.value, AgentNamespace.SYSTEM.value]:
                    filtered_results.append(r)
            elif namespace == AgentNamespace.COLLECTOR:
                # Collector: own COLLECTOR namespace + SYSTEM
                if r_namespace == AgentNamespace.SYSTEM.value:
                    filtered_results.append(r)
                elif r_namespace == AgentNamespace.COLLECTOR.value and r_notebook == notebook_id:
                    filtered_results.append(r)
            else:
                # SYSTEM: only SYSTEM namespace
                if r_namespace == AgentNamespace.SYSTEM.value:
                    filtered_results.append(r)
        
        # Legacy filter by notebook_id (for backwards compatibility)
        if notebook_id and namespace != AgentNamespace.COLLECTOR:
            filtered_results = [r for r in filtered_results if r.get("source_notebook_id") == notebook_id]
        
        results = filtered_results
        
        # Score and rank
        scored_results = []
        now = datetime.utcnow()
        
        for r in results[:limit]:
            # Similarity score (from LanceDB, typically 0-1 for cosine)
            similarity = 1.0 - r.get("_distance", 0)  # Convert distance to similarity
            
            # Recency score (decay over 30 days)
            created = datetime.fromisoformat(r["created_at"])
            days_old = (now - created).days
            recency = max(0, 1 - (days_old / 30))
            
            # Combined score
            combined = (1 - recency_weight) * similarity + recency_weight * recency
            
            entry = ArchivalMemoryEntry(
                id=r["id"],
                content=r["content"],
                content_type=r["content_type"],
                source_type=MemorySourceType(r["source_type"]),
                source_id=r["source_id"] if r["source_id"] else None,
                source_notebook_id=r["source_notebook_id"] if r["source_notebook_id"] else None,
                topics=json.loads(r["topics"]),
                entities=json.loads(r["entities"]),
                importance=MemoryImportance(r["importance"]),
                created_at=datetime.fromisoformat(r["created_at"]),
                last_accessed=datetime.fromisoformat(r["last_accessed"]),
                access_count=r["access_count"],
            )
            
            scored_results.append(MemorySearchResult(
                entry=entry,
                similarity_score=similarity,
                recency_score=recency,
                combined_score=combined
            ))
        
        # Sort by combined score
        scored_results.sort(key=lambda x: x.combined_score, reverse=True)
        
        return scored_results
    
    def get_archival_memory_count(
        self, 
        namespace: Optional[AgentNamespace] = None,
        notebook_id: Optional[str] = None
    ) -> int:
        """Get total number of archival memories, optionally filtered by namespace"""
        try:
            table = self.archival_db.open_table("archival_memories")
            if namespace is None and notebook_id is None:
                return table.count_rows()
            
            # Filter and count
            all_rows = table.to_pandas()
            if namespace:
                all_rows = all_rows[all_rows["namespace"] == namespace.value]
            if notebook_id:
                all_rows = all_rows[all_rows["source_notebook_id"] == notebook_id]
            return len(all_rows)
        except Exception:
            return 0
    
    def delete_notebook_memories(self, notebook_id: str) -> int:
        """Delete all archival memories associated with a notebook"""
        try:
            table = self.archival_db.open_table("archival_memories")
            df = table.to_pandas()
            before = len(df)
            keep = df[df["source_notebook_id"] != notebook_id]
            deleted = before - len(keep)
            
            if deleted > 0:
                # Recreate table without the deleted rows
                table.delete(f'source_notebook_id = "{notebook_id}"')
                print(f"[MemoryStore] Deleted {deleted} archival memories for notebook {notebook_id}")
            
            return deleted
        except Exception as e:
            print(f"[MemoryStore] Error deleting notebook memories: {e}")
            return 0
    
    def update_archival_access(self, memory_id: str, was_useful: bool = True) -> None:
        """
        Update access tracking for an archival memory.
        Uses SQLite table since LanceDB doesn't support updates.
        """
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        
        now = datetime.utcnow().isoformat()
        usefulness_delta = 0.1 if was_useful else -0.05
        
        # Upsert access record
        cursor.execute("""
            INSERT INTO archival_access (memory_id, access_count, last_accessed, usefulness_score)
            VALUES (?, 1, ?, 0.5)
            ON CONFLICT(memory_id) DO UPDATE SET
                access_count = access_count + 1,
                last_accessed = ?,
                usefulness_score = MIN(1.0, MAX(0.0, usefulness_score + ?))
        """, (memory_id, now, now, usefulness_delta))
        
        conn.commit()
        conn.close()
    
    def get_archival_access_stats(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get access statistics for an archival memory"""
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM archival_access WHERE memory_id = ?",
            (memory_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return {
            "memory_id": row[0],
            "access_count": row[1],
            "last_accessed": row[2],
            "usefulness_score": row[3],
            "decay_rate": row[4]
        }
    
    # =========================================================================
    # User Signals (Negative Signal Learning - Enhancement #3)
    # =========================================================================
    
    def record_user_signal(
        self,
        notebook_id: str,
        signal_type: str,
        item_id: Optional[str] = None,
        query: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Record a user signal for learning.
        
        Signal types:
        - 'view': Item shown to user (start ignore timer)
        - 'click': User engaged with item (positive)
        - 'ignore': Item shown 7+ days, never clicked (negative)
        - 'search_miss': User searched, no Collector results (gap)
        - 'manual_add': User added content Collector missed (gap)
        """
        import uuid
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO user_signals (id, notebook_id, signal_type, item_id, query, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()),
            notebook_id,
            signal_type,
            item_id,
            query,
            datetime.utcnow().isoformat(),
            json.dumps(metadata) if metadata else None
        ))
        
        conn.commit()
        conn.close()
    
    def get_user_signals(
        self,
        notebook_id: str,
        signal_type: Optional[str] = None,
        since_days: int = 30,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get user signals for a notebook"""
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        
        cutoff = (datetime.utcnow() - timedelta(days=since_days)).isoformat()
        
        query_sql = """
            SELECT * FROM user_signals 
            WHERE notebook_id = ? AND timestamp > ?
        """
        params = [notebook_id, cutoff]
        
        if signal_type:
            query_sql += " AND signal_type = ?"
            params.append(signal_type)
        
        query_sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query_sql, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [{
            "id": row[0],
            "notebook_id": row[1],
            "signal_type": row[2],
            "item_id": row[3],
            "query": row[4],
            "timestamp": row[5],
            "metadata": json.loads(row[6]) if row[6] else None
        } for row in rows]
    
    def get_ignored_items(self, notebook_id: str, days_threshold: int = 7) -> List[str]:
        """Get items that were viewed but never clicked (negative signal)"""
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        
        cutoff = (datetime.utcnow() - timedelta(days=days_threshold)).isoformat()
        
        cursor.execute("""
            SELECT DISTINCT item_id FROM user_signals 
            WHERE notebook_id = ? 
            AND signal_type = 'view'
            AND timestamp < ?
            AND item_id NOT IN (
                SELECT item_id FROM user_signals 
                WHERE signal_type = 'click' AND notebook_id = ?
            )
        """, (notebook_id, cutoff, notebook_id))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [row[0] for row in rows if row[0]]
    
    def get_search_misses(self, notebook_id: str, since_days: int = 30) -> List[str]:
        """Get queries where user searched but Collector had no results"""
        conn = self._get_recall_connection()
        cursor = conn.cursor()
        
        cutoff = (datetime.utcnow() - timedelta(days=since_days)).isoformat()
        
        cursor.execute("""
            SELECT DISTINCT query FROM user_signals 
            WHERE notebook_id = ? 
            AND signal_type = 'search_miss'
            AND timestamp > ?
        """, (notebook_id, cutoff))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [row[0] for row in rows if row[0]]
    
    # =========================================================================
    # Memory Statistics
    # =========================================================================
    
    def get_memory_stats(self, namespace: Optional[AgentNamespace] = None) -> Dict[str, Any]:
        """Get statistics about all memory tiers, optionally filtered by namespace"""
        core = self.load_core_memory()
        
        stats = {
            "core_memory": {
                "entries": len(core.entries),
                "tokens": core.total_tokens(),
                "max_tokens": core.max_tokens,
                "usage_percent": (core.total_tokens() / core.max_tokens) * 100
            },
            "recall_memory": {
                "entries": self.get_recall_entry_count(),
            },
            "archival_memory": {
                "total_entries": self.get_archival_memory_count(),
                "by_namespace": {
                    "system": self.get_archival_memory_count(AgentNamespace.SYSTEM),
                    "curator": self.get_archival_memory_count(AgentNamespace.CURATOR),
                    "collector": self.get_archival_memory_count(AgentNamespace.COLLECTOR),
                }
            }
        }
        
        if namespace:
            stats["archival_memory"]["filtered_entries"] = self.get_archival_memory_count(namespace)
        
        return stats


# Singleton instance
memory_store = MemoryStore()
