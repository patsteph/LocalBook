"""
Startup Checks Service

Handles all first-launch and upgrade checks:
1. Data migration to ~/Library/Application Support/LocalBook/
2. Model readiness — are the configured MLX models actually on disk?
3. Embedding dimension migration (768 -> 1024 for all tables)
4. Knowledge graph table schema validation

Model verification used to ask Ollama over HTTP (`/api/version`, `/api/tags`). MLX models
live in the HuggingFace cache, so presence is a filesystem question — `model_presence` answers
it without a server, and there is no engine version to floor-check any more.
"""
import lancedb
import pyarrow as pa
from typing import List, Tuple, Dict, Any
from config import settings
import logging
logger = logging.getLogger(__name__)

# Required models — derived ENTIRELY from the configured roles, never a hardcoded set.
# Deduped, because main and vision are the same gemma checkpoint under MLX and listing it
# twice would report one missing download as two.
_ROLE_DESCRIPTIONS = [
    ("mlx_main_model", "Main model (chat/synthesis)"),
    ("mlx_fast_model", "Fast response model for follow-ups"),
    ("mlx_embedding_model", "Embedding model (1024 dimensions)"),
    ("mlx_vision_model", "Vision model for PDF image/chart extraction"),
]


def _required_models() -> List[Tuple[str, str]]:
    """Resolved at CALL time, not import time. A role can be repointed in the Locker while
    the app runs, and an import-time snapshot would keep checking the old model forever."""
    out, seen = [], set()
    for attr, desc in _ROLE_DESCRIPTIONS:
        name = getattr(settings, attr, "") or ""
        if name and name not in seen:
            seen.add(name)
            out.append((name, desc))
    return out


# Back-compat alias: health_portal renders this. A property-like call would be better but
# this stays a plain list so the import in api/health_portal.py keeps working.
REQUIRED_MODELS = _required_models()

# Expected embedding dimension for snowflake-arctic-embed2
# Updated from 768 (nomic-embed-text) to 1024 in v0.6.0
EXPECTED_EMBEDDING_DIM = 1024


async def run_all_startup_checks(status_callback=None) -> Dict[str, Any]:
    """
    Run all startup checks and migrations.
    
    Args:
        status_callback: Optional function(status, message, progress) to report progress
    
    Returns:
        Dict with results of all checks
    """
    results = {
        "data_migration": None,
        "models_verified": None,
        "models_missing": [],
        "embedding_migration": None,
        "kg_migration": None,
        "errors": []
    }
    
    def update_status(status: str, message: str, progress: int):
        if status_callback:
            status_callback(status, message, progress)
        print(f"[Startup] {message}")
    
    try:
        # Step 1: Data migration check (already handled by config.py, just verify)
        update_status("checking", "Verifying data directory...", 10)
        results["data_migration"] = verify_data_directory()
        
        # Step 2: Are the configured models actually downloaded?
        update_status("checking", "Checking AI models...", 20)
        available, missing = await check_models_present()
        results["models_verified"] = len(missing) == 0
        results["models_missing"] = missing
        
        if missing:
            # NEVER auto-download on startup (Wave 9 decision #1) — auto-pulling at boot
            # surprised users with multi-GB downloads and stalled launch. Report and let
            # them install explicitly from LLM Studio.
            _missing_names = ", ".join([m[0] for m in missing])
            update_status("warning", f"Models not installed (install in LLM Studio): {_missing_names}", 25)
            results["errors"].append(
                f"Models not installed: {_missing_names}. Install them in LLM Studio. "
                f"(Not auto-downloaded — Wave 9 decision.)")
        
        # Step 3: Check RAG embedding dimensions
        update_status("checking", "Checking embedding compatibility...", 50)
        rag_needs_migration = check_rag_embedding_dimensions()
        results["embedding_migration"] = "needed" if rag_needs_migration else "ok"
        
        if rag_needs_migration:
            update_status("migrating", "Migrating RAG embeddings to new format...", 60)
            await migrate_rag_embeddings()
        
        # Step 4: Check Knowledge Graph dimensions
        update_status("checking", "Checking knowledge graph...", 70)
        kg_needs_migration = check_knowledge_graph_dimensions()
        results["kg_migration"] = "needed" if kg_needs_migration else "ok"
        
        if kg_needs_migration:
            update_status("migrating", "Resetting knowledge graph for new embeddings...", 80)
            await reset_knowledge_graph_tables()
        
        # Step 5: Check memory store dimensions
        update_status("checking", "Checking memory store...", 90)
        memory_needs_migration = check_memory_store_dimensions()
        if memory_needs_migration:
            update_status("migrating", "Resetting memory store for new embeddings...", 95)
            await reset_memory_store_tables()
        
        update_status("ready", "All checks complete!", 100)
        
    except Exception as e:
        results["errors"].append(str(e))
        print(f"[Startup] Error during checks: {e}")
    
    return results


def verify_data_directory() -> bool:
    """Verify the data directory exists and is writable."""
    try:
        data_dir = settings.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        
        # Test write access
        test_file = data_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        
        print(f"[Startup] Data directory verified: {data_dir}")
        return True
    except Exception as e:
        print(f"[Startup] Data directory error: {e}")
        return False


async def check_models_present() -> Tuple[List[str], List[Tuple[str, str]]]:
    """Which configured models are on disk, and which are missing.

    A filesystem question now — MLX weights live in the HF cache. Returns the same
    (available, missing) shape the Ollama `/api/tags` version returned so the Health
    portal and the startup reporter did not have to change.
    """
    available: List[str] = []
    missing: List[Tuple[str, str]] = []
    try:
        from services.model_presence import is_present
    except Exception as e:
        # Presence is unknowable → report nothing missing rather than blocking the boot on
        # a false alarm. A genuinely absent model still fails loudly at first use.
        logger.warning(f"[Startup] model presence check unavailable: {e}")
        return [m for m, _ in _required_models()], []

    for name, description in _required_models():
        if is_present(name):
            available.append(name)
        else:
            missing.append((name, description))
    return available, missing


def check_rag_embedding_dimensions() -> bool:
    """
    Check if any RAG notebook tables have wrong embedding dimensions.
    Returns True if migration is needed.
    """
    try:
        db_path = settings.db_path
        if not db_path.exists():
            return False
        
        db = lancedb.connect(str(db_path))
        
        for table_name in db.table_names():
            if table_name.startswith("notebook_"):
                table = db.open_table(table_name)
                if table.count_rows() == 0:
                    continue
                
                schema = table.schema
                for field in schema:
                    if field.name == "vector":
                        if hasattr(field.type, 'list_size'):
                            stored_dim = field.type.list_size
                            if stored_dim != EXPECTED_EMBEDDING_DIM:
                                print(f"[Startup] RAG table {table_name} has {stored_dim}-dim vectors, need {EXPECTED_EMBEDDING_DIM}")
                                return True
        return False
    except Exception as e:
        print(f"[Startup] Error checking RAG dimensions: {e}")
        return False


async def migrate_rag_embeddings():
    """Migrate RAG embeddings by triggering a full reindex."""
    try:
        from api.reindex import reindex_all_notebooks
        result = await reindex_all_notebooks(force=True, drop_tables=True)
        print(f"[Startup] RAG reindex complete: {result.get('message', 'done')}")
    except Exception as e:
        print(f"[Startup] RAG reindex error: {e}")


def check_knowledge_graph_dimensions() -> bool:
    """
    Check if knowledge graph concepts table has wrong embedding dimensions.
    Returns True if reset is needed.
    """
    try:
        kg_path = settings.data_dir / "knowledge_graph" / "graph_db"
        if not kg_path.exists():
            return False
        
        db = lancedb.connect(str(kg_path))
        
        if "concepts" not in db.table_names():
            return False
        
        table = db.open_table("concepts")
        if table.count_rows() == 0:
            return False
        
        schema = table.schema
        for field in schema:
            if field.name == "vector":
                if hasattr(field.type, 'list_size'):
                    stored_dim = field.type.list_size
                    if stored_dim != EXPECTED_EMBEDDING_DIM:
                        print(f"[Startup] Knowledge graph has {stored_dim}-dim vectors, need {EXPECTED_EMBEDDING_DIM}")
                        return True
        return False
    except Exception as e:
        print(f"[Startup] Error checking KG dimensions: {e}")
        return False


async def reset_knowledge_graph_tables():
    """Drop and recreate knowledge graph tables with correct schema."""
    try:
        kg_path = settings.data_dir / "knowledge_graph" / "graph_db"
        db = lancedb.connect(str(kg_path))
        
        # Drop tables if they exist
        for table_name in ["concepts", "links", "clusters", "contradictions"]:
            if table_name in db.table_names():
                db.drop_table(table_name)
                print(f"[Startup] Dropped KG table: {table_name}")
        
        # Recreate concepts table with correct embedding dimensions
        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("name", pa.string()),
            pa.field("description", pa.string()),
            pa.field("source_chunk_ids", pa.string()),
            pa.field("source_notebook_ids", pa.string()),
            pa.field("frequency", pa.int32()),
            pa.field("importance", pa.float32()),
            pa.field("cluster_id", pa.string()),
            pa.field("created_at", pa.string()),
            pa.field("updated_at", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), EXPECTED_EMBEDDING_DIM)),
        ])
        db.create_table("concepts", schema=schema)
        
        # Links table
        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("source_id", pa.string()),
            pa.field("target_id", pa.string()),
            pa.field("source_type", pa.string()),
            pa.field("target_type", pa.string()),
            pa.field("link_type", pa.string()),
            pa.field("strength", pa.float32()),
            pa.field("evidence", pa.string()),
            pa.field("source_notebook_id", pa.string()),
            pa.field("auto_detected", pa.bool_()),
            pa.field("verified", pa.bool_()),
            pa.field("created_at", pa.string()),
        ])
        db.create_table("links", schema=schema)
        
        # Clusters table
        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("name", pa.string()),
            pa.field("description", pa.string()),
            pa.field("concept_ids", pa.string()),
            pa.field("coherence_score", pa.float32()),
            pa.field("size", pa.int32()),
            pa.field("notebook_ids", pa.string()),
            pa.field("created_at", pa.string()),
            pa.field("updated_at", pa.string()),
        ])
        db.create_table("clusters", schema=schema)
        
        # Contradictions table
        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("chunk_id_1", pa.string()),
            pa.field("chunk_id_2", pa.string()),
            pa.field("text_1", pa.string()),
            pa.field("text_2", pa.string()),
            pa.field("explanation", pa.string()),
            pa.field("severity", pa.string()),
            pa.field("notebook_ids", pa.string()),
            pa.field("resolved", pa.bool_()),
            pa.field("created_at", pa.string()),
        ])
        db.create_table("contradictions", schema=schema)
        
        print(f"[Startup] Knowledge graph tables recreated with {EXPECTED_EMBEDDING_DIM}-dim schema")
        
        # Reset the singleton service state
        try:
            from services.knowledge_graph import knowledge_graph_service
            knowledge_graph_service._initialized = False
            knowledge_graph_service._cache_loaded = False
            knowledge_graph_service._concept_name_cache = {}
        except Exception as _e:
            logger.debug(f"[startup-checks] {type(_e).__name__}: {_e}")
            
    except Exception as e:
        print(f"[Startup] Error resetting KG tables: {e}")


def check_memory_store_dimensions() -> bool:
    """
    Check if memory store has wrong embedding dimensions.
    Returns True if reset is needed.
    """
    try:
        memory_path = settings.data_dir / "memory" / "archival_db"
        if not memory_path.exists():
            return False
        
        db = lancedb.connect(str(memory_path))
        
        if "archival_memories" not in db.table_names():
            return False
        
        table = db.open_table("archival_memories")
        if table.count_rows() == 0:
            return False
        
        schema = table.schema
        for field in schema:
            if field.name == "vector":
                if hasattr(field.type, 'list_size'):
                    stored_dim = field.type.list_size
                    if stored_dim != EXPECTED_EMBEDDING_DIM:
                        print(f"[Startup] Memory store has {stored_dim}-dim vectors, need {EXPECTED_EMBEDDING_DIM}")
                        return True
        return False
    except Exception as e:
        print(f"[Startup] Error checking memory dimensions: {e}")
        return False


async def reset_memory_store_tables():
    """Drop and recreate memory store tables with correct schema."""
    try:
        memory_path = settings.data_dir / "memory" / "archival_db"
        if not memory_path.exists():
            return
        
        db = lancedb.connect(str(memory_path))
        
        if "archival_memories" in db.table_names():
            db.drop_table("archival_memories")
            print("[Startup] Dropped archival_memories table")
        
        # Recreate with correct embedding dimensions
        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("content", pa.string()),
            pa.field("content_type", pa.string()),
            pa.field("source_type", pa.string()),
            pa.field("source_id", pa.string()),
            pa.field("source_notebook_id", pa.string()),
            pa.field("topics", pa.string()),
            pa.field("entities", pa.string()),
            pa.field("importance", pa.string()),
            pa.field("created_at", pa.string()),
            pa.field("last_accessed", pa.string()),
            pa.field("access_count", pa.int32()),
            pa.field("vector", pa.list_(pa.float32(), EXPECTED_EMBEDDING_DIM)),
        ])
        db.create_table("archival_memories", schema=schema)
        print(f"[Startup] Memory store table recreated with {EXPECTED_EMBEDDING_DIM}-dim schema")
        
    except Exception as e:
        print(f"[Startup] Error resetting memory tables: {e}")
