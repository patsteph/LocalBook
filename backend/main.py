"""FastAPI main application"""
import multiprocessing
import sys

# PyInstaller multiprocessing freeze support - must be at very top
if getattr(sys, 'frozen', False):
    multiprocessing.freeze_support()

import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from api import notebooks, sources, chat, skills, audio, source_viewer, web, settings as settings_api, embeddings, timeline, export, reindex, memory, graph, constellation_ws, updates, content, exploration
from api.updates import check_if_upgrade, set_startup_status, mark_startup_complete, CURRENT_VERSION
from config import settings
from services.model_warmup import start_warmup_task, stop_warmup_task
from services.rag_engine import check_and_reindex_on_startup

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for the application"""
    print(f"🚀 LocalBook API starting on {settings.api_host}:{settings.api_port}")
    print(f"📁 Data directory: {settings.data_dir}")
    print(f"🤖 LLM Provider: {settings.llm_provider}")
    print(f"🔥 Model: {settings.ollama_model}")
    
    # Check if this is an upgrade
    is_upgrade, previous_version = check_if_upgrade()
    if is_upgrade:
        print(f"⬆️ Upgrading from v{previous_version} to v{CURRENT_VERSION}")
        set_startup_status("upgrading", f"Upgrading from v{previous_version} to v{CURRENT_VERSION}...", 10)
    else:
        set_startup_status("starting", "Starting LocalBook...", 10)
    
    # Start background task to keep models warm
    set_startup_status("starting", "Warming up AI models...", 30)
    await start_warmup_task()
    
    # Check for embedding dimension mismatch and auto-reindex if needed
    if is_upgrade:
        set_startup_status("reindexing", "Checking embeddings compatibility...", 50)
    await check_and_reindex_on_startup()
    
    # Mark startup complete
    mark_startup_complete()
    print(f"✅ LocalBook v{CURRENT_VERSION} ready!")
    
    yield
    
    # Stop warmup task on shutdown
    await stop_warmup_task()
    print("👋 LocalBook API shutting down")

app = FastAPI(
    title="LocalBook API",
    description="Backend API for LocalBook - Your local NotebookLM alternative",
    version=CURRENT_VERSION,
    lifespan=lifespan
)

# CORS middleware
# Allow all origins since backend binds to localhost only (not network-exposed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(notebooks.router, prefix="/notebooks", tags=["notebooks"])
app.include_router(sources.router, prefix="/sources", tags=["sources"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(skills.router, prefix="/skills", tags=["skills"])
app.include_router(audio.router, prefix="/audio", tags=["audio"])
app.include_router(source_viewer.router, prefix="/source-viewer", tags=["source-viewer"])
app.include_router(web.router, prefix="/web", tags=["web"])
app.include_router(settings_api.router, prefix="/settings", tags=["settings"])
app.include_router(embeddings.router, prefix="/embeddings", tags=["embeddings"])
app.include_router(timeline.router, prefix="/timeline", tags=["timeline"])
app.include_router(export.router, prefix="/export", tags=["export"])
app.include_router(reindex.router, prefix="/reindex", tags=["reindex"])
app.include_router(memory.router, tags=["memory"])
app.include_router(graph.router, tags=["knowledge-graph"])
app.include_router(constellation_ws.router, tags=["constellation"])
app.include_router(updates.router, tags=["updates"])
app.include_router(content.router, prefix="/content", tags=["content"])
app.include_router(exploration.router, tags=["exploration"])

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "LocalBook API",
        "version": CURRENT_VERSION,
        "docs": "/docs"
    }

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="warning"
    )
    server = uvicorn.Server(config)
    server.run()
