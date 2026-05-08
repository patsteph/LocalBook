import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.progress_reporter import ProgressReporter
from services.scan_pipeline import scan_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Single-page (legacy; used by watcher + direct file picker) ───────────────
class ScanProcessRequest(BaseModel):
    file_path: str
    notebook_id: Optional[str] = None
    # Any mode in vision_prompts.MODE_PROMPTS — auto-classifier defaults plus
    # the user-pick specialized modes (recipe, resume, glossary, title_page,
    # calendar, form, map, index_page, collage). Defaults to "document".
    mode: str = "document"
    # Optional post-OCR translation. Set to a language name like 'Spanish'
    # to get a Translation section appended; None / 'none' / 'original' skip it.
    target_language: Optional[str] = None


@router.post("/process")
async def process_scan(request: ScanProcessRequest):
    """Process a single scanned image synchronously, return the created note."""
    try:
        logger.info(f"[scan] /process request: {request.file_path}")
        result = await scan_pipeline.process_image(
            file_path=request.file_path,
            notebook_id=request.notebook_id,
            mode=request.mode,
            target_language=request.target_language,
        )
        return {"status": "success", "note": result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"[scan] /process failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Multi-page batch (Sprint 8 — streams SSE progress) ───────────────────────
class ScanBatchRequest(BaseModel):
    """Body for POST /scan/process-batch.

    file_paths must be absolute paths to images the backend can read. For
    Continuity Camera captures these are under <app_data>/scans/continuity/;
    for manual selections via the file picker they're whatever the user chose.
    Order of the list is preserved in the merged note.
    """
    file_paths: List[str] = Field(..., min_length=1)
    notebook_id: Optional[str] = None
    mode: str = "document"
    # Optional post-OCR translation. Set to a language name like 'Spanish'.
    target_language: Optional[str] = None
    # Optional: append the OCR result to an existing note instead of creating
    # a new one. Falls back to create-new if the note doesn't exist.
    append_to: Optional[str] = None


async def _run_batch_with_reporter(
    *,
    file_paths: List[str],
    notebook_id: Optional[str],
    mode: str,
    reporter: ProgressReporter,
    target_language: Optional[str] = None,
    append_to: Optional[str] = None,
) -> None:
    """Background task: run the batch pipeline, always closing the reporter."""
    try:
        await scan_pipeline.process_batch(
            file_paths=file_paths,
            notebook_id=notebook_id,
            mode=mode,
            reporter=reporter,
            target_language=target_language,
            append_to=append_to,
        )
    except FileNotFoundError as e:
        logger.warning(f"[scan-batch] file missing: {e}")
        await reporter.error(f"File not found: {e}")
    except Exception as e:
        logger.exception(f"[scan-batch] failed: {e}")
        await reporter.error(str(e)[:300])
    finally:
        await reporter.close()


@router.post("/process-batch")
async def process_scan_batch(request: ScanBatchRequest):
    """Process multiple scanned pages and stream per-page progress as SSE.

    Mirrors the shape of POST /sources/upload/stream so the frontend can
    reuse the same EventSource handling. Each event is JSON on a single
    `data:` line:
        {"stage": "page_3_start", "percent": 40, "message": "...", "details": {...}}

    Terminal event is stage="complete" (details include note_id, total_pages,
    chars, title) or stage="error" (details include the failure message).
    """
    logger.info(
        f"[scan] /process-batch: {len(request.file_paths)} pages "
        f"(mode={request.mode}, notebook={request.notebook_id})"
    )

    reporter = ProgressReporter()

    # Fire-and-forget background task; the SSE generator below drains the
    # reporter queue. Matches the /sources/upload/stream pattern exactly.
    asyncio.create_task(
        _run_batch_with_reporter(
            file_paths=list(request.file_paths),
            notebook_id=request.notebook_id,
            mode=request.mode,
            reporter=reporter,
            target_language=request.target_language,
            append_to=request.append_to,
        )
    )

    async def _sse_generator():
        # Initial ping so the client knows the connection is live before the
        # pipeline emits its first real event (model warmup can be slow).
        yield ": ping\n\n"
        while True:
            evt = await reporter.queue.get()
            if evt is None:
                yield "event: done\ndata: {}\n\n"
                return
            yield evt.to_sse()

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Inline OCR (no note created — used to insert into the open editor) ───────
class ScanOcrBatchRequest(BaseModel):
    """Body for POST /scan/ocr-batch.

    Same shape as /scan/process-batch but returns the OCR'd markdown inline
    via SSE instead of creating a new note. The frontend uses this when the
    user is mid-edit in a note and wants the scan content inserted at the
    cursor (Sprint 9 — append-to-open-note flow).
    """
    file_paths: List[str] = Field(..., min_length=1)
    mode: str = "document"
    # Optional post-OCR translation. Set to a language name like 'Spanish'.
    target_language: Optional[str] = None


async def _run_ocr_inline_with_reporter(
    *,
    file_paths: List[str],
    mode: str,
    reporter: ProgressReporter,
    target_language: Optional[str] = None,
) -> None:
    """Background task: run inline OCR pipeline, always closing the reporter."""
    try:
        await scan_pipeline.process_batch_inline(
            file_paths=file_paths,
            mode=mode,
            reporter=reporter,
            target_language=target_language,
        )
    except FileNotFoundError as e:
        logger.warning(f"[scan-ocr] file missing: {e}")
        await reporter.error(f"File not found: {e}")
    except Exception as e:
        logger.exception(f"[scan-ocr] failed: {e}")
        await reporter.error(str(e)[:300])
    finally:
        await reporter.close()


@router.post("/ocr-batch")
async def ocr_scan_batch(request: ScanOcrBatchRequest):
    """OCR multiple scanned pages and stream progress + merged text via SSE.

    Unlike /process-batch, this does NOT create a new note. The terminal
    `complete` event's `details` carries the merged markdown so the frontend
    can insert it directly into the currently-open editor.

    Terminal event shape:
        {"stage": "complete", "percent": 100, "message": "...",
         "details": {"merged_text": "...", "page_texts": [...],
                     "total_pages": N, "chars": M}}
    """
    logger.info(
        f"[scan] /ocr-batch: {len(request.file_paths)} pages (mode={request.mode})"
    )

    reporter = ProgressReporter()
    asyncio.create_task(
        _run_ocr_inline_with_reporter(
            file_paths=list(request.file_paths),
            mode=request.mode,
            reporter=reporter,
            target_language=request.target_language,
        )
    )

    async def _sse_generator():
        yield ": ping\n\n"
        while True:
            evt = await reporter.queue.get()
            if evt is None:
                yield "event: done\ndata: {}\n\n"
                return
            yield evt.to_sse()

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Vision-model warmup ──────────────────────────────────────────────────────
#
# Called by the frontend the moment the user opens the Scan menu so that, by
# the time the camera capture window closes and the image is POSTed, the
# vision model is already resident in Ollama (a cold load on Granite-3.3-2B
# can take 5-15 s on a Mac mini and 2-4 s on M-series laptops, which is the
# difference between "instant feedback" and "I think something is broken").
@router.post("/warmup")
async def warmup_vision_model():
    """Best-effort: send a no-op generate to the vision model so Ollama loads it.

    Returns immediately on success or after a short timeout — the frontend
    fires this fire-and-forget, so we never want to block the UI on it.
    """
    import os as _os
    from config import settings as _settings
    import httpx

    model = _os.getenv("LOCALBOOK_VISION_MODEL") or _settings.vision_model
    base = _settings.ollama_base_url.rstrip("/")
    try:
        # /api/generate with empty prompt is the cheapest way to force a load.
        # keep_alive matches the rest of the codebase (5m).
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base}/api/generate",
                json={"model": model, "prompt": "", "keep_alive": "5m"},
            )
        if resp.status_code != 200:
            logger.warning(f"[scan] vision warmup non-200: {resp.status_code} {resp.text[:200]}")
            return {"status": "warning", "model": model, "code": resp.status_code}
        return {"status": "ok", "model": model}
    except Exception as e:
        # Never raise — warmup is purely an optimization.
        logger.warning(f"[scan] vision warmup failed: {e}")
        return {"status": "error", "model": model, "message": str(e)[:200]}
