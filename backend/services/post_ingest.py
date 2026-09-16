"""Everything a source gets AFTER it is chunked and embedded.

One capability, one implementation. Before this module the "full treatment"
lived inside `api/sources.py` — twice, in two slightly different forms (the
synchronous upload path and the large-file background path) — which meant every
OTHER way content enters LocalBook was a fresh chance to forget a step. Linked
Folders found exactly that: files ingested correctly, chunked and embedded and
searchable, but never tagged, with no timeline events and no capture record.
Nothing announced the omission, because nothing downstream knows what it was
supposed to receive.

So the post-ingest steps live here, in one place, and every ingest path calls
this. Adding a fifth step means editing one function.

Every step is NON-FATAL and independent. The source is already ingested and
useful by the time we arrive; a tagging failure must never mark a good source
bad, and one step failing must not skip the rest.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

class _NoText(Exception):
    """Internal: there is no content to derive tags or dates from."""


# Formats whose images are worth a vision pass.
_IMAGE_BEARING = {"pdf", "pptx"}


async def finalize_source(
    notebook_id: str,
    source_id: str,
    filename: str,
    text: str,
    *,
    raw_bytes: Optional[bytes] = None,
    origin: str = "upload",
    notify: bool = True,
    defer: Optional[Callable] = None,
) -> dict:
    """Apply the full post-ingest treatment. Returns what actually happened.

    `origin` is recorded on the capture event, so "where did this source come
    from" stays answerable later ("upload", "linked_folder", "browser", …).

    `defer` is an optional scheduler — FastAPI's `BackgroundTasks.add_task` —
    for callers holding an HTTP connection they need to release. When None the
    work is awaited inline, which is what every background caller wants.
    """
    done = {"tagged": False, "timeline": False, "images": False,
            "notified": False, "logged": False, "tags": []}

    # REFUSE nonsense rather than performing it. 2026-09-16: a caller read the
    # wrong key off `document_processor.process` and passed source_id=None with
    # empty text. Every step below "succeeded" against a source that did not
    # exist, nothing raised, nothing logged, and the user got an untagged file
    # with no trace of why. Silence about missing input is the worst outcome
    # available here — it is indistinguishable from working.
    if not source_id:
        logger.error(
            f"[post-ingest] refusing to finalise {filename!r}: no source id. "
            f"The caller did not get an id back from ingestion."
        )
        done["error"] = "no source id"
        return done
    if not (text or "").strip():
        logger.warning(
            f"[post-ingest] {filename}: no text to work from — tags and timeline "
            f"would be generated from nothing, so both are skipped."
        )
        done["error"] = "no text"

    # ── tags ────────────────────────────────────────────────────────────
    # First, because tags are what the user SEES immediately in the source
    # list — an untagged source looks like a half-finished one.
    has_text = bool((text or "").strip())
    try:
        if not has_text:
            raise _NoText()
        from services.auto_tagger import auto_tagger
        tags = await auto_tagger.tag_source_in_notebook(
            notebook_id, source_id, filename, (text or "")[:3000]
        )
        done["tagged"] = True
        done["tags"] = list(tags or [])
    except _NoText:
        pass
    except Exception as e:
        logger.warning(f"[post-ingest] auto-tag failed for {filename} (non-fatal): {e}")

    # ── timeline ────────────────────────────────────────────────────────
    try:
        if not has_text:
            raise _NoText()
        from api.timeline import extract_timeline_for_source
        if defer:
            defer(extract_timeline_for_source, notebook_id, source_id, text, filename)
        else:
            await extract_timeline_for_source(notebook_id, source_id, text, filename)
        done["timeline"] = True
    except _NoText:
        pass
    except Exception as e:
        logger.warning(f"[post-ingest] timeline failed for {filename} (non-fatal): {e}")

    # ── images ──────────────────────────────────────────────────────────
    # SPAWN, never await: vision descriptions of an image-heavy PDF take many
    # minutes, and the text is already usable. Awaiting here is what produced
    # "PDF stuck in processing".
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if raw_bytes and ext in _IMAGE_BEARING:
        try:
            from services.document_processor import document_processor
            from utils.tasks import safe_create_task
            safe_create_task(
                document_processor.process_images_background(
                    raw_bytes, notebook_id, source_id, filename
                ),
                name=f"image-ocr-{source_id}",
            )
            done["images"] = True
        except Exception as e:
            logger.warning(f"[post-ingest] image pass failed for {filename}: {e}")

    # ── tell the UI ─────────────────────────────────────────────────────
    if notify:
        try:
            from api.constellation_ws import notify_source_updated
            await notify_source_updated({
                "notebook_id": notebook_id,
                "source_id": source_id,
                "status": "completed",
                "title": filename,
                "tags": done["tags"],
            })
            done["notified"] = True
        except Exception as e:
            logger.debug(f"[post-ingest] notify failed: {e}")

    # ── capture event ───────────────────────────────────────────────────
    try:
        from services.event_logger import log_document_captured
        log_document_captured(notebook_id, filename, filename, origin)
        done["logged"] = True
    except Exception as e:
        logger.debug(f"[post-ingest] capture log failed: {e}")

    skipped = [k for k in ("tagged", "timeline") if not done[k]]
    if skipped:
        logger.warning(f"[post-ingest] {filename}: incomplete — {', '.join(skipped)} did not run")
    else:
        # Log the success too. "No warnings" was indistinguishable from "never
        # called" during the 2026-09-16 investigation, which cost real time.
        logger.info(f"[post-ingest] {filename}: tagged {done['tags']}, timeline ok ({origin})")
    return done
