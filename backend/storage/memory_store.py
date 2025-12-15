"""
Memory Store - Persistent storage for MemGPT-style memory architecture

Three-tier storage:
1. Core Memory: JSON file (~2K tokens, always in context)
2. Recall Memory: SQLite (recent conversations, searchable by text)
3. Archival Memory: LanceDB (unlimited long-term, vector search)
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import threading
import lancedb
from sentence_transformers import SentenceTransformer

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
    # Embedding Model
    # =========================================================================
    
    @property
    def embedding_model(self) -> SentenceTransformer:
        """Lazy load embedding model"""
        if self._embedding_model is None:
            self._embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        return self._embedding_model
    
    def get_embedding(self, text: str) -> List[float]:
        """Generate embedding for text"""
        return self.embedding_model.encode(text).tolist()
    
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
        """Initialize LanceDB for archival memory"""
        self.archival_db = lancedb.connect(str(self.archival_db_path))
        
        # Create table if it doesn't exist
        if "archival_memories" not in self.archival_db.table_names():
            # Create with sample data structure
            import pyarrow as pa
            schema = pa.schema([
                pa.field("id", pa.string()),
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
                pa.field("vector", pa.list_(pa.float32(), 384)),  # MiniLM embedding size
            ])
            self.archival_db.create_table("archival_memories", schema=schema)
    
    def add_archival_memory(self, entry: ArchivalMemoryEntry) -> None:
        """Add entry to archival memory with embedding"""
        table = self.archival_db.open_table("archival_memories")
        
        # Generate embedding
        embedding = self.get_embedding(entry.content)
        
        # Prepare record
        record = {
            "id": entry.id,
            "content": entry.content,
            "content_type": entry.content_type,
            "source_type": entry.source_type.value,
            "source_id": entry.source_id or "",
            "source_notebook_id": entry.source_notebook_id or "",
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
        notebook_id: Optional[str] = None,
        recency_weight: float = 0.2
    ) -> List[MemorySearchResult]:
        """
        Search archival memory by semantic similarity.
        Combines vector similarity with recency scoring.
        """
        table = self.archival_db.open_table("archival_memories")
        
        # Generate query embedding
        query_embedding = self.get_embedding(query)
        
        # Vector search
        results = table.search(query_embedding).limit(limit * 2).to_list()
        
        # Filter by notebook if specified
        if notebook_id:
            results = [r for r in results if r.get("source_notebook_id") == notebook_id]
        
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
    
    def get_archival_memory_count(self) -> int:
        """Get total number of archival memories"""
        try:
            table = self.archival_db.open_table("archival_memories")
            return table.count_rows()
        except Exception:
            return 0
    
    def update_archival_access(self, memory_id: str) -> None:
        """Update last_accessed and access_count for a memory"""
        # LanceDB doesn't support updates well, so we'd need to delete and re-add
        # For now, we'll skip this optimization
        pass
    
    # =========================================================================
    # Memory Statistics
    # =========================================================================
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """Get statistics about all memory tiers"""
        core = self.load_core_memory()
        
        return {
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
                "entries": self.get_archival_memory_count(),
            }
        }


# Singleton instance
memory_store = MemoryStore()
