"""Voice Notes API endpoints

Provides voice recording transcription using Whisper (local).
Transcribed text is automatically added as a source to the notebook.
"""
import asyncio
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from storage.source_store import source_store


router = APIRouter(prefix="/voice", tags=["voice"])


# =============================================================================
# Models
# =============================================================================

class TranscriptionResult(BaseModel):
    text: str
    duration_seconds: float
    language: Optional[str] = None
    source_id: Optional[str] = None  # If added as source


class VoiceNoteCreate(BaseModel):
    notebook_id: str
    title: Optional[str] = None
    add_as_source: bool = True


# =============================================================================
# Whisper Transcription (v1.1.0: MLX-accelerated on Apple Silicon)
# =============================================================================

async def _transcribe_audio(audio_path: str) -> dict:
    """Transcribe through the one speech-to-text service (LB-3): Parakeet v3,
    whisper as fallback, audio decoded in-process — no ffmpeg on PATH needed."""
    from services.speech_to_text import transcribe

    try:
        return await transcribe(audio_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")


# =============================================================================
# API Endpoints
# =============================================================================

@router.post("/transcribe", response_model=TranscriptionResult)
async def transcribe_audio(
    file: UploadFile = File(...),
    notebook_id: str = Form(...),
    title: Optional[str] = Form(None),
    add_as_source: bool = Form(True)
):
    """Transcribe an audio file and optionally add as source.
    
    Accepts: mp3, wav, m4a, webm, ogg audio files
    """
    # Validate file type
    allowed_extensions = {'.mp3', '.wav', '.m4a', '.webm', '.ogg', '.flac'}
    file_ext = Path(file.filename).suffix.lower() if file.filename else '.wav'
    
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format. Allowed: {', '.join(allowed_extensions)}"
        )
    
    # Save to temp file
    temp_dir = Path(tempfile.gettempdir())
    temp_path = temp_dir / f"voice_{uuid.uuid4()}{file_ext}"
    
    try:
        # Write uploaded file
        content = await file.read()
        temp_path.write_bytes(content)
        
        # Transcribe
        result = await _transcribe_audio(str(temp_path))
        
        text = result.get("text", "").strip()
        language = result.get("language", "en")
        
        # Estimate duration from segments
        segments = result.get("segments", [])
        duration = segments[-1]["end"] if segments else 0.0
        
        source_id = None
        
        # Add as source if requested
        if add_as_source and text:
            from services.source_ingestion import create_and_ingest_source
            
            # Generate title if not provided
            if not title:
                # Use first few words of transcription
                words = text.split()[:5]
                title = " ".join(words) + "..." if len(words) == 5 else " ".join(words)
                title = f"Voice Note: {title}"
            
            result_src = await create_and_ingest_source(
                notebook_id=notebook_id,
                filename=title,
                text=text,
                source_type="voice_note",
                extra_metadata={
                    "duration_seconds": duration,
                    "language": language,
                    "transcribed_at": datetime.utcnow().isoformat(),
                },
            )
            source_id = result_src["source_id"]

        return TranscriptionResult(
            text=text,
            duration_seconds=duration,
            language=language,
            source_id=source_id
        )
        
    finally:
        # Cleanup temp file
        if temp_path.exists():
            temp_path.unlink()


@router.post("/transcribe-quick")
async def transcribe_quick(
    file: UploadFile = File(...),
):
    """Quick transcription without adding to notebook.
    
    Useful for previewing transcription before saving.
    """
    allowed_extensions = {'.mp3', '.wav', '.m4a', '.webm', '.ogg', '.flac'}
    file_ext = Path(file.filename).suffix.lower() if file.filename else '.wav'
    
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format. Allowed: {', '.join(allowed_extensions)}"
        )
    
    temp_dir = Path(tempfile.gettempdir())
    temp_path = temp_dir / f"voice_{uuid.uuid4()}{file_ext}"
    
    try:
        content = await file.read()
        temp_path.write_bytes(content)
        
        result = await _transcribe_audio(str(temp_path))
        
        return {
            "text": result.get("text", "").strip(),
            "language": result.get("language", "en"),
            "segments": result.get("segments", [])[:10]  # First 10 segments
        }
        
    finally:
        if temp_path.exists():
            temp_path.unlink()


@router.get("/status")
async def get_voice_status():
    """Is voice transcription available? Codec + both engines importable."""
    from config import settings
    from services.audio_codec import codec_ok

    problems = []
    if not codec_ok():
        problems.append("audio codec (PyAV) unavailable")
    try:
        import parakeet_mlx  # noqa: F401
    except Exception as e:
        problems.append(f"parakeet-mlx: {e}")
    return {
        "available": codec_ok(),
        "model": settings.stt_model,
        "fallback": settings.stt_fallback_model,
        "backend": "mlx",
        "message": "; ".join(problems) or "Speech-to-text ready",
    }
