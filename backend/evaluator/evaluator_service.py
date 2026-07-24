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


async def _run_phase_with_timeout(coro, phase_name: str, timeout: int = _PHASE_TIMEOUT_SECONDS):
    """Run a test phase with a hard timeout. Returns results or empty list on timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        print(f"[EVALUATOR] ⚠️ Phase '{phase_name}' timed out after {timeout}s — skipping")
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
    from evaluator.model_registry import model_registry as _mr
    combo_snapshot = {
        "ollama_model": getattr(settings, "ollama_model", "") or "",
        "ollama_fast_model": getattr(settings, "ollama_fast_model", "") or "",
        # Record the RESOLVED vision model (what the runners actually test), so the
        # report matches reality instead of a configured granite that isn't used.
        "vision_model": _mr.resolve_vision_model(
            getattr(settings, "ollama_model", "") or "",
            getattr(settings, "vision_model", "") or "",
        ),
        "embedding_model": getattr(settings, "embedding_model", "") or "",
    }

    # Build C (2026-07-07): derive the tested model's RunProfile ONCE and make the
    # scorer normalize every output through it (strip <think>, extract JSON) BEFORE
    # scoring — so a thinking / differently-templated model is scored on its final
    # answer, not penalised for not behaving like olmo/gemma. One run = one model,
    # so a single active profile is correct. Cleared in the finally below.
    try:
        from evaluator.run_profile import derive_run_profile
        from evaluator import scoring as _scoring
        _rp = derive_run_profile(combo_snapshot["ollama_model"])
        _scoring.set_active_run_profile(_rp)
        print(f"[EVALUATOR] RunProfile: {combo_snapshot['ollama_model']} "
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
        all_tps = []
        all_ttft = []

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
        for r in stream_results:
            if r.tokens_per_second > 0:
                all_tps.append(r.tokens_per_second)
            if r.time_to_first_token_ms > 0:
                all_ttft.append(r.time_to_first_token_ms)

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

        # ── Phase 22: Score & Persist ────────────────────────────────────
        _update_progress(22, "Scoring & persisting results")

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
        summary.avg_tokens_per_sec = sum(all_tps) / len(all_tps) if all_tps else 0
        summary.avg_ttft_ms = sum(all_ttft) / len(all_ttft) if all_ttft else 0
        summary.total_run_time_seconds = time.time() - run_start

        # Collect warnings
        for cat_name, cat in category_results.items():
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
        for k, snap_v in combo_snapshot.items():
            # Compare LIKE-FOR-LIKE. The snapshot stored the RESOLVED vision model (what the
            # runners actually test); reading raw settings.vision_model here compared a resolved
            # value against a raw one, so vision phantom-drifted every run where the raw setting
            # (e.g. granite3.2-vision:2b) differs from the resolved main (gemma4:e4b) — a false
            # "swap detected" (user report 2026-07-24). Re-resolve vision the same way.
            if k == "vision_model":
                cur_v = _mr.resolve_vision_model(
                    getattr(settings, "ollama_model", "") or "",
                    getattr(settings, "vision_model", "") or "",
                )
            else:
                cur_v = getattr(settings, k, "") or ""
            if cur_v != snap_v:
                drifted.append(f"{k}: started with '{snap_v}', ended on '{cur_v}'")
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
        # ── Phase 23: Cleanup ────────────────────────────────────────────
        _update_progress(23, "Cleaning up test notebook")
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


def _build_category(name: str, display_name: str, results: list[EvalResult]) -> CategoryResult:
    """Build a CategoryResult from individual test results.

    v1.8.2: if every test in a category was skipped (e.g. vision category on a
    text-only model), mark the whole category as skipped so it can be excluded
    from the overall weighted average rather than scored as zero.
    """
    score, grade = scoring.compute_category_score(results)
    all_skipped = bool(results) and all(r.skipped for r in results)
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
        skip_reason=(results[0].skip_reason if all_skipped and results else ""),
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
