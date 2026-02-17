"""
JSON → SQLite migration script.

Reads all existing JSON store files and imports their data into localbook.db.
Runs once on first launch when use_sqlite is enabled.
JSON files are preserved as backups.
"""
import json
import logging
from pathlib import Path
from typing import Dict, Any

from config import settings
from storage.database import get_db

logger = logging.getLogger(__name__)


def _load_json_safe(path: Path) -> Dict[str, Any]:
    """Load a JSON file, returning empty dict on failure."""
    if not path.exists():
        return {}
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[Migration] Failed to load {path}: {e}")
        return {}


def is_migrated() -> bool:
    """Check if migration has already been completed."""
    db = get_db()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT value FROM migration_meta WHERE key = 'json_migrated'"
    ).fetchone()
    return row is not None and row['value'] == 'true'


def mark_migrated():
    """Mark migration as completed."""
    db = get_db()
    conn = db.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO migration_meta (key, value) VALUES ('json_migrated', 'true')"
    )
    conn.commit()


def migrate_notebooks(data_dir: Path, conn):
    """Migrate notebooks.json → notebooks table."""
    data = _load_json_safe(data_dir / "notebooks.json")
    notebooks = data.get("notebooks", {})
    count = 0
    for nb in notebooks.values():
        conn.execute(
            """INSERT OR IGNORE INTO notebooks (id, title, description, color, source_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (nb['id'], nb.get('title', ''), nb.get('description'),
             nb.get('color'), nb.get('source_count', 0),
             nb.get('created_at', ''), nb.get('updated_at', ''))
        )
        count += 1
    logger.info(f"[Migration] Migrated {count} notebooks")
    return count


def migrate_sources(data_dir: Path, conn):
    """Migrate sources.json → sources table."""
    data = _load_json_safe(data_dir / "sources.json")
    sources = data.get("sources", {})
    count = 0
    for src in sources.values():
        # Extract known fields, put the rest in metadata_json
        known_fields = {'id', 'notebook_id', 'filename', 'content', 'url', 'author',
                        'date', 'format', 'type', 'notes', 'notes_updated_at',
                        'tags', 'tags_updated_at', 'created_at'}
        metadata = {k: v for k, v in src.items() if k not in known_fields}
        tags = json.dumps(src.get('tags', []))
        
        conn.execute(
            """INSERT OR IGNORE INTO sources 
               (id, notebook_id, filename, content, url, author, date, format, type,
                notes, notes_updated_at, tags, tags_updated_at, created_at, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (src['id'], src.get('notebook_id', ''), src.get('filename', ''),
             src.get('content'), src.get('url'), src.get('author'),
             src.get('date'), src.get('format'), src.get('type'),
             src.get('notes'), src.get('notes_updated_at'),
             tags, src.get('tags_updated_at'),
             src.get('created_at', ''), json.dumps(metadata))
        )
        count += 1
    logger.info(f"[Migration] Migrated {count} sources")
    return count


def migrate_highlights(data_dir: Path, conn):
    """Migrate highlights.json → highlights table."""
    data = _load_json_safe(data_dir / "highlights.json")
    highlights = data.get("highlights", {})
    count = 0
    for h in highlights.values():
        conn.execute(
            """INSERT OR IGNORE INTO highlights
               (highlight_id, notebook_id, source_id, start_offset, end_offset,
                highlighted_text, color, annotation, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (h['highlight_id'], h.get('notebook_id', ''), h.get('source_id', ''),
             h.get('start_offset', 0), h.get('end_offset', 0),
             h.get('highlighted_text', ''), h.get('color', 'yellow'),
             h.get('annotation', ''), h.get('created_at', ''), h.get('updated_at', ''))
        )
        count += 1
    logger.info(f"[Migration] Migrated {count} highlights")
    return count


def migrate_skills(data_dir: Path, conn):
    """Migrate skills.json → skills table."""
    data = _load_json_safe(data_dir / "skills.json")
    skills = data.get("skills", {})
    count = 0
    for s in skills.values():
        conn.execute(
            """INSERT OR IGNORE INTO skills (skill_id, name, system_prompt, description, is_builtin)
               VALUES (?, ?, ?, ?, ?)""",
            (s['skill_id'], s.get('name', ''), s.get('system_prompt', ''),
             s.get('description'), 1 if s.get('is_builtin') else 0)
        )
        count += 1
    logger.info(f"[Migration] Migrated {count} skills")
    return count


def migrate_findings(data_dir: Path, conn):
    """Migrate findings/{notebook_id}.json → findings table."""
    findings_dir = data_dir / "findings"
    if not findings_dir.exists():
        logger.info("[Migration] No findings directory found, skipping")
        return 0
    
    count = 0
    for fpath in findings_dir.glob("*.json"):
        notebook_id = fpath.stem
        try:
            with open(fpath, 'r') as f:
                findings = json.load(f)
            for finding in findings.values():
                conn.execute(
                    """INSERT OR IGNORE INTO findings
                       (id, notebook_id, type, title, content_json, tags, starred, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (finding['id'], finding.get('notebook_id', notebook_id),
                     finding.get('type', ''), finding.get('title', ''),
                     json.dumps(finding.get('content', {})),
                     json.dumps(finding.get('tags', [])),
                     1 if finding.get('starred') else 0,
                     finding.get('created_at', ''), finding.get('updated_at', ''))
                )
                count += 1
        except Exception as e:
            logger.warning(f"[Migration] Failed to migrate findings from {fpath}: {e}")
    
    logger.info(f"[Migration] Migrated {count} findings")
    return count


def migrate_audio(data_dir: Path, conn):
    """Migrate audio_generations.json → audio_generations table."""
    data = _load_json_safe(data_dir / "audio_generations.json")
    generations = data.get("generations", {})
    count = 0
    for g in generations.values():
        conn.execute(
            """INSERT OR IGNORE INTO audio_generations
               (audio_id, notebook_id, script, topic, duration_minutes,
                host1_gender, host2_gender, accent, skill_id,
                audio_file_path, duration_seconds, status, error_message,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (g['audio_id'], g.get('notebook_id', ''),
             g.get('script', ''), g.get('topic', ''),
             g.get('duration_minutes', 10),
             g.get('host1_gender', 'male'), g.get('host2_gender', 'female'),
             g.get('accent', 'us'), g.get('skill_id'),
             g.get('audio_file_path'), g.get('duration_seconds'),
             g.get('status', 'pending'), g.get('error_message'),
             g.get('created_at', ''), g.get('updated_at', ''))
        )
        count += 1
    logger.info(f"[Migration] Migrated {count} audio generations")
    return count


def migrate_content(data_dir: Path, conn):
    """Migrate content_generations.json → content_generations table."""
    data = _load_json_safe(data_dir / "content_generations.json")
    generations = data.get("generations", {})
    count = 0
    for g in generations.values():
        conn.execute(
            """INSERT OR IGNORE INTO content_generations
               (content_id, notebook_id, skill_id, skill_name, content,
                topic, sources_used, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (g['content_id'], g.get('notebook_id', ''),
             g.get('skill_id', ''), g.get('skill_name', ''),
             g.get('content', ''), g.get('topic'),
             g.get('sources_used', 0),
             g.get('created_at', ''), g.get('updated_at', ''))
        )
        count += 1
    logger.info(f"[Migration] Migrated {count} content generations")
    return count


def migrate_exploration(data_dir: Path, conn):
    """Migrate exploration.json → exploration_queries/topics/sources tables."""
    data = _load_json_safe(data_dir / "exploration.json")
    explorations = data.get("explorations", {})
    q_count = 0
    t_count = 0
    s_count = 0
    
    for notebook_id, exploration in explorations.items():
        # Migrate queries
        for q in exploration.get("queries", []):
            conn.execute(
                """INSERT OR IGNORE INTO exploration_queries
                   (id, notebook_id, query, topics, sources_used, confidence, answer_preview, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (q.get('id', ''), notebook_id, q.get('query', ''),
                 json.dumps(q.get('topics', [])),
                 json.dumps(q.get('sources_used', [])),
                 q.get('confidence', 0.0),
                 q.get('answer_preview', ''),
                 q.get('timestamp', ''))
            )
            q_count += 1
        
        # Migrate topics
        for topic, info in exploration.get("topics_explored", {}).items():
            conn.execute(
                """INSERT OR IGNORE INTO exploration_topics
                   (notebook_id, topic, count, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?)""",
                (notebook_id, topic, info.get('count', 0),
                 info.get('first_seen', ''), info.get('last_seen', ''))
            )
            t_count += 1
        
        # Migrate sources
        for source_id, info in exploration.get("sources_accessed", {}).items():
            conn.execute(
                """INSERT OR IGNORE INTO exploration_sources
                   (notebook_id, source_id, count, first_accessed, last_accessed)
                   VALUES (?, ?, ?, ?, ?)""",
                (notebook_id, source_id, info.get('count', 0),
                 info.get('first_accessed', ''), info.get('last_accessed', ''))
            )
            s_count += 1
    
    logger.info(f"[Migration] Migrated {q_count} queries, {t_count} topics, {s_count} source accesses")
    return q_count


def _needs_remigration(conn) -> bool:
    """Safety check: if SQLite is marked migrated but empty while JSON has data, re-migrate."""
    data_dir = settings.data_dir
    
    # Check if SQLite has notebooks
    sqlite_count = conn.execute("SELECT COUNT(*) as cnt FROM notebooks").fetchone()['cnt']
    if sqlite_count > 0:
        return False  # SQLite has data, we're good
    
    # SQLite is empty — check if JSON has notebooks
    json_path = data_dir / "notebooks.json"
    if not json_path.exists():
        return False  # No JSON either, nothing to migrate
    
    try:
        data = _load_json_safe(json_path)
        json_count = len(data.get("notebooks", {}))
        if json_count > 0:
            logger.warning(
                "[Migration] SQLite marked as migrated but empty (%d notebooks in JSON). "
                "Forcing re-migration.", json_count
            )
            return True
    except Exception:
        pass
    
    return False


def run_migration():
    """Run full JSON → SQLite migration if not already done."""
    db = get_db()
    conn = db.get_connection()
    
    if is_migrated() and not _needs_remigration(conn):
        logger.info("[Migration] Already migrated, skipping")
        return
    
    logger.info("[Migration] Starting JSON → SQLite migration...")
    data_dir = settings.data_dir
    
    try:
        # Disable foreign keys during migration — JSON data may have orphaned
        # references (e.g. highlights pointing to deleted sources)
        conn.execute("PRAGMA foreign_keys=OFF")
        
        migrate_notebooks(data_dir, conn)
        migrate_sources(data_dir, conn)
        migrate_highlights(data_dir, conn)
        migrate_skills(data_dir, conn)
        migrate_findings(data_dir, conn)
        migrate_audio(data_dir, conn)
        migrate_content(data_dir, conn)
        migrate_exploration(data_dir, conn)
        
        conn.commit()
        
        # Re-enable foreign keys
        conn.execute("PRAGMA foreign_keys=ON")
        
        mark_migrated()
        logger.info("[Migration] JSON → SQLite migration complete. JSON files preserved as backups.")
    except Exception as e:
        conn.rollback()
        conn.execute("PRAGMA foreign_keys=ON")
        logger.error(f"[Migration] Migration failed: {e}")
        raise
