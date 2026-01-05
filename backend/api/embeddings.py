"""Embeddings API endpoints"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from storage.notebook_store import notebook_store
from services.rag_engine import rag_engine

router = APIRouter()

class ChangeModelRequest(BaseModel):
    model_name: str

@router.get("/model/{notebook_id}")
async def get_current_model(notebook_id: str):
    """Get the current embedding model for a notebook"""
    try:
        notebook = await notebook_store.get(notebook_id)
        if not notebook:
            raise HTTPException(status_code=404, detail="Notebook not found")

        # Get model from notebook metadata or use default (snowflake-arctic-embed2 via Ollama)
        model_name = notebook.get("embedding_model", "snowflake-arctic-embed2")
        needs_reembedding = notebook.get("needs_reembedding", False)

        # Get model dimensions based on model name
        dimensions_map = {
            "snowflake-arctic-embed2": 1024,
            "nomic-embed-text": 768,
            "mxbai-embed-large": 1024,
            "all-minilm": 384,
        }

        dimensions = dimensions_map.get(model_name, 1024)

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
