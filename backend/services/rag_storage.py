"""
RAG Storage — LanceDB table management, document ingestion, append, and deletion.

Extracted from rag_engine.py Phase 4. Owns all vector store I/O:
- LanceDB connection management (lazy singleton)
- Table creation with schema definition
- Document ingestion (chunk + embed + store + background tasks)
- Document append (for background image descriptions)
- Source deletion with entity cleanup
- Search chunks (simple vector search without LLM)
- Embedding dimension mismatch detection

External callers continue to use rag_engine.ingest_document() etc. —
RAGEngine delegates here.
"""
import asyncio
import os
import re
from typing import Dict, List, Optional, Tuple

import httpx
import lancedb
import numpy as np

from config import settings
from services import rag_embeddings
from services import rag_chunking
from services.entity_extractor import entity_extractor
from services.entity_graph import entity_graph

_concept_extraction_semaphore = asyncio.Semaphore(int(os.getenv("LOCALBOOK_KG_CONCURRENCY", "4")))


# ─── Lazy DB connection ──────────────────────────────────────────────────────────

_db = None


def _get_db():
    """Get or create the LanceDB connection (lazy singleton)."""
    global _db
    if _db is None:
        _db = lancedb.connect(str(settings.db_path))
    return _db


# ─── Table Management ────────────────────────────────────────────────────────────

def get_table(notebook_id: str):
    """Get or create LanceDB table for notebook."""
    db = _get_db()
    table_name = f"notebook_{notebook_id}"

    # Try to open existing table first (fast path)
    try:
        return db.open_table(table_name)
    except Exception:
        pass

    # Table doesn't exist — create with placeholder to define schema
    try:
        placeholder_embedding = rag_embeddings.encode("placeholder")[0].tolist()
        db.create_table(
            table_name,
            data=[{
                "vector": placeholder_embedding,
                "text": "placeholder",
                "parent_text": "",  # v0.60: Parent document context
                "synthetic_questions": "",  # v1.1.0: HyDE metadata enrichment
                "source_id": "placeholder",
                "chunk_index": 0,
                "filename": "placeholder",
                "source_type": "placeholder"
            }]
        )
        table = db.open_table(table_name)
        table.delete("source_id = 'placeholder'")
        return table
    except Exception:
        # Race condition: another request created it between our check and create
        return db.open_table(table_name)


def get_stored_vector_dim(table) -> Optional[int]:
    """Get the dimension of vectors stored in a table from schema."""
    try:
        schema = table.schema
        for field in schema:
            if field.name == "vector":
                type_str = str(field.type)
                if "fixed_size_list" in type_str:
                    match = re.search(r'\[(\d+)\]', type_str)
                    if match:
                        return int(match.group(1))
    except Exception:
        pass
    return None


def table_has_parent_text(table) -> bool:
    """Check if table schema includes parent_text column."""
    try:
        schema = table.schema
        for field in schema:
            if field.name == "parent_text":
                return True
    except Exception:
        pass
    return False


def table_has_synthetic_questions(table) -> bool:
    """Check if table schema includes synthetic_questions column."""
    try:
        schema = table.schema
        for field in schema:
            if field.name == "synthetic_questions":
                return True
    except Exception:
        pass
    return False


# ─── Document Ingestion ──────────────────────────────────────────────────────────

async def ingest_document(
    notebook_id: str,
    source_id: str,
    text: str,
    filename: str = "Unknown",
    source_type: str = "document",
) -> Dict:
    """Ingest a document into the RAG system."""

    # Use source-type-aware chunking for better retrieval
    chunks = rag_chunking.chunk_text_smart(text, source_type, filename)

    # Skip summary for web sources (they have search snippets already)
    # Only generate summaries for uploaded files (PDFs, docs, etc.)
    summary = None
    if source_type not in ['web', 'youtube']:
        summary = await generate_document_summary(text, filename, source_type)
        if summary:
            print(f"[RAG] Generated summary for {filename}: {len(summary)} chars")

    # Generate synthetic questions for HyDE
    questions = await generate_chunk_questions(chunks)
    texts_to_embed = [f"{c}\n\nQuestions this answers:\n{q}" if q else c for c, q in zip(chunks, questions)]

    # Generate embeddings
    embeddings = await rag_embeddings.encode_async(texts_to_embed)

    # Insert into LanceDB
    table = get_table(notebook_id)

    # Check schema evolution flags
    has_parent_text = table_has_parent_text(table)
    has_synthetic_questions = table_has_synthetic_questions(table)

    # Prepare data for insertion with metadata
    data = []
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        row = {
            "vector": embedding.tolist(),
            "text": chunk,
            "source_id": source_id,
            "chunk_index": i,
            "filename": filename,
            "source_type": source_type
        }
        if has_parent_text:
            row["parent_text"] = rag_chunking.get_parent_context(chunks, i, max_parent_chars=2000)
        if has_synthetic_questions:
            row["synthetic_questions"] = questions[i] if i < len(questions) else ""
        data.append(row)

    # Add summary as a special chunk (chunk_index = -1) for quick retrieval
    if summary:
        summary_embedding = rag_embeddings.encode(summary)[0].tolist()
        summary_row = {
            "vector": summary_embedding,
            "text": f"[SUMMARY] {summary}",
            "source_id": source_id,
            "chunk_index": -1,  # Special index for summaries
            "filename": filename,
            "source_type": "summary"
        }
        if has_parent_text:
            summary_row["parent_text"] = ""
        if has_synthetic_questions:
            summary_row["synthetic_questions"] = ""
        data.append(summary_row)

    table.add(data)

    # Fire-and-forget topic modeling in background
    from utils.tasks import safe_create_task
    safe_create_task(_add_to_topic_model(
        notebook_id=notebook_id,
        source_id=source_id,
        chunks=chunks,
        embeddings=embeddings
    ))
    print(f"[RAG] Queued topic modeling for {filename} (background)")

    # v1.0.3: Entity extraction + relationship mapping in background
    async def _extract_entities_and_relationships_background():
        try:
            entities = await entity_extractor.extract_from_text(
                text=text[:8000],  # Limit for speed
                notebook_id=notebook_id,
                source_id=source_id,
                use_llm=len(text) > 500  # Only use LLM for substantial docs
            )
            if entities:
                print(f"[RAG] Extracted {len(entities)} entities from {filename}")

                # v1.0.4: Extract relationships between entities (Phase 2 Graph RAG)
                if len(entities) >= 2:
                    entity_dicts = [{"name": e.name, "type": e.type} for e in entities]
                    relationships = await entity_graph.extract_relationships(
                        text=text[:4000],
                        notebook_id=notebook_id,
                        source_id=source_id,
                        entities=entity_dicts
                    )
                    if relationships:
                        print(f"[RAG] Extracted {len(relationships)} relationships from {filename}")
                        
                        # GraphRAG Phase 2: Detect communities and build missing summaries
                        try:
                            from services.community_detection import community_detector
                            await community_detector.detect_communities(notebook_id, entity_graph)
                            # Run summary building as a separate nested task to not block
                            safe_create_task(
                                community_detector.build_missing_summaries(notebook_id, entity_graph),
                                name="community-summary-builder"
                            )
                        except Exception as comm_err:
                            print(f"[RAG] Community detection failed: {comm_err}")
        except Exception as e:
            print(f"[RAG] Entity/relationship extraction failed (non-fatal): {e}")

    safe_create_task(_extract_entities_and_relationships_background(), name="entity-extraction")

    # Auto-refresh people coaching insights when new sources are added
    if source_type not in ("people_profile", "coaching_notes", "summary"):
        try:
            from services.coaching_insights import schedule_insight_refresh
            schedule_insight_refresh(notebook_id)
        except Exception:
            pass  # Non-fatal — insights refresh is best-effort

    return {
        "source_id": source_id,
        "chunks": len(chunks),
        "characters": len(text),
        "summary": summary
    }


# ─── Document Append ─────────────────────────────────────────────────────────────

async def append_to_document(
    notebook_id: str,
    source_id: str,
    text: str,
    chunk_prefix: str = "",
) -> Dict:
    """Append additional content to an existing source's index.
    
    Used for background processing (e.g., image descriptions) that should
    be added to an already-indexed document without re-processing everything.
    """
    if not text or not text.strip():
        return {"chunks_added": 0}

    # Chunk the new text
    chunks = rag_chunking.chunk_text_smart(text, "supplementary", "background")

    if not chunks:
        return {"chunks_added": 0}

    # Add prefix to chunks if specified
    if chunk_prefix:
        chunks = [f"{chunk_prefix}{chunk}" for chunk in chunks]

    # Generate synthetic questions
    questions = await generate_chunk_questions(chunks)
    texts_to_embed = [f"{c}\n\nQuestions this answers:\n{q}" if q else c for c, q in zip(chunks, questions)]

    # Generate embeddings
    embeddings = await rag_embeddings.encode_async(texts_to_embed)

    # Get existing table
    table = get_table(notebook_id)

    # Get existing chunk count for this source to continue numbering
    try:
        existing = table.search([0.0] * settings.embedding_dim).where(
            f"source_id = '{source_id}'"
        ).limit(1000).to_list()
        max_chunk_index = max((r.get("chunk_index", 0) for r in existing), default=0)
    except Exception:
        max_chunk_index = 0

    # Check if table supports legacy columns
    has_parent_text = table_has_parent_text(table)
    has_synthetic_questions = table_has_synthetic_questions(table)

    # Prepare data for insertion
    data = []
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        row = {
            "vector": embedding.tolist(),
            "text": chunk,
            "source_id": source_id,
            "chunk_index": max_chunk_index + i + 1,
            "filename": "background_content",
            "source_type": "supplementary"
        }
        if has_parent_text:
            row["parent_text"] = ""
        if has_synthetic_questions:
            row["synthetic_questions"] = questions[i] if i < len(questions) else ""
        data.append(row)

    table.add(data)

    print(f"[RAG] Appended {len(chunks)} chunks to source {source_id}")
    return {"chunks_added": len(chunks)}


# ─── Source Deletion ─────────────────────────────────────────────────────────────

async def delete_source(notebook_id: str, source_id: str) -> bool:
    """Delete all chunks for a source from LanceDB.
    
    Designed to be robust - returns True even if source was never indexed.
    """
    try:
        db = _get_db()
        table_name = f"notebook_{notebook_id}"

        # Check if table even exists - if not, nothing to delete
        if table_name not in db.table_names():
            print(f"[RAG] No table exists for notebook {notebook_id}, nothing to delete")
            return True

        table = db.open_table(table_name)

        # LanceDB delete uses SQL-like filter syntax
        table.delete(f"source_id = '{source_id}'")
        print(f"[RAG] Deleted all chunks for source {source_id} from LanceDB")

        # v1.0.3: Clean up entities for this source
        entity_extractor.delete_source_entities(notebook_id, source_id)

        return True
    except Exception as e:
        print(f"[RAG] Error deleting source {source_id} from LanceDB: {e}")
        return True


# ─── Search Chunks (simple vector search, no LLM) ───────────────────────────────

def search_chunks(notebook_id: str, query_text: str, top_k: int = 5) -> List[Dict]:
    """Search for relevant chunks in a notebook's vector store.
    
    Simple vector similarity search without LLM generation.
    """
    try:
        table = get_table(notebook_id)
        if table.count_rows() == 0:
            return []
        query_emb = rag_embeddings.encode(query_text)[0].tolist()
        results = table.search(query_emb).limit(top_k).to_list()
        return results
    except Exception as e:
        print(f"[RAG] search_chunks failed for {notebook_id}: {e}")
        return []


# ─── LLM Question & Summary Generation ──────────────────────────────────────────

async def generate_chunk_questions(chunks: List[str]) -> List[str]:
    """Generate synthetic questions for each chunk using phi4-mini (HyDE)."""
    if not chunks:
        return []
    
    semaphore = asyncio.Semaphore(5)  # Limit concurrency
    timeout = httpx.Timeout(45.0)
    
    async def get_questions(chunk: str) -> str:
        prompt = (
            "Read the following text and write exactly 3 short, specific questions "
            "that this text directly answers. Do not include any intro/outro.\n\n"
            f"Text:\n{chunk[:2000]}\n\nQuestions:"
        )
        async with semaphore:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        f"{settings.ollama_base_url}/api/generate",
                        json={
                            "model": settings.ollama_fast_model,
                            "prompt": prompt,
                            "stream": False,
                            "options": {
                                "temperature": 0.3,
                                "num_predict": 100,
                            }
                        }
                    )
                    if response.status_code == 200:
                        return response.json().get("response", "").strip()
            except Exception as e:
                print(f"[RAG] Question generation failed: {e}")
        return ""

    tasks = [get_questions(c) for c in chunks]
    return await asyncio.gather(*tasks)


# ─── Document Summary ────────────────────────────────────────────────────────────

async def generate_document_summary(text: str, filename: str, source_type: str) -> Optional[str]:
    """Generate a summary of the document at ingestion time."""
    # For very short documents, don't generate summary
    if len(text) < 500:
        return None

    # Truncate for summary generation
    text_sample = text[:4000]

    if source_type in ['xlsx', 'csv', 'tabular']:
        prompt = f"""Summarize this tabular data from '{filename}'. Include:
- What entities/people are tracked
- What metrics/values are recorded  
- Time periods covered
- Key totals or patterns

Data sample:
{text_sample}

Summary (2-3 sentences):"""
    else:
        prompt = f"""Summarize the key points from '{filename}' in 2-3 sentences. Focus on:
- Main topic/purpose
- Key facts or findings
- Important entities mentioned

Content:
{text_sample}

Summary:"""

    try:
        timeout = httpx.Timeout(30.0, read=60.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_fast_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 200,
                    }
                }
            )

            if response.status_code == 200:
                result = response.json()
                summary = result.get("response", "").strip()
                if summary and len(summary) > 20:
                    return summary
    except Exception as e:
        print(f"[RAG] Summary generation failed for {filename}: {e}")

    return None


# ─── Topic Modeling (background) ─────────────────────────────────────────────────

async def _add_to_topic_model(
    notebook_id: str,
    source_id: str,
    chunks: List[str],
    embeddings: np.ndarray,
):
    """Add document chunks to BERTopic model for topic discovery."""
    async with _concept_extraction_semaphore:
        try:
            from services.topic_modeling import topic_modeling_service

            if not chunks:
                print(f"[TopicModel] No chunks for source {source_id}, skipping")
                return

            print(f"[TopicModel] Adding {len(chunks)} chunks from source {source_id}")

            result = await topic_modeling_service.add_documents(
                texts=chunks,
                source_id=source_id,
                notebook_id=notebook_id,
                embeddings=embeddings
            )

            topic_count = len(result.get("topics", []))
            if topic_count > 0:
                print(f"[TopicModel] Found {topic_count} topics for source {source_id}")
            else:
                print(f"[TopicModel] No new topics for source {source_id} (status: {result.get('status', 'unknown')})")

            # Check if we should auto-rebuild topics
            if topic_modeling_service.should_rebuild(notebook_id):
                try:
                    from services.job_queue import job_queue, JobType, JobStatus
                    running_jobs = await job_queue.list_jobs(notebook_id=notebook_id, status=JobStatus.RUNNING)
                    has_running_rebuild = any(
                        j.get("job_type") == JobType.TOPIC_REBUILD.value
                        for j in running_jobs
                    )
                    if not has_running_rebuild:
                        topic_modeling_service.mark_rebuild_started(notebook_id)
                        job_id = await job_queue.submit(
                            job_type=JobType.TOPIC_REBUILD,
                            params={"notebook_id": notebook_id},
                            notebook_id=notebook_id
                        )
                        print(f"[TopicModel] Auto-triggered rebuild for {notebook_id} (job {job_id})")
                    else:
                        print(f"[TopicModel] Rebuild already running for {notebook_id}, skipping auto-trigger")
                except Exception as rebuild_err:
                    print(f"[TopicModel] Auto-rebuild trigger failed (non-fatal): {rebuild_err}")

        except Exception as e:
            import traceback
            print(f"[TopicModel] Error adding to topic model: {e}")
            traceback.print_exc()


# ─── Embedding Dimension Mismatch ────────────────────────────────────────────────

def check_embedding_dimension_mismatch() -> List[str]:
    """Check all notebook tables for embedding dimension mismatch.
    Returns list of notebook IDs that need re-indexing."""
    db = _get_db()
    current_dim = rag_embeddings.get_current_embedding_dim()
    mismatched_notebooks = []

    for table_name in db.table_names():
        if table_name.startswith("notebook_"):
            try:
                table = db.open_table(table_name)
                stored_dim = get_stored_vector_dim(table)
                if stored_dim is not None and stored_dim != current_dim:
                    notebook_id = table_name.replace("notebook_", "")
                    mismatched_notebooks.append(notebook_id)
                    print(f"[RAG] Dimension mismatch: {table_name} has {stored_dim}-dim vectors, current model uses {current_dim}-dim")
            except Exception as e:
                print(f"[RAG] Error checking {table_name}: {e}")

    return mismatched_notebooks
