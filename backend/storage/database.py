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
import logging
logger = logging.getLogger(__name__)


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
        
        # -- video_generations --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS video_generations (
                video_id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                topic TEXT DEFAULT '',
                duration_minutes INTEGER DEFAULT 5,
                visual_style TEXT DEFAULT 'classic',
                voice TEXT DEFAULT 'us_female',
                format_type TEXT DEFAULT 'explainer',
                video_file_path TEXT,
                duration_seconds REAL,
                storyboard TEXT,
                narration_script TEXT,
                slide_count INTEGER,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_video_notebook ON video_generations(notebook_id)
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

        # -- quiz_generations (Tier 5, 2026-06-02) --
        # Persists generated quizzes so they appear in the Library archive.
        # Questions live as a JSON blob — they're heterogeneous (different
        # types, different fields per type) and querying individual questions
        # is rare so JSON is the simpler tradeoff.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS quiz_generations (
                quiz_id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                topic TEXT,
                difficulty TEXT,
                num_questions INTEGER DEFAULT 0,
                questions_json TEXT NOT NULL,
                source_summary TEXT,
                sources_used INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_quiz_notebook ON quiz_generations(notebook_id)
        """)

        # -- visual_generations (Tier 5, 2026-06-02) --
        # Persists generated visuals (SVG or Mermaid). Either svg_markup or
        # mermaid_code is populated, never both. critic_overall is nullable
        # because the legacy template path doesn't produce a critic score.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS visual_generations (
                visual_id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                topic TEXT,
                title TEXT,
                svg_markup TEXT,
                mermaid_code TEXT,
                template_id TEXT,
                v2_path TEXT,
                critic_overall REAL,
                prompt TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_visual_notebook ON visual_generations(notebook_id)
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
        
        # -- notebook_sections --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notebook_sections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                collapsed INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        
        # Add section_id and sort_order to notebooks if missing (safe migration)
        try:
            cursor.execute("ALTER TABLE notebooks ADD COLUMN section_id TEXT REFERENCES notebook_sections(id) ON DELETE SET NULL")
        except Exception as _e:
            logger.warning(f"[database] {type(_e).__name__}: {_e}")
        try:
            cursor.execute("ALTER TABLE notebooks ADD COLUMN sort_order INTEGER DEFAULT 0")
        except Exception as _e:
            logger.warning(f"[database] {type(_e).__name__}: {_e}")
        
        # -- canvas_notes --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS canvas_notes (
                id TEXT PRIMARY KEY,
                notebook_id TEXT,
                title TEXT NOT NULL DEFAULT '',
                content_markdown TEXT DEFAULT '',
                content_blocknote_json TEXT DEFAULT '{}',
                source_type TEXT DEFAULT 'typed',
                tags TEXT DEFAULT '[]',
                note_type TEXT DEFAULT 'note',
                voice_weight REAL DEFAULT 1.0,
                original_image_paths TEXT DEFAULT '[]',
                scan_confidence REAL,
                wikilinks_out TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                saved_as_source_id TEXT,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE SET NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_canvas_notes_notebook ON canvas_notes(notebook_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_canvas_notes_updated ON canvas_notes(updated_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_canvas_notes_source_type ON canvas_notes(source_type)
        """)

        # -- voice_observations --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS voice_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text_sample TEXT NOT NULL,
                source_type TEXT NOT NULL,
                voice_weight REAL DEFAULT 1.0,
                word_count INTEGER DEFAULT 0,
                notebook_id TEXT,
                source_note_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (notebook_id) REFERENCES notebooks(id) ON DELETE SET NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_voice_obs_created ON voice_observations(created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_voice_obs_weight ON voice_observations(voice_weight DESC)
        """)

        # -- voice_profile (singleton row) --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS voice_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                profile_json TEXT NOT NULL DEFAULT '{}',
                sample_count INTEGER DEFAULT 0,
                last_rebuilt TEXT NOT NULL DEFAULT '',
                rebuild_version INTEGER DEFAULT 0
            )
        """)

        # -- articles (Phase 1 Tier 2, 2026-06-09) --
        # Per-article rows for Correspondent newsletters. Each newsletter
        # source can have N articles. Lazy-populated on first source-viewer
        # open for existing data; populated at ingest time for new data.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id              TEXT PRIMARY KEY,
                source_id       TEXT NOT NULL,
                notebook_id     TEXT NOT NULL,
                position        INTEGER NOT NULL,
                title           TEXT,
                body_text       TEXT NOT NULL,
                body_html       TEXT,
                summary         TEXT,
                topic_tags      TEXT DEFAULT '[]',
                sender          TEXT,
                created_at      TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_notebook ON articles(notebook_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_sender ON articles(sender, created_at DESC)
        """)
        # Phase 2.1 (2026-06-09) — add embedding column for clustering.
        # SQLite doesn't support ADD COLUMN IF NOT EXISTS prior to 3.35;
        # catch the duplicate-column error so the migration is idempotent.
        try:
            cursor.execute("ALTER TABLE articles ADD COLUMN embedding BLOB")
        except sqlite3.OperationalError as _e:
            if "duplicate column" not in str(_e).lower():
                raise
        # P1C.2 (2026-06-10) — character offset of this article's body
        # within the parent newsletter's flattened text. Used for exact
        # scroll-to-article in the source viewer. -1 if unknown.
        try:
            cursor.execute("ALTER TABLE articles ADD COLUMN body_text_offset INTEGER DEFAULT -1")
        except sqlite3.OperationalError as _e:
            if "duplicate column" not in str(_e).lower():
                raise
        # P1C.3 (2026-06-10) — rag_indexed flag so we know which articles
        # already have their own LanceDB chunks (for skip-on-retry).
        try:
            cursor.execute("ALTER TABLE articles ADD COLUMN rag_indexed INTEGER DEFAULT 0")
        except sqlite3.OperationalError as _e:
            if "duplicate column" not in str(_e).lower():
                raise

        # -- topic_clusters (Phase 2.2 Tier 2, 2026-06-09) --
        # Output of the article-level hot/cold clustering pass.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS topic_clusters (
                id              TEXT PRIMARY KEY,
                label           TEXT,
                article_ids     TEXT NOT NULL DEFAULT '[]',
                sender_counts   TEXT NOT NULL DEFAULT '{}',
                notebook_counts TEXT NOT NULL DEFAULT '{}',
                avg_embedding   BLOB,
                recent_size     INTEGER DEFAULT 0,
                baseline_size   INTEGER DEFAULT 0,
                last_built_at   TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_clusters_built ON topic_clusters(last_built_at DESC)
        """)

        # -- correspondent_events (Phase 5 Tier 2 / I telemetry, 2026-06-10) --
        # Generic event log for Correspondent operations. Powers the
        # last three dashboard metrics: approval throughput, dedup hit
        # rate, IMAP delete success rate. Best-effort writes — never
        # blocks the hot path.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS correspondent_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT NOT NULL,
                event_type    TEXT NOT NULL,
                sender        TEXT,
                item_id       TEXT,
                duration_ms   INTEGER,
                payload_json  TEXT DEFAULT '{}'
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_correspondent_events_ts ON correspondent_events(ts DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_correspondent_events_type ON correspondent_events(event_type, ts DESC)
        """)

        # -- pending_unsubscribes (Phase 5 Tier 2 / F follow-up, 2026-06-10) --
        # Tokens for the two-step unsubscribe confirmation flow.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_unsubscribes (
                token         TEXT PRIMARY KEY,
                sender_email  TEXT NOT NULL,
                target        TEXT NOT NULL,
                target_type   TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                expires_at    TEXT NOT NULL
            )
        """)

        # -- unsubscribe_log (Phase 5 Tier 2 / F follow-up, 2026-06-10) --
        # Append-only audit of every unsubscribe attempt. Never deleted.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS unsubscribe_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT NOT NULL,
                sender_email  TEXT NOT NULL,
                target        TEXT NOT NULL,
                target_type   TEXT NOT NULL,
                result        TEXT NOT NULL,
                result_detail TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_unsubscribe_log_ts ON unsubscribe_log(ts DESC)
        """)

        # -- routing_decisions (Phase 4 Tier 2 / J, 2026-06-10) --
        # One row per incoming newsletter routing decision. Powers the
        # @correspondent show routing histogram so the user can see if
        # their threshold (0.75 default) is tuned right.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS routing_decisions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              TEXT NOT NULL,
                sender          TEXT,
                top_cosine      REAL NOT NULL,
                threshold       REAL NOT NULL,
                decision_verb   TEXT NOT NULL,
                top_notebook_id TEXT,
                bias_applied    TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_routing_decisions_ts ON routing_decisions(ts DESC)
        """)

        # -- sender_settings (Phase 4 Tier 2 / G, 2026-06-10) --
        # Per-sender frequency tuner mode. Default 'live' (current
        # behavior). 'weekly_digest' holds incoming messages in a buffer
        # and the composer ingests one summary per week.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sender_settings (
                sender_email   TEXT PRIMARY KEY,
                bundle_mode    TEXT NOT NULL DEFAULT 'live',
                digest_day     INTEGER NOT NULL DEFAULT 1,
                digest_hour    INTEGER NOT NULL DEFAULT 8,
                set_at         TEXT NOT NULL
            )
        """)

        # -- pending_digest (Phase 4 Tier 2 / G, 2026-06-10) --
        # Held messages for senders in weekly_digest mode.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_digest (
                id             TEXT PRIMARY KEY,
                sender_email   TEXT NOT NULL,
                raw_bytes_b64  TEXT NOT NULL,
                received_at    TEXT NOT NULL,
                email_account  TEXT,
                notebook_id    TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_pending_digest_sender ON pending_digest(sender_email, received_at)
        """)

        # -- sender_blocklist (Phase 3 Tier 2, 2026-06-10) --
        # Senders the user has decided to stop ingesting from. Doesn't
        # actually unsubscribe (that requires List-Unsubscribe handling
        # in a future polish pass); just stops new sources from being
        # created for that sender.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sender_blocklist (
                sender_email          TEXT PRIMARY KEY,
                reason                TEXT,
                blocked_at            TEXT NOT NULL,
                snooze_until          TEXT,
                last_unsubscribe_url  TEXT
            )
        """)

        # -- newsletter_scorecards (Phase 2.4 Tier 2, 2026-06-09) --
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS newsletter_scorecards (
                sender_email       TEXT PRIMARY KEY,
                volume_per_week    REAL DEFAULT 0,
                read_through       REAL DEFAULT 0,
                highlight_rate     REAL DEFAULT 0,
                citation_rate      REAL DEFAULT 0,
                action_conversion  REAL DEFAULT 0,
                composite_score    REAL DEFAULT 0,
                grade              TEXT DEFAULT '',
                last_built_at      TEXT NOT NULL,
                trend_data         TEXT DEFAULT '{}'
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
