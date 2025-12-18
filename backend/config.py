"""Application configuration"""
import os
import sys
from pathlib import Path
from pydantic_settings import BaseSettings

def get_data_directory() -> Path:
    """Get the appropriate data directory based on environment.
    
    - Development: ./data (relative to project)
    - Production (bundled app): ~/Library/Application Support/LocalBook/
    """
    # Check if running as a bundled PyInstaller app
    if getattr(sys, 'frozen', False):
        # Running as bundled app - use Application Support
        app_support = Path.home() / "Library" / "Application Support" / "LocalBook"
        
        # Auto-migrate from old bundle location if needed
        _migrate_old_data(app_support)
        
        return app_support
    else:
        # Development mode - use relative path
        return Path("data")


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
            print(f"[Config] Migration complete!")
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
    ollama_model: str = "phi4:14b"  # System 2: Main model for conversation and reasoning
    ollama_fast_model: str = "llama3.2:3b"  # System 1: Fast model for quick responses
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Embedding settings
    # nomic-embed-text: 768 dims, 8192 context, outperforms OpenAI ada-002
    embedding_model: str = "nomic-embed-text"  # Via Ollama
    use_ollama_embeddings: bool = True  # Use Ollama for embeddings instead of sentence-transformers
    chunk_size: int = 1000
    chunk_overlap: int = 200
    
    # Reranker settings (two-stage retrieval)
    use_reranker: bool = True  # Enable cross-encoder reranking for better retrieval
    reranker_model: str = "BAAI/bge-reranker-v2-m3"  # Cross-encoder model
    retrieval_overcollect: int = 12  # Candidates from vector search before reranking
    retrieval_top_k: int = 5  # Final chunks after reranking

    class Config:
        env_file = ".env"

settings = Settings()

# Ensure data directories exist
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.db_path.mkdir(parents=True, exist_ok=True)
