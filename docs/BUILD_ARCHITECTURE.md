# LocalBook Build Architecture

> Internal reference for maintainers. Users should just run `./build.sh`.

## Overview

LocalBook is a Tauri desktop app with:
1. **Frontend**: React/TypeScript (Vite)
2. **Backend**: Python FastAPI (bundled via PyInstaller)
3. **External Dependency**: Ollama (local LLM server)

---

## App Startup Sequence

```
User launches LocalBook.app
    │
    ├─► Tauri app starts (lib.rs)
    │       ├─► ensure_ollama_running() → starts Ollama if needed
    │       ├─► start_backend() → runs bundled Python backend
    │       └─► wait_for_backend_ready() → polls localhost:8000/health
    │
    ├─► Backend starts (main.py)
    │       ├─► multiprocessing.freeze_support() [PyInstaller fix]
    │       ├─► FastAPI app with all routes
    │       ├─► Model warmup (Ollama)
    │       └─► uvicorn on 0.0.0.0:8000
    │
    └─► Frontend loads → connects to localhost:8000
```

---

## Python Dependencies (Complete List)

### Core Framework
| Package | Purpose | PyInstaller Notes |
|---------|---------|-------------------|
| fastapi | Web framework | Standard |
| uvicorn | ASGI server | Standard |
| pydantic | Data validation | Standard |
| pydantic-settings | Settings management | Standard |
| python-multipart | File uploads | Standard |

### AI/ML (Heavy)
| Package | Purpose | PyInstaller Notes |
|---------|---------|-------------------|
| torch | Neural networks | `--collect-all=torch` |
| sentence-transformers | Embeddings | `--collect-all=sentence_transformers` |
| transformers | Model loading | `--collect-all=transformers` |
| lancedb | Vector database | `--collect-data=lancedb` |
| openai-whisper | Audio transcription | May need `--collect-all=whisper` |

### LLM Clients
| Package | Purpose | PyInstaller Notes |
|---------|---------|-------------------|
| anthropic | Claude API | Standard |
| openai | OpenAI API | Standard |
| httpx | HTTP client | `--hidden-import=httpx` |

### Document Processing
| Package | Purpose | PyInstaller Notes |
|---------|---------|-------------------|
| PyMuPDF (fitz) | PDF reading | May need `--collect-all=fitz` |
| pdfplumber | PDF extraction | Standard |
| python-docx | Word docs | Standard |
| python-pptx | PowerPoint | Standard |
| openpyxl | Excel (xlsx) | Standard |
| xlrd | Excel (xls) | Standard |
| pandas | Data frames | Standard |

### Web Scraping
| Package | Purpose | PyInstaller Notes |
|---------|---------|-------------------|
| trafilatura | Web content extraction | `--collect-all=trafilatura` |
| beautifulsoup4 | HTML parsing | Standard |
| requests | HTTP requests | Standard |

### YouTube
| Package | Purpose | PyInstaller Notes |
|---------|---------|-------------------|
| youtube-transcript-api | Transcripts | `--hidden-import=youtube_transcript_api` |
| moviepy | Video processing | May need `--collect-all=moviepy` |

### Utilities
| Package | Purpose | PyInstaller Notes |
|---------|---------|-------------------|
| python-dotenv | Env files | Standard |
| keyring | Secure storage | `--hidden-import=keyring` |
| dateparser | Date parsing | `--hidden-import=dateparser` |
| tiktoken | Token counting | `--collect-data=tiktoken` |

---

## Local Modules (Must be bundled)

All of these need `--add-data` and `--hidden-import`:

```
backend/
├── main.py              # Entry point
├── config.py            # Settings
├── api/                 # API routes
│   ├── __init__.py
│   ├── audio.py
│   ├── chat.py
│   ├── embeddings.py
│   ├── export.py
│   ├── notebooks.py
│   ├── reindex.py
│   ├── settings.py
│   ├── skills.py
│   ├── source_viewer.py
│   ├── sources.py
│   ├── timeline.py
│   └── web.py
├── services/            # Business logic
│   ├── __init__.py
│   ├── audio_generator.py
│   ├── document_processor.py
│   ├── llm_service.py
│   ├── model_warmup.py
│   ├── rag_engine.py
│   └── web_scraper.py
├── storage/             # Data persistence
│   ├── __init__.py
│   ├── audio_store.py
│   ├── highlights_store.py
│   ├── notebook_store.py
│   ├── skills_store.py
│   └── source_store.py
├── models/              # Pydantic models
│   ├── __init__.py
│   └── chat.py
└── utils/               # Utilities
    └── __init__.py
```

---

## PyInstaller Command (Complete)

```bash
pyinstaller \
    --onedir \
    --name "localbook-backend" \
    --distpath "$OUTPUT_DIR" \
    --workpath "./build" \
    --specpath "./build" \
    --clean \
    --noconfirm \
    # Local modules
    --paths="$SCRIPT_DIR" \
    --add-data="$SCRIPT_DIR/api:api" \
    --add-data="$SCRIPT_DIR/services:services" \
    --add-data="$SCRIPT_DIR/storage:storage" \
    --add-data="$SCRIPT_DIR/models:models" \
    --add-data="$SCRIPT_DIR/utils:utils" \
    --add-data="$SCRIPT_DIR/config.py:." \
    # Local module imports
    --hidden-import=api \
    --hidden-import=api.notebooks \
    --hidden-import=api.sources \
    --hidden-import=api.chat \
    --hidden-import=api.skills \
    --hidden-import=api.audio \
    --hidden-import=api.source_viewer \
    --hidden-import=api.web \
    --hidden-import=api.settings \
    --hidden-import=api.embeddings \
    --hidden-import=api.timeline \
    --hidden-import=api.export \
    --hidden-import=api.reindex \
    --hidden-import=services \
    --hidden-import=services.llm_service \
    --hidden-import=services.rag_engine \
    --hidden-import=services.document_processor \
    --hidden-import=services.audio_generator \
    --hidden-import=services.model_warmup \
    --hidden-import=services.web_scraper \
    --hidden-import=storage \
    --hidden-import=storage.notebook_store \
    --hidden-import=storage.source_store \
    --hidden-import=storage.vector_store \
    --hidden-import=storage.skill_store \
    --hidden-import=storage.chat_store \
    --hidden-import=storage.audio_store \
    --hidden-import=storage.highlights_store \
    --hidden-import=storage.skills_store \
    --hidden-import=models \
    --hidden-import=models.chat \
    --hidden-import=config \
    --hidden-import=utils \
    # Heavy ML packages
    --collect-all=sentence_transformers \
    --collect-all=torch \
    --collect-all=transformers \
    --collect-all=trafilatura \
    --collect-all=whisper \
    --collect-data=lancedb \
    --collect-data=tiktoken \
    # External dependencies
    --hidden-import=trafilatura \
    --hidden-import=httpx \
    --hidden-import=youtube_transcript_api \
    --hidden-import=keyring \
    --hidden-import=dateparser \
    --hidden-import=fitz \
    --hidden-import=pdfplumber \
    --hidden-import=docx \
    --hidden-import=pptx \
    --hidden-import=openpyxl \
    --hidden-import=xlrd \
    --hidden-import=moviepy \
    --hidden-import=anthropic \
    --hidden-import=openai \
    main.py
```

---

## Known PyInstaller Issues

### 1. Double Startup (Port Already in Use)
**Cause**: PyInstaller + multiprocessing spawns child processes that re-execute main.py
**Fix**: Add at top of main.py:
```python
import multiprocessing
import sys
if getattr(sys, 'frozen', False):
    multiprocessing.freeze_support()
```

### 2. Module Not Found
**Cause**: PyInstaller doesn't detect dynamic imports
**Fix**: Add `--hidden-import=module_name` for each missing module

### 3. Data Files Missing
**Cause**: Some packages need data files (models, configs)
**Fix**: Use `--collect-all=package` or `--collect-data=package`

### 4. uvicorn String Import
**Cause**: `uvicorn.run("main:app")` doesn't work in frozen apps
**Fix**: Use `uvicorn.Server(config).run()` with app object directly

---

## Build Process

### build.sh Flow

```
./build.sh
    │
    ├─► Check/install prerequisites
    │       ├─► Homebrew
    │       ├─► Python 3.11+
    │       ├─► Node.js 18+
    │       ├─► Rust/Cargo
    │       └─► Ollama
    │
    ├─► Build backend (if needed)
    │       ├─► Create/activate venv
    │       ├─► pip install requirements.txt
    │       └─► ./build_backend.sh (PyInstaller)
    │
    ├─► Build frontend
    │       └─► npm install
    │
    ├─► Build Tauri app
    │       └─► npm run tauri build
    │           ├─► Bundles frontend (dist/)
    │           ├─► Bundles backend (resources/)
    │           └─► Creates LocalBook.app
    │
    └─► Download Ollama models
            ├─► mistral-nemo:12b-instruct-2407-q4_K_M
            └─► phi4-mini
```

---

## File Locations

### Source
- Frontend: `/src/`
- Backend: `/backend/`
- Tauri config: `/src-tauri/`

### Build Output
- Backend binary: `/src-tauri/resources/backend/localbook-backend/`
- Tauri app: `/src-tauri/target/release/bundle/macos/LocalBook.app`
- Copied app: `/LocalBook.app`

### Runtime (in .app bundle)
- Backend: `LocalBook.app/Contents/Resources/resources/backend/localbook-backend/localbook-backend`
- Frontend: `LocalBook.app/Contents/Resources/` (Tauri handles this)

---

## Troubleshooting Checklist

### Backend won't start
1. Check if port 8000 is in use: `lsof -i :8000`
2. Kill existing process: `pkill -f localbook-backend`
3. Run manually to see errors: `./src-tauri/resources/backend/localbook-backend/localbook-backend`

### Module not found
1. Check if module is in requirements.txt
2. Add `--hidden-import=module` to build_backend.sh
3. Rebuild: `rm -rf src-tauri/resources/backend && cd backend && ./build_backend.sh`

### App stuck on "Starting"
1. Check Ollama: `curl http://localhost:11434/api/tags`
2. Check backend: `curl http://localhost:8000/health`
3. Check Console.app for errors

### Feature doesn't work in built app but works in dev
1. Run backend manually from built app
2. Try the feature
3. Check terminal for errors
4. Add missing `--hidden-import` or `--collect-all`
