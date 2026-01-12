"""
Startup Checks Service for LocalBook v0.6.0

Handles all first-launch and upgrade checks:
1. Data migration to ~/Library/Application Support/LocalBook/
2. Ollama model verification (olmo-3:7b-instruct, phi4-mini, snowflake-arctic-embed2)
3. Embedding dimension migration (768 -> 1024 for all tables)
4. Knowledge graph table schema validation
"""
import asyncio
import httpx
import lancedb
import pyarrow as pa
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from config import settings

# Required Ollama models for v0.6.0
REQUIRED_MODELS = [
    ("olmo-3:7b-instruct", "Main model (7B parameters, chat/synthesis)"),
    ("phi4-mini", "Fast response model for follow-ups"),
    ("snowflake-arctic-embed2", "Embedding model (1024 dimensions)"),
]

# Minimum Ollama version required for OLMO model support
MIN_OLLAMA_VERSION = "0.5.0"

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
        
        # Step 2: Check Ollama version (required for OLMO support)
        update_status("checking", "Checking Ollama version...", 15)
        version_ok, current_version, min_version = await check_ollama_version()
        results["ollama_version"] = current_version
        results["ollama_version_ok"] = version_ok
        
        if not version_ok:
            error_msg = f"Ollama version {current_version} is too old. Please update to {min_version}+ for OLMO support. Run: ollama --version to check, then update Ollama from ollama.ai"
            results["errors"].append(error_msg)
            update_status("error", error_msg, 20)
        
        # Step 3: Check Ollama models
        update_status("checking", "Checking AI models...", 20)
        available, missing = await check_ollama_models()
        results["models_verified"] = len(missing) == 0
        results["models_missing"] = missing
        
        if missing:
            update_status("warning", f"Missing models: {', '.join([m[0] for m in missing])}", 25)
            # Try to pull missing models
            for model_name, description in missing:
                update_status("downloading", f"Downloading {model_name}...", 30)
                success = await pull_ollama_model(model_name)
                if not success:
                    results["errors"].append(f"Failed to download {model_name}")
        
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


def parse_version(version_str: str) -> Tuple[int, int, int]:
    """Parse a version string like '0.5.1' into a tuple (0, 5, 1)."""
    try:
        parts = version_str.strip().split('.')
        return tuple(int(p) for p in parts[:3])
    except:
        return (0, 0, 0)


async def check_ollama_version() -> Tuple[bool, str, str]:
    """
    Check if Ollama version meets minimum requirements for OLMO support.
    
    Returns:
        Tuple of (version_ok, current_version, min_version)
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{settings.ollama_base_url}/api/version")
            if response.status_code == 200:
                data = response.json()
                current = data.get("version", "0.0.0")
                current_tuple = parse_version(current)
                min_tuple = parse_version(MIN_OLLAMA_VERSION)
                
                version_ok = current_tuple >= min_tuple
                if not version_ok:
                    print(f"[Startup] Ollama version {current} is below minimum {MIN_OLLAMA_VERSION} required for OLMO")
                else:
                    print(f"[Startup] Ollama version {current} meets requirements")
                
                return version_ok, current, MIN_OLLAMA_VERSION
            else:
                print(f"[Startup] Could not get Ollama version: {response.status_code}")
                return False, "unknown", MIN_OLLAMA_VERSION
    except Exception as e:
        print(f"[Startup] Could not check Ollama version: {e}")
        return False, "unknown", MIN_OLLAMA_VERSION


async def check_ollama_models() -> Tuple[List[str], List[Tuple[str, str]]]:
    """
    Check which required Ollama models are available.
    
    Returns:
        Tuple of (available_models, missing_models)
    """
    available = []
    missing = []
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{settings.ollama_base_url}/api/tags")
            if response.status_code == 200:
                data = response.json()
                installed_models = {m["name"].split(":")[0] for m in data.get("models", [])}
                # Also check full names with tags
                installed_full = {m["name"] for m in data.get("models", [])}
                
                for model_name, description in REQUIRED_MODELS:
                    base_name = model_name.split(":")[0]
                    if model_name in installed_full or base_name in installed_models:
                        available.append(model_name)
                    else:
                        missing.append((model_name, description))
            else:
                print(f"[Startup] Ollama API returned {response.status_code}")
                missing = REQUIRED_MODELS.copy()
    except Exception as e:
        print(f"[Startup] Could not connect to Ollama: {e}")
        missing = REQUIRED_MODELS.copy()
    
    return available, missing


async def pull_ollama_model(model_name: str) -> bool:
    """Pull an Ollama model if not already available."""
    try:
        print(f"[Startup] Pulling model: {model_name}")
        async with httpx.AsyncClient(timeout=600.0) as client:  # 10 min timeout for large models
            response = await client.post(
                f"{settings.ollama_base_url}/api/pull",
                json={"name": model_name, "stream": False}
            )
            if response.status_code == 200:
                print(f"[Startup] Successfully pulled {model_name}")
                return True
            else:
                print(f"[Startup] Failed to pull {model_name}: {response.status_code}")
                return False
    except Exception as e:
        print(f"[Startup] Error pulling {model_name}: {e}")
        return False


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
        
        # Recreate concepts table with 768 dimensions
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
        
        print("[Startup] Knowledge graph tables recreated with 768-dim schema")
        
        # Reset the singleton service state
        try:
            from services.knowledge_graph import knowledge_graph_service
            knowledge_graph_service._initialized = False
            knowledge_graph_service._cache_loaded = False
            knowledge_graph_service._concept_name_cache = {}
        except:
            pass
            
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
        
        # Recreate with 768 dimensions
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
        print("[Startup] Memory store table recreated with 768-dim schema")
        
    except Exception as e:
        print(f"[Startup] Error resetting memory tables: {e}")
