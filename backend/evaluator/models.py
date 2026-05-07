"""Data models for the LLM Evaluator framework.

Defines all typed structures used across the evaluator:
- HardwareProfile: Machine specs + derived tier
- ModelInfo: Registry entry with vendor/origin metadata
- ModelCombo: Named assignment of models to role slots
- EvalResult: Per-test result envelope
- CategoryResult: Aggregated category scores
- ComboEvalSummary: Full run summary with grades
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid


# ─── Hardware ────────────────────────────────────────────────────────────────

@dataclass
class HardwareProfile:
    """Machine hardware fingerprint captured at eval start."""
    chip: str = ""                       # "Apple M4"
    total_cores: int = 0                 # 10
    performance_cores: int = 0           # 4
    efficiency_cores: int = 0            # 6
    gpu_cores: int = 0                   # Metal GPU core count
    memory_gb: int = 0                   # 16, 32, 64, ...
    metal_support: bool = True
    os_version: str = ""                 # "15.2"
    ollama_version: str = ""             # "0.6.2"
    tier: str = ""                       # "entry" | "mid" | "high" | "ultra"

    @property
    def fingerprint(self) -> str:
        """Short identifier for comparison: e.g. 'm4-16gb-entry'."""
        chip_short = self.chip.lower().replace("apple ", "").replace(" ", "")
        return f"{chip_short}-{self.memory_gb}gb-{self.tier}"

    def derive_tier(self):
        """Set tier based on RAM."""
        if self.memory_gb <= 16:
            self.tier = "entry"
        elif self.memory_gb <= 48:
            self.tier = "mid"
        elif self.memory_gb <= 96:
            self.tier = "high"
        else:
            self.tier = "ultra"

    def to_dict(self) -> dict:
        return {
            "chip": self.chip,
            "total_cores": self.total_cores,
            "performance_cores": self.performance_cores,
            "efficiency_cores": self.efficiency_cores,
            "gpu_cores": self.gpu_cores,
            "memory_gb": self.memory_gb,
            "metal_support": self.metal_support,
            "os_version": self.os_version,
            "ollama_version": self.ollama_version,
            "tier": self.tier,
            "fingerprint": self.fingerprint,
        }


# ─── Model Registry ─────────────────────────────────────────────────────────

@dataclass
class ModelInfo:
    """Registry entry for a known Ollama model."""
    ollama_name: str = ""
    display_name: str = ""
    family: str = ""                     # "olmo", "llama", "phi", "qwen"
    parameter_count: str = ""            # "7B", "3B", "14B"

    # Vendor / Origin
    vendor: str = ""                     # "Allen AI", "Meta", "Microsoft"
    origin_country: str = ""             # "US", "CN", "FR"
    license: str = ""                    # "Apache-2.0", "Llama", "MIT"

    # Capabilities
    supported_roles: list = field(default_factory=list)
    context_window: int = 4096
    supports_json_mode: bool = False
    supports_vision: bool = False
    vision_api_style: str = "generate"    # "generate" (LLaVA/Granite) or "chat" (Gemma4/Llama3.2)
    embedding_dim: int = 0               # Non-zero for embedding models

    # Resource Requirements
    disk_size_gb: float = 0.0
    min_ram_gb: int = 0
    recommended_ram_gb: int = 0

    # Status
    is_installed: bool = False

    # Policy Tags
    policy_tags: list = field(default_factory=list)

    # Per-model Ollama generation options (temperature, top_p, top_k, etc.)
    ollama_options: dict = field(default_factory=dict)

    # RAG-specific tuning profile — overrides global defaults in rag_llm.py.
    # Supported keys: think, repeat_penalty, use_chat_endpoint, num_ctx_cap, temperature.
    # Empty dict (default) means: use global defaults unchanged.
    rag_profile: dict = field(default_factory=dict)

    # Vision-specific tuning profile — overrides global defaults in
    # ollama_client.vision_describe(). Supported keys: num_predict, num_ctx, temperature.
    # Empty dict means: use global vision defaults (1500 / 8192 / 0.3).
    vision_profile: dict = field(default_factory=dict)

    # v1.7.0: Backend provider — "ollama" (default) or "llama_server" (sidecar).
    # See services/llm_provider.py. Registry entries without this field are
    # treated as Ollama-hosted for backward compatibility.
    provider: str = "ollama"

    def to_dict(self) -> dict:
        return {
            "ollama_name": self.ollama_name,
            "display_name": self.display_name,
            "family": self.family,
            "parameter_count": self.parameter_count,
            "vendor": self.vendor,
            "origin_country": self.origin_country,
            "license": self.license,
            "supported_roles": self.supported_roles,
            "context_window": self.context_window,
            "supports_vision": self.supports_vision,
            "embedding_dim": self.embedding_dim,
            "disk_size_gb": self.disk_size_gb,
            "min_ram_gb": self.min_ram_gb,
            "policy_tags": self.policy_tags,
            "is_installed": self.is_installed,
            "ollama_options": self.ollama_options,
            "provider": self.provider,
        }


# ─── Model Combo ─────────────────────────────────────────────────────────────

@dataclass
class ModelCombo:
    """Named assignment of models to role slots."""
    name: str = "Default"
    main_model: str = ""
    fast_model: str = ""
    embedding_model: str = ""
    embedding_dim: int = 0
    vision_model: str = ""
    tts_engine: str = "kokoro-mlx"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "main_model": self.main_model,
            "fast_model": self.fast_model,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
            "vision_model": self.vision_model,
            "tts_engine": self.tts_engine,
        }

    @classmethod
    def from_config(cls, settings) -> "ModelCombo":
        """Build from current app config.py settings."""
        main = getattr(settings, "ollama_model", "unknown")
        fast = getattr(settings, "ollama_fast_model", main)
        # Build a human-readable combo name from the actual models
        main_short = main.split(":")[0] if ":" in main else main
        fast_short = fast.split(":")[0] if ":" in fast else fast
        combo_name = f"{main_short} + {fast_short}"
        return cls(
            name=combo_name,
            main_model=main,
            fast_model=fast,
            embedding_model=getattr(settings, "embedding_model", ""),
            embedding_dim=getattr(settings, "embedding_dim", 0),
            vision_model=getattr(settings, "vision_model", ""),
            tts_engine="kokoro-mlx",
        )


# ─── Evaluation Results ─────────────────────────────────────────────────────

@dataclass
class EvalResult:
    """Result for a single test within a category."""
    test_id: str = ""
    category: str = ""
    test_name: str = ""
    model_combo: str = ""                # Combo name
    model_used: str = ""                 # Specific model that ran this test
    hardware_fingerprint: str = ""
    timestamp: str = ""

    # Timing
    total_time_ms: float = 0.0
    time_to_first_token_ms: float = 0.0  # Streaming only
    tokens_per_second: float = 0.0

    # Token Economy
    prompt_tokens: int = 0
    completion_tokens: int = 0
    eval_duration_ns: int = 0

    # Quality Scores (0–100)
    accuracy_score: int = 0
    completeness_score: int = 0
    format_score: int = 0
    overall_score: int = 0
    sub_scores: dict = field(default_factory=dict)

    # Metadata
    input_chars: int = 0
    output_chars: int = 0
    expected_output_summary: str = ""
    actual_output_preview: str = ""
    passed: bool = False
    failure_reason: str = ""
    skipped: bool = False
    skip_reason: str = ""

    # v1.8.2: Provider / backend visibility — stamped by every test runner so
    # results show exactly which backend served each test and whether Bonsai
    # or an Ollama model was running.
    provider: str = ""                   # "ollama" | "llama_server" | ""
    backend_url: str = ""                # e.g. "http://127.0.0.1:8090"
    model_context_window: int = 0        # capability-aware, helps explain truncation

    def stamp_provider(self, model_name: str) -> None:
        """Populate provider/backend_url/context_window from the resolver.

        Called by test runners right after they pick the model so the
        persisted EvalResult carries backend provenance without each runner
        duplicating the routing logic.
        """
        try:
            from evaluator.capabilities import capabilities_for
            caps = capabilities_for(model_name)
            self.model_used = model_name
            self.provider = caps.provider
            self.backend_url = caps.backend_url
            self.model_context_window = caps.context_window
        except Exception:
            # Never let telemetry break a run
            self.model_used = model_name or self.model_used

    def mark_skipped(self, reason: str) -> None:
        """Mark this test as skipped with a human-readable reason.

        Reserved for cases where the capability is *literally not configured*
        for the current combo (e.g. no embedding_model set, no vision_model
        set). A capability that's configured but limited (e.g. small context
        window) should be reported via `mark_degraded()` instead so the
        evaluator still exercises the real code path.
        """
        self.skipped = True
        self.skip_reason = reason
        self.passed = True   # not a failure — feature simply not in this combo
        self.overall_score = 0
        self.failure_reason = ""

    def mark_degraded(self, note: str) -> None:
        """Flag that the test ran but the inputs were adapted to the model's
        physical limits (e.g. prompt trimmed to fit context window).

        The test still scores honestly; this note surfaces in the UI so the
        user sees that Bonsai's 4K context forced a trimmed needle haystack,
        rather than silently assuming 8K worked.
        """
        # Record under sub_scores to keep the flat EvalResult shape stable
        self.sub_scores = dict(self.sub_scores) if self.sub_scores else {}
        self.sub_scores["degraded"] = True
        notes = self.sub_scores.get("degraded_notes", [])
        if not isinstance(notes, list):
            notes = [str(notes)]
        notes.append(note)
        self.sub_scores["degraded_notes"] = notes

    def to_dict(self) -> dict:
        return {
            "test_id": self.test_id,
            "category": self.category,
            "test_name": self.test_name,
            "model_combo": self.model_combo,
            "model_used": self.model_used,
            "hardware_fingerprint": self.hardware_fingerprint,
            "timestamp": self.timestamp,
            "total_time_ms": self.total_time_ms,
            "time_to_first_token_ms": self.time_to_first_token_ms,
            "tokens_per_second": self.tokens_per_second,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "accuracy_score": self.accuracy_score,
            "completeness_score": self.completeness_score,
            "format_score": self.format_score,
            "overall_score": self.overall_score,
            "sub_scores": self.sub_scores,
            "input_chars": self.input_chars,
            "output_chars": self.output_chars,
            "actual_output_preview": self.actual_output_preview[:500],
            "passed": self.passed,
            "failure_reason": self.failure_reason,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            # v1.8.2
            "provider": self.provider,
            "backend_url": self.backend_url,
            "model_context_window": self.model_context_window,
        }


@dataclass
class CategoryResult:
    """Aggregated result for one of the 10 functional categories."""
    category: str = ""
    display_name: str = ""
    tests: list = field(default_factory=list)    # list[EvalResult]
    score: float = 0.0                           # 0–100
    grade: str = ""
    passed: bool = False
    warnings: list = field(default_factory=list)
    total_time_ms: float = 0.0
    # v1.8.2: a category is "skipped" when every test in it was skipped.
    # Skipped categories are excluded from the overall weighted score so a
    # text-only model isn't penalised for lacking vision.
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "display_name": self.display_name,
            "tests": [t.to_dict() for t in self.tests],
            "score": round(self.score, 1),
            "grade": self.grade,
            "passed": self.passed,
            "warnings": self.warnings,
            "total_time_ms": round(self.total_time_ms, 1),
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


@dataclass
class IngestionResult:
    """Result for the content ingestion phase."""
    sources_attempted: int = 0
    sources_completed: int = 0
    sources_failed: int = 0
    total_chunks: int = 0
    total_characters: int = 0
    ingestion_time_ms: float = 0.0
    per_source: list = field(default_factory=list)  # list[dict]
    score: float = 0.0
    grade: str = ""

    def to_dict(self) -> dict:
        return {
            "sources_attempted": self.sources_attempted,
            "sources_completed": self.sources_completed,
            "sources_failed": self.sources_failed,
            "total_chunks": self.total_chunks,
            "total_characters": self.total_characters,
            "ingestion_time_ms": round(self.ingestion_time_ms, 1),
            "per_source": self.per_source,
            "score": round(self.score, 1),
            "grade": self.grade,
        }


def _score_to_grade(score: float) -> str:
    """Convert 0-100 score to letter grade."""
    if score >= 97:
        return "A+"
    elif score >= 93:
        return "A"
    elif score >= 90:
        return "A-"
    elif score >= 87:
        return "B+"
    elif score >= 83:
        return "B"
    elif score >= 80:
        return "B-"
    elif score >= 77:
        return "C+"
    elif score >= 73:
        return "C"
    elif score >= 70:
        return "C-"
    elif score >= 60:
        return "D"
    else:
        return "F"


@dataclass
class ComboEvalSummary:
    """Complete evaluation summary for one combo on one machine."""
    run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    combo: dict = field(default_factory=dict)       # ModelCombo.to_dict()
    hardware: dict = field(default_factory=dict)     # HardwareProfile.to_dict()
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    # Ingestion
    ingestion: dict = field(default_factory=dict)    # IngestionResult.to_dict()

    # Category results
    categories: dict = field(default_factory=dict)   # {name: CategoryResult.to_dict()}
    category_scores: dict = field(default_factory=dict)  # {name: score}

    # Overall
    overall_score: float = 0.0
    overall_grade: str = ""

    # Performance profile
    avg_tokens_per_sec: float = 0.0
    avg_ttft_ms: float = 0.0
    total_run_time_seconds: float = 0.0

    # Verdict
    warnings: list = field(default_factory=list)

    # v1.8.2: Provider provenance — records which backends served which roles
    # and which categories were skipped for capability reasons, so the UI can
    # show "Ran on Ollama + llama-server (Bonsai-8B)" at a glance.
    providers_used: dict = field(default_factory=dict)   # {role: {provider, backend_url, model}}
    skipped_categories: list = field(default_factory=list)  # [{category, reason}]
    # v1.8.3: Production readiness — the "will this combo actually work in
    # the app?" verdict, compressed from raw scores into pass/degraded/fail
    # per user-facing feature plus a single-headline rollup.
    feature_parity: list = field(default_factory=list)   # [{category, feature, verdict, ...}]
    production_readiness: dict = field(default_factory=dict)  # {counts, headline}
    preflight: dict = field(default_factory=dict)         # PreflightReport.to_dict()

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "combo": self.combo,
            "hardware": self.hardware,
            "timestamp": self.timestamp,
            "ingestion": self.ingestion,
            "categories": self.categories,
            "category_scores": self.category_scores,
            "overall_score": round(self.overall_score, 1),
            "overall_grade": self.overall_grade,
            "avg_tokens_per_sec": round(self.avg_tokens_per_sec, 1),
            "avg_ttft_ms": round(self.avg_ttft_ms, 1),
            "total_run_time_seconds": round(self.total_run_time_seconds, 1),
            "warnings": self.warnings,
            "providers_used": self.providers_used,
            "skipped_categories": self.skipped_categories,
            # v1.8.3
            "feature_parity": self.feature_parity,
            "production_readiness": self.production_readiness,
            "preflight": self.preflight,
        }


# ─── Progress Tracking ──────────────────────────────────────────────────────

EVAL_PHASES = [
    (0, "Hardware Profile"),
    (1, "Create Test Notebook"),
    (2, "Ingest Content"),
    (3, "Wait for Ingestion"),
    (4, "RAG Chat Q&A"),
    (5, "Streaming Generation"),
    (6, "Fast Follow-Up"),
    (7, "Document Generation"),
    (8, "Structured JSON (Quiz)"),
    (9, "Intent Classification"),
    (10, "Embedding Quality"),
    (11, "Vision / Image"),
    (12, "TTS Audio"),
    (13, "Instruction Following"),
    (14, "Concurrency & Load"),
    (15, "Context Capacity (Needle)"),
    (16, "Prompt Safety (Adversarial)"),
    (17, "Score & Persist"),
    (18, "Cleanup"),
]

TOTAL_PHASES = len(EVAL_PHASES)


@dataclass
class EvalProgress:
    """Live progress tracking for an active evaluation run."""
    running: bool = False
    phase: int = 0
    phase_name: str = ""
    total_phases: int = TOTAL_PHASES
    progress_percent: int = 0
    current_test: str = ""
    elapsed_seconds: float = 0.0
    run_start_time: float = 0.0  # time.time() when run started — for live elapsed computation
    results_so_far: dict = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict:
        import time as _time
        # Compute elapsed dynamically while running
        elapsed = self.elapsed_seconds
        if self.running and self.run_start_time > 0:
            elapsed = _time.time() - self.run_start_time
        return {
            "running": self.running,
            "phase": self.phase,
            "phase_name": self.phase_name,
            "total_phases": self.total_phases,
            "progress_percent": self.progress_percent,
            "current_test": self.current_test,
            "elapsed_seconds": round(elapsed, 1),
            "results_so_far": self.results_so_far,
            "error": self.error,
        }
