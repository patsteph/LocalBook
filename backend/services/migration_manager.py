"""Migration Manager for v0.6.5

Handles seamless upgrades from any previous version to v0.6.5.
Detects current version, determines migration path, and executes with progress updates.

Version History:
- v0.2: nomic-embed-text (768 dims), basic RAG
- v0.3: nomic-embed-text (768 dims), + BM25 hybrid
- v0.5: snowflake-arctic-embed2 (1024 dims), + Adaptive RAG
- v0.6.0: snowflake-arctic-embed2 (1024 dims), + Agentic RAG, Parent Docs, Entity Graph
- v0.6.5: snowflake-arctic-embed2 (1024 dims), + BERTopic topic modeling (replaces custom concepts)
"""
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, AsyncGenerator, Tuple

from config import settings
from version import DATA_SCHEMA_VERSION


class MigrationManager:
    """Handle upgrades from any version to v0.6.5."""
    
    CURRENT_VERSION = DATA_SCHEMA_VERSION
    
    VERSION_INFO = {
        "0.2": {"embedding_dim": 768, "embedding_model": "nomic-embed-text"},
        "0.3": {"embedding_dim": 768, "embedding_model": "nomic-embed-text"},
        "0.5": {"embedding_dim": 1024, "embedding_model": "snowflake-arctic-embed2"},
        "0.6.0": {"embedding_dim": 1024, "embedding_model": "snowflake-arctic-embed2"},
        "0.6.5": {"embedding_dim": 1024, "embedding_model": "snowflake-arctic-embed2"},
    }
    
    def __init__(self):
        self.data_dir = Path(settings.data_dir)
        self.db_path = self.data_dir / "lancedb"
        self.version_file = self.data_dir / "version.json"
        self.backup_dir = self.data_dir / "backups"
    
    def get_stored_version(self) -> str:
        """Get the stored version from version.json."""
        try:
            if self.version_file.exists():
                with open(self.version_file, 'r') as f:
                    data = json.load(f)
                    return data.get("version", "unknown")
        except Exception as e:
            print(f"[Migration] Error reading version: {e}")
        return "unknown"
    
    def set_version(self, version: str) -> None:
        """Store the current version."""
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with open(self.version_file, 'w') as f:
                json.dump({
                    "version": version,
                    "updated_at": datetime.utcnow().isoformat(),
                    "embedding_model": settings.embedding_model,
                    "embedding_dim": settings.embedding_dim
                }, f, indent=2)
            print(f"[Migration] Version set to {version}")
        except Exception as e:
            print(f"[Migration] Error setting version: {e}")
    
    async def detect_version(self) -> str:
        """Detect current data version by inspecting schema and data."""
        # First check version file
        stored = self.get_stored_version()
        if stored != "unknown":
            return stored
        
        # No version file - try to detect from data
        try:
            import lancedb
            
            if not self.db_path.exists():
                return "fresh"  # No data yet
            
            db = lancedb.connect(str(self.db_path))
            table_names = db.table_names()
            
            if not table_names:
                return "fresh"
            
            # Check for v0.60 markers (entities table in knowledge graph)
            kg_path = self.data_dir / "knowledge_graph" / "graph_db"
            if kg_path.exists():
                kg_db = lancedb.connect(str(kg_path))
                if "entities" in kg_db.table_names():
                    return "0.60"
            
            # Check embedding dimensions from a notebook table
            for table_name in table_names:
                if table_name.startswith("notebook_"):
                    try:
                        table = db.open_table(table_name)
                        # Get a sample row
                        sample = table.search().limit(1).to_list()
                        if sample and "vector" in sample[0]:
                            dim = len(sample[0]["vector"])
                            if dim == 1024:
                                # Check for parent_text (v0.6.0 feature)
                                if "parent_text" in sample[0]:
                                    return "0.6.0"
                                return "0.5"
                            elif dim == 768:
                                # Could be 0.2 or 0.3
                                return "0.3"  # Assume latest pre-1024
                    except Exception:
                        continue
            
            return "unknown"
            
        except Exception as e:
            print(f"[Migration] Version detection error: {e}")
            return "unknown"
    
    def needs_migration(self, current_version: str) -> Tuple[bool, str]:
        """Check if migration is needed and what type.
        
        Returns: (needs_migration, migration_type)
        migration_type: 'none', 'full_reindex', 'incremental'
        """
        if current_version in ["fresh", "0.6.0", "0.60"]:
            return False, "none"
        
        if current_version in ["0.2", "0.3", "unknown"]:
            # Embedding dimension change required
            return True, "full_reindex"
        
        if current_version == "0.5":
            # Same embedding dim, just add new features
            return True, "incremental"
        
        return False, "none"
    
    async def create_backup(self) -> Optional[Path]:
        """Create backup before migration."""
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            backup_path = self.backup_dir / f"pre_060_{timestamp}"
            backup_path.mkdir(parents=True, exist_ok=True)
            
            # Backup LanceDB
            if self.db_path.exists():
                shutil.copytree(self.db_path, backup_path / "lancedb")
            
            # Backup version file
            if self.version_file.exists():
                shutil.copy2(self.version_file, backup_path / "version.json")
            
            print(f"[Migration] Backup created at {backup_path}")
            return backup_path
            
        except Exception as e:
            print(f"[Migration] Backup failed: {e}")
            return None
    
    async def migrate(self) -> AsyncGenerator[Dict, None]:
        """Execute migration with progress updates.
        
        Yields progress updates: {"status": str, "progress": int (0-100), "warning": str?}
        """
        current_version = await self.detect_version()
        needs_migration, migration_type = self.needs_migration(current_version)
        
        print(f"[Migration] Current version: {current_version}, needs migration: {needs_migration}, type: {migration_type}")
        
        if not needs_migration:
            if current_version == "fresh":
                # Fresh install - just set version
                self.set_version(self.CURRENT_VERSION)
                yield {"status": "Fresh installation - ready to use!", "progress": 100}
            else:
                yield {"status": "Already on latest version", "progress": 100}
            return
        
        # Create backup first
        yield {"status": "📋 Creating backup...", "progress": 5}
        backup_path = await self.create_backup()
        if not backup_path:
            yield {"status": "⚠️ Backup failed - proceeding anyway", "progress": 10, "warning": "Backup failed"}
        
        # Simplification S1/A6 (2026-07-03): the old multi-step migration bodies were
        # progress theater — schema evolution is handled idempotently every launch
        # (storage/database.py CREATE IF NOT EXISTS + ALTER ADD COLUMN; LanceDB tables
        # self-create). The only real effects are: warn on embedding-dim changes
        # (re-upload needed) and bump the stored version. A dead rollback() helper
        # (zero callers) was removed with the bodies — the backup dir remains for
        # manual recovery.
        try:
            if migration_type == "full_reindex":
                yield {
                    "status": "⚠️ Embedding model changed — documents need re-indexing. Please re-upload your files.",
                    "progress": 80,
                    "warning": "Re-upload required",
                }
            self.set_version(self.CURRENT_VERSION)
            yield {"status": "✅ Upgrade complete!", "progress": 100}
        except Exception as e:
            yield {"status": f"❌ Migration error: {e}", "progress": 100, "error": str(e)}


# Singleton instance
migration_manager = MigrationManager()


async def check_and_migrate_on_startup() -> Dict:
    """Called at startup to check if migration is needed.
    
    Returns status dict for logging/display.
    """
    current = await migration_manager.detect_version()
    needs, migration_type = migration_manager.needs_migration(current)
    
    result = {
        "current_version": current,
        "target_version": MigrationManager.CURRENT_VERSION,
        "needs_migration": needs,
        "migration_type": migration_type
    }
    
    if not needs:
        # Ensure version is set for fresh installs
        if current == "fresh":
            migration_manager.set_version(MigrationManager.CURRENT_VERSION)
            result["current_version"] = MigrationManager.CURRENT_VERSION
    
    return result
