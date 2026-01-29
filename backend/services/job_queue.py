"""Batch Processing Job Queue

Async job system for long-running operations (>30 seconds).
Provides status tracking, progress reporting, and cancellation support.

Usage:
    job_id = await job_queue.submit(
        job_type="topic_rebuild",
        handler=my_async_function,
        params={"notebook_id": "abc123"},
        on_progress=lambda p: print(f"Progress: {p}%")
    )
    
    status = await job_queue.get_status(job_id)
    result = await job_queue.get_result(job_id)
    await job_queue.cancel(job_id)
"""

import asyncio
import uuid
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Awaitable
from collections import OrderedDict


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    TOPIC_REBUILD = "topic_rebuild"
    DOCUMENT_INGEST = "document_ingest"
    BATCH_INGEST = "batch_ingest"
    RLM_QUERY = "rlm_query"
    CONTRADICTION_SCAN = "contradiction_scan"
    TIMELINE_EXTRACT = "timeline_extract"
    EXPORT = "export"
    CUSTOM = "custom"


@dataclass
class JobProgress:
    """Progress update for a job."""
    percent: int = 0
    message: str = ""
    current_step: int = 0
    total_steps: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Job:
    """Represents a queued or running job."""
    id: str
    job_type: JobType
    status: JobStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    progress: JobProgress = field(default_factory=JobProgress)
    result: Any = None
    error: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    notebook_id: Optional[str] = None
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    _cancel_event: Optional[asyncio.Event] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "id": self.id,
            "job_type": self.job_type.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "progress": {
                "percent": self.progress.percent,
                "message": self.progress.message,
                "current_step": self.progress.current_step,
                "total_steps": self.progress.total_steps,
                "details": self.progress.details
            },
            "result": self.result if self.status == JobStatus.COMPLETED else None,
            "error": self.error,
            "params": self.params,
            "notebook_id": self.notebook_id,
            "duration_seconds": self._get_duration()
        }
    
    def _get_duration(self) -> Optional[float]:
        """Get job duration in seconds."""
        if not self.started_at:
            return None
        end = self.completed_at or datetime.utcnow()
        return (end - self.started_at).total_seconds()


# Type for job handlers
JobHandler = Callable[[Dict[str, Any], Callable[[JobProgress], Awaitable[None]], asyncio.Event], Awaitable[Any]]


class JobQueue:
    """Async job queue with status tracking and progress reporting."""
    
    MAX_COMPLETED_JOBS = 100  # Keep last N completed jobs in memory
    MAX_CONCURRENT_JOBS = 3  # Max parallel jobs
    
    def __init__(self):
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._handlers: Dict[JobType, JobHandler] = {}
        self._progress_listeners: Dict[str, List[Callable[[Job], Awaitable[None]]]] = {}
        self._global_listeners: List[Callable[[Job], Awaitable[None]]] = []
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_JOBS)
        self._worker_task: Optional[asyncio.Task] = None
        self._pending_queue: asyncio.Queue = asyncio.Queue()
        print("[JobQueue] Initialized")
    
    def register_handler(self, job_type: JobType, handler: JobHandler):
        """Register a handler for a job type.
        
        Handler signature:
            async def handler(
                params: Dict[str, Any],
                report_progress: Callable[[JobProgress], Awaitable[None]],
                cancel_event: asyncio.Event
            ) -> Any
        """
        self._handlers[job_type] = handler
        print(f"[JobQueue] Registered handler for {job_type.value}")
    
    async def submit(
        self,
        job_type: JobType,
        params: Optional[Dict[str, Any]] = None,
        notebook_id: Optional[str] = None,
        handler: Optional[JobHandler] = None
    ) -> str:
        """Submit a job to the queue.
        
        Args:
            job_type: Type of job
            params: Parameters to pass to handler
            notebook_id: Optional notebook context
            handler: Optional custom handler (overrides registered handler)
            
        Returns:
            Job ID
        """
        job_id = str(uuid.uuid4())[:8]
        
        job = Job(
            id=job_id,
            job_type=job_type,
            status=JobStatus.PENDING,
            created_at=datetime.utcnow(),
            params=params or {},
            notebook_id=notebook_id,
            _cancel_event=asyncio.Event()
        )
        
        self._jobs[job_id] = job
        
        # Use custom handler or registered handler
        actual_handler = handler or self._handlers.get(job_type)
        if not actual_handler:
            job.status = JobStatus.FAILED
            job.error = f"No handler registered for job type: {job_type.value}"
            await self._notify_listeners(job)
            return job_id
        
        # Start job execution
        job._task = asyncio.create_task(
            self._execute_job(job, actual_handler)
        )
        
        print(f"[JobQueue] Submitted job {job_id} ({job_type.value})")
        await self._notify_listeners(job)
        
        return job_id
    
    async def _execute_job(self, job: Job, handler: JobHandler):
        """Execute a job with the semaphore for concurrency control."""
        async with self._semaphore:
            job.status = JobStatus.RUNNING
            job.started_at = datetime.utcnow()
            await self._notify_listeners(job)
            
            async def report_progress(progress: JobProgress):
                job.progress = progress
                await self._notify_listeners(job)
            
            try:
                result = await handler(
                    job.params,
                    report_progress,
                    job._cancel_event
                )
                
                if job._cancel_event.is_set():
                    job.status = JobStatus.CANCELLED
                    job.error = "Job was cancelled"
                else:
                    job.status = JobStatus.COMPLETED
                    job.result = result
                    job.progress.percent = 100
                    job.progress.message = "Completed"
                    
            except asyncio.CancelledError:
                job.status = JobStatus.CANCELLED
                job.error = "Job was cancelled"
            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = str(e)
                print(f"[JobQueue] Job {job.id} failed: {e}")
                traceback.print_exc()
            finally:
                job.completed_at = datetime.utcnow()
                await self._notify_listeners(job)
                self._cleanup_old_jobs()
    
    async def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job status."""
        job = self._jobs.get(job_id)
        if not job:
            return None
        return job.to_dict()
    
    async def get_result(self, job_id: str) -> Optional[Any]:
        """Get job result (only available after completion)."""
        job = self._jobs.get(job_id)
        if not job or job.status != JobStatus.COMPLETED:
            return None
        return job.result
    
    async def cancel(self, job_id: str) -> bool:
        """Request job cancellation.
        
        Returns True if cancellation was requested, False if job not found or already done.
        """
        job = self._jobs.get(job_id)
        if not job:
            return False
        
        if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            return False
        
        # Signal cancellation
        if job._cancel_event:
            job._cancel_event.set()
        
        # Cancel the task if running
        if job._task and not job._task.done():
            job._task.cancel()
        
        print(f"[JobQueue] Cancellation requested for job {job_id}")
        return True
    
    async def list_jobs(
        self,
        notebook_id: Optional[str] = None,
        status: Optional[JobStatus] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """List jobs with optional filters."""
        jobs = list(self._jobs.values())
        
        if notebook_id:
            jobs = [j for j in jobs if j.notebook_id == notebook_id]
        if status:
            jobs = [j for j in jobs if j.status == status]
        
        # Most recent first
        jobs = sorted(jobs, key=lambda j: j.created_at, reverse=True)
        
        return [j.to_dict() for j in jobs[:limit]]
    
    def add_listener(
        self,
        callback: Callable[[Job], Awaitable[None]],
        job_id: Optional[str] = None
    ):
        """Add a progress listener.
        
        If job_id is provided, only listen to that job.
        Otherwise, listen to all jobs.
        """
        if job_id:
            if job_id not in self._progress_listeners:
                self._progress_listeners[job_id] = []
            self._progress_listeners[job_id].append(callback)
        else:
            self._global_listeners.append(callback)
    
    def remove_listener(
        self,
        callback: Callable[[Job], Awaitable[None]],
        job_id: Optional[str] = None
    ):
        """Remove a progress listener."""
        if job_id and job_id in self._progress_listeners:
            try:
                self._progress_listeners[job_id].remove(callback)
            except ValueError:
                pass
        else:
            try:
                self._global_listeners.remove(callback)
            except ValueError:
                pass
    
    async def _notify_listeners(self, job: Job):
        """Notify all relevant listeners about job update."""
        listeners = self._global_listeners.copy()
        if job.id in self._progress_listeners:
            listeners.extend(self._progress_listeners[job.id])
        
        for listener in listeners:
            try:
                await listener(job)
            except Exception as e:
                print(f"[JobQueue] Listener error: {e}")
    
    def _cleanup_old_jobs(self):
        """Remove old completed jobs to prevent memory growth."""
        completed = [
            j for j in self._jobs.values() 
            if j.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
        ]
        
        if len(completed) > self.MAX_COMPLETED_JOBS:
            # Sort by completion time, remove oldest
            completed.sort(key=lambda j: j.completed_at or datetime.min)
            to_remove = completed[:-self.MAX_COMPLETED_JOBS]
            for job in to_remove:
                del self._jobs[job.id]
                if job.id in self._progress_listeners:
                    del self._progress_listeners[job.id]
    
    async def wait_for_job(self, job_id: str, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Wait for a job to complete.
        
        Args:
            job_id: Job ID to wait for
            timeout: Optional timeout in seconds
            
        Returns:
            Final job status dict, or None if timeout
        """
        job = self._jobs.get(job_id)
        if not job:
            return None
        
        if job._task:
            try:
                await asyncio.wait_for(job._task, timeout=timeout)
            except asyncio.TimeoutError:
                return None
            except asyncio.CancelledError:
                pass
        
        return job.to_dict()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        jobs = list(self._jobs.values())
        return {
            "total_jobs": len(jobs),
            "pending": len([j for j in jobs if j.status == JobStatus.PENDING]),
            "running": len([j for j in jobs if j.status == JobStatus.RUNNING]),
            "completed": len([j for j in jobs if j.status == JobStatus.COMPLETED]),
            "failed": len([j for j in jobs if j.status == JobStatus.FAILED]),
            "cancelled": len([j for j in jobs if j.status == JobStatus.CANCELLED]),
            "max_concurrent": self.MAX_CONCURRENT_JOBS
        }


# Singleton instance
job_queue = JobQueue()


# =============================================================================
# Built-in Job Handlers
# =============================================================================

async def _topic_rebuild_handler(
    params: Dict[str, Any],
    report_progress: Callable[[JobProgress], Awaitable[None]],
    cancel_event: asyncio.Event
) -> Dict[str, Any]:
    """Handler for topic model rebuild jobs.
    
    Rebuilds BERTopic model from all sources in a notebook.
    Integrates with constellation WebSocket for UI progress updates.
    """
    from services.topic_modeling import topic_modeling_service
    from storage.source_store import source_store
    from services.rag_engine import rag_engine
    from api.constellation_ws import notify_build_progress, notify_build_complete
    import numpy as np
    
    notebook_id = params.get("notebook_id")
    if not notebook_id:
        raise ValueError("notebook_id is required")
    
    try:
        print(f"[TopicModel] Starting rebuild for notebook {notebook_id}")
        
        await report_progress(JobProgress(
            percent=5,
            message="Loading sources...",
            current_step=1,
            total_steps=4
        ))
        
        # Also notify constellation WebSocket for existing UI
        await notify_build_progress({
            "notebook_id": notebook_id,
            "progress": 5,
            "status": "Loading sources..."
        })
        
        # Get all sources
        sources = await source_store.list(notebook_id)
        if not sources:
            await notify_build_complete()
            return {"topics": 0, "message": "No sources found"}
        
        print(f"[TopicModel] Found {len(sources)} sources to process")
        
        if cancel_event.is_set():
            await notify_build_complete()
            return {"cancelled": True}
        
        await report_progress(JobProgress(
            percent=10,
            message=f"Processing {len(sources)} sources...",
            current_step=2,
            total_steps=4,
            details={"source_count": len(sources)}
        ))
        
        # Collect all chunks and embeddings
        all_chunks = []
        all_embeddings = []
        chunk_metadata = []
        
        for i, source in enumerate(sources):
            if cancel_event.is_set():
                await notify_build_complete()
                return {"cancelled": True}
            
            content = source.get("content", "")
            if not content or len(content) < 100:
                print(f"[TopicModel] Skipping source {i+1}: no content or too short")
                continue
            
            source_id = source.get("id", "")
            filename = source.get("filename", "unknown")
            print(f"[TopicModel] Chunking source {i+1}/{len(sources)}: {filename}")
            
            # Chunk the content
            chunks = rag_engine._chunk_text(content)
            if not chunks:
                continue
            
            # Generate embeddings for chunks
            embeddings = rag_engine.encode(chunks)
            
            # Collect
            all_chunks.extend(chunks)
            all_embeddings.append(embeddings)
            for chunk in chunks:
                chunk_metadata.append({"source_id": source_id, "notebook_id": notebook_id})
            
            # Update progress (10-50% range for chunking)
            pct = 10 + int(40 * (i + 1) / len(sources))
            await report_progress(JobProgress(
                percent=pct,
                message=f"Chunked {i + 1}/{len(sources)} sources ({len(all_chunks)} chunks)",
                current_step=2,
                total_steps=4,
                details={"chunks_collected": len(all_chunks), "current_source": filename}
            ))
            
            await notify_build_progress({
                "notebook_id": notebook_id,
                "progress": pct,
                "status": f"Chunked {i + 1}/{len(sources)} sources ({len(all_chunks)} chunks)"
            })
            
            await asyncio.sleep(0.01)  # Yield for responsiveness
        
        if not all_chunks:
            print(f"[TopicModel] No chunks collected from any source")
            await notify_build_complete()
            return {"topics": 0, "message": "No chunks found in sources"}
        
        # Combine embeddings
        combined_embeddings = np.vstack(all_embeddings) if all_embeddings else None
        
        print(f"[TopicModel] Collected {len(all_chunks)} chunks, fitting BERTopic...")
        
        await report_progress(JobProgress(
            percent=55,
            message=f"Discovering topics from {len(all_chunks)} chunks...",
            current_step=3,
            total_steps=4,
            details={"total_chunks": len(all_chunks)}
        ))
        
        await notify_build_progress({
            "notebook_id": notebook_id,
            "progress": 55,
            "status": f"Discovering topics from {len(all_chunks)} chunks..."
        })
        
        # Fit topic model
        result = await topic_modeling_service.fit_all(
            texts=all_chunks,
            embeddings=combined_embeddings,
            metadata=chunk_metadata,
            notebook_id=notebook_id
        )
        
        print(f"[TopicModel] BERTopic fit complete: {result}")
        
        await report_progress(JobProgress(
            percent=90,
            message=f"Found {result.get('topics_found', 0)} topics",
            current_step=4,
            total_steps=4
        ))
        
        await notify_build_progress({
            "notebook_id": notebook_id,
            "progress": 90,
            "status": f"Found {result.get('topics_found', 0)} topics"
        })
        
        # Get final stats
        stats = await topic_modeling_service.get_stats(notebook_id)
        topics_found = stats.get("total_topics", result.get("topics_found", 0))
        
        print(f"[TopicModel] Rebuild complete: {stats}")
        
        await notify_build_progress({
            "notebook_id": notebook_id,
            "progress": 100,
            "topics_found": topics_found
        })
        
        await notify_build_complete()
        
        return {
            "topics": topics_found,
            "chunks_processed": len(all_chunks),
            "sources_processed": len(sources),
            "message": f"Rebuilt topic model with {topics_found} topics from {len(all_chunks)} chunks"
        }
        
    except Exception as e:
        print(f"[TopicModel] Rebuild error: {e}")
        traceback.print_exc()
        await notify_build_complete()
        raise


# Register built-in handlers
job_queue.register_handler(JobType.TOPIC_REBUILD, _topic_rebuild_handler)
