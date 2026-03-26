#!/usr/bin/env python3
"""
Upgrade RAG V3 - Backfills existing notebooks with Advanced RAG features.

This script executes Phase 3 of the V3 architecture:
1. Re-Indexer (HyDE Metadata Enrichment): Iterates over existing LanceDB tables,
   generates synthetic questions for every chunk, re-embeds the text with the
   questions injected, and overwrites the table to support the V3 schema.
2. GraphRAG Backfill: Iterates over all generated entity_graphs and forces
   community detection + permanent LLM community summary caching for all 
   historical networks.

Usage:
    cd backend
    python scripts/upgrade_rag_v3.py
"""
import sys
import os
import asyncio
from pathlib import Path

# Fix python path so we can import backend modules standalone
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import settings

from services.rag_storage import (
    _get_db,
    generate_chunk_questions,
    table_has_synthetic_questions
)
from services import rag_embeddings
from services.entity_graph import entity_graph
from services.community_detection import community_detector


async def upgrade_notebook_table(table_name: str, db):
    """Upgrade a single LanceDB table with HyDE questions and re-embed."""
    notebook_id = table_name.replace("notebook_", "")
    print(f"\n[Upgrade] Processing {table_name}...")
    
    table = db.open_table(table_name)
    
    row_count = table.count_rows()
    if row_count == 0:
        print(f"[Upgrade] Skipped (empty table)")
        return
    
    # Read all rows using the proven search API pattern
    zero_vec = [0.0] * settings.embedding_dim
    rows = table.search(zero_vec).limit(row_count + 100).to_list()
    
    if not rows:
        print(f"[Upgrade] Skipped (could not read rows)")
        return
        
    # Check if this table already looks upgraded
    if table_has_synthetic_questions(table):
        # Even if it has the column, we check if they are populated
        sample_q = next((r.get("synthetic_questions") for r in rows if r.get("chunk_index", 0) >= 0 and r.get("synthetic_questions")), None)
        if sample_q:
            print(f"[Upgrade] Skipped (already has synthetic_questions populated)")
            return

    print(f"[Upgrade] Upgrading {len(rows)} chunks with HyDE Metadata...")
    
    # Strip LanceDB internal fields (e.g. _distance) before re-insertion
    internal_fields = {"_distance", "_relevance_score"}
    
    # Process in batches of 50 to avoid overloading LLM
    new_rows = []
    batch_size = 50
    
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        print(f"[Upgrade] Processing chunks {i} to {min(i+batch_size, len(rows))} / {len(rows)}")
        
        # Isolate text chunks
        chunks = [r["text"] for r in batch]
        
        # 1. Generate Synthetic Questions (Batch parallel)
        questions = await generate_chunk_questions(chunks)
        
        # 2. Re-embed with the new metadata
        texts_to_embed = [
            f"{c}\n\nQuestions this answers:\n{q}" if q else c
            for c, q in zip(chunks, questions)
        ]
        embeddings = await rag_embeddings.encode_async(texts_to_embed)
        
        # 3. Update Row Dicts
        for j, row in enumerate(batch):
            # Clean internal LanceDB fields
            clean_row = {k: v for k, v in row.items() if k not in internal_fields}
            clean_row["vector"] = embeddings[j].tolist()
            clean_row["synthetic_questions"] = questions[j] if j < len(questions) else ""
            
            # Ensure v0.60 parent_text column exists
            if "parent_text" not in clean_row:
                clean_row["parent_text"] = ""
                
            new_rows.append(clean_row)
            
    # Overwrite table with updated schema + vectors
    if new_rows:
        if len(new_rows) != len(rows):
            print(f"[ERROR] Row count mismatch! Expected {len(rows)}, got {len(new_rows)}. Aborting overwrite to prevent data loss.")
            return
            
        db.create_table(table_name, data=new_rows, mode="overwrite")
        print(f"[Upgrade] Overwrote {table_name} with {len(new_rows)} enhanced chunks")


async def upgrade_graphrag_summaries(notebook_id: str):
    """Detect and build community summaries for an existing entity graph."""
    print(f"[Upgrade] Building GraphRAG global summaries for {notebook_id}...")
    try:
        # detect_communities loads from graph and groups them
        await community_detector.detect_communities(notebook_id, entity_graph)
        
        # build_missing_summaries calls LLM to write persistent summaries
        generated = await community_detector.build_missing_summaries(notebook_id, entity_graph)
        if generated == 0:
            print(f"[Upgrade] GraphRAG summaries already fully up-to-date for {notebook_id}")
    except Exception as e:
        print(f"[Upgrade] GraphRAG failure on {notebook_id}: {e}")


async def main():
    print("=========================================================")
    print(" RAG Architecture V3 Upgrade Tool (HyDE + GraphRAG)")
    print("=========================================================")
    print("This will process all historical notebooks, which may take")
    print("several minutes to an hour depending on your local LLM speed.")
    print("Press Ctrl+C to safely exit anytime (progress saves per notebook).")
    print("...")
    
    # ─── SAFETY FIRST: Automatic Backups ───
    import shutil
    from datetime import datetime
    
    db_path = Path(settings.db_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    backup_db = db_path.parent / f"{db_path.name}_backup_{timestamp}"
    print(f"\n[Safety] Creating backup of LanceDB at: {backup_db}")
    try:
        if db_path.exists():
            shutil.copytree(db_path, backup_db)
            print("[Safety] LanceDB backup successful.")
        
        community_file = db_path.parent / "communities.json"
        if community_file.exists():
            backup_comm = db_path.parent / f"communities_backup_{timestamp}.json"
            shutil.copy2(community_file, backup_comm)
            print("[Safety] communities.json backup successful.")
    except Exception as e:
        print(f"\n[FATAL] Failed to create safety backups. Aborting upgrade to prevent data risk: {e}")
        return
    # ───────────────────────────────────────
    
    db = _get_db()
    table_names = [t for t in db.table_names() if t.startswith("notebook_")]
    
    print(f"Found {len(table_names)} notebooks to inspect.\n")
    
    for table_name in table_names:
        notebook_id = table_name.replace("notebook_", "")
        
        # Phase 1: Re-indexer for HyDE Vectors
        await upgrade_notebook_table(table_name, db)
        
        # Phase 2: Compute GraphRAG Community Summaries
        await upgrade_graphrag_summaries(notebook_id)
        
    print("\n=========================================================")
    print(" Upgrade Complete! Your RAG Engine is fully V3 Compatible.")
    print("=========================================================")

if __name__ == "__main__":
    asyncio.run(main())
