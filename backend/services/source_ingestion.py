"""Shared source ingestion helper — standardizes the create→ingest→update pattern.

Every ingestion path (document upload, browser capture, web scrape, voice note)
should follow the same workflow:
1. Create source with status="processing", chunks=0
2. Call rag_engine.ingest_document()
3. Update source with chunks, status="completed", content
4. Optionally extract content_date and store it
5. Log the document capture event

This module provides that workflow as a single function so new paths
don't need to reimplement it, and existing paths can migrate over time.
"""
import logging
from typing import Dict, Any, Optional

from storage.source_store import source_store
from services.rag_engine import rag_engine
from services.content_date_extractor import extract_content_date
from services.event_logger import log_document_captured

logger = logging.getLogger(__name__)


async def create_and_ingest_source(
    notebook_id: str,
    filename: str,
    text: str,
    source_type: str = "document",
    url: Optional[str] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
    content_for_date_extraction: Optional[str] = None,
    source_id_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Standard source creation + RAG ingestion pipeline.

    Args:
        notebook_id: Target notebook
        filename: Display name / filename for the source
        text: Full text content to ingest
        source_type: e.g. "web", "voice_note", "pdf", "youtube"
        url: Optional URL for web sources
        extra_metadata: Additional fields to store on the source record
        content_for_date_extraction: Text snippet for date extraction (defaults to text[:800])
        source_id_override: Pre-generated source ID (if caller needs it upfront)

    Returns:
        Dict with source_id, chunks, characters, status, content_date (if found)
    """
    # Extract content_date
    date_text = content_for_date_extraction or (text[:800] if text else "")
    content_date = None
    try:
        content_date = extract_content_date(filename, date_text)
    except Exception:
        pass

    # Build metadata
    metadata: Dict[str, Any] = {
        "type": source_type,
        "format": source_type,
        "status": "processing",
        "chunks": 0,
        "characters": 0,
    }
    if url:
        metadata["url"] = url
    if content_date:
        metadata["content_date"] = content_date
    if source_id_override:
        metadata["id"] = source_id_override
    if extra_metadata:
        metadata.update(extra_metadata)

    # 1. Create source record
    source = await source_store.create(
        notebook_id=notebook_id,
        filename=filename,
        metadata=metadata,
    )
    sid = source["id"]

    # 2. Ingest into RAG
    try:
        result = await rag_engine.ingest_document(
            notebook_id=notebook_id,
            source_id=sid,
            text=text,
            filename=filename,
            source_type=source_type,
        )

        chunks = result.get("chunks", 0)
        characters = result.get("characters", len(text))

        # 3. Update source with results
        await source_store.update(notebook_id, sid, {
            "chunks": chunks,
            "characters": characters,
            "status": "completed",
            "content": text,
        })

        logger.info(f"[SourceIngestion] {filename}: {chunks} chunks, {characters} chars")

    except Exception as e:
        logger.error(f"[SourceIngestion] Failed to ingest {filename}: {e}")
        await source_store.update(notebook_id, sid, {
            "status": "failed",
            "error": str(e)[:200],
        })
        raise

    # 4. Log event
    try:
        log_document_captured(notebook_id, url or filename, filename, source_type)
    except Exception:
        pass

    return {
        "source_id": sid,
        "chunks": chunks,
        "characters": characters,
        "status": "completed",
        "content_date": content_date,
    }
