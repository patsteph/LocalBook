"""Parakeet speech-to-text inside the MLX engine (LB-3).

Lives beside `mlx_engine`, not in it (that file is past 1300 lines), but runs on
ITS single MLX thread: MLX's GPU streams are thread-local, and a model loaded on
one thread and run on another fails. The resident model sits in
`engine._asr_resident`, so the engine's budget, LRU eviction and `resident()`
report count it like any other model.

Audio arrives already decoded (`audio_codec.decode_pcm`): parakeet-mlx's own
loader shells out to an `ffmpeg` on PATH, which the MDM Mac does not have.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Long audio is decoded in windows with an overlap, the way parakeet-mlx's own
# `transcribe(chunk_duration=...)` does. Two minutes keeps attention memory flat
# on a 16 GB Mac while leaving whole sentences inside each window.
CHUNK_SECONDS = 120.0
OVERLAP_SECONDS = 15.0


def _load(engine, model_id: str):
    model = engine._asr_resident.get(model_id)
    if model is None:
        engine._ensure_memory_limit()
        from parakeet_mlx import from_pretrained

        from services.mlx_engine import offline_if_cached

        logger.info(f"[mlx-asr] loading {model_id} …")
        t0 = time.perf_counter()
        with offline_if_cached(model_id):
            model = from_pretrained(model_id)
        engine._asr_resident[model_id] = model
        logger.info(f"[mlx-asr] loaded {model_id} in {time.perf_counter() - t0:.1f}s")
    engine._last_used[model_id] = time.monotonic()
    return model


def _result_dict(result) -> Dict[str, Any]:
    segments = [
        {"start": round(float(s.start), 3), "end": round(float(s.end), 3), "text": s.text.strip()}
        for s in getattr(result, "sentences", []) or []
    ]
    return {"text": (result.text or "").strip(), "segments": segments}


def transcribe_on_thread(engine, pcm: np.ndarray, model_id: str) -> Dict[str, Any]:
    """float32 mono PCM at the model's rate → {text, segments}. MLX thread only."""
    import mlx.core as mx
    from parakeet_mlx.alignment import (
        merge_longest_common_subsequence,
        merge_longest_contiguous,
        sentences_to_result,
        tokens_to_sentences,
    )
    from parakeet_mlx.audio import get_logmel
    from parakeet_mlx.parakeet import DecodingConfig

    model = _load(engine, model_id)
    cfg = model.preprocessor_config
    config = DecodingConfig()
    # float32, as parakeet's own loader hands it over (its `dtype` argument is ignored there);
    # get_logmel on bf16 input fails inside the STFT matmul.
    audio = mx.array(np.asarray(pcm, dtype=np.float32))

    total = len(pcm)
    chunk = int(CHUNK_SECONDS * cfg.sample_rate)
    if total <= chunk:
        return _result_dict(model.generate(get_logmel(audio, cfg), decoding_config=config)[0])

    overlap = int(OVERLAP_SECONDS * cfg.sample_rate)
    tokens = []
    for start in range(0, total, chunk - overlap):
        end = min(start + chunk, total)
        if end - start < cfg.hop_length:
            break
        part = model.generate(get_logmel(audio[start:end], cfg), decoding_config=config)[0]
        offset = start / cfg.sample_rate
        for sentence in part.sentences:
            for token in sentence.tokens:
                token.start += offset
                token.end = token.start + token.duration
        if tokens:
            try:
                tokens = merge_longest_contiguous(tokens, part.tokens, overlap_duration=OVERLAP_SECONDS)
            except RuntimeError:
                tokens = merge_longest_common_subsequence(tokens, part.tokens,
                                                          overlap_duration=OVERLAP_SECONDS)
        else:
            tokens = part.tokens
        if end >= total:
            break
    return _result_dict(sentences_to_result(tokens_to_sentences(tokens, config.sentence)))


def sample_rate(engine, model_id: str) -> Optional[int]:
    model = engine._asr_resident.get(model_id)
    return int(model.preprocessor_config.sample_rate) if model is not None else None


async def transcribe(pcm: np.ndarray, model_id: str) -> Dict[str, Any]:
    """Make room in the budget, then run on the engine's MLX thread."""
    from services.mlx_engine import mlx_engine

    if model_id not in mlx_engine._asr_resident:
        await mlx_engine._make_room_for(model_id)
    return await mlx_engine._run(transcribe_on_thread, mlx_engine, pcm, model_id)
