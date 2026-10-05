"""Speech-to-text: one entry point for every caller (LB-3).

`/voice/transcribe`, `/audio-llm/asr`, speech-to-speech, audio and video source
ingest, and `/v1/audio/transcriptions` all come through `transcribe()`.

Audio is decoded once, in-process (`audio_codec`), then Parakeet v3 runs in the
MLX engine. Any Parakeet failure falls back to mlx-whisper on the SAME decoded
array — so neither engine ever shells out to an `ffmpeg` on PATH. The engine that
actually answered is reported, never assumed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import numpy as np

from services import audio_codec

logger = logging.getLogger(__name__)

# Both Parakeet and Whisper take 16 kHz mono.
SAMPLE_RATE = 16000


class TranscriptionError(RuntimeError):
    """Neither engine could transcribe the audio."""


def _whisper_sync(pcm: np.ndarray, model_id: str, language: Optional[str]) -> Dict[str, Any]:
    import mlx_whisper

    kw: Dict[str, Any] = {"path_or_hf_repo": model_id}
    if language:
        kw["language"] = language
    result = mlx_whisper.transcribe(pcm, **kw)
    segments = [
        {"start": round(float(s["start"]), 3), "end": round(float(s["end"]), 3),
         "text": (s.get("text") or "").strip()}
        for s in result.get("segments") or []
    ]
    return {"text": (result.get("text") or "").strip(), "segments": segments,
            "language": result.get("language")}


async def transcribe(audio: audio_codec.Source, *, language: Optional[str] = None) -> Dict[str, Any]:
    """Audio bytes or a path → {text, segments, language, duration, engine, seconds}.

    Raises `audio_codec.CodecError` for audio that cannot be read at all, and
    `TranscriptionError` when both engines fail.
    """
    from config import settings

    t0 = time.perf_counter()
    pcm = await asyncio.to_thread(audio_codec.decode_pcm, audio, SAMPLE_RATE)
    duration = audio_codec.duration_seconds(pcm, SAMPLE_RATE)
    if len(pcm) == 0:
        return {"text": "", "segments": [], "language": language, "duration": 0.0,
                "engine": None, "seconds": 0.0}

    errors = []
    try:
        from services import mlx_asr

        out = await mlx_asr.transcribe(pcm, settings.stt_model)
        out.update(engine=settings.stt_model, language=language)
    except Exception as exc:
        errors.append(f"parakeet: {exc}")
        logger.warning("[stt] Parakeet failed (%s) — falling back to whisper", exc)
        try:
            out = await asyncio.to_thread(_whisper_sync, pcm, settings.stt_fallback_model, language)
            out["engine"] = settings.stt_fallback_model
        except Exception as exc2:
            errors.append(f"whisper: {exc2}")
            raise TranscriptionError("; ".join(errors)) from exc2

    out["duration"] = duration
    out["seconds"] = round(time.perf_counter() - t0, 3)
    logger.info("[stt] %.1fs of audio in %.2fs via %s", duration, out["seconds"], out["engine"])
    return out
