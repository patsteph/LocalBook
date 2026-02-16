"""
SQLite database manager for LocalBook storage.

Provides a single localbook.db file replacing all JSON file stores.
Thread-safe with WAL journal mode for concurrent read/write.
"""
import sqlite3
import threading
from pathlib import Path
from typing import Optional
from config import settings


class Database:
    """Thread-safe SQLite connection manager with WAL mode."""
    
    _instance: Optional['Database'] = None
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
        self.db_path: Path = settings.data_dir / "localbook.db"
        self._local = threading.local()
        self._init_schema()
        self._initialized = True
    
    def get_connection(self) -> sqlite3.Connection:
        """Get a thread-local SQLite connection."""
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn
    
    def _init_schema(self):
        """Create all tables if they don't exist."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # -- notebooks --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notebooks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                color TEXT,
                source_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # -- sources --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                content TEXT,
                url TEXT,
                author TEXT,
                date TEXT,
                format TEXT,
                type TEXT,
                notes TEXT,
                notes_updated_at TEXT,
                tags TEXT DEFAULT '[]',
                tags_updated_at TEXT,
                created_at TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}',
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sources_notebook ON sources(notebook_id)
        """)
        
        # -- highlights --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS highlights (
                highlight_id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                highlighted_text TEXT NOT NULL,
                color TEXT DEFAULT 'yellow',
                annotation TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE,
                FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_highlights_notebook_source ON highlights(notebook_id, source_id)
        """)
        
        # -- skills --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                skill_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                description TEXT,
                is_builtin INTEGER DEFAULT 0
            )
        """)
        
        # -- findings --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS findings (
                id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                content_json TEXT DEFAULT '{}',
                tags TEXT DEFAULT '[]',
                starred INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_findings_notebook ON findings(notebook_id)
        """)
        
        # -- audio_generations --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audio_generations (
                audio_id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                script TEXT DEFAULT '',
                topic TEXT DEFAULT '',
                duration_minutes INTEGER DEFAULT 10,
                host1_gender TEXT DEFAULT 'male',
                host2_gender TEXT DEFAULT 'female',
                accent TEXT DEFAULT 'us',
                skill_id TEXT,
                audio_file_path TEXT,
                duration_seconds REAL,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audio_notebook ON audio_generations(notebook_id)
        """)
        
        # -- content_generations --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS content_generations (
                content_id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                content TEXT NOT NULL,
                topic TEXT,
                sources_used INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_content_notebook ON content_generations(notebook_id)
        """)
        
        # -- exploration --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS exploration_queries (
                id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                query TEXT NOT NULL,
                topics TEXT DEFAULT '[]',
                sources_used TEXT DEFAULT '[]',
                confidence REAL DEFAULT 0.0,
                answer_preview TEXT DEFAULT '',
                timestamp TEXT NOT NULL,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_exploration_notebook ON exploration_queries(notebook_id)
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS exploration_topics (
                notebook_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                count INTEGER DEFAULT 0,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                PRIMARY KEY (notebook_id, topic),
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS exploration_sources (
                notebook_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                count INTEGER DEFAULT 0,
                first_accessed TEXT NOT NULL,
                last_accessed TEXT NOT NULL,
                PRIMARY KEY (notebook_id, source_id),
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            )
        """)
        
        # -- migration tracking --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS migration_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        
        conn.commit()
    
    def close(self):
        """Close the thread-local connection if open."""
        conn = getattr(self._local, 'conn', None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def get_db() -> Database:
    """Get the singleton Database instance."""
    return Database()
