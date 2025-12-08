"""Application configuration"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # API settings
    api_port: int = 8000
    api_host: str = "0.0.0.0"

    # Data paths
    data_dir: Path = Path("data")
    db_path: Path = Path("data/lancedb")

    # LLM settings
    llm_provider: str = "ollama"  # ollama, openai, or anthropic
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "mistral-nemo:12b-instruct-2407-q4_K_M"  # Q4_K_M quantization for better quality
    ollama_fast_model: str = "phi4-mini"  # Fast model for quick summaries
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Embedding settings
    # all-MiniLM-L6-v2: Fastest option, good quality, 384 dims
    embedding_model: str = "all-MiniLM-L6-v2"
    chunk_size: int = 1000
    chunk_overlap: int = 200

    class Config:
        env_file = ".env"

settings = Settings()

# Ensure data directories exist
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.db_path.mkdir(parents=True, exist_ok=True)
