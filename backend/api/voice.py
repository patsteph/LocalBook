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

_whisper_model = None
_whisper_type = None  # "mlx" or "openai"


def _get_whisper_model():
    """Lazy load Whisper model.
    
    v1.20: Uses mlx-whisper exclusively (dropped openai-whisper for Python 3.12+).
    """
    global _whisper_model, _whisper_type
    
    if _whisper_model is not None:
        return _whisper_model, _whisper_type
    
    # MLX Whisper — primary and only whisper provider (v1.20+)
    try:
        import mlx_whisper
        _whisper_model = mlx_whisper
        _whisper_type = "mlx"
        print("[Voice] ✓ MLX Whisper loaded")
        return _whisper_model, _whisper_type
    except ImportError:
        print("[Voice] mlx-whisper not installed")
        raise HTTPException(
            status_code=500, 
            detail="Whisper transcription not available. Install with: pip install mlx-whisper"
        )
    except Exception as e:
        print(f"[Voice] MLX Whisper failed: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Whisper transcription failed to load: {str(e)}"
        )


async def _transcribe_audio(audio_path: str) -> dict:
    """Transcribe audio file using MLX Whisper.
    
    v1.20: Uses mlx-whisper exclusively.
    """
    model, model_type = _get_whisper_model()
    
    # Run in thread pool to not block
    loop = asyncio.get_event_loop()
    
    result = await loop.run_in_executor(
        None,
        lambda: model.transcribe(audio_path, path_or_hf_repo="mlx-community/whisper-base-mlx")
    )
    return result


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
    """Check if voice transcription is available."""
    try:
        import mlx_whisper
        return {
            "available": True,
            "model": "base",
            "backend": "mlx",
            "message": "MLX Whisper ready"
        }
    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        print(f"[Voice] mlx_whisper import failed: {e}\n{err_detail}")
        return {
            "available": False,
            "model": None,
            "backend": None,
            "message": f"Whisper import failed: {str(e)}"
        }
