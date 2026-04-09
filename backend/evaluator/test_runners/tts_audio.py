"""TTS Audio test runner — tests podcast generation through the full audio pipeline.

TTS is a REQUIRED capability — failures are scored as FAIL, not SKIP.
"""

import time
from datetime import datetime
from pathlib import Path
from evaluator.models import EvalResult


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Generate a podcast via the real audio pipeline and evaluate."""
    from services.audio_generator import audio_service
    from config import settings

    audio_config = config.get("audio_generation", {})
    result = EvalResult(
        test_id="tts_audio_podcast",
        category="tts_audio",
        test_name="Podcast Generation (TTS Required)",
        model_combo=combo_name,
        model_used="kokoro-mlx",
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )

    try:
        start = time.time()

        generation = await audio_service.generate(
            notebook_id=notebook_id,
            topic=audio_config.get("topic", "model evaluation"),
            duration_minutes=audio_config.get("duration_minutes", 2),
            host1_gender=audio_config.get("host1_gender", "male"),
            host2_gender=audio_config.get("host2_gender", "female"),
            accent=audio_config.get("accent", "us"),
        )

        import asyncio
        from storage.audio_store import audio_store

        audio_id = generation.get("id") or generation.get("audio_id")
        if audio_id:
            timeout = audio_config.get("timeout_seconds", 300)
            start_poll = time.time()
            while time.time() - start_poll < timeout:
                gen_record = await audio_store.get(audio_id)
                if gen_record and gen_record.get("status") in ["completed", "failed"]:
                    generation = gen_record
                    break
                await asyncio.sleep(5)

        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed

        # Check script generation
        script = generation.get("script", "")
        audio_path = generation.get("audio_file_path", "")
        status = generation.get("status", "")
        duration_seconds = generation.get("duration_seconds", 0)

        result.output_chars = len(script)
        result.actual_output_preview = f"Status: {status}, Script: {len(script)} chars, Audio: {audio_path}"

        # Score: Script generated
        script_score = 100 if len(script) > 100 else max(0, int(len(script)))
        if not script:
            script_score = 0

        # Score: Audio file exists and has content
        audio_exists = False
        audio_score = 0
        if audio_path:
            audio_file = Path(audio_path)
            audio_exists = audio_file.exists() and audio_file.stat().st_size > 1000
            audio_score = 100 if audio_exists else 0

        # Score: Duration accuracy (within ±50% of target)
        target_seconds = audio_config.get("duration_minutes", 2) * 60
        duration_score = 0
        if duration_seconds and duration_seconds > 0:
            ratio = duration_seconds / target_seconds
            if 0.5 <= ratio <= 1.5:
                duration_score = 100
            elif 0.3 <= ratio <= 2.0:
                duration_score = 60
            else:
                duration_score = 30

        # Score: Pipeline time
        max_time = audio_config.get("timeout_seconds", 300) * 1000
        speed_score = 100 if elapsed < max_time else max(0, int(100 - (elapsed - max_time) / 1000))

        result.accuracy_score = script_score
        result.format_score = audio_score
        result.completeness_score = duration_score
        result.overall_score = int(
            script_score * 0.30 + audio_score * 0.30 + duration_score * 0.20 + speed_score * 0.20
        )
        result.passed = script_score > 0 and audio_score > 0

        if not result.passed:
            reasons = []
            if script_score == 0:
                reasons.append("No script generated")
            if audio_score == 0:
                reasons.append("No audio file produced" if not audio_path else "Audio file empty/missing")
            result.failure_reason = "; ".join(reasons)

        print(f"[EVAL-TTS] Score={result.overall_score}, script={len(script)} chars, "
              f"audio={'OK' if audio_exists else 'MISSING'}, {elapsed:.0f}ms")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-TTS] FAILED: {e}")

    return [result]
