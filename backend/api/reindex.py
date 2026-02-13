"""Re-indexing API endpoints for fixing sources that weren't properly ingested"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, List
import lancedb
from config import settings
from storage.source_store import source_store
from storage.notebook_store import notebook_store
from services.rag_engine import rag_engine

router = APIRouter()


class ReindexResponse(BaseModel):
    message: str
    processed: int
    failed: int
    details: list


@router.post("/notebook/{notebook_id}")
async def reindex_notebook(notebook_id: str, force: bool = False):
    """Re-index all sources in a notebook that have content but weren't properly ingested.
    
    Args:
        notebook_id: The notebook to reindex
        force: If True, reindex all sources even if they already have chunks
    """
    
    notebook = await notebook_store.get(notebook_id)
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook not found")
    
    sources = await source_store.list(notebook_id)
    
    processed = 0
    failed = 0
    details = []
    
    for source in sources:
        source_id = source.get("id")
        filename = source.get("filename", "Unknown")
        
        # Check if source has content
        content_data = await source_store.get_content(notebook_id, source_id)
        
        if not content_data or not content_data.get("content"):
            details.append({
                "source_id": source_id,
                "filename": filename,
                "status": "skipped",
                "reason": "No content available"
            })
            continue
        
        text = content_data["content"]
        
        # Check if already has chunks (skip unless force=True)
        if not force and source.get("chunks", 0) > 0:
            details.append({
                "source_id": source_id,
                "filename": filename,
                "status": "skipped",
                "reason": f"Already has {source.get('chunks')} chunks (use force=true to reindex)"
            })
            continue
        
        # Re-ingest into RAG system
        try:
            # IMPORTANT: Delete old chunks first to avoid duplicates
            await rag_engine.delete_source(notebook_id, source_id)
            
            source_type = source.get("type", source.get("format", "document"))
            result = await rag_engine.ingest_document(
                notebook_id=notebook_id,
                source_id=source_id,
                text=text,
                filename=filename,
                source_type=source_type
            )
            
            # Update source with chunk count
            await source_store.update(notebook_id, source_id, {
                "chunks": result.get("chunks", 0),
                "characters": result.get("characters", len(text)),
                "status": "completed"
            })
            
            processed += 1
            details.append({
                "source_id": source_id,
                "filename": filename,
                "status": "success",
                "chunks": result.get("chunks", 0)
            })
            
        except Exception as e:
            failed += 1
            details.append({
                "source_id": source_id,
                "filename": filename,
                "status": "failed",
                "error": str(e)
            })
    
    return ReindexResponse(
        message=f"Re-indexed {processed} sources, {failed} failed",
        processed=processed,
        failed=failed,
        details=details
    )


@router.post("/all")
async def reindex_all_notebooks(force: bool = True, drop_tables: bool = False):
    """Re-index all sources in all notebooks.
    
    Useful after changing embedding models.
    
    Args:
        force: If True (default), reindex all sources even if they already have chunks
        drop_tables: If True, drop existing LanceDB tables first (required when embedding dimensions change)
    """
    # If drop_tables is True, clear all existing vector tables
    if drop_tables:
        try:
            import lancedb
            from config import settings
            db = lancedb.connect(str(settings.db_path))
            existing_tables = db.table_names()
            for table_name in existing_tables:
                if table_name.startswith("notebook_"):
                    db.drop_table(table_name)
                    print(f"Dropped table: {table_name}")
        except Exception as e:
            print(f"Error dropping tables: {e}")
    
    notebooks = await notebook_store.list()
    
    total_processed = 0
    total_failed = 0
    notebook_results = []
    
    for notebook in notebooks:
        notebook_id = notebook.get("id")
        sources = await source_store.list(notebook_id)
        
        processed = 0
        failed = 0
        
        for source in sources:
            source_id = source.get("id")
            filename = source.get("filename", "Unknown")
            
            content_data = await source_store.get_content(notebook_id, source_id)
            
            if not content_data or not content_data.get("content"):
                continue
            
            text = content_data["content"]
            
            if not force and source.get("chunks", 0) > 0:
                continue
            
            try:
                # IMPORTANT: Delete old chunks first to avoid duplicates
                await rag_engine.delete_source(notebook_id, source_id)
                
                source_type = source.get("type", source.get("format", "document"))
                result = await rag_engine.ingest_document(
                    notebook_id=notebook_id,
                    source_id=source_id,
                    text=text,
                    filename=filename,
                    source_type=source_type
                )
                
                await source_store.update(notebook_id, source_id, {
                    "chunks": result.get("chunks", 0),
                    "characters": result.get("characters", len(text)),
                    "status": "completed"
                })
                
                processed += 1
                
            except Exception as e:
                failed += 1
                print(f"Failed to reindex {filename}: {e}")
        
        total_processed += processed
        total_failed += failed
        notebook_results.append({
            "notebook_id": notebook_id,
            "title": notebook.get("title", "Unknown"),
            "processed": processed,
            "failed": failed
        })
    
    return {
        "message": f"Re-indexed {total_processed} sources across {len(notebooks)} notebooks, {total_failed} failed",
        "total_processed": total_processed,
        "total_failed": total_failed,
        "notebooks": notebook_results
    }


@router.get("/status/{notebook_id}")
async def get_index_status(notebook_id: str):
    """Get indexing status for all sources in a notebook"""
    
    notebook = await notebook_store.get(notebook_id)
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook not found")
    
    sources = await source_store.list(notebook_id)
    
    indexed = 0
    not_indexed = 0
    no_content = 0
    
    source_status = []
    
    for source in sources:
        source_id = source.get("id")
        chunks = source.get("chunks", 0)
        has_content = bool(source.get("content"))
        
        if chunks > 0:
            indexed += 1
            status = "indexed"
        elif has_content:
            not_indexed += 1
            status = "not_indexed"
        else:
            no_content += 1
            status = "no_content"
        
        source_status.append({
            "source_id": source_id,
            "filename": source.get("filename", "Unknown"),
            "chunks": chunks,
            "has_content": has_content,
            "status": status
        })
    
    return {
        "notebook_id": notebook_id,
        "total_sources": len(sources),
        "indexed": indexed,
        "not_indexed": not_indexed,
        "no_content": no_content,
        "sources": source_status
    }


@router.get("/integrity")
async def check_data_integrity():
    """Check for orphaned data in LanceDB that doesn't match sources.json.
    
    Returns a report of:
    - Orphaned chunks (in LanceDB but source deleted from sources.json)
    - Missing chunks (in sources.json but not in LanceDB)
    """
    # Load all valid source IDs from sources.json
    sources_data = source_store._load_data()
    valid_sources = sources_data.get("sources", {})
    valid_source_ids = set(valid_sources.keys())
    
    # Connect to LanceDB
    db = lancedb.connect(str(settings.db_path))
    
    orphaned_by_notebook: Dict[str, List[str]] = {}
    missing_chunks: List[Dict] = []
    total_chunks = 0
    total_orphaned = 0
    
    # Check each notebook table
    for table_name in db.table_names():
        if not table_name.startswith("notebook_"):
            continue
        
        notebook_id = table_name.replace("notebook_", "")
        table = db.open_table(table_name)
        
        try:
            results = table.search().limit(50000).to_list()
        except Exception as e:
            print(f"[INTEGRITY] Error reading {table_name}: {e}")
            continue
        
        total_chunks += len(results)
        
        # Find orphaned source_ids in this table
        source_ids_in_table = set()
        for r in results:
            sid = r.get("source_id", "")
            if sid and sid != "placeholder":
                source_ids_in_table.add(sid)
                if sid not in valid_source_ids:
                    if notebook_id not in orphaned_by_notebook:
                        orphaned_by_notebook[notebook_id] = []
                    if sid not in orphaned_by_notebook[notebook_id]:
                        orphaned_by_notebook[notebook_id].append(sid)
                    total_orphaned += 1
        
        # Check for sources that should be in this notebook but aren't in LanceDB
        for sid, source in valid_sources.items():
            if source.get("notebook_id") == notebook_id:
                if sid not in source_ids_in_table and source.get("chunks", 0) > 0:
                    missing_chunks.append({
                        "notebook_id": notebook_id,
                        "source_id": sid,
                        "filename": source.get("filename", "Unknown"),
                        "expected_chunks": source.get("chunks", 0)
                    })
    
    return {
        "status": "clean" if not orphaned_by_notebook and not missing_chunks else "issues_found",
        "total_chunks_in_lancedb": total_chunks,
        "total_orphaned_chunks": total_orphaned,
        "orphaned_sources_by_notebook": orphaned_by_notebook,
        "missing_from_lancedb": missing_chunks,
        "valid_sources_count": len(valid_source_ids)
    }


@router.post("/cleanup")
async def cleanup_orphaned_data():
    """Remove orphaned chunks from LanceDB that no longer have matching sources.
    
    This cleans up stale data left behind when sources were deleted without
    proper LanceDB cleanup.
    """
    # Load valid source IDs
    sources_data = source_store._load_data()
    valid_source_ids = set(sources_data.get("sources", {}).keys())
    
    # Connect to LanceDB
    db = lancedb.connect(str(settings.db_path))
    
    cleaned_by_notebook: Dict[str, int] = {}
    total_cleaned = 0
    
    for table_name in db.table_names():
        if not table_name.startswith("notebook_"):
            continue
        
        notebook_id = table_name.replace("notebook_", "")
        table = db.open_table(table_name)
        
        try:
            results = table.search().limit(50000).to_list()
        except Exception:
            continue
        
        # Find orphaned source_ids
        orphaned_sids = set()
        for r in results:
            sid = r.get("source_id", "")
            if sid and sid != "placeholder" and sid not in valid_source_ids:
                orphaned_sids.add(sid)
        
        # Delete orphaned chunks
        for sid in orphaned_sids:
            try:
                table.delete(f"source_id = '{sid}'")
                count = sum(1 for r in results if r.get("source_id") == sid)
                total_cleaned += count
                cleaned_by_notebook[notebook_id] = cleaned_by_notebook.get(notebook_id, 0) + count
                print(f"[CLEANUP] Deleted {count} orphaned chunks for source {sid[:8]}...")
            except Exception as e:
                print(f"[CLEANUP] Error deleting {sid}: {e}")
    
    return {
        "message": f"Cleaned up {total_cleaned} orphaned chunks",
        "total_cleaned": total_cleaned,
        "cleaned_by_notebook": cleaned_by_notebook
    }


@router.post("/repair-zero-vectors")
async def repair_zero_vectors():
    """Find and re-embed chunks that have zero vectors (failed embeddings).
    
    Zero vectors make chunks invisible to search. This scans all notebooks
    and attempts to regenerate embeddings for any chunks with zero vectors.
    """
    from services.rag_engine import rag_engine
    
    db = lancedb.connect(str(settings.db_path))
    
    total_found = 0
    total_fixed = 0
    results_by_notebook = {}
    
    for table_name in db.table_names():
        if not table_name.startswith("notebook_"):
            continue
        
        notebook_id = table_name.replace("notebook_", "")
        table = db.open_table(table_name)
        df = table.to_pandas()
        
        # Find zero vectors
        zero_mask = df['vector'].apply(lambda v: all(x == 0 for x in v))
        zero_df = df[zero_mask]
        
        if len(zero_df) == 0:
            continue
        
        total_found += len(zero_df)
        fixed = 0
        
        for _, row in zero_df.iterrows():
            try:
                # Generate new embedding
                embedding = rag_engine.encode(row['text'])[0].tolist()
                
                if all(x == 0 for x in embedding):
                    continue  # Still failed
                
                # Replace in table
                table.delete(f"source_id = '{row['source_id']}' AND chunk_index = {row['chunk_index']}")
                table.add([{
                    'vector': embedding,
                    'text': row['text'],
                    'source_id': row['source_id'],
                    'chunk_index': row['chunk_index'],
                    'filename': row['filename'],
                    'source_type': row.get('source_type', 'document')
                }])
                fixed += 1
            except Exception as e:
                print(f"[REPAIR] Error fixing chunk: {e}")
        
        total_fixed += fixed
        results_by_notebook[notebook_id] = {"found": len(zero_df), "fixed": fixed}
    
    return {
        "message": f"Found {total_found} zero vectors, fixed {total_fixed}",
        "total_found": total_found,
        "total_fixed": total_fixed,
        "by_notebook": results_by_notebook
    }


@router.post("/recover-stuck")
async def recover_stuck_sources():
    """Manually trigger recovery of sources stuck in 'processing' status.
    
    Sources are considered stuck if they've been in 'processing' for >10 minutes.
    Recovery will:
    - Re-ingest if content exists but no chunks
    - Mark completed if chunks already exist in LanceDB
    - Mark failed if no content available
    """
    from services.stuck_source_recovery import stuck_source_recovery
    
    result = await stuck_source_recovery.check_and_recover()
    
    return {
        "message": f"Found {result['stuck_found']} stuck sources, recovered {result['recovered']}, failed {result['failed']}",
        "stuck_found": result["stuck_found"],
        "recovered": result["recovered"],
        "failed": result["failed"],
        "details": result["details"]
    }
