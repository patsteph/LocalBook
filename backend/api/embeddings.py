"""Embeddings API endpoints"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from storage.notebook_store import notebook_store
from config import settings

router = APIRouter()

class ChangeModelRequest(BaseModel):
    model_name: str

@router.get("/info")
async def get_embedding_info():
    """Get current embedding and reranker configuration"""
    return {
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dim,
        "reranker": settings.reranker_model,
        "reranker_type": settings.reranker_type
    }

@router.get("/model/{notebook_id}")
async def get_current_model(notebook_id: str):
    """Get the current embedding model for a notebook"""
    try:
        notebook = await notebook_store.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # One embedder serves every notebook on this Mac: the live one (per Mac, not per
        # notebook — a notebook's old `embedding_model` field predates MLX and is ignored).
        from config import settings
        model_name = settings.embedding_model
        needs_reembedding = notebook.get("needs_reembedding", False)
        dimensions = int(settings.embedding_dim)

        return {
            "model_name": model_name,
            "dimensions": dimensions,
            "needs_reembedding": needs_reembedding
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get model: {str(e)}")

@router.post("/model/{notebook_id}")
async def change_model(notebook_id: str, request: ChangeModelRequest):
    """Change the embedding model for a notebook"""
    try:
        notebook = await notebook_store.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # Update notebook metadata
        notebook["embedding_model"] = request.model_name
        notebook["needs_reembedding"] = True

        await notebook_store.update(notebook_id, notebook)

        return {"message": "Model changed successfully", "needs_reembedding": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to change model: {str(e)}")

@router.post("/reembed/{notebook_id}")
async def reembed_notebook(notebook_id: str):
    """Re-embed all documents in a notebook with the new model"""
    try:
        notebook = await notebook_store.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # Start re-embedding process
        # This would typically be done in the background
        # For now, we'll mark it as in progress
        notebook["reembed_status"] = "in_progress"
        notebook["reembed_progress"] = {"current": 0, "total": 0}

        await notebook_store.update(notebook_id, notebook)

        # TODO: Implement actual re-embedding logic
        # This should:
        # 1. Get all sources for the notebook
        # 2. Re-extract and re-embed each source's text
        # 3. Update progress as it goes
        # 4. Mark needs_reembedding=False when complete

        return {"message": "Re-embedding started"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start re-embedding: {str(e)}")

@router.get("/progress/{notebook_id}")
async def get_reembed_progress(notebook_id: str):
    """Get the re-embedding progress for a notebook"""
    try:
        notebook = await notebook_store.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        progress = notebook.get("reembed_progress", {"current": 0, "total": 0})
        status = notebook.get("reembed_status", "idle")

        return {
            "current": progress.get("current", 0),
            "total": progress.get("total", 0),
            "status": status
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get progress: {str(e)}")


# ── switching the embedding model (shadow build → switch at next launch) ─────
# services/embedding_migration.py. The Locker's "Use" on an embedder starts here; a direct
# swap is refused (/evaluator/swap) because it drops and rebuilds every index in place.


@router.get("/migration")
async def migration_status():
    from services import embedding_migration
    return embedding_migration.status()


@router.get("/migration/preflight")
async def migration_preflight(model: str):
    from services import embedding_migration
    return embedding_migration.preflight(model)


@router.post("/migration/start")
async def migration_start(payload: dict):
    from services import embedding_migration
    model = (payload or {}).get("model") or ""
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    return await embedding_migration.start(model)


@router.post("/migration/stop")
async def migration_stop():
    from services import embedding_migration
    return {"stopping": embedding_migration.stop()}


@router.post("/migration/switch")
async def migration_switch():
    from services import embedding_migration
    return await embedding_migration.request_switch()


@router.post("/migration/rollback")
async def migration_rollback():
    from services import embedding_migration
    return embedding_migration.request_rollback()


@router.post("/migration/cancel")
async def migration_cancel():
    from services import embedding_migration
    embedding_migration.cancel_pending()
    return {"ok": True}


@router.delete("/migration/previous")
async def migration_remove_previous():
    from services import embedding_migration
    return embedding_migration.remove_previous()
