"""OpenAI-compatible speech at `/v1/audio/*` (LB-3).

`POST /v1/audio/transcriptions` and `POST /v1/audio/speech`, so a companion that
already speaks the OpenAI API (Jocasta, a Telegram bridge) gets LocalBook's own
Parakeet and Kokoro instead of running a second speech stack.

Same rules as `/v1/chat/completions` (`openai_compat`): a per-companion key, here
with scope `audio`; 401 for an unknown key, 403 for one without the scope; and
anything we cannot honour is refused rather than silently ignored.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

from api.openai_compat import _require_companion_key
from services import audio_codec

router = APIRouter()
logger = logging.getLogger(__name__)

# 25 MB, OpenAI's own limit — also bounds what one request can make us decode.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_INPUT_CHARS = 4096          # OpenAI's `input` limit for speech

# OpenAI's voice names → the nearest Kokoro voice. Kokoro ids pass straight through.
OPENAI_VOICES = {
    "alloy": "af_heart", "nova": "af_nova", "shimmer": "af_sky",
    "echo": "am_michael", "onyx": "am_adam", "fable": "bm_george",
}


@router.post("/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),              # accepted; LocalBook picks the engine
    language: Optional[str] = Form(None),
    response_format: str = Form("json"),
    prompt: Optional[str] = Form(None),              # accepted; Parakeet takes no prompt
    temperature: Optional[float] = Form(None),       # accepted; decoding is greedy
    authorization: Optional[str] = Header(None),
):
    _require_companion_key(authorization, "audio")
    if response_format not in ("json", "text", "verbose_json"):
        raise HTTPException(400, f"response_format {response_format!r} is not supported "
                                 "(json, text, verbose_json)")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "audio is larger than 25 MB")

    from services.speech_to_text import TranscriptionError, transcribe
    try:
        result = await transcribe(data, language=language)
    except audio_codec.CodecError as exc:
        raise HTTPException(400, f"could not read the audio: {exc}")
    except TranscriptionError as exc:
        raise HTTPException(503, f"transcription failed: {exc}")

    if response_format == "text":
        return PlainTextResponse(result["text"])
    if response_format == "json":
        return JSONResponse({"text": result["text"]})
    return JSONResponse({
        "task": "transcribe",
        "language": result.get("language"),
        "duration": result.get("duration"),
        "text": result["text"],
        "segments": [dict(id=i, **s) for i, s in enumerate(result.get("segments") or [])],
    })


class SpeechRequest(BaseModel):
    input: str
    model: Optional[str] = None                      # accepted; the voice is Kokoro
    voice: str = "alloy"
    response_format: str = "mp3"
    speed: float = 1.0
    stream: bool = False
    instructions: Optional[str] = None               # refused if set — see below


def _kokoro_voice(voice: str) -> str:
    return OPENAI_VOICES.get(voice, voice)


# The gap `_crossfade_segments` puts between chunks in a whole file; a stream
# cannot crossfade across a chunk it has already sent, so it gets the pause alone.
_STREAM_PAUSE_S = 0.08


def _chunks_in_thread(text: str, voice: str, speed: float, loop, queue: asyncio.Queue):
    """Run Kokoro's blocking chunk generator on a worker thread, handing each
    chunk to the event loop as it lands. `None` ends the stream; an exception
    object is re-raised on the other side."""
    from services.audio_llm import audio_llm
    try:
        for chunk in audio_llm.tts_chunks(text, voice, speed, first_sentence_alone=True):
            loop.call_soon_threadsafe(queue.put_nowait, chunk)
        loop.call_soon_threadsafe(queue.put_nowait, None)
    except Exception as exc:          # surfaced to the response, not swallowed
        loop.call_soon_threadsafe(queue.put_nowait, exc)


@router.post("/audio/speech")
async def speech(req: SpeechRequest, authorization: Optional[str] = Header(None)):
    _require_companion_key(authorization, "audio")
    fmt = req.response_format
    if fmt not in audio_codec.FORMATS:
        raise HTTPException(400, f"response_format {fmt!r} is not supported "
                                 f"({', '.join(audio_codec.FORMATS)})")
    if req.instructions:
        raise HTTPException(400, "`instructions` (voice steering) is not supported by Kokoro.")
    text = (req.input or "").strip()
    if not text:
        raise HTTPException(400, "input is empty")
    if len(text) > MAX_INPUT_CHARS:
        raise HTTPException(400, f"input is longer than {MAX_INPUT_CHARS} characters")
    if not 0.25 <= req.speed <= 4.0:
        raise HTTPException(400, "speed must be between 0.25 and 4.0")

    from services.audio_llm import SAMPLE_RATE, audio_llm
    if not audio_llm.is_available:
        await audio_llm.initialize()
        if not audio_llm.is_available:
            raise HTTPException(503, "text-to-speech is not available")

    voice = _kokoro_voice(req.voice)
    media_type = audio_codec.MEDIA_TYPES[fmt]

    if not req.stream:
        def _whole() -> bytes:
            import numpy as np
            parts = list(audio_llm.tts_chunks(text, voice, req.speed))
            if not parts:
                raise RuntimeError("no audio was generated")
            pcm = audio_llm._crossfade_segments(parts) if len(parts) > 1 else parts[0]
            return audio_codec.encode(np.asarray(pcm, dtype=np.float32), SAMPLE_RATE, fmt)
        try:
            data = await asyncio.to_thread(_whole)
        except Exception as exc:
            raise HTTPException(503, f"speech synthesis failed: {exc}")
        return Response(content=data, media_type=media_type)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    threading.Thread(target=_chunks_in_thread, name="v1-speech",
                     args=(text, voice, req.speed, loop, queue), daemon=True).start()

    async def _body():
        import numpy as np
        encoder = audio_codec.StreamEncoder(fmt, SAMPLE_RATE)
        pause = np.zeros(int(SAMPLE_RATE * _STREAM_PAUSE_S), dtype=np.float32)
        first = True
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                logger.warning("[v1/speech] synthesis failed mid-stream: %s", item)
                break           # headers are sent; end the audio cleanly where it stopped
            data = encoder.feed(item if first else np.concatenate([pause, item]))
            first = False
            if data:
                yield data
        tail = encoder.close()
        if tail:
            yield tail

    return StreamingResponse(_body(), media_type=media_type)
