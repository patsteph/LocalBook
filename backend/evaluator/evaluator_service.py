"""Evaluator Service — Core orchestrator for end-to-end LLM evaluation.

Creates test notebook → ingests content → runs 19 test categories →
scores everything → persists results → cleans up.

Every category that runs is WEIGHTED (eval_config.json) and MAPPED to a user-facing feature
(feature_parity). Six once were neither, so they burned runtime and moved nothing;
`tests/test_no_undefined_names.py`'s sibling `test_every_runner_category_is_weighted_and_mapped`
now fails if a new runner is added without both.
"""

import json
import time
import asyncio
from datetime import datetime
from pathlib import Path

from evaluator.models import (
    EvalResult, CategoryResult, ComboEvalSummary, EvalProgress,
    ModelCombo, EVAL_PHASES, TOTAL_PHASES, _score_to_grade,
)
from evaluator.hardware_profiler import get_hardware_profile
from evaluator.test_runners import (
    ingestion,
    rag_chat,
    streaming,
    fast_followup,
    document_gen,
    structured_json,
    intent_classify,
    embedding_quality,
    vision,
    tts_audio,
    instruction_follow,
    concurrency,
    needle_haystack,
    prompt_safety,
    voice_modifier,
    capture_modes,
    refinement,
    translation,
    field_edges,
    retrieval,
    image_gen,
    entity_extract,
)
from evaluator import scoring

# Config path
_CONFIG_PATH = Path(__file__).parent / "test_fixtures" / "eval_config.json"

# Results storage
_RESULTS_DIR: Path | None = None

# Singleton progress tracker
_progress = EvalProgress()


def _get_results_dir() -> Path:
    """Get the results directory (under app data, not repo)."""
    global _RESULTS_DIR
    if _RESULTS_DIR is None:
        from config import settings
        _RESULTS_DIR = Path(settings.data_dir) / "eval_results"
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (_RESULTS_DIR / "runs").mkdir(exist_ok=True)
    return _RESULTS_DIR


def get_progress() -> EvalProgress:
    """Get current evaluation progress."""
    return _progress


def _update_progress(phase: int, test_name: str = "", **kwargs):
    """Update progress tracker."""
    _progress.phase = phase
    _progress.phase_name = EVAL_PHASES[phase][1] if phase < len(EVAL_PHASES) else "Done"
    _progress.progress_percent = int((phase / TOTAL_PHASES) * 100)
    _progress.current_test = test_name
    if "elapsed" in kwargs:
        _progress.elapsed_seconds = kwargs["elapsed"]
    if "results" in kwargs:
        _progress.results_so_far = kwargs["results"]


def _load_config() -> dict:
    """Load the evaluation configuration."""
    return json.loads(_CONFIG_PATH.read_text())


_PHASE_TIMEOUT_SECONDS = 180  # 3 min max per test phase — prevents indefinite hangs


# ─── Tiers ──────────────────────────────────────────────────────────────────
#
# The full suite takes 15-30 minutes on a 16 GB box and spends a third of it downloading a
# YouTube transcript and a Wikipedia page — neither of which measures the model. A harness that
# expensive does not get run, and a harness that does not get run is not a safety net. It was
# twice mistaken for hung during a release on 2026-09-14.
#
# SMOKE answers one question: can this model do the job AT ALL? It keeps the categories that
# would make a model unusable if they failed, and drops everything that is slow, network-bound,
# or a refinement of something already covered.
#
# ⚠️ A smoke score is computed over FEWER categories, so it is NOT comparable to a full score.
# The tier is persisted on the run and the regression gate refuses to compare across tiers —
# without that, one `--tier smoke` run would poison the baseline and the next full run would
# look like a catastrophic regression.
SMOKE_CATEGORIES = frozenset({
    "ingestion",          # local files only in smoke (see _tier_config)
    "retrieval",          # fast, judge-free, and the core of every answer
    "rag_chat",           # the product loop itself
    "structured_json",    # JSON capability — half the app's features need it
    "instruction_follow", # does it do what it is told
    "embedding_quality",  # cheap, and everything retrieval-shaped depends on it
    "entity_extract",     # fast, judge-free, feeds the graph AND retrieval
})

# Sources that cost network time rather than telling us anything about the model.
_NETWORK_SOURCES = ("youtube", "web")


def _in_tier(category: str, tier: str) -> bool:
    return tier != "smoke" or category in SMOKE_CATEGORIES


def _tier_skip_reason(category: str, tier: str) -> str:
    """Why a category produced nothing — deliberately excluded, or genuinely absent.

    `_build_category` assumed that no results meant the phase TIMED OUT, which was true until
    tiers existed. The first smoke run then reported fourteen categories as
    "phase timed out — no data recorded", which is alarming, wrong, and exactly the kind of
    misleading report this whole overhaul has been about. A skipped category should say it was
    skipped.
    """
    if not _in_tier(category, tier):
        return f"not part of the {tier} tier — run the full tier to measure it"
    return ""


def _tier_gate(category: str, tier: str, coro):
    """Return `coro` when the category is in this tier, else discard it and return no results.

    Creating a coroutine does not run it, so gating here costs nothing — but an un-awaited
    coroutine warns, hence the explicit close(). An empty result list flows into
    `_build_category`, which already treats "no results" as not-applicable rather than zero.
    """
    if _in_tier(category, tier):
        return coro
    coro.close()

    async def _noop():
        return []

    return _noop()


def _tier_config(config: dict, tier: str) -> dict:
    """Smoke drops the network-fetched sources: on 2026-09-14 YouTube took 94s and the web
    scrape 90s of a 352s ingestion, and neither says anything about the model."""
    if tier != "smoke":
        return config
    trimmed = dict(config)
    sources = {k: v for k, v in (config.get("content_sources") or {}).items()
               if k not in _NETWORK_SOURCES}
    trimmed["content_sources"] = sources
    return trimmed



def _count_engine_fallbacks() -> int:
    """How many engine-fallback signals exist right now (a monotonic watermark)."""
    try:
        from services.quality_signals import quality_signals
        return sum(1 for s in quality_signals.get_recent(days=1)
                   if s.get("type") == "fallback" and s.get("component") == "llm_service")
    except Exception:
        return 0


def _engine_fallbacks_since(watermark: int) -> tuple:
    """(count, detail) of engine fallbacks recorded since the watermark."""
    try:
        from services.quality_signals import quality_signals
        rows = [s for s in quality_signals.get_recent(days=1)
                if s.get("type") == "fallback" and s.get("component") == "llm_service"]
        fresh = rows[watermark:] if len(rows) > watermark else []
        return len(fresh), [{"detail": r.get("detail", ""), "key": r.get("key", ""),
                             "ts": r.get("ts", "")} for r in fresh[:20]]
    except Exception:
        return 0, []


# Phases that timed out during THIS run. A timeout is "no data", not "scored zero" — see
# _build_category. Keyed by the display name passed to _run_phase_with_timeout.
_TIMED_OUT: set = set()


async def _run_phase_with_timeout(coro, phase_name: str, timeout: int = _PHASE_TIMEOUT_SECONDS):
    """Run a test phase with a hard timeout. Returns results, or [] and records the timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        print(f"[EVALUATOR] ⚠️ Phase '{phase_name}' timed out after {timeout}s — recorded as "
              f"NO DATA (not a zero score)")
        _TIMED_OUT.add(phase_name)
        return []


def _check_available_memory() -> tuple[bool, str]:
    """Pre-flight check: is there enough free RAM to safely run the evaluator?"""
    try:
        import psutil
        mem = psutil.virtual_memory()
        available_gb = mem.available / (1024 ** 3)
        total_gb = mem.total / (1024 ** 3)
        if available_gb < 1.0:
            return False, (
                f"Insufficient available memory: {available_gb:.1f}GB free of {total_gb:.0f}GB total. "
                f"The evaluator needs at least 1GB free RAM to run safely. "
                f"Close other applications or wait for current Ollama operations to finish."
            )
        return True, f"{available_gb:.1f}GB available"
    except ImportError:
        # psutil not installed — skip check
        return True, "psutil not available, skipping memory check"


async def run_full_evaluation(tier: str = "full") -> ComboEvalSummary:
    """Public entry point. Wraps the real run in an EXCLUSIVE guard — see `_run_evaluation`."""
    # An evaluation is a measurement, and a measurement shares nothing. Background work —
    # enrichment, digests, the correspondent poller, idle research — competes for exactly the
    # resource being measured, and on the 16 GB dev box that competition IS the result: the
    # 2026-09-14 runs warned of "sustained swap-out … timing numbers are not representative",
    # scored speed 28/100, and one generation took 23 minutes at ~2.8s/token.
    #
    # `foreground_guard` is the existing lever: the Enrichment Worker treats it as the ACTIVE
    # presence tier and CANCELS in-flight background work the instant it is raised, and
    # `await_background_clearance()` callers block until it drops. Depth-counted, so this
    # composes with any foreground op already running.
    #
    # ⚠️ This machine is the TIGHTEST of the three (16 GB M4; the others are ≥18 GB M-Pro).
    # Numbers measured here are a floor, not a representative figure — quitting other apps
    # before a run is the difference between measuring the model and measuring the swap.
    from services.memory_steward import foreground_guard

    async with foreground_guard("evaluator"):
        return await _run_evaluation(tier)


async def _run_evaluation(tier: str = "full") -> ComboEvalSummary:
    """Run the complete evaluation suite.
    
    This is the main entry point. It:
    1. Profiles hardware
    2. Creates a test notebook  
    3. Ingests all test content
    4. Runs all 19 test categories
    5. Scores, persists, and returns results
    6. Cleans up the test notebook
    """
    from config import settings

    tier = (tier or "full").lower()
    if tier not in ("full", "smoke"):
        tier = "full"

    # Sweep any leftover test notebook BEFORE starting. A run that is killed — which happened
    # twice during the v2.3.0 release, and is now a documented escape hatch (SKIP_EVAL, the
    # release timeout) — never reaches its own cleanup, so its notebook and LanceDB table stay
    # on disk forever. `api/evaluator.py` already did this for UI-triggered runs; the headless
    # entry point did not, so `python -m evaluator.run` accumulated one orphan per abandoned
    # run. Matches on the configured test-notebook NAME only, so it cannot touch real data.
    try:
        await cleanup_stale_notebook()
    except Exception as _ce:
        print(f"[EVALUATOR] stale-notebook sweep skipped (non-fatal): {_ce}")

    # Note: _progress.running and run_start_time are already set by the /run endpoint
    # to prevent race conditions with the frontend status polling.

    _progress.running = True
    _progress.error = ""
    _progress.results_so_far = {}
    run_start = time.time()
    notebook_id = None

    # Combo snapshot taken at the very top of the run. Every model name
    # the report cites comes from this dict, not from live settings.* —
    # so a Locker swap that lands mid-run is visible (we log a warning at
    # end) but doesn't silently corrupt the score by switching models
    # between phases. Tests still read settings.* for production code
    # paths (rag_engine etc. don't take a combo arg) so a mid-run swap
    # WILL affect later phases; the snapshot's job is to make the
    # corruption legible in the report rather than invisible.
    # Built from ModelCombo.from_config, which is ENGINE-AWARE and already resolves the model
    # that actually serves each role. Hand-building this from `settings.main_model` recorded
    # Ollama names even on an all-MLX run — so a persisted result was mislabelled and an
    # engine A/B would have silently compared Ollama against Ollama.
    # Watermark the engine-fallback log so we can count ONLY this run's fallbacks. A silent
    # MLX→Ollama fallback makes an "MLX run" partly an Ollama run — invisible, because the
    # answers still arrive, and fatal to any engine comparison built on the result.
    _fallback_watermark = _count_engine_fallbacks()

    # Memory sampling for the whole run, appending to disk as it goes — a run that dies from
    # memory pressure is exactly the one whose trace we must not lose.
    _mem_sampler = None

    from evaluator.models import ModelCombo as _MC
    _combo = _MC.from_config(settings)
    combo_snapshot = {
        "main_model": _combo.main_model,
        "fast_model": _combo.fast_model,
        "vision_model": _combo.vision_model,
        "embedding_model": _combo.embedding_model,
        # The engines are what make the snapshot falsifiable — without them a reader cannot
        # tell which runtime produced these numbers.
        "main_engine": _combo.main_engine,
        "fast_engine": _combo.fast_engine,
        "vision_engine": _combo.vision_engine,
        "embed_engine": _combo.embed_engine,
    }

    # Build C (2026-07-07): derive the tested model's RunProfile ONCE and make the
    # scorer normalize every output through it (strip <think>, extract JSON) BEFORE
    # scoring — so a thinking / differently-templated model is scored on its final
    # answer, not penalised for not behaving like olmo/gemma. One run = one model,
    # so a single active profile is correct. Cleared in the finally below.
    try:
        from evaluator.run_profile import derive_run_profile
        from evaluator import scoring as _scoring
        # provider must match the engine actually serving the main role, or an MLX run is
        # profiled with Ollama's template/stop assumptions.
        _rp = derive_run_profile(combo_snapshot["main_model"],
                                 provider=combo_snapshot.get("main_engine", "ollama"))
        _scoring.set_active_run_profile(_rp)
        print(f"[EVALUATOR] RunProfile: {combo_snapshot['main_model']} "
              f"engine={combo_snapshot.get('main_engine')} "
              f"thinking_capable={_rp.thinking_capable} stops={len(_rp.stop_sequences)} "
              f"filters={_rp.normalize_filters}")
    except Exception as _e:
        print(f"[EVALUATOR] RunProfile derivation skipped (non-fatal): {_e}")

    try:
        # ── Pre-flight (memory + all backends the combo uses) ──────────
        from evaluator.preflight import run_preflight, providers_used_summary
        preflight_report = await run_preflight(settings)
        print("[EVALUATOR] Pre-flight report:")
        for c in preflight_report.checks:
            print(f"  [{c.status.upper()}] {c.name}: {c.message}")
        if preflight_report.blocking_failure:
            raise RuntimeError(f"Pre-flight failed: {preflight_report.blocking_failure}")

        config = _tier_config(_load_config(), tier)
        combo = ModelCombo.from_config(settings)

        # ── Phase 0: Hardware Profile ────────────────────────────────────
        _update_progress(0, "Detecting hardware")
        hw = get_hardware_profile()
        print(f"[EVALUATOR] Hardware: {hw.chip}, {hw.memory_gb}GB RAM, tier={hw.tier}")

        summary = ComboEvalSummary(
            combo=combo.to_dict(),
            hardware=hw.to_dict(),
        )
        try:
            from services import throughput_meter as _tp
            _tp.start(f"{combo.main_engine}:{combo.main_model}")
        except Exception as _tp_e:
            print(f"[EVALUATOR] throughput meter unavailable (non-fatal): {_tp_e}")

        try:
            from evaluator.memory_sampler import MemorySampler, default_path
            _mem_sampler = MemorySampler(
                default_path(summary.run_id, "eval"), interval_s=1.0,
                label=f"{combo.main_engine}:{combo.main_model}",
            ).start()
        except Exception as _ms_e:
            print(f"[EVALUATOR] memory sampling unavailable (non-fatal): {_ms_e}")
        # v1.8.2: record which backend served which role so the summary
        # shows "Ran on Ollama + llama-server (Bonsai-8B)" at a glance.
        summary.providers_used = providers_used_summary(settings)
        print(f"[EVALUATOR] Providers in use: {summary.providers_used}")

        # ── Phase 1: Create Test Notebook ────────────────────────────────
        _update_progress(1, "Creating test notebook")
        notebook_id = await ingestion.create_test_notebook(config)

        # ── Phase 2-3: Ingest Content ────────────────────────────────────
        _update_progress(2, "Ingesting test content")
        ingest_result = await ingestion.ingest_all_content(notebook_id, config)
        summary.ingestion = ingest_result.to_dict()
        _update_progress(3, "Ingestion complete", results={
            "ingestion": {"score": ingest_result.score, "grade": ingest_result.grade}
        })

        if ingest_result.sources_completed == 0:
            raise RuntimeError("No sources ingested successfully — cannot run tests")

        # ── Test Phases 4-13 ─────────────────────────────────────────────
        category_results = {}
        _reset_perf()
        _TIMED_OUT.clear()

        # Phase 4: RAG Chat
        _update_progress(4, "RAG Chat Q&A")
        rag_results = await _run_phase_with_timeout(
            _tier_gate("rag_chat", tier, rag_chat.run(notebook_id, config, combo.name, hw.fingerprint)), "RAG Chat")
        cat = _build_category("rag_chat", "RAG Chat Q&A", rag_results,
                              _tier_skip_reason("rag_chat", tier))
        category_results["rag_chat"] = cat
        _progress.results_so_far["rag_chat"] = {"score": cat.score, "grade": cat.grade}

        # Phase 5: Streaming
        _update_progress(5, "Streaming Generation")
        stream_results = await _run_phase_with_timeout(
            _tier_gate("streaming", tier, streaming.run(notebook_id, config, combo.name, hw.fingerprint)), "Streaming")
        cat = _build_category("streaming", "Streaming Generation", stream_results,
                              _tier_skip_reason("streaming", tier))
        category_results["streaming"] = cat
        _progress.results_so_far["streaming"] = {"score": cat.score, "grade": cat.grade}

        # Phase 6: Fast Follow-Up
        _update_progress(6, "Fast Follow-Up")
        followup_results = await _run_phase_with_timeout(
            _tier_gate("fast_followup", tier, fast_followup.run(notebook_id, config, combo.name, hw.fingerprint)), "Fast Follow-Up")
        cat = _build_category("fast_followup", "Fast Follow-Up", followup_results,
                              _tier_skip_reason("fast_followup", tier))
        category_results["fast_followup"] = cat
        _progress.results_so_far["fast_followup"] = {"score": cat.score, "grade": cat.grade}

        # Phase 7: Document Generation
        _update_progress(7, "Document Generation")
        docgen_results = await _run_phase_with_timeout(
            _tier_gate("document_gen", tier, document_gen.run(notebook_id, config, combo.name, hw.fingerprint)), "Document Gen")
        cat = _build_category("document_gen", "Document Generation", docgen_results,
                              _tier_skip_reason("document_gen", tier))
        category_results["document_gen"] = cat
        _progress.results_so_far["document_gen"] = {"score": cat.score, "grade": cat.grade}

        # Phase 8: Structured JSON (Quiz)
        _update_progress(8, "Structured JSON (Quiz)")
        json_results = await _run_phase_with_timeout(
            _tier_gate("structured_json", tier, structured_json.run(notebook_id, config, combo.name, hw.fingerprint)),
            # Quiz generation is several grammar-constrained generations in sequence; a slower
            # main model (Ornith 9B, 2026-09-15) pushes it past 180s. The wall-clock guard in
            # mlx_engine now bounds each generation, so a longer phase budget cannot hide a
            # runaway one.
            "Structured JSON", timeout=420)
        cat = _build_category("structured_json", "Structured JSON", json_results,
                              _tier_skip_reason("structured_json", tier))
        category_results["structured_json"] = cat
        _progress.results_so_far["structured_json"] = {"score": cat.score, "grade": cat.grade}

        # Phase 9: Intent Classification
        _update_progress(9, "Intent Classification")
        intent_results = await _run_phase_with_timeout(
            _tier_gate("intent_classify", tier, intent_classify.run(notebook_id, config, combo.name, hw.fingerprint)), "Intent Classify")
        cat = _build_category("intent_classify", "Intent Classification", intent_results,
                              _tier_skip_reason("intent_classify", tier))
        category_results["intent_classify"] = cat
        _progress.results_so_far["intent_classify"] = {"score": cat.score, "grade": cat.grade}

        # Phase 10: Embedding Quality
        _update_progress(10, "Embedding Quality")
        embed_results = await _run_phase_with_timeout(
            _tier_gate("embedding_quality", tier, embedding_quality.run(notebook_id, config, combo.name, hw.fingerprint)), "Embedding Quality")
        cat = _build_category("embedding_quality", "Embedding Quality", embed_results,
                              _tier_skip_reason("embedding_quality", tier))
        category_results["embedding_quality"] = cat
        _progress.results_so_far["embedding_quality"] = {"score": cat.score, "grade": cat.grade}

        # Phase 11: Vision
        _update_progress(11, "Vision / Image")
        vision_results = await _run_phase_with_timeout(
            _tier_gate("vision", tier, vision.run(notebook_id, config, combo.name, hw.fingerprint)), "Vision")
        cat = _build_category("vision", "Vision / Image", vision_results,
                              _tier_skip_reason("vision", tier))
        category_results["vision"] = cat
        _progress.results_so_far["vision"] = {"score": cat.score, "grade": cat.grade}

        # Phase 12: TTS Audio
        _update_progress(12, "TTS Audio")
        tts_results = await _run_phase_with_timeout(
            _tier_gate("tts_audio", tier, tts_audio.run(notebook_id, config, combo.name, hw.fingerprint)),
            # Generating a podcast — script THEN synthesis — does not fit in 180s on any
            # machine, so the default timeout was measuring the timeout rather than the model.
            # It timed out on an idle 48 GB box on 2026-09-15 and was excluded from the score,
            # which silently removed a whole capability from the result.
            "TTS Audio", timeout=600)
        cat = _build_category("tts_audio", "TTS Audio", tts_results,
                              _tier_skip_reason("tts_audio", tier))
        category_results["tts_audio"] = cat
        _progress.results_so_far["tts_audio"] = {"score": cat.score, "grade": cat.grade}

        # Phase 13: Instruction Following
        _update_progress(13, "Instruction Following")
        instruct_results = await _run_phase_with_timeout(
            _tier_gate("instruction_follow", tier, instruction_follow.run(notebook_id, config, combo.name, hw.fingerprint)), "Instruction Follow")
        cat = _build_category("instruction_follow", "Instruction Following", instruct_results,
                              _tier_skip_reason("instruction_follow", tier))
        category_results["instruction_follow"] = cat
        _progress.results_so_far["instruction_follow"] = {"score": cat.score, "grade": cat.grade}

        # Phase 14: Concurrency & Load
        _update_progress(14, "Concurrency & Load")
        concurrency_results = await _run_phase_with_timeout(
            _tier_gate("concurrency", tier, concurrency.run(notebook_id, config, combo.name, hw.fingerprint)), "Concurrency")
        cat = _build_category("concurrency", "Concurrency & Load", concurrency_results,
                              _tier_skip_reason("concurrency", tier))
        category_results["concurrency"] = cat
        _progress.results_so_far["concurrency"] = {"score": cat.score, "grade": cat.grade}

        # Phase 15: Context Capacity (Needle)
        _update_progress(15, "Context Capacity (Needle)")
        # Needle now stresses the model's DEPLOYED window (up to ~75% of a large ctx),
        # so prompt-eval of tens of thousands of tokens can exceed the default 180s.
        # Give this deliberate stress test a longer ceiling so it completes + scores.
        needle_results = await _run_phase_with_timeout(
            _tier_gate("needle_haystack", tier, needle_haystack.run(notebook_id, config, combo.name, hw.fingerprint)), "Needle Haystack", timeout=420)
        cat = _build_category("needle_haystack", "Context Capacity", needle_results,
                              _tier_skip_reason("needle_haystack", tier))
        category_results["needle_haystack"] = cat
        _progress.results_so_far["needle_haystack"] = {"score": cat.score, "grade": cat.grade}

        # Phase 16: Prompt Safety (Adversarial)
        _update_progress(16, "Prompt Safety (Adversarial)")
        safety_results = await _run_phase_with_timeout(
            _tier_gate("prompt_safety", tier, prompt_safety.run(notebook_id, config, combo.name, hw.fingerprint)), "Prompt Safety")
        cat = _build_category("prompt_safety", "Prompt Safety", safety_results,
                              _tier_skip_reason("prompt_safety", tier))
        category_results["prompt_safety"] = cat
        _progress.results_so_far["prompt_safety"] = {"score": cat.score, "grade": cat.grade}

        # Phase 17: Voice Modifier (apples-to-apples voice consistency)
        _update_progress(17, "Voice Modifier")
        voice_results = await _run_phase_with_timeout(
            _tier_gate("voice_modifier", tier, voice_modifier.run(notebook_id, config, combo.name, hw.fingerprint)), "Voice Modifier")
        cat = _build_category("voice_modifier", "Voice Modifier", voice_results,
                              _tier_skip_reason("voice_modifier", tier))
        category_results["voice_modifier"] = cat
        _progress.results_so_far["voice_modifier"] = {"score": cat.score, "grade": cat.grade}

        # Phase 18: Capture Modes — multi-mode vision coverage
        _update_progress(18, "Capture Modes")
        modes_results = await _run_phase_with_timeout(
            _tier_gate("capture_modes", tier, capture_modes.run(notebook_id, config, combo.name, hw.fingerprint)), "Capture Modes")
        cat = _build_category("capture_modes", "Capture Modes", modes_results,
                              _tier_skip_reason("capture_modes", tier))
        category_results["capture_modes"] = cat
        _progress.results_so_far["capture_modes"] = {"score": cat.score, "grade": cat.grade}

        # Phase 19: Refinement Pass Fidelity
        _update_progress(19, "Refinement Pass")
        refine_results = await _run_phase_with_timeout(
            _tier_gate("refinement", tier, refinement.run(notebook_id, config, combo.name, hw.fingerprint)), "Refinement")
        cat = _build_category("refinement", "Refinement Pass", refine_results,
                              _tier_skip_reason("refinement", tier))
        category_results["refinement"] = cat
        _progress.results_so_far["refinement"] = {"score": cat.score, "grade": cat.grade}

        # Phase 20: Translation
        _update_progress(20, "Translation")
        trans_results = await _run_phase_with_timeout(
            _tier_gate("translation", tier, translation.run(notebook_id, config, combo.name, hw.fingerprint)), "Translation")
        cat = _build_category("translation", "Translation", trans_results,
                              _tier_skip_reason("translation", tier))
        category_results["translation"] = cat
        _progress.results_so_far["translation"] = {"score": cat.score, "grade": cat.grade}

        # Phase 21 (Confidence Calibration) was removed on 2026-09-14 and lives in
        # tests/test_scan_pipeline_confidence.py. It called no model — it exercised the pure
        # function `scan_pipeline._compute_confidence` — so it spent minutes of a model
        # evaluation re-answering a question that cannot vary by model, and reported the answer
        # as a property of the model. In pytest it runs in milliseconds on every commit.

        # Phase 22: Field Edges (promoted daily-use near-misses → regression cases)
        _update_progress(22, "Field Edges")
        field_edge_results = await _run_phase_with_timeout(
            _tier_gate("field_edges", tier, field_edges.run(notebook_id, config, combo.name, hw.fingerprint)), "Field Edges")
        cat = _build_category("field_edges", "Field Edges", field_edge_results,
                              _tier_skip_reason("field_edges", tier))
        category_results["field_edges"] = cat
        _progress.results_so_far["field_edges"] = {"score": cat.score, "grade": cat.grade}

        # Retrieval quality — the RANKING, measured directly. Every other retrieval signal
        # arrives through a generated answer, mixed with prompt fit, sampling noise and judge
        # variance; this one stops at the ranking, so an embedding or rerank change is finally
        # measurable. Runs against the same ingested notebook, so it costs queries, not an
        # ingest. (Retrieval-harness gap identified 2026-08-21; built 2026-09-14.)
        _update_progress(23, "Vector Retrieval")
        retrieval_results = await _run_phase_with_timeout(
            _tier_gate("retrieval", tier, retrieval.run(notebook_id, config, combo.name, hw.fingerprint)), "Retrieval")
        cat = _build_category("retrieval", "Vector Retrieval", retrieval_results,
                              _tier_skip_reason("retrieval", tier))
        category_results["retrieval"] = cat
        _progress.results_so_far["retrieval"] = {"score": cat.score, "grade": cat.grade}

        # Entity extraction — feeds the knowledge graph, Constellation, cross-notebook
        # connections AND retrieval. Judge-free precision/recall, so it compares across models.
        _update_progress(24, "Entity Extraction")
        entity_results = await _run_phase_with_timeout(
            _tier_gate("entity_extract", tier, entity_extract.run(notebook_id, config, combo.name, hw.fingerprint)), "Entity Extraction")
        cat = _build_category("entity_extract", "Entity Extraction", entity_results,
                              _tier_skip_reason("entity_extract", tier))
        category_results["entity_extract"] = cat
        _progress.results_so_far["entity_extract"] = {"score": cat.score, "grade": cat.grade}

        # Image generation — the image_model ROLE had NO coverage at all before 2026-09-14, so
        # "this combo works" was a claim about four roles out of five. Skips cleanly when the
        # model is absent; one small draft render, not a quality benchmark.
        _update_progress(25, "Image Generation")
        image_results = await _run_phase_with_timeout(
            _tier_gate("image_gen", tier, image_gen.run(notebook_id, config, combo.name, hw.fingerprint)), "Image Generation",
            timeout=300)
        cat = _build_category("image_gen", "Image Generation", image_results,
                              _tier_skip_reason("image_gen", tier))
        category_results["image_gen"] = cat
        _progress.results_so_far["image_gen"] = {"score": cat.score, "grade": cat.grade}

        # ── Phase 23: Score & Persist ────────────────────────────────────
        _update_progress(26, "Scoring & persisting results")

        # Build summary
        summary.categories = {k: v.to_dict() for k, v in category_results.items()}
        summary.category_scores = {k: v.score for k, v in category_results.items()}

        # v1.8.2: collect skipped categories so the UI can explain why the
        # overall score ignores them, and exclude them from the weighted avg.
        summary.skipped_categories = [
            {"category": k, "display_name": v.display_name, "reason": v.skip_reason}
            for k, v in category_results.items()
            if v.skipped
        ]
        scoring_input = {
            k: v.score for k, v in category_results.items() if not v.skipped
        }

        # Get weights from config
        weights = config.get("scoring", {}).get("category_weights", {})
        if not weights:
            weights = {k: 10 for k in scoring_input}

        overall_score, overall_grade = scoring.compute_overall_score(
            scoring_input, weights
        )
        summary.overall_score = overall_score
        summary.overall_grade = overall_grade
        # Stamp what these numbers MEAN, so a later run can tell whether it is comparing like
        # with like before calling a difference a regression.
        summary.scoring_version = scoring.SCORING_VERSION
        summary.tier = tier

        # Engine fallbacks during THIS run. Recorded on the summary so a reader can tell
        # whether an "MLX run" was actually served by MLX end-to-end. A non-zero count does
        # not mean the app misbehaved — it means these numbers cannot be attributed to one
        # engine, which is exactly what an A/B needs to know.
        try:
            from services import throughput_meter as _tp
            summary.throughput = _tp.stop()
            _t = summary.throughput
            if _t.get("generations"):
                print(f"[EVALUATOR] throughput: {_t['generations']} generations, "
                      f"{_t['tokens_per_sec']} tok/s aggregate "
                      f"(p50 {_t['tps_p50']}, p05 {_t['tps_p05']})")
        except Exception as _tp_e:
            print(f"[EVALUATOR] throughput summary failed: {_tp_e}")

        if _mem_sampler is not None:
            try:
                summary.memory = _mem_sampler.stop()
                _mem = summary.memory
                print(f"[EVALUATOR] memory: peak_rss={_mem.get('peak_rss_gb')}GB "
                      f"mlx_peak={_mem.get('mlx_peak_gb')}GB "
                      f"mlx_active_end={_mem.get('mlx_active_end_gb')}GB "
                      f"swap_delta={_mem.get('swap_out_delta')}")
                if _mem.get("sustained_swap"):
                    # Quantified, so a reader can judge it rather than take it on faith — a bare
                    # claim of over-commitment is what discredited a clean 48 GB run on
                    # 2026-09-15 when the real figure was a rounding error.
                    _swapped = _mem.get("swap_out_delta_mb")
                    summary.warnings.append(
                        f"sustained swap-out during this run "
                        f"({_swapped:.0f} MB paged out system-wide) — timing numbers are not "
                        f"representative" if _swapped is not None else
                        "sustained swap-out during this run — timing numbers are not representative")
            except Exception as _ms_e:
                print(f"[EVALUATOR] memory summary failed: {_ms_e}")

        # A timed-out phase is excluded from the score, so it MUST be loud — otherwise a run
        # with half its phases missing reports a healthy number. The timeout firing is itself
        # a finding: it means a single query took longer than 3 minutes.
        if _TIMED_OUT:
            summary.timed_out_phases = sorted(_TIMED_OUT)
            summary.warnings.append(
                f"{len(_TIMED_OUT)} phase(s) timed out and were EXCLUDED from the score "
                f"({', '.join(sorted(_TIMED_OUT))}) — coverage is incomplete, and a phase "
                f"exceeding {_PHASE_TIMEOUT_SECONDS}s is itself a performance signal")
            print(f"[EVALUATOR] ⚠️ timed-out phases excluded: {sorted(_TIMED_OUT)}")

        _fb_n, _fb_detail = _engine_fallbacks_since(_fallback_watermark)
        summary.engine_fallbacks = _fb_n
        summary.engine_fallback_detail = _fb_detail
        if _fb_n:
            summary.warnings.append(
                f"{_fb_n} engine fallback(s) during this run — results are NOT attributable "
                f"to a single engine and must not be used for an A/B comparison")
            print(f"[EVALUATOR] ⚠️ {_fb_n} engine fallback(s) — run is not engine-pure")

        # v1.8.3: production readiness synthesis — compresses raw scores into
        # a pass/degraded/fail verdict per user-facing feature so the UI shows
        # "will this combo actually work in the app?" at a glance.
        try:
            from evaluator import feature_parity as _fp
            summary.feature_parity = _fp.synthesize(summary.categories)
            summary.production_readiness = _fp.rollup(summary.feature_parity)

            # ── The grade must not outrank the capabilities ──────────────────────────
            #
            # 2026-09-15: a model that could not SEE, could not generate a document, could not
            # emit valid JSON, failed long-context recall and produced zero tokens under
            # concurrency scored **C+ (77/100)**. Arithmetically correct — those seven
            # categories carry 21 of 136 weight, so failing all of them costs ~15 points — and
            # completely wrong as a summary. A weighted mean answers "how good on average",
            # never "is anything broken", which is why `run.blocking_failures` exists for the
            # release gate. The headline number needs the same discipline.
            #
            # The SCORE is left untouched: it is a valid statistic and changing it would make
            # runs incomparable. The GRADE is capped, because the grade is what a human reads.
            _fails = int((summary.production_readiness or {}).get("counts", {}).get("fail", 0))
            if _fails:
                _cap = "C" if _fails < 3 else "D"
                _order = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "F"]
                try:
                    if _order.index(summary.overall_grade) < _order.index(_cap):
                        summary.grade_cap_reason = (
                            f"{_fails} capability/capabilities failed outright — grade capped at "
                            f"{_cap} (raw weighted score {summary.overall_score:.1f})")
                        summary.overall_grade = _cap
                        summary.warnings.append(summary.grade_cap_reason)
                        print(f"[EVALUATOR] {summary.grade_cap_reason}")
                except ValueError:
                    pass
        except Exception as _e:
            print(f"[EVALUATOR] feature_parity synthesis failed (non-fatal): {_e}")

        # Persist the preflight report for result-viewer inspection
        try:
            summary.preflight = preflight_report.to_dict()
        except Exception:
            summary.preflight = {}

        # Performance profile
        _tps, _ttft = _PERF["tps"], _PERF["ttft"]
        summary.avg_tokens_per_sec = sum(_tps) / len(_tps) if _tps else 0
        summary.avg_ttft_ms = sum(_ttft) / len(_ttft) if _ttft else 0
        # Distribution, not just a mean: a p95 TTFT regression is what a user notices, and a
        # sample count is what tells a reader whether the mean means anything.
        summary.perf_samples = len(_tps)
        summary.tps_p50 = _pctl(_tps, 50)
        summary.tps_p05 = _pctl(_tps, 5)          # the slow tail
        summary.ttft_p50 = _pctl(_ttft, 50)
        summary.ttft_p95 = _pctl(_ttft, 95)
        summary.total_run_time_seconds = time.time() - run_start

        # Collect warnings
        for cat_name, cat in category_results.items():
            # A skipped/timed-out category is EXCLUDED from the score, so reporting it as
            # "scored F (0)" contradicts the exclusion warning in the same list. It reads as
            # two failures where there is one absence.
            if cat.skipped:
                continue
            if cat.score < 40:
                summary.warnings.append(f"{cat.display_name} scored F ({cat.score:.0f})")
            elif cat.score < 60:
                summary.warnings.append(f"{cat.display_name} scored D ({cat.score:.0f})")
            summary.warnings.extend(cat.warnings)

        # Combo-drift detection: compare the snapshot we took at the top of
        # the run against settings.* now. If they differ, a Locker swap
        # landed during the eval — surface this loudly so the user knows
        # the report is mixed.
        drifted = []
        # Compare LIKE-FOR-LIKE by re-building the combo the same way the snapshot was built.
        # Two bugs lived here: `_mr` was undefined (crashing every run at the finish line), and
        # comparing the snapshot's RESOLVED values against raw `settings.*` reported phantom
        # drift on every MLX run — the snapshot holds an HF id while `settings.main_model`
        # holds the Ollama name, so they can never be equal. Rebuilding from ModelCombo makes
        # both sides resolved, which is the only comparison that means anything.
        try:
            _now_combo = _MC.from_config(settings)
            _now = {
                "main_model": _now_combo.main_model,
                "fast_model": _now_combo.fast_model,
                "vision_model": _now_combo.vision_model,
                "embedding_model": _now_combo.embedding_model,
                "main_engine": _now_combo.main_engine,
                "fast_engine": _now_combo.fast_engine,
                "vision_engine": _now_combo.vision_engine,
                "embed_engine": _now_combo.embed_engine,
            }
            for k, snap_v in combo_snapshot.items():
                cur_v = _now.get(k, "")
                if cur_v != snap_v:
                    drifted.append(f"{k}: started with '{snap_v}', ended on '{cur_v}'")
        except Exception as _drift_e:
            print(f"[EVALUATOR] drift check skipped (non-fatal): {_drift_e}")

        if drifted:
            warn_msg = (
                "Model swap detected DURING eval — results mix two configurations. "
                "Wait for the run to finish before swapping next time. Drift: "
                + " · ".join(drifted)
            )
            summary.warnings.append(warn_msg)
            print(f"[EVALUATOR] ⚠️  {warn_msg}")
        # The summary.combo dict was built at the same moment as combo_snapshot
        # (line 143 above); it already records the run-start configuration —
        # the drift warning above is the only addition from snapshotting.

        # Persist
        _persist_results(summary)

        print(f"\n[EVALUATOR] ═══════════════════════════════════════════")
        print(f"[EVALUATOR] OVERALL: {summary.overall_score:.1f} ({summary.overall_grade})")
        print(f"[EVALUATOR] Time: {summary.total_run_time_seconds:.0f}s")
        # A category with nothing to run scores 0.0/"F" internally (compute_category_score has
        # no other way to say "empty"), but printing that as `field_edges: 0 (F)` reports a
        # failing grade for a test that never ran — and it is EXCLUDED from the overall, so the
        # same summary shows an F alongside a B+ that does not contain it. The warnings list
        # above already makes this distinction; the breakdown did not.
        _skipped_cats = {c.get("category") for c in (summary.skipped_categories or [])}
        for k, v in summary.category_scores.items():
            if k in _skipped_cats:
                _why = next((c.get("reason") for c in summary.skipped_categories
                             if c.get("category") == k), "") or "nothing to run"
                print(f"[EVALUATOR]   {k}: — (not applicable: {_why})")
                continue
            grade = _score_to_grade(v)
            print(f"[EVALUATOR]   {k}: {v:.0f} ({grade})")
        if summary.warnings:
            print(f"[EVALUATOR] Warnings: {summary.warnings}")
        print(f"[EVALUATOR] ═══════════════════════════════════════════\n")

        return summary

    except Exception as e:
        _progress.error = str(e)
        print(f"[EVALUATOR] FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        raise

    finally:
        # ── Phase 24: Cleanup ────────────────────────────────────────────
        _update_progress(27, "Cleaning up test notebook")
        if notebook_id:
            try:
                await ingestion.cleanup_test_notebook(notebook_id)
            except Exception as ce:
                print(f"[EVALUATOR] Cleanup error (non-fatal): {ce}")

        # Build C: drop the active RunProfile so scoring outside a run is unbiased.
        try:
            from evaluator import scoring as _scoring
            _scoring.clear_active_run_profile()
        except Exception:
            pass

        _progress.running = False
        _progress.elapsed_seconds = time.time() - run_start


# Perf samples across EVERY phase. Previously only Phase 5 (Streaming) contributed, so
# `avg_tokens_per_sec` was effectively a single query's throughput — far too thin to detect a
# regression, and not comparable between runs whose one sampled query happened to differ.
_PERF: dict = {"tps": [], "ttft": []}


def _reset_perf() -> None:
    _PERF["tps"], _PERF["ttft"] = [], []


def _collect_perf(results: list) -> None:
    for r in results or []:
        if getattr(r, "tokens_per_second", 0) > 0:
            _PERF["tps"].append(r.tokens_per_second)
        if getattr(r, "time_to_first_token_ms", 0) > 0:
            _PERF["ttft"].append(r.time_to_first_token_ms)


def _pctl(values: list, p: float) -> float:
    """p-th percentile. p95 TTFT is the number a user actually feels; a mean hides the tail."""
    if not values:
        return 0.0
    v = sorted(values)
    k = max(0, min(len(v) - 1, int(round((p / 100.0) * (len(v) - 1)))))
    return round(float(v[k]), 1)


def _build_category(name: str, display_name: str, results: list[EvalResult],
                    no_data_reason: str = "") -> CategoryResult:
    """Build a CategoryResult from individual test results.

    v1.8.2: if every test in a category was skipped (e.g. vision category on a
    text-only model), mark the whole category as skipped so it can be excluded
    from the overall weighted average rather than scored as zero.
    """
    _collect_perf(results)
    score, grade = scoring.compute_category_score(results)
    # A phase that produced NO results did not fail — it never reported. Previously this scored
    # 0/F and dragged the overall score down, which is wrong in general and actively misleading
    # for an engine A/B: on a memory-constrained machine whichever run happened to hit the 180s
    # phase timeout would score arbitrarily worse, and the delta would be attributed to the
    # engine. Excluded from the weighted average, exactly like a capability-based skip.
    no_data = not results
    all_skipped = (bool(results) and all(r.skipped for r in results)) or no_data
    # Strict verdict — the SINGLE source of truth for every view (breakdown table, feature-parity
    # list, top-line counts). Matches feature_parity._verdict_for so a 69 can't be "Pass" in the
    # table and "degraded" in the parity list (user report 2026-07-24).
    if all_skipped:
        verdict = "not_applicable"
    elif score < 40:
        verdict = "fail"
    elif score < 70:
        verdict = "degraded"
    else:
        verdict = "pass"
    cat = CategoryResult(
        category=name,
        display_name=display_name,
        tests=results,
        score=score,
        grade=grade,
        passed=(score >= 40) or all_skipped,
        verdict=verdict,
        total_time_ms=sum(r.total_time_ms for r in results),
        skipped=all_skipped,
        skip_reason=(
            results[0].skip_reason if all_skipped and results
            else ((no_data_reason or
                   "phase timed out — no data recorded, excluded from the score")
                  if no_data else "")
        ),
    )
    # Add warnings for failed tests
    for r in results:
        if not r.passed and not r.skipped:
            cat.warnings.append(f"{r.test_name}: {r.failure_reason}")
    return cat


def _persist_results(summary: ComboEvalSummary):
    """Save results to disk."""
    results_dir = _get_results_dir()

    # Save full run
    run_filename = (
        f"{summary.timestamp[:16].replace(':', '-')}_"
        f"{summary.combo.get('name', 'unknown').lower().replace(' ', '_')}_"
        f"{summary.hardware.get('fingerprint', 'unknown')}.json"
    )
    run_path = results_dir / "runs" / run_filename
    run_path.write_text(json.dumps(summary.to_dict(), indent=2, default=str))
    print(f"[EVALUATOR] Results saved: {run_path}")

    # Update summary index
    summary_path = results_dir / "summary.json"
    try:
        existing = json.loads(summary_path.read_text()) if summary_path.exists() else {"runs": []}
    except Exception:
        existing = {"runs": []}

    existing["runs"].append({
        "run_id": summary.run_id,
        "timestamp": summary.timestamp,
        "file": run_filename,
        "combo": summary.combo.get("name", ""),
        "main_model": summary.combo.get("main_model", ""),
        "fast_model": summary.combo.get("fast_model", ""),
        "hardware": summary.hardware.get("fingerprint", ""),
        "overall_score": summary.overall_score,
        "overall_grade": summary.overall_grade,
        "total_time_seconds": summary.total_run_time_seconds,
    })

    # Keep last 50 runs
    existing["runs"] = existing["runs"][-50:]
    summary_path.write_text(json.dumps(existing, indent=2, default=str))


def get_results_list() -> list[dict]:
    """Get list of all historical evaluation runs."""
    results_dir = _get_results_dir()
    summary_path = results_dir / "summary.json"
    if not summary_path.exists():
        return []
    try:
        data = json.loads(summary_path.read_text())
        return data.get("runs", [])
    except Exception:
        return []


def get_result_by_id(run_id: str) -> dict | None:
    """Load a specific run's full results."""
    results_dir = _get_results_dir()
    runs = get_results_list()
    for run in runs:
        if run.get("run_id") == run_id:
            run_path = results_dir / "runs" / run["file"]
            if run_path.exists():
                return json.loads(run_path.read_text())
    return None


def get_latest_result(tier: str | None = None) -> dict | None:
    """Get the most recent evaluation run, optionally restricted to one tier.

    `tier` matters for the regression gate. Without it, alternating a quick `--tier smoke`
    check with occasional full runs means every run's predecessor is the OTHER tier, the gate
    declines to compare every time, and the safety net silently stops working. Comparing
    smoke-to-smoke and full-to-full keeps both meaningful.

    Runs written before tiers existed carry no `tier` field and were all full runs.
    """
    runs = get_results_list()
    if not runs:
        return None
    results_dir = _get_results_dir()
    for entry in reversed(runs):
        run_path = results_dir / "runs" / entry["file"]
        if not run_path.exists():
            continue
        try:
            data = json.loads(run_path.read_text())
        except Exception:
            continue
        if tier is None or (data.get("tier") or "full") == tier:
            return data
    return None


async def cleanup_stale_notebook():
    """Delete any leftover test notebook from a failed/interrupted run."""
    from storage.notebook_store import notebook_store

    config = _load_config()
    test_name = config.get("notebook_name", "🧪 LLM Evaluator Test Notebook")

    notebooks = await notebook_store.list()
    for nb in notebooks:
        if nb.get("title") == test_name:
            print(f"[EVALUATOR] Found stale test notebook: {nb['id']}, cleaning up...")
            await ingestion.cleanup_test_notebook(nb["id"])
