"""Application configuration"""
import sys
from pathlib import Path
from pydantic_settings import BaseSettings

def get_data_directory() -> Path:
    """Get the data directory - ALWAYS uses production location.
    
    All environments (dev, bundled) use: ~/Library/Application Support/LocalBook/
    This ensures consistent data across development and production.
    """
    app_support = Path.home() / "Library" / "Application Support" / "LocalBook"
    
    # Auto-migrate from old bundle location if needed (for bundled apps)
    if getattr(sys, 'frozen', False):
        _migrate_old_data(app_support)
    
    return app_support


def _migrate_old_data(new_data_dir: Path) -> None:
    """Migrate data from old bundle location to Application Support.
    
    This handles users upgrading from versions that stored data inside the app bundle.
    """
    import shutil
    
    # Only migrate if new location is empty/missing
    if new_data_dir.exists() and any(new_data_dir.iterdir()):
        return  # Already has data, don't overwrite
    
    # Check for old data in bundle location (relative to frozen executable)
    old_data_dir = Path(sys.executable).parent / "data"
    
    if old_data_dir.exists() and any(old_data_dir.iterdir()):
        print(f"[Config] Migrating data from {old_data_dir} to {new_data_dir}")
        try:
            new_data_dir.mkdir(parents=True, exist_ok=True)
            for item in old_data_dir.iterdir():
                dest = new_data_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
            print("[Config] Migration complete!")
        except Exception as e:
            print(f"[Config] Migration failed: {e}")

class Settings(BaseSettings):
    # API settings
    api_port: int = 8000
    api_host: str = "127.0.0.1"

    # Data paths - computed based on environment
    data_dir: Path = get_data_directory()
    db_path: Path = get_data_directory() / "lancedb"

    # LLM settings
    llm_provider: str = "ollama"  # ollama, openai, or anthropic
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "olmo-3:7b-instruct"  # System 2: Main model - 64K context, chat/synthesis, streams properly
    ollama_fast_model: str = "phi4-mini:latest"  # System 1: Fast model - Microsoft Phi-4 mini, better than llama3.2:3b
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Embedding settings
    # snowflake-arctic-embed2: 1024 dims, frontier model, excellent retrieval quality
    # Upgrade from nomic-embed-text (768 dims) for better semantic matching
    embedding_model: str = "snowflake-arctic-embed2"  # Via Ollama - best balance of speed/quality
    embedding_dim: int = 1024  # snowflake-arctic-embed2 uses 1024 dimensions
    use_ollama_embeddings: bool = True  # Use Ollama for embeddings instead of sentence-transformers
    chunk_size: int = 1000
    chunk_overlap: int = 200
    
    # Reranker settings (two-stage retrieval)
    # FlashRank: Ultra-fast, no torch needed, runs on CPU, ~34MB
    use_reranker: bool = True  # Enable cross-encoder reranking for better retrieval
    reranker_model: str = "ms-marco-MiniLM-L-12-v2"  # FlashRank model - best quality
    reranker_type: str = "flashrank"  # "flashrank" (fast, CPU) or "cross-encoder" (slower, GPU)
    retrieval_overcollect: int = 12  # Candidates from vector search before reranking
    retrieval_top_k: int = 5  # Final chunks after reranking

    # Debug mode — enables diagnostic endpoints (health portal, RAG health)
    debug_mode: bool = False  # Set LOCALBOOK_DEBUG_MODE=true to enable

    # Storage backend — use SQLite instead of JSON files
    use_sqlite: bool = False  # Set USE_SQLITE=true to enable

    class Config:
        env_file = ".env"

settings = Settings()

# Ensure data directories exist
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.db_path.mkdir(parents=True, exist_ok=True)
