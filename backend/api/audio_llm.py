"""Audio LLM API endpoints

API for Kokoro-82M TTS, mlx-whisper ASR, and decomposed speech-to-speech.
"""

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import tempfile
import os

router = APIRouter(prefix="/audio-llm", tags=["audio-llm"])


class TTSRequest(BaseModel):
    """Text-to-speech request."""
    text: str
    voice: str = "af_heart"
    speed: float = 1.0


class TTSResponse(BaseModel):
    """Text-to-speech response."""
    success: bool
    audio_path: Optional[str] = None
    error: Optional[str] = None


class ASRResponse(BaseModel):
    """Automatic speech recognition response."""
    success: bool
    text: Optional[str] = None
    error: Optional[str] = None


class S2SRequest(BaseModel):
    """Speech-to-speech request config."""
    system_prompt: str = "You are a helpful assistant."
    voice: str = "af_heart"


class S2SResponse(BaseModel):
    """Speech-to-speech response."""
    success: bool
    response_text: Optional[str] = None
    audio_path: Optional[str] = None
    error: Optional[str] = None


class PodcastAudioRequest(BaseModel):
    """Generate audio for podcast script."""
    script: str
    voice: str = "bm_george"


@router.get("/status")
async def get_audio_llm_status():
    """Check if Kokoro TTS + mlx-whisper ASR are available."""
    from services.audio_llm import check_audio_llm_available
    return await check_audio_llm_available()


@router.get("/voices")
async def get_available_voices():
    """Return full Kokoro voice catalog with language and gender info."""
    from services.audio_llm import KOKORO_VOICES, VOICE_ALIASES, SUPPORTED_LANGUAGES
    return {
        "voices": KOKORO_VOICES,
        "aliases": VOICE_ALIASES,
        "languages": SUPPORTED_LANGUAGES,
    }


@router.post("/tts", response_model=TTSResponse)
async def text_to_speech(request: TTSRequest):
    """Convert text to speech using Kokoro-82M."""
    try:
        from services.audio_llm import audio_llm
        
        audio_path = await audio_llm.text_to_speech(
            text=request.text,
            voice=request.voice,
            speed=request.speed,
        )
        
        return TTSResponse(success=True, audio_path=audio_path)
    except Exception as e:
        return TTSResponse(success=False, error=str(e))


@router.post("/asr", response_model=ASRResponse)
async def automatic_speech_recognition(audio: UploadFile = File(...)):
    """Transcribe audio to text using mlx-whisper."""
    try:
        from services.audio_llm import audio_llm
        
        # Save uploaded audio to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            content = await audio.read()
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            text = await audio_llm.transcribe(tmp_path)
            return ASRResponse(success=True, text=text)
        finally:
            os.unlink(tmp_path)
            
    except Exception as e:
        return ASRResponse(success=False, error=str(e))


@router.post("/speech-to-speech", response_model=S2SResponse)
async def speech_to_speech(
    audio: UploadFile = File(...),
    system_prompt: str = "You are a helpful assistant.",
    voice: str = "af_heart"
):
    """Speech-to-speech: mlx-whisper ASR → Ollama LLM → Kokoro TTS."""
    try:
        from services.audio_llm import audio_llm
        
        # Save uploaded audio to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            content = await audio.read()
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            response_text, audio_path = await audio_llm.speech_to_speech(
                audio_path=tmp_path,
                system_prompt=system_prompt,
                voice=voice
            )
            
            return S2SResponse(
                success=True,
                response_text=response_text,
                audio_path=audio_path
            )
        finally:
            os.unlink(tmp_path)
            
    except Exception as e:
        return S2SResponse(success=False, error=str(e))


@router.post("/podcast-audio", response_model=TTSResponse)
async def generate_podcast_audio(request: PodcastAudioRequest):
    """Generate audio for a podcast script."""
    try:
        from services.audio_llm import audio_llm
        
        audio_path = await audio_llm.generate_podcast_audio(
            script=request.script,
            voice=request.voice
        )
        
        return TTSResponse(success=True, audio_path=audio_path)
    except Exception as e:
        return TTSResponse(success=False, error=str(e))


@router.get("/file/{filename}")
async def get_audio_file(filename: str):
    """Serve generated audio file."""
    from config import settings
    from pathlib import Path
    
    audio_dir = Path(settings.data_dir) / "audio_output"
    file_path = audio_dir / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    
    return FileResponse(
        path=str(file_path),
        media_type="audio/wav",
        filename=filename
    )
