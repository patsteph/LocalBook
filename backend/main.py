"""FastAPI main application"""
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from api import notebooks, sources, chat, skills, audio, source_viewer, web, settings as settings_api, embeddings, timeline, export, reindex
from config import settings
from services.model_warmup import start_warmup_task, stop_warmup_task

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for the application"""
    print(f"🚀 LocalBook API starting on {settings.api_host}:{settings.api_port}")
    print(f"📁 Data directory: {settings.data_dir}")
    print(f"🤖 LLM Provider: {settings.llm_provider}")
    print(f"🔥 Model: {settings.ollama_model}")
    
    # Start background task to keep models warm
    await start_warmup_task()
    
    yield
    
    # Stop warmup task on shutdown
    await stop_warmup_task()
    print("👋 LocalBook API shutting down")

app = FastAPI(
    title="LocalBook API",
    description="Backend API for LocalBook - Your local NotebookLM alternative",
    version="0.1.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
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

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "LocalBook API",
        "version": "0.1.0",
        "docs": "/docs"
    }

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,  # Pass app object directly for PyInstaller compatibility
        host=settings.api_host,
        port=settings.api_port,
        log_level="warning"  # Reduce noise in production
    )
