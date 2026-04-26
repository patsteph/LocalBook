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
    mode: str = "document"  # "document" or "photo"


@router.post("/process")
async def process_scan(request: ScanProcessRequest):
    """Process a single scanned image synchronously, return the created note."""
    try:
        logger.info(f"[scan] /process request: {request.file_path}")
        result = await scan_pipeline.process_image(
            file_path=request.file_path,
            notebook_id=request.notebook_id,
            mode=request.mode,
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


async def _run_batch_with_reporter(
    *,
    file_paths: List[str],
    notebook_id: Optional[str],
    mode: str,
    reporter: ProgressReporter,
) -> None:
    """Background task: run the batch pipeline, always closing the reporter."""
    try:
        await scan_pipeline.process_batch(
            file_paths=file_paths,
            notebook_id=notebook_id,
            mode=mode,
            reporter=reporter,
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
