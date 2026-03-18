"""Video API endpoints"""
import logging
import os
import traceback
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional, Literal
from pathlib import Path
from services.video_generator import video_generator
from services.event_logger import log_content_generated
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


class VideoGenerateRequest(BaseModel):
    """Request model for video generation"""
    notebook_id: str
    topic: Optional[str] = None
    duration_minutes: int = 5
    visual_style: str = "classic"
    narrator_gender: str = "female"  # "male" or "female"
    accent: str = "us"               # "us", "uk", "es", "fr", etc.
    voice: Optional[str] = None       # Legacy: direct Kokoro voice ID override
    format_type: Literal["explainer", "brief"] = "explainer"
    chat_context: Optional[str] = None  # Recent chat conversation for "From Chat" mode


class VideoGeneration(BaseModel):
    """Video generation model"""
    video_id: str
    notebook_id: str
    topic: str
    duration_minutes: int
    visual_style: str
    voice: str
    format_type: str
    video_file_path: Optional[str] = None
    duration_seconds: Optional[int] = None
    slide_count: Optional[int] = None
    status: str
    error_message: Optional[str] = None
    created_at: str


def _preflight_check():
    """Verify FFmpeg and Playwright are available before starting pipeline.

    Uses the same Playwright browser detection paths as health_portal.py.
    """
    import shutil
    import sys
    errors = []

    # ── FFmpeg ──
    if not shutil.which("ffmpeg"):
        found = False
        for p in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
            if Path(p).exists():
                found = True
                break
        if not found:
            errors.append("FFmpeg not found. Install with: brew install ffmpeg")

    # ── Playwright: check package, then auto-install browsers if needed ──
    try:
        import playwright  # noqa: F401
    except ImportError:
        errors.append("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return errors

    # Trigger auto-install of Chromium browsers if missing.
    # Without this, a fresh machine would fail the browser check below
    # even though playwright_utils can auto-download Chromium.
    try:
        from services.playwright_utils import ensure_playwright_browsers_path
        ensure_playwright_browsers_path()
    except Exception as e:
        logger.warning(f"[Video] Playwright browser auto-install failed: {e}")

    pw_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    chromium_found = False
    home = Path.home()
    search_paths = []
    if pw_browsers_path:
        search_paths.append(Path(pw_browsers_path))
    search_paths.extend([
        home / "Library" / "Caches" / "ms-playwright",
        home / ".cache" / "ms-playwright",
    ])
    if getattr(sys, 'frozen', False):
        bundle_dir = Path(sys._MEIPASS)
        search_paths.append(bundle_dir / "playwright" / "driver" / "package" / ".local-browsers")
    for sp in search_paths:
        if sp.exists():
            for child in sp.iterdir():
                if child.is_dir() and "chromium" in child.name.lower():
                    chromium_found = True
                    break
        if chromium_found:
            break
    if not chromium_found:
        errors.append("Playwright Chromium browser not found. Run: playwright install chromium")

    return errors


@router.post("/generate", response_model=VideoGeneration)
async def generate_video(request: VideoGenerateRequest):
    """Generate an explainer video from notebook sources"""
    try:
        # Pre-flight: check required tools before queuing background work
        preflight_errors = _preflight_check()
        if preflight_errors:
            raise HTTPException(
                status_code=422,
                detail=f"Video prerequisites missing: {'; '.join(preflight_errors)}"
            )

        logger.info(
            f"[STUDIO] Video generation started for notebook={request.notebook_id}, "
            f"duration={request.duration_minutes}min, style={request.visual_style}"
        )
        result = await video_generator.generate(
            notebook_id=request.notebook_id,
            topic=request.topic,
            duration_minutes=request.duration_minutes,
            visual_style=request.visual_style,
            narrator_gender=request.narrator_gender,
            accent=request.accent,
            voice=request.voice,  # Legacy override — None unless explicitly set
            format_type=request.format_type,
            chat_context=request.chat_context,
        )
        logger.info(f"[STUDIO] Video generation queued: video_id={result.get('video_id', 'unknown')}")
        log_content_generated(request.notebook_id, "video", request.format_type, request.topic or "")
        return result
    except Exception as e:
        logger.error(f"[STUDIO] Video generation failed: {type(e).__name__}: {str(e)}")
        logger.error(f"[STUDIO] Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Video generation failed: {str(e)}")


@router.get("/{notebook_id}")
async def list_videos(notebook_id: str):
    """List all video generations for a notebook"""
    try:
        generations = await video_generator.list(notebook_id)
        return {"generations": generations}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{video_id}")
async def get_video_status(video_id: str):
    """Get status of a video generation"""
    generation = await video_generator.get(video_id)
    if not generation:
        raise HTTPException(status_code=404, detail="Video not found")
    return generation


@router.get("/stream/{video_id}")
async def stream_video(video_id: str, request: Request):
    """Stream/serve the generated video file with HTTP range request support.

    Range requests are required for HTML5 <video> seeking, duration display,
    and reliable playback across browsers (Chrome, Safari, Firefox).
    """
    generation = await video_generator.get(video_id)
    if not generation:
        raise HTTPException(status_code=404, detail="Video not found")

    video_path = generation.get("video_file_path")
    if not video_path or not Path(video_path).exists():
        raise HTTPException(status_code=404, detail="Video file not found")

    file_path = Path(video_path)
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")

    if range_header:
        # Parse Range: bytes=START-END
        range_str = range_header.replace("bytes=", "")
        parts = range_str.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else file_size - 1
        end = min(end, file_size - 1)
        content_length = end - start + 1

        def iter_range():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk_size = min(65536, remaining)
                    data = f.read(chunk_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            iter_range(),
            status_code=206,
            media_type="video/mp4",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
                "Content-Disposition": f'inline; filename="video_{video_id[:8]}.mp4"',
            },
        )

    # No range header — serve full file
    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename=f"video_{video_id[:8]}.mp4",
        headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
    )


@router.delete("/remove/{video_id}")
async def delete_video(video_id: str):
    """Delete a video generation and its files"""
    try:
        deleted = await video_generator.delete(video_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Video not found")
        return {"message": "Video deleted"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/styles/list")
async def list_visual_styles():
    """List available visual styles for video generation, including custom PPTX templates"""
    from templates.slides.styles import list_styles, VISUAL_STYLES, extract_style_from_pptx
    styles = []
    for name in list_styles():
        s = VISUAL_STYLES[name]
        styles.append({
            "id": name,
            "name": name.replace("_", " ").title(),
            "accent_color": s["accent_color"],
            "bg_color": s["bg_color"],
            "is_custom": False,
        })

    # Add custom PPTX templates as style options
    try:
        templates_dir = settings.data_dir / "pptx_templates"
        meta_path = templates_dir / "_meta.json"
        if meta_path.exists():
            import json
            with open(meta_path) as f:
                meta = json.load(f)
            for tpl in meta:
                tpl_path = templates_dir / tpl.get("filename", "")
                if tpl_path.exists():
                    extracted = extract_style_from_pptx(str(tpl_path))
                    styles.append({
                        "id": f"tpl:{tpl['id']}",
                        "name": tpl.get("name", "Custom"),
                        "accent_color": extracted["accent_color"],
                        "bg_color": extracted["bg_color"],
                        "is_custom": True,
                    })
    except Exception as e:
        logger.warning(f"Failed to load custom PPTX templates for video styles: {e}")

    return {"styles": styles}
