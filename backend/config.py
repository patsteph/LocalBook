"""Application configuration"""
import os
import sys
from typing import Optional
from pathlib import Path
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

PRODUCTION_DATA_DIR = Path.home() / "Library" / "Application Support" / "LocalBook"
DEV_DATA_DIR = Path.home() / "Library" / "Application Support" / "LocalBook-dev"

# Set when an unfrozen process has been pointed at the real data dir on purpose.
# main.py reads it to show a banner: the danger is doing it by accident and not
# noticing, so the escape hatch has to be loud.
DEV_USING_PRODUCTION_DATA = False


def get_data_directory() -> Path:
    """Where LocalBook keeps its data.

    A BUNDLED app always uses `~/Library/Application Support/LocalBook`.

    An UNFROZEN process — a dev run, a script, a test, anything started from
    `backend/.venv` — defaults to `LocalBook-dev` instead (LB-10 item 7).

    This used to return the production path unconditionally, with the comment
    "ensures consistent data across development and production". That
    consistency is precisely the hazard: every script, REPL and stray
    `TestClient(main.app)` ran against the user's real notebooks, credentials and
    keys. It has bitten this project repeatedly — `save_default_combo({})` once
    overwrote `user_preferences.json`, and on 2026-09-29 two separate ad-hoc
    checks wrote a stray companion key and rotated `.app_token`.

    The override is deliberately explicit and deliberately loud:

        LOCALBOOK_DATA_DIR=/some/path            use that path
        LOCALBOOK_USE_PRODUCTION_DATA=1          use the real data dir, with a banner

    Nothing here changes behaviour for the shipped app.
    """
    global DEV_USING_PRODUCTION_DATA

    explicit = os.environ.get("LOCALBOOK_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()

    frozen = getattr(sys, "frozen", False)
    if frozen:
        _migrate_old_data(PRODUCTION_DATA_DIR)
        return PRODUCTION_DATA_DIR

    if os.environ.get("LOCALBOOK_USE_PRODUCTION_DATA", "").strip().lower() in ("1", "true", "yes"):
        DEV_USING_PRODUCTION_DATA = True
        return PRODUCTION_DATA_DIR

    return DEV_DATA_DIR


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
    # Hard wall-clock bound on a SINGLE MLX generation, enforced inside the token loop.
    #
    # This is the only place it CAN be enforced. `mlx_engine._run` dispatches to
    # `loop.run_in_executor`, and an executor future cannot be interrupted once running — so
    # `asyncio.wait_for`, the evaluator's 180s phase timeout and the release script's timeout
    # can all bound the WAIT but never the WORK. On 2026-09-15 one generation ran 23 minutes
    # (1,392,230ms) through a 180s phase timeout that never fired.
    #
    # num_predict caps TOKENS, not time: at ~2.8s/token under memory pressure, 500 tokens is
    # 23 minutes. Only a clock catches that.
    mlx_max_generation_seconds: int = 180

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

    # ── Quantized KV cache (2026-09-22) ──────────────────────────────
    # mlx-lm can store the KV cache at 4 or 8 bits instead of fp16. The cache is
    # the only part of inference that grows with conversation length, so this is
    # what buys headroom at long context — weights are fixed, KV is not.
    #
    # 8 bits, not 4: halving the cache is the uncontroversial win, while 4-bit
    # has measurable quality effects on some tasks. Run the Evaluator before
    # changing it — that is what it is for.
    #
    # `quantized_kv_start` is why this is safe to default ON. Below that many
    # tokens the cache is untouched fp16, so ordinary short exchanges are
    # bit-identical to before; quantization begins only where the memory
    # actually matters. Set mlx_kv_bits to None to disable entirely.
    mlx_kv_bits: Optional[int] = 8
    mlx_kv_group_size: int = 64
    mlx_quantized_kv_start: int = 4096

    # ── Shared GPU budget (LB-1, 2026-09-29) ─────────────────────────
    # How much of the GPU's working set belongs to something OTHER than
    # LocalBook — an agent brain sharing the machine, chiefly. LocalBook
    # subtracts this before deciding what it may load, so the other process
    # is not competing for memory LocalBook has already committed.
    #
    # PER-MACHINE, NEVER SYNCED (LB-12h). The Mac mini needs 0; the MBP that
    # also runs a ~24-28 GB agent brain needs about 26. A synced value would
    # be wrong on at least one machine by construction.
    #
    # 0.0 means "LocalBook has the machine to itself", which is the honest
    # default and exactly what was assumed before this existed.
    # The alias keeps the LOCALBOOK_ prefix the rest of the app's env vars use,
    # while still being settable from the data-dir .env like every other
    # setting — a bare os.getenv here would be read at import and would ignore
    # that file entirely.
    external_reserve_gb: float = Field(
        0.0,
        validation_alias=AliasChoices(
            "LOCALBOOK_EXTERNAL_RESERVE_GB", "external_reserve_gb"
        ),
    )

    # ── Backups (LB-10) ──────────────────────────────────────────────
    # A folder OUTSIDE the data dir: iCloud Drive, an external disk, a NAS.
    # Empty means backups are OFF — there is no sensible default, and guessing
    # one would write the archive inside the thing it is backing up.
    # PER-MACHINE, never synced.
    backup_destination: str = Field(
        "",
        validation_alias=AliasChoices(
            "LOCALBOOK_BACKUP_DESTINATION", "backup_destination"
        ),
    )
    # Off for the nightly run: a real data dir is ~600 MB with generated audio
    # and ~80 MB without, and 7 daily + 4 weekly is the difference between
    # ~6 GB and ~900 MB. Audio is regenerable from its notebook. A manual
    # backup still includes them by default.
    backup_include_blobs_nightly: bool = False
    # How many archives to keep. Two by default: at ~550 MB each, the old
    # 7-daily + 4-weekly scheme was ~6 GB for a slowly-growing corpus.
    # ⚠️ This is also the "how long until you notice" window — with two archives
    # on a daily cadence, a corruption unnoticed for three days is in both.
    backup_keep: int = 2

    # ── Encryption at rest (LB-11) ───────────────────────────────────
    # A sparsebundle's size is a CEILING, not an allocation: bands on disk only
    # ever total what the data needs. Generous so it never has to be resized,
    # which is an operation with its own failure modes.
    volume_max_size_gb: int = 512
    # Per-machine, NEVER synced (D11). The plan rolls encryption out one Mac at
    # a time; a synced flag would switch it on for a machine with no volume and
    # lock that machine out of its own data.
    encryption_enabled: bool = Field(
        False,
        validation_alias=AliasChoices(
            "LOCALBOOK_ENCRYPTION_ENABLED", "encryption_enabled"
        ),
    )

    # MLX's internal buffer cache. Unbounded it will happily hold on to every
    # buffer it has ever allocated, which reads as LocalBook hoarding memory
    # the moment anything else on the machine wants some.
    mlx_cache_limit_gb: float = 2.0

    # ── Linked Folders (2026-09-16) ──────────────────────────────────
    # How often the watcher loop wakes. This is NOT the scan cadence: each
    # link carries its own frequency (hourly … weekly) and the loop only acts
    # on links that are due. Live-editable via schedule_store("folder-watch").
    folder_watch_interval_seconds: int = 300
    # Files above this are skipped with a visible reason rather than silently.
    # A transcript is small; a 200 MB stray file in a watched folder is not
    # something to embed by accident.
    folder_link_max_file_mb: int = 25
    # Ceiling on files ingested per link per pass. A first scan of a large
    # folder therefore drains over several passes instead of monopolising the
    # machine — and the UI reports what is still pending rather than implying
    # the folder is done.
    folder_link_batch_limit: int = 25

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


def encryption_flag_path(data_dir: Path) -> Path:
    """Where `encryption_enabled` is persisted: BESIDE the data dir, never inside.

    It used to be written to `<data dir>/.env` — which, once encrypted, is inside
    the volume. A failed mount then hid the very flag that says a mount is
    required, the gate read "encryption off", and the app came up empty and
    started writing into the mount point: the exact failure measure 1 exists to
    prevent. The flag has to survive the volume being unavailable.
    """
    data_dir = Path(data_dir)
    return data_dir.parent / f"{data_dir.name}.encryption-enabled"


if encryption_flag_path(settings.data_dir).exists():
    settings.encryption_enabled = True

# ── LB-11 measure 1: do NOT create the data directory at import ─────────────
# When encryption is on, `data_dir` is a MOUNT POINT. Creating it here — which
# this did unconditionally — is precisely what made a failed mount
# indistinguishable from a new install: an empty directory appears, nothing above
# can tell why, and LocalBook comes up blank and starts writing a fresh corpus
# over the top of encrypted data it simply could not open.
#
# With encryption off, nothing has changed. With it on, the directories are
# created AFTER a successful mount, by `volume_gate.ensure_subdirs()`.
if not bool(getattr(settings, "encryption_enabled", False)):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.mkdir(parents=True, exist_ok=True)
