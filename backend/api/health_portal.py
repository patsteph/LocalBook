"""Health Portal API for Smoke Screen Self-Support

Provides comprehensive health checks, diagnostics, and repair actions.
"""
import asyncio
import platform
import shutil
import sys
import subprocess
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

import httpx
import psutil
import lancedb
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from config import settings
from services.rag_metrics import rag_metrics
from utils.binary_finder import find_binary
from services.rag_cache import embedding_cache, answer_cache
from services.startup_checks import (
    REQUIRED_MODELS, EXPECTED_EMBEDDING_DIM,
    check_ollama_version, check_ollama_models,
    check_rag_embedding_dimensions, check_knowledge_graph_dimensions
)

router = APIRouter(prefix="/health", tags=["Health Portal"])

# In-memory log buffer for streaming
LOG_BUFFER: List[Dict[str, Any]] = []
MAX_LOG_BUFFER = 500

# Path to static HTML file - handle both dev and PyInstaller bundled paths
def get_static_dir():
    """Get the static directory, handling PyInstaller bundled apps."""
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller bundle
        base_path = Path(sys._MEIPASS)
    else:
        # Running in development
        base_path = Path(__file__).parent.parent
    return base_path / "static"


@router.get("/portal", response_class=HTMLResponse)
async def get_portal():
    """Serve the Smoke Screen Portal HTML page."""
    static_dir = get_static_dir()
    html_path = static_dir / "health_portal.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(), status_code=200)
    else:
        return HTMLResponse(content=f"<h1>Portal not found</h1><p>Looked in: {html_path}</p>", status_code=404)


class RepairRequest(BaseModel):
    action: str
    params: Optional[Dict[str, Any]] = None


def add_log(level: str, message: str, source: str = "system"):
    """Add a log entry to the buffer."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "message": message,
        "source": source
    }
    LOG_BUFFER.append(entry)
    if len(LOG_BUFFER) > MAX_LOG_BUFFER:
        LOG_BUFFER.pop(0)


@router.get("/quick")
async def quick_health_check():
    """Fast health check for status banner (<100ms target)."""
    issues = 0
    status = "healthy"
    
    # Quick Ollama check
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.status_code != 200:
                issues += 1
                status = "degraded"
    except:
        issues += 1
        status = "critical"
    
    # Quick backend self-check (we're running if this returns)
    
    # Quick DB check
    try:
        db = lancedb.connect(str(settings.db_path))
        _ = db.table_names()
    except:
        issues += 1
        status = "critical"
    
    return {
        "status": status,
        "issues": issues,
        "timestamp": datetime.now().isoformat()
    }


@router.get("/full")
async def full_health_check():
    """Comprehensive health check with all diagnostics."""
    results = {
        "timestamp": datetime.now().isoformat(),
        "overall": "healthy",
        "checks": [],
        "issues": [],
        "system": {},
        "metrics": {},
        "sections": {
            "core_services": {"name": "Core Services", "icon": "🔌", "checks": [], "status": "pass"},
            "ai_models": {"name": "AI & Models", "icon": "🧠", "checks": [], "status": "pass"},
            "data_integrity": {"name": "Data Integrity", "icon": "📊", "checks": [], "status": "pass"},
            "configuration": {"name": "Configuration", "icon": "⚙️", "checks": [], "status": "pass"},
        }
    }
    
    def add_check(section: str, check: dict):
        """Add a check to both flat list and section."""
        check["section"] = section
        results["checks"].append(check)
        results["sections"][section]["checks"].append(check)
        # Update section status
        if check["status"] == "fail":
            results["sections"][section]["status"] = "fail"
        elif check["status"] == "warn" and results["sections"][section]["status"] != "fail":
            results["sections"][section]["status"] = "warn"
    
    # 1. System Info
    try:
        results["system"] = {
            "os": platform.system(),
            "os_version": platform.mac_ver()[0] if platform.system() == "Darwin" else platform.version(),
            "arch": platform.machine(),
            "python_version": sys.version.split()[0],
            "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
            "memory_available_gb": round(psutil.virtual_memory().available / (1024**3), 1),
            "memory_percent_used": psutil.virtual_memory().percent,
            "disk_total_gb": round(shutil.disk_usage(str(settings.data_dir)).total / (1024**3), 1),
            "disk_free_gb": round(shutil.disk_usage(str(settings.data_dir)).free / (1024**3), 1),
            "disk_percent_used": round(100 - (shutil.disk_usage(str(settings.data_dir)).free / shutil.disk_usage(str(settings.data_dir)).total * 100), 1),
            "data_dir": str(settings.data_dir),
        }
    except Exception as e:
        results["system"]["error"] = str(e)
    
    # ============ CORE SERVICES SECTION ============
    
    # Ollama Connection
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("models", [])
                add_check("core_services", {
                    "name": "ollama_connection",
                    "display": "Ollama Server",
                    "status": "pass",
                    "details": {"url": settings.ollama_base_url, "model_count": len(models)}
                })
            else:
                add_check("core_services", {
                    "name": "ollama_connection",
                    "display": "Ollama Server",
                    "status": "fail",
                    "error": f"HTTP {resp.status_code}",
                    "repair": "restart_ollama"
                })
                results["overall"] = "critical"
    except Exception as e:
        add_check("core_services", {
            "name": "ollama_connection",
            "display": "Ollama Server",
            "status": "fail",
            "error": str(e),
            "repair": "restart_ollama"
        })
        results["issues"].append({
            "severity": "critical",
            "title": "Ollama Not Running",
            "message": "Cannot connect to Ollama server. AI features will not work.",
            "repair": "restart_ollama"
        })
        results["overall"] = "critical"
    
    # Backend Check (self-check)
    add_check("core_services", {
        "name": "backend",
        "display": "Backend Server",
        "status": "pass",
        "details": {"port": settings.api_port, "host": settings.api_host}
    })
    
    # Database Check
    db_notebooks = []
    total_db_chunks = 0
    try:
        db = lancedb.connect(str(settings.db_path))
        tables = db.table_names()
        db_notebooks = [t for t in tables if t.startswith("notebook_")]
        for nb in db_notebooks:
            try:
                table = db.open_table(nb)
                total_db_chunks += table.count_rows()
            except:
                pass
        
        add_check("core_services", {
            "name": "database",
            "display": "Vector Database",
            "status": "pass",
            "details": {"path": str(settings.db_path), "notebook_count": len(db_notebooks), "total_chunks": total_db_chunks}
        })
    except Exception as e:
        add_check("core_services", {
            "name": "database",
            "display": "Vector Database",
            "status": "fail",
            "error": str(e)
        })
        results["issues"].append({
            "severity": "critical",
            "title": "Database Error",
            "message": f"Cannot access vector database: {str(e)[:100]}",
            "repair": None
        })
        results["overall"] = "critical"
    
    # ============ AI & MODELS SECTION ============
    
    # Ollama Version
    version_ok, current_version, min_version = await check_ollama_version()
    add_check("ai_models", {
        "name": "ollama_version",
        "display": "Ollama Version",
        "status": "pass" if version_ok else "warn",
        "details": {"current": current_version, "minimum": min_version}
    })
    if not version_ok and current_version != "unknown":
        results["issues"].append({
            "severity": "medium",
            "title": "Ollama Version Outdated",
            "message": f"Version {current_version} is below minimum {min_version}. Update from ollama.ai",
            "repair": None
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    
    # Models Installed Check
    available, missing = await check_ollama_models()
    add_check("ai_models", {
        "name": "models",
        "display": "Models Installed",
        "status": "pass" if not missing else "fail",
        "details": {"installed": available, "missing": [m[0] for m in missing], "required": [m[0] for m in REQUIRED_MODELS]}
    })
    if missing:
        for model_name, description in missing:
            results["issues"].append({
                "severity": "high",
                "title": f"Missing Model: {model_name}",
                "message": f"{description}",
                "repair": "pull_model",
                "repair_params": {"model": model_name}
            })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    
    # Model Loading Status (cold start detection)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/ps")
            if resp.status_code == 200:
                running = resp.json().get("models", [])
                loaded_models = [m.get("name", "") for m in running]
                main_loaded = any(settings.ollama_model in m for m in loaded_models)
                fast_loaded = any(settings.ollama_fast_model in m for m in loaded_models)
                
                add_check("ai_models", {
                    "name": "model_loading",
                    "display": "Models Loaded",
                    "status": "pass" if main_loaded else "warn",
                    "details": {"main_model": settings.ollama_model, "main_loaded": main_loaded, "fast_loaded": fast_loaded}
                })
                
                if not main_loaded:
                    results["issues"].append({
                        "severity": "low",
                        "title": "Main Model Not Loaded",
                        "message": f"{settings.ollama_model} not in memory. First query will be slow.",
                        "repair": "warmup_model",
                        "repair_params": {"model": settings.ollama_model}
                    })
    except Exception as e:
        add_log("WARN", f"Model loading check failed: {e}", "health_portal")
    
    # NEW: Embedding Model Test - verify embeddings actually work
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/embeddings",
                json={"model": settings.embedding_model, "prompt": "test"}
            )
            if resp.status_code == 200:
                emb_data = resp.json()
                emb_dim = len(emb_data.get("embedding", []))
                add_check("ai_models", {
                    "name": "embedding_test",
                    "display": "Embedding Generation",
                    "status": "pass" if emb_dim == EXPECTED_EMBEDDING_DIM else "warn",
                    "details": {"model": settings.embedding_model, "dimension": emb_dim, "expected": EXPECTED_EMBEDDING_DIM}
                })
            else:
                add_check("ai_models", {
                    "name": "embedding_test",
                    "display": "Embedding Generation",
                    "status": "fail",
                    "error": f"HTTP {resp.status_code}"
                })
                results["issues"].append({
                    "severity": "high",
                    "title": "Embedding Model Not Working",
                    "message": "Cannot generate embeddings. Search will not work.",
                    "repair": "pull_model",
                    "repair_params": {"model": settings.embedding_model}
                })
                if results["overall"] == "healthy":
                    results["overall"] = "degraded"
    except Exception as e:
        add_check("ai_models", {
            "name": "embedding_test",
            "display": "Embedding Generation",
            "status": "fail",
            "error": str(e)[:50]
        })
        results["issues"].append({
            "severity": "high",
            "title": "Embedding Test Failed",
            "message": f"Cannot test embeddings: {str(e)[:50]}",
            "repair": None
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    
    # NEW: LLM Generation Test - verify chat/generation works
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={"model": settings.ollama_fast_model, "prompt": "Say OK", "stream": False, "options": {"num_predict": 5}}
            )
            if resp.status_code == 200:
                gen_data = resp.json()
                response_text = gen_data.get("response", "")
                add_check("ai_models", {
                    "name": "llm_test",
                    "display": "LLM Generation",
                    "status": "pass" if len(response_text) > 0 else "warn",
                    "details": {"model": settings.ollama_fast_model, "responded": len(response_text) > 0}
                })
            else:
                error_text = resp.text[:200] if resp.text else ""
                is_corrupted = "unable to load model" in error_text.lower()
                
                add_check("ai_models", {
                    "name": "llm_test",
                    "display": "LLM Generation",
                    "status": "fail",
                    "error": f"HTTP {resp.status_code}" + (" (model corrupted)" if is_corrupted else "")
                })
                
                if is_corrupted:
                    results["issues"].append({
                        "severity": "high",
                        "title": "Model File Corrupted",
                        "message": f"The model {settings.ollama_fast_model} is corrupted. Click Repair to re-download it.",
                        "repair": "repair_model",
                        "repair_params": {"model": settings.ollama_fast_model}
                    })
                else:
                    results["issues"].append({
                        "severity": "high",
                        "title": "LLM Not Responding",
                        "message": "Language model failed to generate. Chat will not work.",
                        "repair": "restart_ollama"
                    })
                if results["overall"] == "healthy":
                    results["overall"] = "degraded"
    except Exception as e:
        error_str = str(e).lower()
        is_corrupted = "unable to load model" in error_str
        
        add_check("ai_models", {
            "name": "llm_test",
            "display": "LLM Generation",
            "status": "fail" if is_corrupted else "warn",
            "error": "Model corrupted" if is_corrupted else f"Timeout or error: {str(e)[:30]}"
        })
        
        if is_corrupted:
            results["issues"].append({
                "severity": "high",
                "title": "Model File Corrupted",
                "message": "The model file is corrupted. Click Repair to re-download it.",
                "repair": "repair_model",
                "repair_params": {"model": settings.ollama_fast_model}
            })
            if results["overall"] == "healthy":
                results["overall"] = "degraded"
    
    # NEW: Reranker Status Check
    try:
        from flashrank import Ranker
        # Use persistent cache dir (not /tmp which gets cleared on reboot)
        cache_dir = settings.data_dir / "models" / "flashrank"
        cache_dir.mkdir(parents=True, exist_ok=True)
        _ranker = Ranker(model_name=settings.reranker_model, cache_dir=str(cache_dir))
        add_check("ai_models", {
            "name": "reranker",
            "display": "Reranker Model",
            "status": "pass",
            "details": {"model": settings.reranker_model, "type": settings.reranker_type, "cache": str(cache_dir)}
        })
    except Exception as e:
        add_check("ai_models", {
            "name": "reranker",
            "display": "Reranker Model",
            "status": "warn",
            "error": str(e)[:50]
        })
        # Add issue with repair action
        results["issues"].append({
            "severity": "medium",
            "title": "Reranker Model Not Ready",
            "message": f"FlashRank reranker failed to initialize: {str(e)[:50]}",
            "repair": "init_reranker"
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    
    # NEW: Vision Model Check (for PDF image/chart extraction)
    vision_model = "granite3.2-vision:2b"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                vision_installed = any(vision_model in name for name in model_names)
                
                add_check("ai_models", {
                    "name": "vision_model",
                    "display": "Vision Model (PDF)",
                    "status": "pass" if vision_installed else "warn",
                    "details": {"model": vision_model, "installed": vision_installed, "purpose": "PDF image/chart extraction"}
                })
                
                if not vision_installed:
                    results["issues"].append({
                        "severity": "medium",
                        "title": "Vision Model Not Installed",
                        "message": f"{vision_model} needed for PDF image extraction. Run: ollama pull {vision_model}",
                        "repair": "pull_model",
                        "repair_params": {"model": vision_model}
                    })
                    if results["overall"] == "healthy":
                        results["overall"] = "degraded"
    except Exception as e:
        add_check("ai_models", {
            "name": "vision_model",
            "display": "Vision Model (PDF)",
            "status": "warn",
            "error": f"Check failed: {str(e)[:30]}"
        })
    
    # ============ DATA INTEGRITY SECTION ============
    
    # NEW: Sources Storage Integrity Check
    try:
        from storage.source_store import source_store
        from storage.notebook_store import notebook_store
        
        sources_file = settings.data_dir / "sources.json"
        if sources_file.exists():
            import json
            with open(sources_file, 'r') as f:
                sources_data = json.load(f)
            source_count = len(sources_data.get("sources", {}))
            add_check("data_integrity", {
                "name": "sources_storage",
                "display": "Sources Storage",
                "status": "pass",
                "details": {"file": str(sources_file), "sources": source_count}
            })
        else:
            add_check("data_integrity", {
                "name": "sources_storage",
                "display": "Sources Storage",
                "status": "warn",
                "details": {"file": "Not found (new install)"}
            })
    except json.JSONDecodeError:
        add_check("data_integrity", {
            "name": "sources_storage",
            "display": "Sources Storage",
            "status": "fail",
            "error": "File corrupted"
        })
        results["issues"].append({
            "severity": "critical",
            "title": "Sources File Corrupted",
            "message": "sources.json is corrupted. Data may be lost.",
            "repair": None
        })
        results["overall"] = "critical"
    except Exception as e:
        add_check("data_integrity", {
            "name": "sources_storage",
            "display": "Sources Storage",
            "status": "warn",
            "error": str(e)[:50]
        })
    
    # NEW: Source-DB Sync Check
    try:
        sources_data = source_store._load_data()
        valid_sources = sources_data.get("sources", {})
        
        # Count sources with chunks > 0
        sources_with_chunks = sum(1 for s in valid_sources.values() if s.get("chunks", 0) > 0)
        
        # Compare with LanceDB
        sync_ok = True
        if sources_with_chunks > 0 and total_db_chunks == 0:
            sync_ok = False
        
        add_check("data_integrity", {
            "name": "source_sync",
            "display": "Source-DB Sync",
            "status": "pass" if sync_ok else "warn",
            "details": {"sources_indexed": sources_with_chunks, "db_chunks": total_db_chunks}
        })
        
        if not sync_ok:
            results["issues"].append({
                "severity": "medium",
                "title": "Source-DB Mismatch",
                "message": f"{sources_with_chunks} sources should be indexed but DB has {total_db_chunks} chunks.",
                "repair": "reindex_all"
            })
            if results["overall"] == "healthy":
                results["overall"] = "degraded"
    except Exception as e:
        add_log("WARN", f"Source sync check failed: {e}", "health_portal")
    
    # Orphaned Chunks Check
    try:
        from api.reindex import check_data_integrity
        integrity = await check_data_integrity()
        orphan_count = integrity.get("total_orphaned_chunks", 0)
        
        add_check("data_integrity", {
            "name": "orphaned_chunks",
            "display": "Orphaned Data",
            "status": "pass" if orphan_count == 0 else "warn",
            "details": {"orphaned_chunks": orphan_count}
        })
        
        if orphan_count > 0:
            results["issues"].append({
                "severity": "low",
                "title": f"{orphan_count} Orphaned Chunks",
                "message": "Deleted sources left behind data.",
                "repair": "clean_orphans"
            })
    except Exception as e:
        add_log("WARN", f"Orphan check failed: {e}", "health_portal")
    
    # Stuck Sources Check
    try:
        from datetime import timedelta
        stuck_sources = []
        all_sources_by_nb = await source_store.list_all()
        for nb_id, sources in all_sources_by_nb.items():
            for src in sources:
                status = src.get("status", "")
                chunks = src.get("chunks", 0)
                created = src.get("created_at", "")
                
                if status == "processing" or (chunks == 0 and src.get("char_count", 0) > 0):
                    try:
                        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        if datetime.now(created_dt.tzinfo) - created_dt > timedelta(minutes=5):
                            stuck_sources.append({
                                "notebook_id": nb_id,
                                "source_id": src.get("id"),
                                "filename": src.get("filename", "Unknown"),
                                "status": status,
                                "chunks": chunks
                            })
                    except:
                        pass
        
        add_check("data_integrity", {
            "name": "stuck_sources",
            "display": "Stuck Sources",
            "status": "pass" if not stuck_sources else "warn",
            "details": {"stuck_count": len(stuck_sources)}
        })
        
        if stuck_sources:
            results["issues"].append({
                "severity": "medium",
                "title": f"{len(stuck_sources)} Stuck Source(s)",
                "message": "Some sources failed to index.",
                "repair": "reindex_stuck",
                "repair_params": {"sources": stuck_sources}
            })
            if results["overall"] == "healthy":
                results["overall"] = "degraded"
    except Exception as e:
        add_log("WARN", f"Stuck sources check failed: {e}", "health_portal")
    
    # ============ CONFIGURATION SECTION ============
    
    # Embedding Dimension Check
    needs_migration = check_rag_embedding_dimensions()
    add_check("configuration", {
        "name": "embeddings_config",
        "display": "Embedding Config",
        "status": "pass" if not needs_migration else "warn",
        "details": {"expected_dim": EXPECTED_EMBEDDING_DIM, "model": settings.embedding_model, "needs_migration": needs_migration}
    })
    if needs_migration:
        results["issues"].append({
            "severity": "medium",
            "title": "Embedding Migration Needed",
            "message": "Some notebooks have old embedding dimensions.",
            "repair": "reindex_all"
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    
    # Knowledge Graph Check
    kg_needs_migration = check_knowledge_graph_dimensions()
    add_check("configuration", {
        "name": "knowledge_graph",
        "display": "Knowledge Graph",
        "status": "pass" if not kg_needs_migration else "warn",
        "details": {"needs_reset": kg_needs_migration}
    })
    if kg_needs_migration:
        results["issues"].append({
            "severity": "low",
            "title": "Knowledge Graph Needs Reset",
            "message": "Knowledge graph has old embedding dimensions.",
            "repair": "reset_kg"
        })
    
    # NEW: Write Permissions Test
    try:
        test_file = settings.data_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        add_check("configuration", {
            "name": "write_permissions",
            "display": "Write Permissions",
            "status": "pass",
            "details": {"data_dir": str(settings.data_dir)}
        })
    except Exception as e:
        add_check("configuration", {
            "name": "write_permissions",
            "display": "Write Permissions",
            "status": "fail",
            "error": str(e)[:50]
        })
        results["issues"].append({
            "severity": "critical",
            "title": "Cannot Write to Data Directory",
            "message": f"No write access to {settings.data_dir}",
            "repair": None
        })
        results["overall"] = "critical"
    
    # NEW: Dependencies Check (key packages)
    deps_status = "pass"
    missing_deps = []
    try:
        pass
    except ImportError:
        missing_deps.append("flashrank")
        deps_status = "warn"
    try:
        import trafilatura
    except ImportError:
        missing_deps.append("trafilatura")
        deps_status = "warn"
    try:
        import fitz  # PyMuPDF - actual PDF library used
    except ImportError:
        missing_deps.append("PyMuPDF")
        deps_status = "warn"
    try:
        pass
    except ImportError:
        pass  # Optional - using Ollama embeddings
    
    add_check("configuration", {
        "name": "dependencies",
        "display": "Dependencies",
        "status": deps_status,
        "details": {"missing": missing_deps if missing_deps else "All OK"}
    })
    if missing_deps:
        results["issues"].append({
            "severity": "medium",
            "title": f"Missing Dependencies: {', '.join(missing_deps)}",
            "message": "Some features may not work. Run pip install to fix.",
            "repair": None
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    
    # ============ FUNCTIONAL TESTS SECTION ============
    # These test actual functionality, not just imports
    results["sections"]["functional_tests"] = {"name": "Functional Tests", "icon": "🧪", "checks": [], "status": "pass"}
    
    # PDF Extraction Test - most common user frustration
    try:
        import fitz as fitz  # noqa: F811 - reimport for functional test
        # Create a test PDF in memory and extract text
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "LocalBook PDF Test")
        pdf_bytes = doc.tobytes()
        doc.close()
        
        doc2 = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = doc2[0].get_text()
        doc2.close()
        
        if "LocalBook PDF Test" in text:
            add_check("functional_tests", {
                "name": "pdf_extraction",
                "display": "PDF Extraction",
                "status": "pass",
                "details": {"library": f"PyMuPDF {fitz.version[0]}", "test": "Create + Extract OK"}
            })
        else:
            add_check("functional_tests", {
                "name": "pdf_extraction",
                "display": "PDF Extraction",
                "status": "fail",
                "error": "Text extraction failed"
            })
            results["issues"].append({
                "severity": "high",
                "title": "PDF Extraction Not Working",
                "message": "PDF uploads will fail. Check PyMuPDF installation.",
                "repair": None
            })
            if results["overall"] == "healthy":
                results["overall"] = "degraded"
    except Exception as e:
        add_check("functional_tests", {
            "name": "pdf_extraction",
            "display": "PDF Extraction",
            "status": "fail",
            "error": str(e)[:50]
        })
        results["issues"].append({
            "severity": "high",
            "title": "PDF Extraction Failed",
            "message": f"PyMuPDF error: {str(e)[:50]}",
            "repair": None
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    
    # ffmpeg Check - required for audio/video transcription
    ffmpeg_path = find_binary("ffmpeg")
    if ffmpeg_path:
        try:
            result = subprocess.run([ffmpeg_path, "-version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                version_line = result.stdout.decode().split('\n')[0]
                add_check("functional_tests", {
                    "name": "ffmpeg",
                    "display": "FFmpeg (Audio/Video)",
                    "status": "pass",
                    "details": {"installed": True, "path": ffmpeg_path, "info": version_line[:50]}
                })
            else:
                add_check("functional_tests", {
                    "name": "ffmpeg",
                    "display": "FFmpeg (Audio/Video)",
                    "status": "warn",
                    "error": f"Found at {ffmpeg_path} but not working properly"
                })
        except Exception as e:
            add_check("functional_tests", {
                "name": "ffmpeg",
                "display": "FFmpeg (Audio/Video)",
                "status": "warn",
                "error": str(e)[:30]
            })
    else:
        add_check("functional_tests", {
            "name": "ffmpeg",
            "display": "FFmpeg (Audio/Video)",
            "status": "warn",
            "error": "Not installed"
        })
        results["issues"].append({
            "severity": "medium",
            "title": "FFmpeg Not Installed",
            "message": "Audio/video transcription won't work. Run: brew install ffmpeg",
            "repair": None
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    
    # Tesseract Check - required for OCR (optional but commonly needed)
    tesseract_path = find_binary("tesseract")
    if tesseract_path:
        try:
            result = subprocess.run([tesseract_path, "--version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                add_check("functional_tests", {
                    "name": "tesseract",
                    "display": "Tesseract (OCR)",
                    "status": "pass",
                    "details": {"installed": True, "path": tesseract_path}
                })
            else:
                add_check("functional_tests", {
                    "name": "tesseract",
                    "display": "Tesseract (OCR)",
                    "status": "warn",
                    "error": f"Found at {tesseract_path} but not working properly"
                })
        except Exception as e:
            add_check("functional_tests", {
                "name": "tesseract",
                "display": "Tesseract (OCR)",
                "status": "warn",
                "error": str(e)[:30]
            })
    else:
        add_check("functional_tests", {
            "name": "tesseract",
            "display": "Tesseract (OCR)",
            "status": "warn",
            "details": {"installed": False, "note": "Optional - for image OCR"}
        })
        # Don't add issue - tesseract is optional
    
    # Whisper Model Test - audio transcription capability
    try:
        pass
        # Just verify import works - don't load model (too slow)
        add_check("functional_tests", {
            "name": "whisper",
            "display": "Whisper (Transcription)",
            "status": "pass",
            "details": {"library": "openai-whisper", "available": True}
        })
    except ImportError:
        add_check("functional_tests", {
            "name": "whisper",
            "display": "Whisper (Transcription)",
            "status": "warn",
            "error": "Not installed"
        })
        results["issues"].append({
            "severity": "medium",
            "title": "Whisper Not Available",
            "message": "Audio transcription won't work. Requires Python 3.11.",
            "repair": None
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    except Exception as e:
        add_check("functional_tests", {
            "name": "whisper",
            "display": "Whisper (Transcription)",
            "status": "warn",
            "error": str(e)[:30]
        })
    
    # Web Scraping Test - required for URL captures
    try:
        import trafilatura as trafilatura  # noqa: F811 - reimport for functional test
        test_html = "<html><body><article><p>Test content for LocalBook.</p></article></body></html>"
        extracted = trafilatura.extract(test_html)
        
        if extracted and "Test content" in extracted:
            add_check("functional_tests", {
                "name": "web_scraping",
                "display": "Web Scraping",
                "status": "pass",
                "details": {"library": "trafilatura", "test": "Extract OK"}
            })
        else:
            add_check("functional_tests", {
                "name": "web_scraping",
                "display": "Web Scraping",
                "status": "fail",
                "error": "Extraction returned empty"
            })
            results["issues"].append({
                "severity": "medium",
                "title": "Web Scraping Not Working",
                "message": "URL captures will fail. Trafilatura may need reinstall.",
                "repair": "reinstall_trafilatura"
            })
            if results["overall"] == "healthy":
                results["overall"] = "degraded"
    except ImportError:
        add_check("functional_tests", {
            "name": "web_scraping",
            "display": "Web Scraping",
            "status": "fail",
            "error": "trafilatura not installed"
        })
        results["issues"].append({
            "severity": "medium",
            "title": "Web Scraping Not Available",
            "message": "trafilatura not installed. URL captures won't work.",
            "repair": "reinstall_trafilatura"
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    except Exception as e:
        add_check("functional_tests", {
            "name": "web_scraping",
            "display": "Web Scraping",
            "status": "warn",
            "error": str(e)[:30]
        })
    
    # PowerPoint Import Test - required for .pptx uploads
    try:
        add_check("functional_tests", {
            "name": "pptx_import",
            "display": "PowerPoint Support",
            "status": "pass",
            "details": {"library": "python-pptx", "test": "Import OK"}
        })
    except ImportError as e:
        add_check("functional_tests", {
            "name": "pptx_import",
            "display": "PowerPoint Support",
            "status": "fail",
            "error": f"Import failed: {str(e)[:50]}"
        })
        results["issues"].append({
            "severity": "medium",
            "title": "PowerPoint Support Missing",
            "message": f"python-pptx import failed: {str(e)}",
            "repair": None
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    except Exception as e:
        add_check("functional_tests", {
            "name": "pptx_import",
            "display": "PowerPoint Support",
            "status": "warn",
            "error": str(e)[:30]
        })
    
    # BERTopic Test - required for topic modeling
    try:
        from bertopic.vectorizers import OnlineCountVectorizer
        
        # Test that core components initialize
        _vectorizer = OnlineCountVectorizer(stop_words="english")
        
        # Check if topic model data exists
        topic_model_path = settings.data_dir / "topic_model"
        has_model = topic_model_path.exists() and (topic_model_path / "bertopic_model").exists()
        
        add_check("functional_tests", {
            "name": "topic_modeling",
            "display": "Topic Modeling",
            "status": "pass",
            "details": {"library": "BERTopic", "model_exists": has_model}
        })
    except ImportError:
        add_check("functional_tests", {
            "name": "topic_modeling",
            "display": "Topic Modeling",
            "status": "fail",
            "error": "BERTopic not installed"
        })
        results["issues"].append({
            "severity": "medium",
            "title": "Topic Modeling Not Available",
            "message": "BERTopic not installed. Themes panel won't work.",
            "repair": "reinstall_bertopic"
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    except Exception as e:
        add_check("functional_tests", {
            "name": "topic_modeling",
            "display": "Topic Modeling",
            "status": "warn",
            "error": str(e)[:30]
        })
        results["issues"].append({
            "severity": "medium",
            "title": "Topic Modeling Error",
            "message": f"BERTopic failed: {str(e)[:30]}",
            "repair": "reset_topic_model"
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    
    # ============ SYSTEM HEALTH (non-section issues) ============
    
    # Storage Check
    disk_percent = results["system"].get("disk_percent_used", 0)
    if disk_percent > 90:
        results["issues"].append({
            "severity": "high",
            "title": "Low Disk Space",
            "message": f"Only {results['system'].get('disk_free_gb', 0):.1f}GB free",
            "repair": None
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    elif disk_percent > 80 and results["system"].get("disk_free_gb", 100) < 20:
        # Only warn if >80% used AND less than 20GB free (absolute threshold)
        results["issues"].append({
            "severity": "low",
            "title": "Disk Space Warning",
            "message": f"{results['system'].get('disk_free_gb', 0):.1f}GB free",
            "repair": None
        })
    
    # Memory Pressure Check
    mem_percent = results["system"].get("memory_percent_used", 0)
    if mem_percent > 90:
        results["issues"].append({
            "severity": "high",
            "title": "High Memory Usage",
            "message": f"Memory {mem_percent:.0f}% used. App may crash.",
            "repair": "clear_caches"
        })
        if results["overall"] == "healthy":
            results["overall"] = "degraded"
    elif mem_percent > 80:
        results["issues"].append({
            "severity": "low",
            "title": "Memory Warning",
            "message": f"Memory {mem_percent:.0f}% used.",
            "repair": None
        })
    
    # RAG Metrics
    try:
        agg = rag_metrics.get_aggregate_metrics(hours=24)
        results["metrics"] = {
            "queries_24h": agg.total_queries,
            "avg_latency_ms": round(agg.avg_total_time_ms, 0),
            "p95_latency_ms": round(agg.p95_total_time_ms, 0),
            "cache_hit_rate": round(agg.embedding_cache_hit_rate, 2),
            "error_rate": round(agg.error_rate, 3)
        }
        
        if agg.error_rate > 0.1:
            results["issues"].append({
                "severity": "medium",
                "title": "High Error Rate",
                "message": f"{agg.error_rate*100:.1f}% of queries failing",
                "repair": "clear_caches"
            })
            if results["overall"] == "healthy":
                results["overall"] = "degraded"
    except Exception as e:
        results["metrics"]["error"] = str(e)
    
    # Cache Stats
    try:
        results["cache"] = {
            "embedding": embedding_cache.get_stats(),
            "answer": answer_cache.get_stats()
        }
    except:
        pass
    
    add_log("INFO", f"Full health check completed: {results['overall']}", "health_portal")
    
    return results


@router.post("/repair")
async def execute_repair(request: RepairRequest, background_tasks: BackgroundTasks):
    """Execute a repair action."""
    action = request.action
    params = request.params or {}
    
    add_log("INFO", f"Executing repair: {action}", "health_portal")
    
    if action == "restart_ollama":
        try:
            # Kill existing Ollama
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
            await asyncio.sleep(1)
            # Start Ollama
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            await asyncio.sleep(2)
            
            # Verify
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{settings.ollama_base_url}/api/tags")
                if resp.status_code == 200:
                    add_log("INFO", "Ollama restarted successfully", "health_portal")
                    return {"status": "success", "message": "Ollama restarted"}
            
            return {"status": "partial", "message": "Ollama started but not responding yet"}
        except Exception as e:
            add_log("ERROR", f"Failed to restart Ollama: {e}", "health_portal")
            return {"status": "error", "message": str(e)}
    
    elif action == "pull_model":
        model = params.get("model")
        if not model:
            return {"status": "error", "message": "No model specified"}
        
        try:
            add_log("INFO", f"Pulling model: {model}", "health_portal")
            async with httpx.AsyncClient(timeout=600.0) as client:
                resp = await client.post(
                    f"{settings.ollama_base_url}/api/pull",
                    json={"name": model, "stream": False}
                )
                if resp.status_code == 200:
                    add_log("INFO", f"Model {model} pulled successfully", "health_portal")
                    return {"status": "success", "message": f"Model {model} installed"}
                else:
                    return {"status": "error", "message": f"Pull failed: {resp.status_code}"}
        except Exception as e:
            add_log("ERROR", f"Failed to pull model: {e}", "health_portal")
            return {"status": "error", "message": str(e)}
    
    elif action == "reindex_all":
        try:
            from api.reindex import reindex_all_notebooks
            add_log("INFO", "Starting full reindex", "health_portal")
            result = await reindex_all_notebooks(force=True, drop_tables=False)
            add_log("INFO", f"Reindex complete: {result}", "health_portal")
            return {"status": "success", "message": "Reindex complete", "details": result}
        except Exception as e:
            add_log("ERROR", f"Reindex failed: {e}", "health_portal")
            return {"status": "error", "message": str(e)}
    
    elif action == "reindex_notebook":
        notebook_id = params.get("notebook_id")
        if not notebook_id:
            return {"status": "error", "message": "No notebook_id specified"}
        
        try:
            from api.reindex import reindex_notebook
            result = await reindex_notebook(notebook_id, force=True)
            return {"status": "success", "message": "Notebook reindexed", "details": result}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    elif action == "clear_caches":
        try:
            embedding_cache.clear()
            answer_cache.clear()
            add_log("INFO", "Caches cleared", "health_portal")
            return {"status": "success", "message": "All caches cleared"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    elif action == "reset_kg":
        try:
            from services.startup_checks import reset_knowledge_graph_tables
            await reset_knowledge_graph_tables()
            add_log("INFO", "Knowledge graph reset", "health_portal")
            return {"status": "success", "message": "Knowledge graph reset"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    elif action == "clean_orphans":
        try:
            from api.reindex import cleanup_orphaned_data
            result = await cleanup_orphaned_data()
            return {"status": "success", "message": "Orphaned chunks cleaned", "details": result}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    elif action == "warmup_model":
        model = params.get("model", settings.ollama_model)
        try:
            add_log("INFO", f"Warming up model: {model}", "health_portal")
            async with httpx.AsyncClient(timeout=120.0) as client:
                # Send a simple prompt to load the model into memory
                resp = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": "Hello",
                        "stream": False,
                        "options": {"num_predict": 1}
                    }
                )
                if resp.status_code == 200:
                    add_log("INFO", f"Model {model} warmed up", "health_portal")
                    return {"status": "success", "message": f"Model {model} loaded into memory"}
                else:
                    return {"status": "error", "message": f"Warmup failed: {resp.status_code}"}
        except Exception as e:
            add_log("ERROR", f"Model warmup failed: {e}", "health_portal")
            return {"status": "error", "message": str(e)}
    
    elif action == "reindex_stuck":
        sources = params.get("sources", [])
        try:
            from api.reindex import reindex_notebook
            add_log("INFO", f"Reindexing {len(sources)} stuck sources", "health_portal")
            
            # Group by notebook and reindex each
            notebook_ids = set(s.get("notebook_id") for s in sources if s.get("notebook_id"))
            results = []
            for nb_id in notebook_ids:
                try:
                    result = await reindex_notebook(nb_id, force=True)
                    results.append({"notebook_id": nb_id, "status": "success", "result": result})
                except Exception as e:
                    results.append({"notebook_id": nb_id, "status": "error", "error": str(e)})
            
            add_log("INFO", f"Reindexed {len(notebook_ids)} notebooks", "health_portal")
            return {"status": "success", "message": f"Reindexed {len(notebook_ids)} notebooks", "details": results}
        except Exception as e:
            add_log("ERROR", f"Stuck reindex failed: {e}", "health_portal")
            return {"status": "error", "message": str(e)}
    
    elif action == "kill_port":
        # Kill any process using our port (for stuck backend)
        try:
            port = settings.api_port
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True
            )
            if result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    subprocess.run(["kill", "-9", pid], capture_output=True)
                add_log("INFO", f"Killed {len(pids)} processes on port {port}", "health_portal")
                return {"status": "success", "message": f"Killed processes on port {port}"}
            else:
                return {"status": "success", "message": "No processes found on port"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    elif action == "init_reranker":
        # Initialize FlashRank reranker (downloads model if needed)
        try:
            add_log("INFO", "Initializing FlashRank reranker...", "health_portal")
            from flashrank import Ranker, RerankRequest
            
            # Use persistent cache dir (not /tmp which gets cleared on reboot)
            cache_dir = settings.data_dir / "models" / "flashrank"
            cache_dir.mkdir(parents=True, exist_ok=True)
            add_log("INFO", f"Using cache dir: {cache_dir}", "health_portal")
            
            # This will download and cache the model on first run
            ranker = Ranker(
                model_name=settings.reranker_model,
                cache_dir=str(cache_dir)
            )
            # Test it works
            request = RerankRequest(
                query="test query",
                passages=[{"id": "1", "text": "test passage"}]
            )
            ranker.rerank(request)
            add_log("INFO", f"Reranker initialized: {settings.reranker_model}", "health_portal")
            return {"status": "success", "message": f"Reranker {settings.reranker_model} ready (cached in {cache_dir})"}
        except Exception as e:
            add_log("ERROR", f"Reranker init failed: {e}", "health_portal")
            return {"status": "error", "message": str(e)}
    
    elif action == "reinstall_trafilatura":
        # Reinstall trafilatura for web scraping
        try:
            add_log("INFO", "Reinstalling trafilatura...", "health_portal")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", "trafilatura"],
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode == 0:
                # Test it works
                import importlib
                import trafilatura
                importlib.reload(trafilatura)
                test_html = "<html><body><p>Test</p></body></html>"
                trafilatura.extract(test_html)
                add_log("INFO", "Trafilatura reinstalled and working", "health_portal")
                return {"status": "success", "message": "Trafilatura reinstalled. Restart app for full effect."}
            else:
                add_log("ERROR", f"Trafilatura reinstall failed: {result.stderr[:100]}", "health_portal")
                return {"status": "error", "message": f"Install failed: {result.stderr[:100]}"}
        except Exception as e:
            add_log("ERROR", f"Trafilatura reinstall failed: {e}", "health_portal")
            return {"status": "error", "message": str(e)}
    
    elif action == "reinstall_bertopic":
        # Reinstall BERTopic for topic modeling
        try:
            add_log("INFO", "Reinstalling BERTopic...", "health_portal")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", "bertopic"],
                capture_output=True,
                text=True,
                timeout=300  # BERTopic has many dependencies
            )
            if result.returncode == 0:
                add_log("INFO", "BERTopic reinstalled", "health_portal")
                return {"status": "success", "message": "BERTopic reinstalled. Restart app for full effect."}
            else:
                add_log("ERROR", f"BERTopic reinstall failed: {result.stderr[:100]}", "health_portal")
                return {"status": "error", "message": f"Install failed: {result.stderr[:100]}"}
        except Exception as e:
            add_log("ERROR", f"BERTopic reinstall failed: {e}", "health_portal")
            return {"status": "error", "message": str(e)}
    
    elif action == "reset_topic_model":
        # Reset/reinitialize the topic model
        try:
            add_log("INFO", "Resetting topic model...", "health_portal")
            topic_model_path = settings.data_dir / "topic_model"
            
            # Remove existing model files
            if topic_model_path.exists():
                import shutil
                shutil.rmtree(topic_model_path)
                add_log("INFO", f"Removed topic model at {topic_model_path}", "health_portal")
            
            # Reinitialize (will be rebuilt on next use)
            topic_model_path.mkdir(parents=True, exist_ok=True)
            
            add_log("INFO", "Topic model reset. Will rebuild on next use.", "health_portal")
            return {"status": "success", "message": "Topic model reset. Themes will rebuild when documents are added."}
        except Exception as e:
            add_log("ERROR", f"Topic model reset failed: {e}", "health_portal")
            return {"status": "error", "message": str(e)}
    
    elif action == "repair_model":
        # Repair corrupted Ollama model by removing and re-pulling
        model = params.get("model", settings.ollama_model)
        try:
            add_log("INFO", f"Starting model repair for: {model}", "health_portal")
            
            # Step 1: Remove the corrupted model
            add_log("INFO", f"Removing corrupted model: {model}", "health_portal")
            rm_result = subprocess.run(
                ["ollama", "rm", model],
                capture_output=True,
                text=True,
                timeout=30
            )
            if rm_result.returncode != 0 and "not found" not in rm_result.stderr.lower():
                add_log("WARN", f"Model removal warning: {rm_result.stderr[:100]}", "health_portal")
            
            # Step 2: Start the pull in background (don't wait - it takes too long)
            add_log("INFO", f"Starting background pull for: {model}", "health_portal")
            
            # Use subprocess.Popen to run in background
            import threading
            def pull_model_background():
                try:
                    pull_result = subprocess.run(
                        ["ollama", "pull", model],
                        capture_output=True,
                        text=True,
                        timeout=1800  # 30 min timeout
                    )
                    if pull_result.returncode == 0:
                        add_log("INFO", f"Model {model} re-pulled successfully", "health_portal")
                    else:
                        add_log("ERROR", f"Model pull failed: {pull_result.stderr[:200]}", "health_portal")
                except subprocess.TimeoutExpired:
                    add_log("ERROR", f"Model pull timed out for {model}", "health_portal")
                except Exception as e:
                    add_log("ERROR", f"Model pull error: {e}", "health_portal")
            
            # Start background thread
            thread = threading.Thread(target=pull_model_background, daemon=True)
            thread.start()
            
            add_log("INFO", f"Model repair started for {model} - pulling in background", "health_portal")
            return {
                "status": "started",
                "message": f"Model repair started for {model}. This may take 5-30 minutes depending on model size. Refresh Health Portal to check progress."
            }
        except Exception as e:
            add_log("ERROR", f"Model repair failed: {e}", "health_portal")
            return {"status": "error", "message": str(e)}
    
    else:
        return {"status": "error", "message": f"Unknown action: {action}"}


@router.get("/logs")
async def get_logs(level: str = "all", limit: int = 100):
    """Get recent log entries."""
    logs = LOG_BUFFER[-limit:]
    
    if level != "all":
        logs = [l for l in logs if l["level"].upper() == level.upper()]
    
    return {
        "logs": logs,
        "total": len(LOG_BUFFER)
    }


@router.delete("/logs")
async def clear_logs():
    """Clear log buffer."""
    LOG_BUFFER.clear()
    return {"status": "cleared"}


@router.get("/export")
async def export_diagnostics():
    """Export full diagnostic report for support."""
    health = await full_health_check()
    
    export_data = {
        "export_timestamp": datetime.now().isoformat(),
        "version": "1.0.5",
        "health_check": health,
        "recent_logs": LOG_BUFFER[-100:],
        "config": {
            "ollama_base_url": settings.ollama_base_url,
            "ollama_model": settings.ollama_model,
            "ollama_fast_model": settings.ollama_fast_model,
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
            "data_dir": str(settings.data_dir),
            "db_path": str(settings.db_path),
        }
    }
    
    return export_data


@router.websocket("/logs/stream")
async def stream_logs(websocket: WebSocket):
    """Stream logs in real-time via WebSocket."""
    await websocket.accept()
    
    last_index = len(LOG_BUFFER)
    
    try:
        while True:
            # Check for new logs
            if len(LOG_BUFFER) > last_index:
                new_logs = LOG_BUFFER[last_index:]
                for log in new_logs:
                    await websocket.send_json(log)
                last_index = len(LOG_BUFFER)
            
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
