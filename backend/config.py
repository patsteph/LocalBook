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

    # Browser extension — pinned ID derived from extension/.key.pem manifest key.
    # Used by P0.1e (Origin-checked /auth/bootstrap) and P0.1g (CORS allowlist).
    # If the key ever rotates (private key compromise), regenerate and update
    # this string. P0.1d (2026-05-15).
    extension_id: str = "opnfhnhhcahpkglaepaplpafogdhemon"

    # Data paths - computed based on environment
    data_dir: Path = get_data_directory()
    db_path: Path = get_data_directory() / "lancedb"

    # LLM settings
    llm_provider: str = "ollama"  # ollama, openai, or anthropic
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:e4b"  # System 2: Main model - 8B Q4, native vision + JSON-mode + thinking control; per FINAL_CODE_REVIEW_Gemma4_Migration.md, replaces olmo-3:7b-instruct
    ollama_fast_model: str = "phi4-mini:latest"  # System 1: Fast model - Microsoft Phi-4 mini, better than llama3.2:3b
    # Vision model used by scan pipeline + multimodal PDF extraction.
    # The pipeline is model-agnostic: it loads whatever name is
    # configured here (or via LOCALBOOK_VISION_MODEL env / the Settings
    # → Models combo picker) and uses the registry entry's
    # `vision_api_style` to route through /api/generate or /api/chat.
    # When the configured model fails the user gets a typed
    # `VisionModelError` surfaced as a clear "Vision model X failed"
    # banner with a hint to swap models — no need to ship a code change
    # to react to a broken upstream model file. The default below is
    # just a known-good starting point; users are expected to swap it
    # to whatever they prefer (gemma4:e4b, llava, moondream, etc.).
    vision_model: str = "granite3.2-vision:2b"
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Embedding settings
    # snowflake-arctic-embed2: 1024 dims, frontier model, excellent retrieval quality
    # Upgrade from nomic-embed-text (768 dims) for better semantic matching
    embedding_model: str = "snowflake-arctic-embed2"  # Via Ollama - best balance of speed/quality
    embedding_dim: int = 1024  # snowflake-arctic-embed2 uses 1024 dimensions
    use_ollama_embeddings: bool = True  # Use Ollama for embeddings instead of sentence-transformers
    use_spacy_extractor: bool = True  # NER via spaCy (en_core_web_sm) instead of phi4 LLM — faster, deterministic, frees the fast lane
    chunk_size: int = 1000
    chunk_overlap: int = 200
    
    # Reranker settings (two-stage retrieval)
    # FlashRank: Ultra-fast, no torch needed, runs on CPU, ~34MB
    use_reranker: bool = True  # Enable cross-encoder reranking for better retrieval
    reranker_model: str = "ms-marco-MiniLM-L-12-v2"  # FlashRank model - best quality
    reranker_type: str = "flashrank"  # "flashrank" (fast, CPU) or "cross-encoder" (slower, GPU)
    retrieval_overcollect: int = 12  # Candidates from vector search before reranking
    retrieval_top_k: int = 5  # Final chunks after reranking

    # Tabular structured Q&A — load xlsx/xls/csv into typed SQLite tables at ingest and
    # answer aggregate/count/list questions via local text-to-SQL (100% accurate counts),
    # instead of vector top-k retrieval which cannot aggregate. Additive + tabular-only;
    # set LOCALBOOK_TABULAR_STRUCTURED_ENABLED=false to fully disable (pure vector RAG).
    tabular_structured_enabled: bool = True

    # Debug mode — enables diagnostic endpoints (health portal, RAG health)
    debug_mode: bool = False  # Set LOCALBOOK_DEBUG_MODE=true to enable

    # Curator pre-triage: when True, collector._add_to_approval_queue runs
    # each candidate through curator._judge_single_item before queuing. The
    # curator can auto-approve high-confidence items, auto-reject obvious
    # rejects (e.g. high overlap with existing knowledge), or stamp the
    # item with its decision and let it queue for the user. Set False to
    # restore prior behaviour (no curator pre-triage). Curator Phase 1.
    curator_pre_triage_enabled: bool = True

    # Engagement telemetry: when True, the curator brain records what
    # the user actually interacts with (RAG queries, source rejections,
    # which @curator intents fire, brief opens, story clicks, thumbs
    # reactions). Powers smart morning brief (Phase 5) + calibrated
    # uncertainty (Phase 4). Data is local-only — never leaves the
    # device. Flip to False to disable; record_engagement becomes a
    # no-op and the /curator/engagement capture endpoint returns
    # {ok: True, suppressed: True} without persisting. Curator Phase 2a.
    engagement_tracking_enabled: bool = True

    # Storage backend — use SQLite instead of JSON files
    use_sqlite: bool = True  # SQLite is default — auto-migrates from JSON on first launch

    # Auth enforcement (P0.1f). When True, AppTokenAuthMiddleware returns
    # 401 on missing/invalid X-LocalBook-Token; when False, it logs a
    # warning and lets the request through. Default True (production).
    # Override at launch time with the LOCALBOOK_AUTH_ENFORCE env var or
    # by adding `LOCALBOOK_AUTH_ENFORCE=false` to the .env file in the
    # data dir — use this temporarily while diagnosing a 401 regression.
    auth_enforce: bool = True

    class Config:
        # Read .env from the user data dir so production .app bundles
        # (read-only CWD) can still be configured by editing
        # ~/Library/Application Support/LocalBook/.env.
        # The local cwd .env stays as a secondary lookup for dev mode.
        env_file = (get_data_directory() / ".env", ".env")
        extra = "ignore"  # LLM Locker writes LOCALBOOK_-prefixed keys to .env; ignore them

settings = Settings()

# Ensure data directories exist
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.db_path.mkdir(parents=True, exist_ok=True)
