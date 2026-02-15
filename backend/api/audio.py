"""Audio API endpoints"""
import logging
import traceback
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
from services.audio_generator import audio_service
from services.event_logger import log_content_generated
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


class AudioGenerateRequest(BaseModel):
    """Request model for audio generation - matches frontend AudioGenerateRequest"""
    notebook_id: str
    topic: Optional[str] = None
    duration_minutes: int = 10
    skill_id: Optional[str] = None
    host1_gender: str = "male"
    host2_gender: str = "female"
    accent: str = "us"


class AudioGeneration(BaseModel):
    """Audio generation model - matches frontend AudioGeneration interface"""
    audio_id: str
    notebook_id: str
    script: str
    audio_file_path: Optional[str] = None
    duration_seconds: Optional[int] = None
    status: str  # 'pending', 'processing', 'completed', 'failed'
    error_message: Optional[str] = None
    created_at: str


@router.post("/generate", response_model=AudioGeneration)
async def generate_audio(request: AudioGenerateRequest):
    """Generate podcast audio from notebook"""
    try:
        logger.info(f"[STUDIO] Podcast generation started for notebook={request.notebook_id}, duration={request.duration_minutes}min")
        result = await audio_service.generate(
            notebook_id=request.notebook_id,
            topic=request.topic,
            duration_minutes=request.duration_minutes,
            skill_id=request.skill_id,
            host1_gender=request.host1_gender,
            host2_gender=request.host2_gender,
            accent=request.accent
        )
        logger.info(f"[STUDIO] Podcast generation completed: audio_id={result.get('audio_id', 'unknown')}")
        log_content_generated(request.notebook_id, "audio", request.skill_id or "podcast", request.topic or "")
        return result
    except Exception as e:
        logger.error(f"[STUDIO] Podcast generation failed for notebook={request.notebook_id}")
        logger.error(f"[STUDIO] Error: {type(e).__name__}: {str(e)}")
        logger.error(f"[STUDIO] Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Podcast generation failed: {str(e)}")


# NOTE: Specific routes must come BEFORE /{notebook_id} to avoid route conflicts
@router.delete("/remove/{audio_id}")
async def delete_audio(audio_id: str):
    """Delete an audio generation and its files"""
    import shutil
    try:
        audio_dir = settings.data_dir / "audio"
        deleted_files = []
        if audio_dir.exists():
            for file_path in audio_dir.glob(f"{audio_id}*"):
                if file_path.is_dir():
                    shutil.rmtree(file_path, ignore_errors=True)
                else:
                    file_path.unlink(missing_ok=True)
                deleted_files.append(str(file_path.name))
        
        deleted = await audio_service.delete(audio_id)
        
        if not deleted and not deleted_files:
            raise HTTPException(status_code=404, detail="Audio not found")
        
        return {"message": "Audio deleted", "files_removed": deleted_files}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download/{audio_id}")
async def download_audio(audio_id: str):
    """Download audio file"""
    from config import settings
    
    audio = await audio_service.get_by_id(audio_id)
    if not audio:
        raise HTTPException(status_code=404, detail="Audio not found")
    
    # Try stored path first
    file_path = None
    if audio.get("audio_file_path"):
        stored_path = Path(audio["audio_file_path"])
        if stored_path.exists():
            file_path = stored_path
    
    # If stored path doesn't work, search for the file
    if not file_path:
        audio_dir = settings.data_dir / "audio"
        for ext in [".m4a", ".mp3", ".aiff", ".wav"]:
            candidate = audio_dir / f"{audio_id}{ext}"
            if candidate.exists():
                file_path = candidate
                break
    
    if not file_path:
        raise HTTPException(status_code=404, detail="Audio file not found")
    
    # Determine media type based on file extension
    ext = file_path.suffix.lower()
    media_types = {
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aiff": "audio/aiff",
        ".wav": "audio/wav"
    }
    media_type = media_types.get(ext, "audio/mpeg")
    
    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=f"podcast_{audio_id}{ext}"
    )


@router.get("/{notebook_id}")
async def list_audio(notebook_id: str):
    """List all audio files for a notebook"""
    audio_files = await audio_service.list(notebook_id)
    return audio_files


@router.get("/{notebook_id}/{audio_id}")
async def get_audio(notebook_id: str, audio_id: str):
    """Get audio file info"""
    audio = await audio_service.get(notebook_id, audio_id)
    if not audio:
        raise HTTPException(status_code=404, detail="Audio not found")
    return audio


