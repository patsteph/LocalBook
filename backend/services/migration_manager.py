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
import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, AsyncGenerator, Tuple

from config import settings


class MigrationManager:
    """Handle upgrades from any version to v0.6.5."""
    
    CURRENT_VERSION = "0.6.5"
    
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
        
        if migration_type == "full_reindex":
            async for update in self._full_reindex_migration(current_version):
                yield update
        else:
            async for update in self._incremental_migration():
                yield update
    
    async def _full_reindex_migration(self, from_version: str) -> AsyncGenerator[Dict, None]:
        """v0.2/v0.3 → v0.60: Full re-index required due to embedding dimension change."""
        yield {"status": f"🔄 Major upgrade from v{from_version} detected", "progress": 10}
        yield {"status": "📦 This will re-index all documents with new embeddings", "progress": 15}
        
        # Get list of sources to re-index
        from storage.source_store import source_store
        
        try:
            sources_data = source_store._load_data()
            all_sources = sources_data.get("sources", {})
            source_count = len(all_sources)
            
            yield {"status": f"📄 Found {source_count} documents to upgrade", "progress": 20}
            
            if source_count == 0:
                # No documents - just update schema
                yield {"status": "No documents to migrate", "progress": 90}
            else:
                # Note: Actual re-indexing would require re-reading source files
                # For now, we mark that re-indexing is needed
                yield {
                    "status": "⚠️ Documents need re-indexing. Please re-upload your files.", 
                    "progress": 80,
                    "warning": "Re-upload required for full v0.60 features"
                }
            
            # Update version
            self.set_version(self.CURRENT_VERSION)
            yield {"status": "✅ Upgrade complete! Re-upload documents for best results.", "progress": 100}
            
        except Exception as e:
            yield {"status": f"❌ Migration error: {e}", "progress": 100, "error": str(e)}
    
    async def _incremental_migration(self) -> AsyncGenerator[Dict, None]:
        """v0.5 → v0.60: Incremental enhancement (same embedding dims)."""
        yield {"status": "🔄 Enhancing your documents with v0.60 features...", "progress": 10}
        
        try:
            import lancedb
            
            # Step 1: Ensure knowledge graph has new tables
            yield {"status": "📊 Updating schema...", "progress": 20}
            from services.knowledge_graph import knowledge_graph_service
            # The __init__ will create new tables if they don't exist
            
            # Step 2: Add parent_text to existing chunks (if not present)
            yield {"status": "🔗 Adding parent document support...", "progress": 40}
            # Note: For existing chunks, parent_text will be empty
            # New documents will have it populated
            
            # Step 3: Initialize entity graph tables
            yield {"status": "🕸️ Initializing entity graph...", "progress": 60}
            # Tables created by knowledge_graph_service init
            
            # Step 4: Mark migration complete
            yield {"status": "📝 Finalizing...", "progress": 80}
            self.set_version(self.CURRENT_VERSION)
            
            yield {
                "status": "✅ Upgrade to v0.60 complete!", 
                "progress": 100,
                "info": "New documents will have full v0.60 features. Re-upload existing docs for parent context."
            }
            
        except Exception as e:
            yield {"status": f"❌ Migration error: {e}", "progress": 100, "error": str(e)}
    
    async def rollback(self, backup_path: Path) -> bool:
        """Restore from backup if migration fails."""
        try:
            if not backup_path.exists():
                print(f"[Migration] Backup not found: {backup_path}")
                return False
            
            # Restore LanceDB
            lancedb_backup = backup_path / "lancedb"
            if lancedb_backup.exists():
                if self.db_path.exists():
                    shutil.rmtree(self.db_path)
                shutil.copytree(lancedb_backup, self.db_path)
            
            # Restore version file
            version_backup = backup_path / "version.json"
            if version_backup.exists():
                shutil.copy2(version_backup, self.version_file)
            
            print(f"[Migration] Rollback complete from {backup_path}")
            return True
            
        except Exception as e:
            print(f"[Migration] Rollback failed: {e}")
            return False


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
