"""Application configuration"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # API settings
    api_port: int = 8000
    api_host: str = "127.0.0.1"

    # Data paths
    data_dir: Path = Path("data")
    db_path: Path = Path("data/lancedb")

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
