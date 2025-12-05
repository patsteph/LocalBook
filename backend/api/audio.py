"""Audio API endpoints"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from pathlib import Path
from services.audio_generator import audio_service
from config import settings

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
        result = await audio_service.generate(
            notebook_id=request.notebook_id,
            topic=request.topic,
            duration_minutes=request.duration_minutes,
            skill_id=request.skill_id,
            host1_gender=request.host1_gender,
            host2_gender=request.host2_gender,
            accent=request.accent
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# NOTE: /download route must come BEFORE /{notebook_id} to avoid route conflicts
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
