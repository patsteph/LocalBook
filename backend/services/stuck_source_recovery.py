"""Stuck Source Recovery Service

Automatically detects and recovers sources stuck in "processing" status.
Runs on startup and periodically to prevent orphaned sources.

v1.1.0: Added 10-minute threshold for auto-recovery
"""

import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import lancedb

from config import settings
from storage.source_store import source_store


# Configuration
STUCK_THRESHOLD_MINUTES = 10  # Sources stuck longer than this get recovered
CHECK_INTERVAL_MINUTES = 5    # How often to check for stuck sources


class StuckSourceRecovery:
    """Service to detect and recover stuck sources."""
    
    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._data_dir = settings.data_dir
    
    async def check_and_recover(self) -> Dict:
        """Check for stuck sources and attempt recovery.
        
        Returns summary of actions taken.
        """
        result = {
            "checked_at": datetime.now().isoformat(),
            "stuck_found": 0,
            "recovered": 0,
            "failed": 0,
            "details": []
        }
        
        try:
            sources_data = source_store._load_data()
            now = datetime.now()
            threshold = now - timedelta(minutes=STUCK_THRESHOLD_MINUTES)
            
            for source_id, source in sources_data.get("sources", {}).items():
                if source.get("status") != "processing":
                    continue
                
                # Check if stuck (created more than threshold ago)
                created_at = source.get("created_at")
                if not created_at:
                    # No timestamp, assume stuck
                    is_stuck = True
                else:
                    try:
                        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        # Make naive for comparison
                        if created_dt.tzinfo:
                            created_dt = created_dt.replace(tzinfo=None)
                        is_stuck = created_dt < threshold
                    except:
                        is_stuck = True
                
                if not is_stuck:
                    continue
                
                result["stuck_found"] += 1
                title = source.get("title") or source.get("filename", "Unknown")
                
                # Attempt recovery
                recovery_result = await self._recover_source(source_id, source)
                
                if recovery_result["success"]:
                    result["recovered"] += 1
                    result["details"].append({
                        "source_id": source_id,
                        "title": title,
                        "action": recovery_result["action"],
                        "chunks": recovery_result.get("chunks", 0)
                    })
                    print(f"[StuckRecovery] Recovered: {title} ({recovery_result['action']})")
                else:
                    result["failed"] += 1
                    result["details"].append({
                        "source_id": source_id,
                        "title": title,
                        "action": "failed",
                        "error": recovery_result.get("error", "Unknown")
                    })
                    print(f"[StuckRecovery] Failed: {title} - {recovery_result.get('error')}")
            
            if result["stuck_found"] > 0:
                print(f"[StuckRecovery] Found {result['stuck_found']} stuck, recovered {result['recovered']}, failed {result['failed']}")
            
        except Exception as e:
            print(f"[StuckRecovery] Error during check: {e}")
            result["error"] = str(e)
        
        return result
    
    async def _recover_source(self, source_id: str, source: Dict) -> Dict:
        """Attempt to recover a single stuck source.
        
        Strategy:
        1. If has content and 0 chunks -> re-ingest
        2. If has content and chunks exist in LanceDB -> mark completed
        3. If no content -> mark as failed
        """
        try:
            content = source.get("content", "")
            existing_chunks = source.get("chunks", 0)
            notebook_id = source.get("notebook_id")
            title = source.get("title") or source.get("filename", "Unknown")
            
            if not notebook_id:
                return {"success": False, "error": "No notebook_id"}
            
            # Check if chunks already exist in LanceDB
            chunks_in_db = await self._count_chunks_in_db(notebook_id, source_id)
            
            if chunks_in_db > 0:
                # Chunks exist, just update status
                source_store.update(source_id, {
                    "status": "completed",
                    "chunks": chunks_in_db
                })
                return {"success": True, "action": "marked_completed", "chunks": chunks_in_db}
            
            if not content:
                # No content to ingest, mark as failed
                source_store.update(source_id, {
                    "status": "failed",
                    "error": "No content available for ingestion"
                })
                return {"success": True, "action": "marked_failed_no_content"}
            
            # Has content but no chunks - re-ingest
            chunks_created = await self._ingest_content(notebook_id, source_id, content, title, source)
            
            if chunks_created > 0:
                source_store.update(source_id, {
                    "status": "completed",
                    "chunks": chunks_created
                })
                return {"success": True, "action": "re_ingested", "chunks": chunks_created}
            else:
                source_store.update(source_id, {
                    "status": "failed",
                    "error": "Ingestion produced 0 chunks"
                })
                return {"success": True, "action": "marked_failed_no_chunks"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _count_chunks_in_db(self, notebook_id: str, source_id: str) -> int:
        """Count existing chunks for a source in LanceDB."""
        try:
            db = lancedb.connect(str(self._data_dir / "lancedb"))
            table_name = f"notebook_{notebook_id}"
            
            if table_name not in db.table_names():
                return 0
            
            table = db.open_table(table_name)
            # Count rows with this source_id
            df = table.to_pandas()
            count = len(df[df["source_id"] == source_id])
            return count
        except Exception as e:
            print(f"[StuckRecovery] Error counting chunks: {e}")
            return 0
    
    async def _ingest_content(
        self, 
        notebook_id: str, 
        source_id: str, 
        content: str, 
        title: str,
        source: Dict
    ) -> int:
        """Ingest content into LanceDB."""
        try:
            from services.rag_engine import rag_engine
            
            db = lancedb.connect(str(self._data_dir / "lancedb"))
            table_name = f"notebook_{notebook_id}"
            
            if table_name not in db.table_names():
                # No table exists, can't add
                return 0
            
            table = db.open_table(table_name)
            
            # Determine source type
            source_type = source.get("format", "web")
            if source_type in ["pdf", "docx", "pptx"]:
                source_type = "document"
            
            # Chunk the content
            chunks = rag_engine._chunk_text_smart(content, source_type, title)
            
            if not chunks:
                return 0
            
            # Generate embeddings
            embeddings = rag_engine.encode(chunks)
            
            # Prepare rows
            rows = []
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                rows.append({
                    "vector": emb.tolist(),
                    "text": chunk,
                    "source_id": source_id,
                    "chunk_index": i,
                    "filename": title,
                })
            
            # Add to table
            table.add(rows)
            
            return len(chunks)
            
        except Exception as e:
            print(f"[StuckRecovery] Ingestion error: {e}")
            return 0
    
    def start_background_task(self):
        """Start periodic background checking."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._background_loop())
        print(f"[StuckRecovery] Started background task (check every {CHECK_INTERVAL_MINUTES} min, threshold {STUCK_THRESHOLD_MINUTES} min)")
    
    def stop_background_task(self):
        """Stop the background task."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        print("[StuckRecovery] Stopped background task")
    
    async def _background_loop(self):
        """Background loop that periodically checks for stuck sources."""
        # Initial check on startup after short delay
        await asyncio.sleep(30)  # Wait 30s after startup
        
        while self._running:
            try:
                await self.check_and_recover()
            except Exception as e:
                print(f"[StuckRecovery] Background check error: {e}")
            
            # Wait for next check
            await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)


# Singleton instance
stuck_source_recovery = StuckSourceRecovery()
