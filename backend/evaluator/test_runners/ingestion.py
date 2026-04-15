"""Ingestion test runner — creates test notebook and ingests all test content.

Handles PDF, PPTX, DOCX uploads, YouTube/web URL additions, and note creation.
Waits for all sources to reach 'completed' status before returning.
"""

import asyncio
import time
from pathlib import Path
from evaluator.models import EvalResult, IngestionResult, _score_to_grade
import logging
logger = logging.getLogger(__name__)

EVALUATOR_DIR = Path(__file__).parent.parent


async def create_test_notebook(config: dict) -> str:
    """Create the test notebook. Returns notebook_id."""
    from storage.notebook_store import notebook_store

    notebook = await notebook_store.create(
        title=config["notebook_name"],
        description=config.get("notebook_description", "LLM Evaluator test notebook"),
    )
    notebook_id = notebook["id"]
    print(f"[EVAL-INGEST] Created test notebook: {notebook_id}")
    return notebook_id


async def ingest_all_content(notebook_id: str, config: dict) -> IngestionResult:
    """Ingest all test content into the notebook. Returns IngestionResult."""
    from storage.source_store import source_store
    from services.document_processor import document_processor
    from services.rag_engine import rag_engine
    from services.web_scraper import web_scraper

    sources_config = config["content_sources"]
    result = IngestionResult()
    start_time = time.time()

    source_ids = []

    # ── Upload files (PDF, PPTX, DOCX) ───────────────────────────────────
    for file_type in ["pdf", "pptx", "docx"]:
        if file_type not in sources_config:
            continue

        src_config = sources_config[file_type]
        file_path = EVALUATOR_DIR / src_config["file"]

        if not file_path.exists():
            print(f"[EVAL-INGEST] WARN: Test file not found: {file_path}")
            result.per_source.append({
                "type": file_type,
                "file": str(file_path),
                "status": "failed",
                "error": "File not found — run generate_test_files.py first",
                "time_ms": 0,
            })
            result.sources_failed += 1
            result.sources_attempted += 1
            continue

        result.sources_attempted += 1
        src_start = time.time()

        try:
            content = file_path.read_bytes()
            filename = file_path.name

            proc_result = await document_processor.process(
                content=content,
                filename=filename,
                notebook_id=notebook_id,
            )

            source_id = proc_result.get("source_id", "")
            chunks = proc_result.get("chunks", 0)
            characters = proc_result.get("characters", 0)

            source_ids.append(source_id)
            result.sources_completed += 1
            result.total_chunks += chunks
            result.total_characters += characters

            elapsed = (time.time() - src_start) * 1000
            result.per_source.append({
                "type": file_type,
                "file": filename,
                "source_id": source_id,
                "status": "completed",
                "chunks": chunks,
                "characters": characters,
                "time_ms": round(elapsed, 1),
            })
            print(f"[EVAL-INGEST] {file_type.upper()} ingested: {chunks} chunks, {characters} chars ({elapsed:.0f}ms)")

        except Exception as e:
            elapsed = (time.time() - src_start) * 1000
            result.sources_failed += 1
            result.per_source.append({
                "type": file_type,
                "file": str(file_path.name),
                "status": "failed",
                "error": str(e)[:200],
                "time_ms": round(elapsed, 1),
            })
            print(f"[EVAL-INGEST] {file_type.upper()} FAILED: {e}")

    # ── YouTube video ────────────────────────────────────────────────────
    if "youtube" in sources_config:
        yt_config = sources_config["youtube"]
        result.sources_attempted += 1
        src_start = time.time()

        try:
            # Scrape YouTube transcript
            scraped = await web_scraper.scrape_urls([yt_config["url"]])

            if scraped and scraped[0].get("success") and scraped[0].get("text"):
                text = scraped[0]["text"]
                title = scraped[0].get("title", yt_config.get("title", "YouTube Video"))

                # Create source record
                source = await source_store.create(
                    notebook_id=notebook_id,
                    filename=title,
                    metadata={"type": "youtube", "format": "web", "url": yt_config["url"], "status": "processing"}
                )

                # Ingest into RAG
                rag_result = await rag_engine.ingest_document(
                    notebook_id=notebook_id,
                    source_id=source["id"],
                    text=text,
                    filename=title,
                    source_type="youtube",
                )

                chunks = rag_result.get("chunks", 0)
                characters = rag_result.get("characters", len(text))

                await source_store.update(notebook_id, source["id"], {
                    "chunks": chunks, "characters": characters,
                    "status": "completed", "content": text,
                })

                source_ids.append(source["id"])
                result.sources_completed += 1
                result.total_chunks += chunks
                result.total_characters += characters

                elapsed = (time.time() - src_start) * 1000
                result.per_source.append({
                    "type": "youtube",
                    "url": yt_config["url"],
                    "source_id": source["id"],
                    "status": "completed",
                    "chunks": chunks,
                    "characters": characters,
                    "time_ms": round(elapsed, 1),
                })
                print(f"[EVAL-INGEST] YouTube ingested: {chunks} chunks, {characters} chars ({elapsed:.0f}ms)")
            else:
                raise ValueError("YouTube scrape returned no content")

        except Exception as e:
            elapsed = (time.time() - src_start) * 1000
            result.sources_failed += 1
            result.per_source.append({
                "type": "youtube",
                "url": yt_config["url"],
                "status": "failed",
                "error": str(e)[:200],
                "time_ms": round(elapsed, 1),
            })
            print(f"[EVAL-INGEST] YouTube FAILED: {e}")

    # ── Web scrape ───────────────────────────────────────────────────────
    if "web" in sources_config:
        web_config = sources_config["web"]
        result.sources_attempted += 1
        src_start = time.time()

        try:
            scraped = await web_scraper.scrape_urls([web_config["url"]])

            if scraped and scraped[0].get("success") and scraped[0].get("text"):
                text = scraped[0]["text"]
                title = scraped[0].get("title", web_config.get("title", "Web Article"))

                source = await source_store.create(
                    notebook_id=notebook_id,
                    filename=title,
                    metadata={"type": "web", "format": "web", "url": web_config["url"], "status": "processing"}
                )

                rag_result = await rag_engine.ingest_document(
                    notebook_id=notebook_id,
                    source_id=source["id"],
                    text=text,
                    filename=title,
                    source_type="web",
                )

                chunks = rag_result.get("chunks", 0)
                characters = rag_result.get("characters", len(text))

                await source_store.update(notebook_id, source["id"], {
                    "chunks": chunks, "characters": characters,
                    "status": "completed", "content": text,
                })

                source_ids.append(source["id"])
                result.sources_completed += 1
                result.total_chunks += chunks
                result.total_characters += characters

                elapsed = (time.time() - src_start) * 1000
                result.per_source.append({
                    "type": "web",
                    "url": web_config["url"],
                    "source_id": source["id"],
                    "status": "completed",
                    "chunks": chunks,
                    "characters": characters,
                    "time_ms": round(elapsed, 1),
                })
                print(f"[EVAL-INGEST] Web scrape ingested: {chunks} chunks, {characters} chars ({elapsed:.0f}ms)")
            else:
                raise ValueError("Web scrape returned no content")

        except Exception as e:
            elapsed = (time.time() - src_start) * 1000
            result.sources_failed += 1
            result.per_source.append({
                "type": "web",
                "url": web_config["url"],
                "status": "failed",
                "error": str(e)[:200],
                "time_ms": round(elapsed, 1),
            })
            print(f"[EVAL-INGEST] Web scrape FAILED: {e}")

    # ── Note creation ────────────────────────────────────────────────────
    if "note" in sources_config:
        note_config = sources_config["note"]
        result.sources_attempted += 1
        src_start = time.time()

        try:
            title = note_config["title"]
            text = note_config["content"]

            source = await source_store.create(
                notebook_id=notebook_id,
                filename=title,
                metadata={"type": "note", "format": "markdown", "size": len(text), "status": "processing"}
            )

            rag_result = await rag_engine.ingest_document(
                notebook_id=notebook_id,
                source_id=source["id"],
                text=text,
                filename=title,
                source_type="note",
            )

            chunks = rag_result.get("chunks", 0)
            characters = rag_result.get("characters", len(text))

            await source_store.update(notebook_id, source["id"], {
                "chunks": chunks, "characters": characters,
                "status": "completed", "content": text,
            })

            source_ids.append(source["id"])
            result.sources_completed += 1
            result.total_chunks += chunks
            result.total_characters += characters

            elapsed = (time.time() - src_start) * 1000
            result.per_source.append({
                "type": "note",
                "title": title,
                "source_id": source["id"],
                "status": "completed",
                "chunks": chunks,
                "characters": characters,
                "time_ms": round(elapsed, 1),
            })
            print(f"[EVAL-INGEST] Note created: {chunks} chunks, {characters} chars ({elapsed:.0f}ms)")

        except Exception as e:
            elapsed = (time.time() - src_start) * 1000
            result.sources_failed += 1
            result.per_source.append({
                "type": "note",
                "title": note_config.get("title", ""),
                "status": "failed",
                "error": str(e)[:200],
                "time_ms": round(elapsed, 1),
            })
            print(f"[EVAL-INGEST] Note creation FAILED: {e}")

    # ── Compute ingestion totals ─────────────────────────────────────────
    result.ingestion_time_ms = (time.time() - start_time) * 1000

    # Score: based on completion rate and chunk counts
    if result.sources_attempted > 0:
        completion_rate = result.sources_completed / result.sources_attempted
        chunk_score = min(100, (result.total_chunks / max(1, result.sources_completed)) * 10)
        result.score = round(completion_rate * 70 + (chunk_score / 100) * 30, 1)
    else:
        result.score = 0.0

    result.grade = _score_to_grade(result.score)

    print(f"[EVAL-INGEST] Complete: {result.sources_completed}/{result.sources_attempted} sources, "
          f"{result.total_chunks} chunks, {result.total_characters} chars, "
          f"score={result.score} ({result.grade}), {result.ingestion_time_ms:.0f}ms")

    return result


async def cleanup_test_notebook(notebook_id: str):
    """Delete the test notebook and all its data."""
    from storage.notebook_store import notebook_store
    from storage.source_store import source_store
    from services.rag_engine import rag_engine
    import shutil
    from config import settings

    try:
        # Delete RAG data (LanceDB table)
        try:
            sources = await source_store.list(notebook_id)
            for source in sources:
                try:
                    await rag_engine.delete_source(notebook_id, source["id"])
                except Exception as _e:
                    logger.warning(f"[ingestion] {type(_e).__name__}: {_e}")
        except Exception as _e:
            logger.warning(f"[ingestion] {type(_e).__name__}: {_e}")

        # Delete sources
        try:
            await source_store.delete_all(notebook_id)
        except Exception as _e:
            logger.warning(f"[ingestion] {type(_e).__name__}: {_e}")

        # Delete notebook data directory
        try:
            nb_dir = Path(settings.data_dir) / "notebooks" / notebook_id
            if nb_dir.exists():
                shutil.rmtree(nb_dir)
        except Exception as _e:
            logger.debug(f"[ingestion] {type(_e).__name__}: {_e}")

        # Delete notebook record
        await notebook_store.delete(notebook_id)
        print(f"[EVAL-CLEANUP] Deleted test notebook: {notebook_id}")

    except Exception as e:
        print(f"[EVAL-CLEANUP] Cleanup error (non-fatal): {e}")
