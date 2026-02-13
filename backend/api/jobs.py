"""Jobs API - Batch Processing Queue Endpoints

Provides REST API and WebSocket for managing long-running jobs.

Endpoints:
- POST /jobs/submit - Submit a new job
- GET /jobs/{job_id} - Get job status
- GET /jobs/{job_id}/result - Get job result
- POST /jobs/{job_id}/cancel - Cancel a job
- GET /jobs - List jobs
- GET /jobs/stats - Queue statistics
- WS /jobs/ws - WebSocket for real-time progress updates
"""

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel

from services.job_queue import job_queue, JobType, JobStatus, Job


router = APIRouter(prefix="/jobs", tags=["jobs"])


# =============================================================================
# Request/Response Models
# =============================================================================

class JobSubmitRequest(BaseModel):
    """Request to submit a new job."""
    job_type: str
    params: Optional[Dict[str, Any]] = None
    notebook_id: Optional[str] = None


class JobSubmitResponse(BaseModel):
    """Response after submitting a job."""
    job_id: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    """Job status response."""
    id: str
    job_type: str
    status: str
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    progress: Dict[str, Any]
    error: Optional[str]
    params: Dict[str, Any]
    notebook_id: Optional[str]
    duration_seconds: Optional[float]


class JobListResponse(BaseModel):
    """List of jobs."""
    jobs: List[Dict[str, Any]]
    total: int


class JobStatsResponse(BaseModel):
    """Queue statistics."""
    total_jobs: int
    pending: int
    running: int
    completed: int
    failed: int
    cancelled: int
    max_concurrent: int


# =============================================================================
# REST Endpoints
# =============================================================================

@router.post("/submit", response_model=JobSubmitResponse)
async def submit_job(request: JobSubmitRequest):
    """Submit a new job to the queue.
    
    Job types:
    - topic_rebuild: Rebuild topic model for a notebook
    - document_ingest: Ingest a large document
    - batch_ingest: Ingest multiple documents
    - rlm_query: Run an RLM (recursive) query
    - contradiction_scan: Scan for contradictions
    - timeline_extract: Extract timeline from sources
    - export: Export notebook data
    """
    try:
        job_type = JobType(request.job_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid job type: {request.job_type}. Valid types: {[t.value for t in JobType]}"
        )
    
    job_id = await job_queue.submit(
        job_type=job_type,
        params=request.params,
        notebook_id=request.notebook_id
    )
    
    status = await job_queue.get_status(job_id)
    
    return JobSubmitResponse(
        job_id=job_id,
        status=status.get("status", "pending") if status else "unknown",
        message=f"Job {job_id} submitted successfully"
    )


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get the status of a job."""
    status = await job_queue.get_status(job_id)
    
    if not status:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    return JobStatusResponse(**status)


@router.get("/{job_id}/result")
async def get_job_result(job_id: str):
    """Get the result of a completed job.
    
    Returns 404 if job not found, 400 if job not completed.
    """
    status = await job_queue.get_status(job_id)
    
    if not status:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    if status.get("status") != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} is not completed (status: {status.get('status')})"
        )
    
    result = await job_queue.get_result(job_id)
    return {"job_id": job_id, "result": result}


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Request cancellation of a job.
    
    Returns success if cancellation was requested.
    The job may not stop immediately.
    """
    success = await job_queue.cancel(job_id)
    
    if not success:
        status = await job_queue.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job {job_id} (status: {status.get('status')})"
        )
    
    return {"job_id": job_id, "message": "Cancellation requested"}


@router.get("", response_model=JobListResponse)
async def list_jobs(
    notebook_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200)
):
    """List jobs with optional filters."""
    status_filter = None
    if status:
        try:
            status_filter = JobStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {status}. Valid: {[s.value for s in JobStatus]}"
            )
    
    jobs = await job_queue.list_jobs(
        notebook_id=notebook_id,
        status=status_filter,
        limit=limit
    )
    
    return JobListResponse(jobs=jobs, total=len(jobs))


@router.get("/stats", response_model=JobStatsResponse)
async def get_queue_stats():
    """Get queue statistics."""
    stats = job_queue.get_stats()
    return JobStatsResponse(**stats)


# =============================================================================
# WebSocket for Real-time Updates
# =============================================================================

class WebSocketManager:
    """Manages WebSocket connections for job progress updates."""
    
    def __init__(self):
        self.connections: Dict[str, List[WebSocket]] = {}  # job_id -> websockets
        self.global_connections: List[WebSocket] = []  # All jobs
    
    async def connect(self, websocket: WebSocket, job_id: Optional[str] = None):
        """Accept a new WebSocket connection."""
        await websocket.accept()
        
        if job_id:
            if job_id not in self.connections:
                self.connections[job_id] = []
            self.connections[job_id].append(websocket)
        else:
            self.global_connections.append(websocket)
        
        # Register as job queue listener
        async def on_job_update(job: Job):
            await self.broadcast(job, job_id)
        
        job_queue.add_listener(on_job_update, job_id)
        
        return on_job_update
    
    def disconnect(self, websocket: WebSocket, job_id: Optional[str], listener):
        """Remove a WebSocket connection."""
        if job_id and job_id in self.connections:
            try:
                self.connections[job_id].remove(websocket)
            except ValueError:
                pass
        else:
            try:
                self.global_connections.remove(websocket)
            except ValueError:
                pass
        
        job_queue.remove_listener(listener, job_id)
    
    async def broadcast(self, job: Job, filter_job_id: Optional[str] = None):
        """Broadcast job update to relevant connections."""
        message = json.dumps(job.to_dict())
        
        # Send to job-specific connections
        if job.id in self.connections:
            dead = []
            for ws in self.connections[job.id]:
                try:
                    await ws.send_text(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.connections[job.id].remove(ws)
        
        # Send to global connections (if not filtered)
        if not filter_job_id:
            dead = []
            for ws in self.global_connections:
                try:
                    await ws.send_text(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.global_connections.remove(ws)


ws_manager = WebSocketManager()


@router.websocket("/ws")
async def websocket_all_jobs(websocket: WebSocket):
    """WebSocket endpoint for all job updates.
    
    Connect to receive real-time updates for all jobs.
    Messages are JSON with full job status.
    """
    listener = await ws_manager.connect(websocket, job_id=None)
    
    try:
        # Send current queue stats on connect
        await websocket.send_json({
            "type": "connected",
            "stats": job_queue.get_stats()
        })
        
        # Keep connection alive, handle any incoming messages
        while True:
            try:
                data = await websocket.receive_text()
                # Could handle commands here (e.g., subscribe to specific job)
                msg = json.loads(data)
                
                if msg.get("action") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg.get("action") == "status":
                    job_id = msg.get("job_id")
                    if job_id:
                        status = await job_queue.get_status(job_id)
                        await websocket.send_json({
                            "type": "status",
                            "job": status
                        })
                        
            except json.JSONDecodeError:
                pass
                
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket, None, listener)


@router.websocket("/ws/{job_id}")
async def websocket_job(websocket: WebSocket, job_id: str):
    """WebSocket endpoint for a specific job's updates.
    
    Connect to receive real-time updates for a single job.
    """
    # Check job exists
    status = await job_queue.get_status(job_id)
    if not status:
        await websocket.close(code=4004, reason="Job not found")
        return
    
    listener = await ws_manager.connect(websocket, job_id=job_id)
    
    try:
        # Send current status on connect
        await websocket.send_json({
            "type": "connected",
            "job": status
        })
        
        while True:
            try:
                data = await websocket.receive_text()
                msg = json.loads(data)
                
                if msg.get("action") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg.get("action") == "cancel":
                    success = await job_queue.cancel(job_id)
                    await websocket.send_json({
                        "type": "cancel_response",
                        "success": success
                    })
                    
            except json.JSONDecodeError:
                pass
                
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket, job_id, listener)
