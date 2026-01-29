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

from config import settings
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
    
    v1.1.0: Prefers lightning-whisper-mlx on macOS for 10x faster transcription.
    Falls back to openai-whisper if MLX unavailable.
    """
    global _whisper_model, _whisper_type
    
    if _whisper_model is not None:
        return _whisper_model, _whisper_type
    
    # Try lightning-whisper-mlx first (10x faster on Apple Silicon)
    try:
        from lightning_whisper_mlx import LightningWhisperMLX
        _whisper_model = LightningWhisperMLX(model="base", batch_size=12, quant=None)
        _whisper_type = "mlx"
        print("[Voice] ✓ Lightning Whisper MLX loaded (10x faster on Apple Silicon)")
        return _whisper_model, _whisper_type
    except ImportError:
        print("[Voice] lightning-whisper-mlx not available, trying openai-whisper...")
    except Exception as e:
        print(f"[Voice] MLX Whisper failed: {e}, trying openai-whisper...")
    
    # Fallback to openai-whisper
    try:
        import whisper
        _whisper_model = whisper.load_model("base")
        _whisper_type = "openai"
        print("[Voice] ✓ OpenAI Whisper loaded (fallback)")
        return _whisper_model, _whisper_type
    except Exception as e:
        print(f"[Voice] Failed to load any Whisper model: {e}")
        raise HTTPException(
            status_code=500, 
            detail="Whisper transcription not available. Install lightning-whisper-mlx or openai-whisper."
        )


async def _transcribe_audio(audio_path: str) -> dict:
    """Transcribe audio file using Whisper (MLX or OpenAI).
    
    v1.1.0: Uses lightning-whisper-mlx when available for 10x speedup.
    """
    model, model_type = _get_whisper_model()
    
    # Run in thread pool to not block
    loop = asyncio.get_event_loop()
    
    if model_type == "mlx":
        # Lightning Whisper MLX API
        result = await loop.run_in_executor(
            None,
            lambda: model.transcribe(audio_path)
        )
        # Normalize result format to match openai-whisper
        if isinstance(result, dict):
            return result
        else:
            # MLX returns text directly in some versions
            return {"text": str(result), "language": "en", "segments": []}
    else:
        # OpenAI Whisper API
        result = await loop.run_in_executor(
            None,
            lambda: model.transcribe(audio_path)
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
            from services.rag_engine import rag_engine
            
            # Generate title if not provided
            if not title:
                # Use first few words of transcription
                words = text.split()[:5]
                title = " ".join(words) + "..." if len(words) == 5 else " ".join(words)
                title = f"Voice Note: {title}"
            
            source_id = str(uuid.uuid4())
            char_count = len(text)
            
            # Create source with proper metadata
            source = await source_store.create(
                notebook_id=notebook_id,
                filename=title,
                metadata={
                    "id": source_id,
                    "type": "voice_note",
                    "format": "voice_note",
                    "content": text,
                    "char_count": char_count,
                    "characters": char_count,
                    "duration_seconds": duration,
                    "language": language,
                    "status": "processing",
                    "chunks": 0,
                    "transcribed_at": datetime.utcnow().isoformat()
                }
            )
            
            # Index in RAG
            rag_result = await rag_engine.ingest_document(
                notebook_id=notebook_id,
                source_id=source_id,
                text=text,
                filename=title,
                source_type="voice_note"
            )
            
            # Update source with RAG results
            chunks = rag_result.get("chunks", 0) if rag_result else 0
            await source_store.update(notebook_id, source_id, {
                "chunks": chunks,
                "status": "completed"
            })
        
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
    # Try MLX first
    try:
        from lightning_whisper_mlx import LightningWhisperMLX
        return {
            "available": True,
            "model": "base",
            "backend": "mlx",
            "message": "Lightning Whisper MLX ready (10x faster on Apple Silicon)"
        }
    except ImportError:
        pass
    
    # Try OpenAI Whisper
    try:
        import whisper
        return {
            "available": True,
            "model": "base",
            "backend": "openai",
            "message": "OpenAI Whisper ready"
        }
    except ImportError:
        return {
            "available": False,
            "model": None,
            "backend": None,
            "message": "No Whisper installed. Run: pip install lightning-whisper-mlx"
        }
