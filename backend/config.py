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

    # ── Models, one attribute per role ───────────────────────────────────────
    # v2.3.0: these hold the MLX checkpoint id DIRECTLY. Before the cutover each role was a
    # PAIR — an Ollama name (`ollama_model`) plus an `mlx_main_model`, with an engine flag
    # deciding which one `mlx_model_for_role` returned. With one engine that indirection only
    # created ways to disagree with itself, so the pair is collapsed.
    #
    # ⚠️ The registry (`known_models.json`) is keyed by these ids and carries the curated
    # per-model tuning — rag_profile (num_ctx cap, stop sequences, temperature), vision_profile,
    # structured_profile. A role pointed at an id with no registry row silently loses ALL of
    # that: no error, the tuning just stops applying. Add a row before changing a default.
    main_model: str = "mlx-community/gemma-4-e4b-it-4bit"      # chat / RAG / structured + vision
    fast_model: str = "mlx-community/Phi-4-mini-instruct-4bit"  # intent, follow-ups, classify
    # Option A: the vision-capable main model absorbs the vision slot, so this is deliberately
    # the SAME checkpoint — one gemma resident, not two.
    vision_model: str = "mlx-community/gemma-4-e4b-it-4bit"
    image_model: str = "Runpod/FLUX.2-klein-4B-mflux-4bit"      # FLUX.2 Klein via mflux

    # arctic-embed-l-v2.0 — the SAME model and the SAME 1024 dim as the old Ollama
    # `snowflake-arctic-embed2`, so the existing index needed no re-embedding.
    #
    # MEASURED 2026-08-19 (`backend/scripts/embedding_equivalence.py`, 500 real chunks + 50 real
    # queries) — the previous "bit-identical, cosine 1.0000" claim was an unverified assertion:
    #   bf16  : mean 0.999940, p1 0.999816 · top-5 overlap 0.992, 0/50 top-1 changes → PASSES
    #   8-bit : mean 0.999437, p1 0.999187 · top-5 overlap 0.980, 1/50 top-1 changes → FAILS
    # Hence bf16 is pinned despite costing ~0.5 GB more. Do NOT switch to 8-bit to save memory
    # without re-running that script and accepting a retrieval change.
    embedding_model: str = "mlx-community/snowflake-arctic-embed-l-v2.0-bf16"

    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Embedding settings
    embedding_dim: int = 1024  # arctic-embed-l-v2.0 is 1024-dim
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
    # Primary model for text-to-SQL generation. Default (None) = the main model (gemma), which is
    # far more reliable at SQL than the fast model (phi4 hallucinated spurious WHERE clauses). On
    # timeout/error under load the executor automatically falls back to the fast model, so a
    # contended box still answers. Pin a specific model here to override the primary.
    tabular_sql_model: str | None = None
    # Python hard-compute tier (P2): build Studio document charts by generating Python and running it
    # in the py_compute sandbox, so totals/shares/growth-rates are EXECUTED rather than typed by the
    # model — today's path asks gemma for finished ChartConfig JSON, the one place in the app where
    # plotted numbers are model-authored. OFF until proven in the built app; the caller falls back to
    # the LLM-JSON path whenever the sandbox yields no chart, so flipping it is safe either way.
    py_compute_doc_charts_enabled: bool = False

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
