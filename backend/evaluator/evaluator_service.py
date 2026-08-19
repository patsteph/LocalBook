"""Evaluator Service — Core orchestrator for end-to-end LLM evaluation.

Creates test notebook → ingests content → runs 10 test categories →
scores everything → persists results → cleans up.
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
    confidence,
    field_edges,
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


async def run_full_evaluation() -> ComboEvalSummary:
    """Run the complete evaluation suite.
    
    This is the main entry point. It:
    1. Profiles hardware
    2. Creates a test notebook  
    3. Ingests all test content
    4. Runs all 10 test categories
    5. Scores, persists, and returns results
    6. Cleans up the test notebook
    """
    from config import settings

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
    # that actually serves each role. Hand-building this from `settings.ollama_model` recorded
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
        "ollama_model": _combo.main_model,
        "ollama_fast_model": _combo.fast_model,
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
        _rp = derive_run_profile(combo_snapshot["ollama_model"],
                                 provider=combo_snapshot.get("main_engine", "ollama"))
        _scoring.set_active_run_profile(_rp)
        print(f"[EVALUATOR] RunProfile: {combo_snapshot['ollama_model']} "
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

        config = _load_config()
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
            rag_chat.run(notebook_id, config, combo.name, hw.fingerprint), "RAG Chat")
        cat = _build_category("rag_chat", "RAG Chat Q&A", rag_results)
        category_results["rag_chat"] = cat
        _progress.results_so_far["rag_chat"] = {"score": cat.score, "grade": cat.grade}

        # Phase 5: Streaming
        _update_progress(5, "Streaming Generation")
        stream_results = await _run_phase_with_timeout(
            streaming.run(notebook_id, config, combo.name, hw.fingerprint), "Streaming")
        cat = _build_category("streaming", "Streaming Generation", stream_results)
        category_results["streaming"] = cat
        _progress.results_so_far["streaming"] = {"score": cat.score, "grade": cat.grade}

        # Phase 6: Fast Follow-Up
        _update_progress(6, "Fast Follow-Up")
        followup_results = await _run_phase_with_timeout(
            fast_followup.run(notebook_id, config, combo.name, hw.fingerprint), "Fast Follow-Up")
        cat = _build_category("fast_followup", "Fast Follow-Up", followup_results)
        category_results["fast_followup"] = cat
        _progress.results_so_far["fast_followup"] = {"score": cat.score, "grade": cat.grade}

        # Phase 7: Document Generation
        _update_progress(7, "Document Generation")
        docgen_results = await _run_phase_with_timeout(
            document_gen.run(notebook_id, config, combo.name, hw.fingerprint), "Document Gen")
        cat = _build_category("document_gen", "Document Generation", docgen_results)
        category_results["document_gen"] = cat
        _progress.results_so_far["document_gen"] = {"score": cat.score, "grade": cat.grade}

        # Phase 8: Structured JSON (Quiz)
        _update_progress(8, "Structured JSON (Quiz)")
        json_results = await _run_phase_with_timeout(
            structured_json.run(notebook_id, config, combo.name, hw.fingerprint), "Structured JSON")
        cat = _build_category("structured_json", "Structured JSON", json_results)
        category_results["structured_json"] = cat
        _progress.results_so_far["structured_json"] = {"score": cat.score, "grade": cat.grade}

        # Phase 9: Intent Classification
        _update_progress(9, "Intent Classification")
        intent_results = await _run_phase_with_timeout(
            intent_classify.run(notebook_id, config, combo.name, hw.fingerprint), "Intent Classify")
        cat = _build_category("intent_classify", "Intent Classification", intent_results)
        category_results["intent_classify"] = cat
        _progress.results_so_far["intent_classify"] = {"score": cat.score, "grade": cat.grade}

        # Phase 10: Embedding Quality
        _update_progress(10, "Embedding Quality")
        embed_results = await _run_phase_with_timeout(
            embedding_quality.run(notebook_id, config, combo.name, hw.fingerprint), "Embedding Quality")
        cat = _build_category("embedding_quality", "Embedding Quality", embed_results)
        category_results["embedding_quality"] = cat
        _progress.results_so_far["embedding_quality"] = {"score": cat.score, "grade": cat.grade}

        # Phase 11: Vision
        _update_progress(11, "Vision / Image")
        vision_results = await _run_phase_with_timeout(
            vision.run(notebook_id, config, combo.name, hw.fingerprint), "Vision")
        cat = _build_category("vision", "Vision / Image", vision_results)
        category_results["vision"] = cat
        _progress.results_so_far["vision"] = {"score": cat.score, "grade": cat.grade}

        # Phase 12: TTS Audio
        _update_progress(12, "TTS Audio")
        tts_results = await _run_phase_with_timeout(
            tts_audio.run(notebook_id, config, combo.name, hw.fingerprint), "TTS Audio")
        cat = _build_category("tts_audio", "TTS Audio", tts_results)
        category_results["tts_audio"] = cat
        _progress.results_so_far["tts_audio"] = {"score": cat.score, "grade": cat.grade}

        # Phase 13: Instruction Following
        _update_progress(13, "Instruction Following")
        instruct_results = await _run_phase_with_timeout(
            instruction_follow.run(notebook_id, config, combo.name, hw.fingerprint), "Instruction Follow")
        cat = _build_category("instruction_follow", "Instruction Following", instruct_results)
        category_results["instruction_follow"] = cat
        _progress.results_so_far["instruction_follow"] = {"score": cat.score, "grade": cat.grade}

        # Phase 14: Concurrency & Load
        _update_progress(14, "Concurrency & Load")
        concurrency_results = await _run_phase_with_timeout(
            concurrency.run(notebook_id, config, combo.name, hw.fingerprint), "Concurrency")
        cat = _build_category("concurrency", "Concurrency & Load", concurrency_results)
        category_results["concurrency"] = cat
        _progress.results_so_far["concurrency"] = {"score": cat.score, "grade": cat.grade}

        # Phase 15: Context Capacity (Needle)
        _update_progress(15, "Context Capacity (Needle)")
        # Needle now stresses the model's DEPLOYED window (up to ~75% of a large ctx),
        # so prompt-eval of tens of thousands of tokens can exceed the default 180s.
        # Give this deliberate stress test a longer ceiling so it completes + scores.
        needle_results = await _run_phase_with_timeout(
            needle_haystack.run(notebook_id, config, combo.name, hw.fingerprint),
            "Needle Haystack", timeout=420)
        cat = _build_category("needle_haystack", "Context Capacity", needle_results)
        category_results["needle_haystack"] = cat
        _progress.results_so_far["needle_haystack"] = {"score": cat.score, "grade": cat.grade}

        # Phase 16: Prompt Safety (Adversarial)
        _update_progress(16, "Prompt Safety (Adversarial)")
        safety_results = await _run_phase_with_timeout(
            prompt_safety.run(notebook_id, config, combo.name, hw.fingerprint), "Prompt Safety")
        cat = _build_category("prompt_safety", "Prompt Safety", safety_results)
        category_results["prompt_safety"] = cat
        _progress.results_so_far["prompt_safety"] = {"score": cat.score, "grade": cat.grade}

        # Phase 17: Voice Modifier (apples-to-apples voice consistency)
        _update_progress(17, "Voice Modifier")
        voice_results = await _run_phase_with_timeout(
            voice_modifier.run(notebook_id, config, combo.name, hw.fingerprint), "Voice Modifier")
        cat = _build_category("voice_modifier", "Voice Modifier", voice_results)
        category_results["voice_modifier"] = cat
        _progress.results_so_far["voice_modifier"] = {"score": cat.score, "grade": cat.grade}

        # Phase 18: Capture Modes — multi-mode vision coverage
        _update_progress(18, "Capture Modes")
        modes_results = await _run_phase_with_timeout(
            capture_modes.run(notebook_id, config, combo.name, hw.fingerprint), "Capture Modes")
        cat = _build_category("capture_modes", "Capture Modes", modes_results)
        category_results["capture_modes"] = cat
        _progress.results_so_far["capture_modes"] = {"score": cat.score, "grade": cat.grade}

        # Phase 19: Refinement Pass Fidelity
        _update_progress(19, "Refinement Pass")
        refine_results = await _run_phase_with_timeout(
            refinement.run(notebook_id, config, combo.name, hw.fingerprint), "Refinement")
        cat = _build_category("refinement", "Refinement Pass", refine_results)
        category_results["refinement"] = cat
        _progress.results_so_far["refinement"] = {"score": cat.score, "grade": cat.grade}

        # Phase 20: Translation
        _update_progress(20, "Translation")
        trans_results = await _run_phase_with_timeout(
            translation.run(notebook_id, config, combo.name, hw.fingerprint), "Translation")
        cat = _build_category("translation", "Translation", trans_results)
        category_results["translation"] = cat
        _progress.results_so_far["translation"] = {"score": cat.score, "grade": cat.grade}

        # Phase 21: Confidence Scoring Calibration (pure-function)
        _update_progress(21, "Confidence Calibration")
        conf_results = await _run_phase_with_timeout(
            confidence.run(notebook_id, config, combo.name, hw.fingerprint), "Confidence")
        cat = _build_category("confidence", "Confidence Calibration", conf_results)
        category_results["confidence"] = cat
        _progress.results_so_far["confidence"] = {"score": cat.score, "grade": cat.grade}

        # Phase 22: Field Edges (promoted daily-use near-misses → regression cases)
        _update_progress(22, "Field Edges")
        field_edge_results = await _run_phase_with_timeout(
            field_edges.run(notebook_id, config, combo.name, hw.fingerprint), "Field Edges")
        cat = _build_category("field_edges", "Field Edges", field_edge_results)
        category_results["field_edges"] = cat
        _progress.results_so_far["field_edges"] = {"score": cat.score, "grade": cat.grade}

        # ── Phase 23: Score & Persist ────────────────────────────────────
        _update_progress(23, "Scoring & persisting results")

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
                    summary.warnings.append(
                        "sustained swap-out during this run — the machine was over-committed, "
                        "so timing numbers are not representative")
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
        # drift on every MLX run — the snapshot holds an HF id while `settings.ollama_model`
        # holds the Ollama name, so they can never be equal. Rebuilding from ModelCombo makes
        # both sides resolved, which is the only comparison that means anything.
        try:
            _now_combo = _MC.from_config(settings)
            _now = {
                "ollama_model": _now_combo.main_model,
                "ollama_fast_model": _now_combo.fast_model,
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
        for k, v in summary.category_scores.items():
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
        _update_progress(24, "Cleaning up test notebook")
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


def _build_category(name: str, display_name: str, results: list[EvalResult]) -> CategoryResult:
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
            else ("phase timed out — no data recorded, excluded from the score" if no_data else "")
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


def get_latest_result() -> dict | None:
    """Get the most recent evaluation run."""
    runs = get_results_list()
    if not runs:
        return None
    latest = runs[-1]
    results_dir = _get_results_dir()
    run_path = results_dir / "runs" / latest["file"]
    if run_path.exists():
        return json.loads(run_path.read_text())
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
