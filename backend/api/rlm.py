"""RLM API - Notebook-Scale Analysis Endpoints

v1.1.0: REST API for RLM (Recursive Language Model) analysis.
Provides deep research capabilities for 50+ document notebooks.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import uuid

from services.rlm_executor import rlm_executor, run_rlm_job
from services.job_queue import job_queue, JobStatus
from storage.source_store import source_store

router = APIRouter(prefix="/rlm", tags=["rlm"])


class RLMQueryRequest(BaseModel):
    """Request for RLM analysis."""
    notebook_id: str
    query: str
    async_mode: bool = True  # Default to async since RLM takes 5-10 min


class RLMQueryResponse(BaseModel):
    """Response from RLM analysis."""
    job_id: Optional[str] = None
    status: str
    message: str
    answer: Optional[str] = None
    sources_cited: Optional[List[str]] = None
    iterations: Optional[int] = None


@router.post("/analyze", response_model=RLMQueryResponse)
async def analyze_notebook(
    request: RLMQueryRequest,
    background_tasks: BackgroundTasks
):
    """
    Analyze entire notebook with RLM (Recursive Language Model).
    
    This is for deep research queries that span 50+ documents.
    Expected latency: 5-10 minutes for large notebooks.
    
    By default runs async and returns a job_id to poll for results.
    """
    # Validate notebook exists and has sources
    sources_data = source_store._load_data()
    notebook_sources = [
        s for s in sources_data.get("sources", {}).values()
        if s.get("notebook_id") == request.notebook_id
    ]
    
    if not notebook_sources:
        raise HTTPException(
            status_code=404,
            detail="Notebook not found or has no sources"
        )
    
    source_count = len(notebook_sources)
    
    # Recommend regular chat for small notebooks
    if source_count < 10:
        return RLMQueryResponse(
            status="redirect",
            message=f"This notebook has only {source_count} sources. Use regular /chat for faster results.",
            answer=None
        )
    
    if request.async_mode:
        # Queue as background job
        job_id = str(uuid.uuid4())
        
        async def run_analysis():
            def update_progress(message: str):
                job_queue.update_job(job_id, message=message)
            
            try:
                result = await rlm_executor.analyze_notebook(
                    notebook_id=request.notebook_id,
                    query=request.query,
                    progress_callback=update_progress
                )
                
                job_queue.complete_job(job_id, result={
                    "answer": result.answer,
                    "sources_cited": result.sources_cited,
                    "iterations": result.iterations,
                    "total_time_seconds": result.total_time_seconds
                })
            except Exception as e:
                job_queue.fail_job(job_id, str(e))
        
        # Register job
        job_queue.create_job(
            job_id=job_id,
            job_type="rlm_analysis",
            notebook_id=request.notebook_id,
            metadata={"query": request.query, "source_count": source_count}
        )
        
        background_tasks.add_task(run_analysis)
        
        return RLMQueryResponse(
            job_id=job_id,
            status="queued",
            message=f"Analyzing {source_count} sources. This may take 5-10 minutes. Poll /jobs/{job_id} for status."
        )
    
    else:
        # Synchronous mode (blocking - use with caution)
        result = await rlm_executor.analyze_notebook(
            notebook_id=request.notebook_id,
            query=request.query
        )
        
        return RLMQueryResponse(
            status="completed",
            message=f"Analysis complete after {result.iterations} iterations",
            answer=result.answer,
            sources_cited=result.sources_cited,
            iterations=result.iterations
        )


@router.get("/status/{job_id}")
async def get_analysis_status(job_id: str):
    """Get status of an RLM analysis job."""
    job = job_queue.get_job(job_id)
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    response = {
        "job_id": job_id,
        "status": job.status.value,
        "message": job.message,
        "progress": job.progress,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }
    
    if job.status == JobStatus.COMPLETED and job.result:
        response["answer"] = job.result.get("answer")
        response["sources_cited"] = job.result.get("sources_cited", [])
        response["iterations"] = job.result.get("iterations")
        response["total_time_seconds"] = job.result.get("total_time_seconds")
    
    if job.status == JobStatus.FAILED:
        response["error"] = job.error
    
    return response


@router.get("/capabilities/{notebook_id}")
async def check_rlm_capabilities(notebook_id: str):
    """
    Check if RLM analysis is recommended for a notebook.
    
    Returns info about notebook size and RLM suitability.
    """
    sources_data = source_store._load_data()
    notebook_sources = [
        s for s in sources_data.get("sources", {}).values()
        if s.get("notebook_id") == notebook_id
    ]
    
    source_count = len(notebook_sources)
    total_chars = sum(
        len(s.get("content", "")) or s.get("char_count", 0)
        for s in notebook_sources
    )
    
    # Determine recommendation
    if source_count >= 50:
        recommendation = "strongly_recommended"
        reason = "Large notebook benefits significantly from RLM deep analysis"
    elif source_count >= 20:
        recommendation = "recommended"
        reason = "Notebook size suitable for RLM cross-document analysis"
    elif source_count >= 10:
        recommendation = "optional"
        reason = "RLM can help for complex cross-document queries"
    else:
        recommendation = "not_recommended"
        reason = "Use regular chat for small notebooks - faster results"
    
    return {
        "notebook_id": notebook_id,
        "source_count": source_count,
        "total_characters": total_chars,
        "estimated_tokens": total_chars // 4,
        "rlm_recommendation": recommendation,
        "reason": reason,
        "estimated_analysis_time": f"{max(1, source_count // 10)} - {max(2, source_count // 5)} minutes"
    }
