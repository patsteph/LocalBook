# LocalBook - AI Assistant Context

> **Read this file at the start of every session.** This is your fast-start guide to LocalBook.

---

## Quick Start Checklist

Before doing ANYTHING, verify:
- [ ] Read `READFIRST/SYSTEM_DESIGN.md` - **This is the canonical architecture guide**
- [ ] Understand the user's workflow (see below)
- [ ] Know what NOT to do (see Critical Rules)

---

## What is LocalBook?

**Private, offline AI research assistant.** Users upload documents (PDFs, web pages, YouTube) and chat with them using local LLMs. Everything runs on the user's Mac - no cloud, no data leaving the device.

### Core Value Proposition
> "Your documents, your AI, your machine."

### Key Features
- **Chat with Documents** - RAG-powered Q&A with citations
- **Knowledge Constellation** - 3D visualization of concepts
- **Studio** - Generate quizzes, visuals, documents from sources
- **Memory System** - AI remembers user context across sessions
- **Browser Extension** - Research companion for web browsing
- **Podcast Generator** - Turn documents into audio discussions

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         LOCALBOOK                               │
├─────────────────────────────────────────────────────────────────┤
│  FRONTEND (Tauri + React + TypeScript)                         │
│  └── src/                                                       │
│      ├── components/     UI components                          │
│      ├── services/       API client (api.ts)                    │
│      └── pages/          Main views                             │
├─────────────────────────────────────────────────────────────────┤
│  BACKEND (FastAPI + Python 3.11)                               │
│  └── backend/                                                   │
│      ├── api/            REST endpoints                         │
│      ├── services/       Business logic (RAG, Studio, etc.)     │
│      ├── storage/        Data stores (SQLite-backed)            │
│      ├── agents/         LangGraph agent system                 │
│      └── main.py         FastAPI app entry                      │
├─────────────────────────────────────────────────────────────────┤
│  EXTENSION (Plasmo + React)                                    │
│  └── extension/                                                 │
│      ├── popup.tsx       Extension popup                        │
│      ├── sidepanel.tsx   Side panel UI                          │
│      └── background.ts   Service worker                         │
├─────────────────────────────────────────────────────────────────┤
│  AI LAYER (Ollama - 100% Local)                                │
│  ├── olmo-3:7b           Main reasoning model                   │
│  ├── phi4-mini           Fast model for quick tasks             │
│  ├── snowflake-arctic-embed2  Embeddings (1024 dim)            │
│  └── granite3.2-vision:2b     Vision model                      │
└─────────────────────────────────────────────────────────────────┘
```

### Tech Stack Summary

| Layer | Technology |
|-------|------------|
| Desktop App | Tauri 2.x (Rust) |
| Frontend | React 18 + TypeScript + TailwindCSS |
| Backend | FastAPI + Python 3.11 |
| Database | LanceDB (vectors) + SQLite (metadata) |
| LLMs | Ollama (local) |
| Extension | Plasmo framework |

---

## Critical Rules - READ THESE

### User Workflow (MUST FOLLOW)

1. **User ALWAYS runs the built Tauri .app** - Never dev mode
2. **Build command**: `./build.sh --rebuild`
3. **App location**: `src-tauri/target/release/bundle/macos/LocalBook.app`
4. **Production data**: `~/Library/Application Support/LocalBook/`
5. **Backend port**: 8000 (defined in `src/services/api.ts`)
6. **Never suggest**: `python main.py` or dev mode

### User's Terminal Workflow

```
Terminal 1: kill $(lsof -t -i:8000)     # Kill backend
Terminal 2: ./build.sh --rebuild         # Build app
Terminal 3: cd extension && npm run build # Build extension
Terminal 4: open src-tauri/target/release/bundle/macos/LocalBook.app
Terminal 5: ./release.sh                 # Release script
Terminal 6: Git commands
Terminal 7: Troubleshooting
```

### What NOT To Do

❌ **Never** run `python main.py` directly  
❌ **Never** check `backend/data/` - use `~/Library/Application Support/LocalBook/`  
❌ **Never** hardcode ports - use existing constants  
❌ **Never** delete tests without explicit direction  
❌ **Never** add comments unless asked  
❌ **Never** create unnecessary .md files  
❌ **Never** use dev mode  

### What TO Do

✅ **Always** use `./build.sh --rebuild` for changes  
✅ **Always** check existing code before implementing  
✅ **Always** use existing patterns from the codebase  
✅ **Always** verify with the user before major changes  
✅ **Always** read SYSTEM_DESIGN.md for architectural decisions  

---

## Key Services & Files

### Backend Services (`backend/services/`)

| Service | Purpose |
|---------|---------|
| `rag_engine.py` | Core RAG - hybrid search, reranking, answer generation |
| `document_processor.py` | Document ingestion pipeline |
| `hierarchical_chunker.py` | Multi-level chunking (doc/section/para/sentence) |
| `query_decomposer.py` | Breaks complex queries into sub-questions |
| `entity_extractor.py` | Extracts entities from documents |
| `entity_graph.py` | Relationship mapping between entities |
| `topic_modeling.py` | BERTopic for concept discovery |
| `studio_*.py` | Quiz, visual, document generation |
| `memory_agent.py` | Cross-session memory |
| `web_scraper.py` | Web search and scraping |
| `audio_generator.py` | Podcast/audio generation (LFM2.5-Audio) |

### API Endpoints (`backend/api/`)

| File | Endpoints |
|------|-----------|
| `chat.py` | `/chat`, `/chat/stream` |
| `sources.py` | Source CRUD operations |
| `notebooks.py` | Notebook management |
| `studio.py` | `/studio/quiz`, `/studio/visual`, `/studio/document` |
| `browser.py` | Extension capture endpoints |
| `graph.py` | Knowledge graph endpoints |
| `web.py` | Web search and scraping |

### Frontend Key Files (`src/`)

| File | Purpose |
|------|---------|
| `services/api.ts` | API client - **API_BASE_URL = localhost:8000** |
| `components/ChatPanel.tsx` | Main chat interface |
| `components/Studio.tsx` | Content generation UI |
| `components/Constellation.tsx` | 3D knowledge visualization |

---

## READFIRST Documentation Index

**Always check these before major work:**

| Document | Purpose | Priority |
|----------|---------|----------|
| `SYSTEM_DESIGN.md` | **Canonical architecture guide** | 🔴 READ FIRST |
| `ARCHITECTURE_v0.90.md` | Current architecture roadmap | High |
| `RAG_ENGINE_RESEARCH.md` | RAG capabilities and gaps | High |
| `TECHNOLOGY_RESEARCH_2026-01.md` | Tech evaluation (CaRR, Agent Browser) | Medium |
| `RLM_Integration_Analysis.md` | RLM proposal | Medium |
| `RLM_EVALUATION_REPORT.md` | RLM + alternatives evaluation | Medium |
| `BUILD_v1.0.5.md` | Build and release procedures | Reference |
| `PROMPT_AUDIT.md` | LLM prompt patterns | Reference |

---

## Design Principles (from SYSTEM_DESIGN.md)

### Speed First
> "Sub-second responses for common operations"

- Use caching aggressively
- Parallel processing where possible
- Fast models for quick tasks (phi4-mini)

### Function Over Form
> "A button that works beats a beautiful button that doesn't"

- Prioritize working features over polish
- Test in production builds

### Cutting-Edge, Not Bleeding-Edge
> "We adopt new techniques AFTER they've proven valuable"

- Validate before integrating
- Phased rollouts for new features

### Privacy Above All
> "Your research stays yours, always"

- 100% local processing
- No cloud dependencies
- Works offline

---

## Current RAG Capabilities

Already implemented (don't re-implement):

| Feature | Status | Location |
|---------|--------|----------|
| Hybrid Search (BM25 + Vector) | ✅ | `rag_engine._hybrid_search()` |
| Hierarchical Chunking | ✅ | `hierarchical_chunker.py` |
| Parent Context Expansion | ✅ | `parent_text` in LanceDB |
| Query Decomposition | ✅ | `query_decomposer.py` |
| FlashRank Reranking | ✅ | `rag_engine.rerank()` |
| Adaptive Multi-Strategy Search | ✅ | `rag_engine._adaptive_search()` |
| Entity Extraction | ✅ | `entity_extractor.py` |
| Corrective RAG | ✅ | `rag_engine._corrective_retrieval()` |
| Answer Caching | ✅ | `rag_cache.py` |
| Embedding Caching | ✅ | `rag_cache.py` |

---

## Common Patterns

### Source Creation Pattern
```python
# Standard workflow (from document_processor.py):
1. Create source with status: "processing", chunks: 0
2. Call rag_engine.ingest_document() - capture result
3. Update source with chunks, status: "completed"
```

### Background Task Pattern
```python
# Fire-and-forget for non-critical processing:
asyncio.create_task(some_background_work())

# For request-scoped background work:
background_tasks.add_task(process_function, args)
```

### LLM Call Pattern
```python
# Use httpx for async Ollama calls:
async with httpx.AsyncClient(timeout=timeout) as client:
    response = await client.post(
        f"{settings.ollama_base_url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False}
    )
```

---

## Model Configuration

### Ollama Models

| Model | Purpose | Context | Notes |
|-------|---------|---------|-------|
| `olmo-3:7b-instruct` | Main reasoning | 64K tokens | Chat, synthesis, streaming |
| `phi4-mini:latest` | Fast tasks | 8K tokens | Quick analysis, summaries |
| `snowflake-arctic-embed2` | Embeddings | N/A | **1024 dimensions** |
| `granite3.2-vision:2b` | Vision | 8K tokens | Image analysis |

### Critical Settings (from `backend/config.py`)

```python
# Embedding - MUST match when switching models
embedding_model: str = "snowflake-arctic-embed2"
embedding_dim: int = 1024  # If you change models, update this!

# Retrieval settings
chunk_size: int = 1000
chunk_overlap: int = 200
retrieval_overcollect: int = 12  # Candidates before reranking
retrieval_top_k: int = 5  # Final chunks after reranking

# Reranker
reranker_model: str = "ms-marco-MiniLM-L-12-v2"  # FlashRank
reranker_type: str = "flashrank"  # Ultra-fast, CPU-only
```

### Temperature Guidelines

| Use Case | Temperature | Why |
|----------|-------------|-----|
| Factual Q&A | 0.1-0.3 | Consistent, accurate |
| Summaries | 0.3 | Slight variation OK |
| Creative (quiz options) | 0.5-0.7 | More variety |
| Document generation | 0.4-0.6 | Balance accuracy/flow |

---

## Config File Locations

| File | Purpose | Key Settings |
|------|---------|--------------|
| `backend/config.py` | All backend settings | Models, ports, paths, retrieval params |
| `src/services/api.ts` | Frontend API client | `API_BASE_URL = http://localhost:8000` |
| `.claude/settings.local.json` | Claude Code allowed commands | Auto-approved bash commands |
| `backend/.env` | Environment overrides | API keys (if using cloud LLMs) |
| `src-tauri/tauri.conf.json` | Tauri app config | Window size, app name, bundle ID |

### Important: Version Bump Locations

When releasing, update version in:
1. `package.json` - `version` field
2. `src-tauri/tauri.conf.json` - `version` field
3. `src-tauri/Cargo.toml` - `version` field
4. `README.md` - badge and changelog references
5. `CHANGELOG.md` - new version section

---

## Known Issues & Gotchas

### Source Creation Pattern (CRITICAL)

**Bug fixed Jan 12, 2026** - Different ingestion paths were inconsistent. All paths MUST follow:

```python
# 1. Create source with processing status
source = await source_store.create(notebook_id, {
    "filename": title,
    "status": "processing",
    "chunks": 0,
    "char_count": 0,
    ...
})

# 2. Ingest and capture result
result = await rag_engine.ingest_document(...)

# 3. Update with completion status
await source_store.update(notebook_id, source_id, {
    "status": "completed",
    "chunks": result["chunks"],
    "char_count": result["characters"]
})
```

**Files that create sources** (all must follow same pattern):
- `backend/services/document_processor.py` ✅ Reference implementation
- `backend/api/browser.py` - 3 endpoints
- `backend/api/web.py` - add-to-notebook, quick-add
- `backend/api/voice.py` - transcribe
- `backend/agents/tools.py` - capture_page_tool

### Extension Gotchas

- Notebook selector uses `title` field, not `name`
- Side panel is ~1,173 lines - split is on post-v1.0 TODO
- Must rebuild extension separately: `cd extension && npm run build`

### Embedding Model Changes

If you change embedding models:
1. Update `embedding_dim` in config.py to match
2. **Existing notebooks need re-indexing** (vectors have different dimensions)
3. Old notebooks will fail silently until re-indexed

### LanceDB Quirks

- Delete syntax: `table.delete(f"source_id = '{source_id}'")`
- Schema changes require table recreation
- `parent_text` column added in v0.60 - older tables may not have it

---

## Testing Philosophy

### No Traditional Test Suite

LocalBook tests in **production builds**, not dev mode:

```bash
# The test workflow:
./build.sh --rebuild  # Build production app
open src-tauri/target/release/bundle/macos/LocalBook.app  # Launch
# Manually verify features work
```

### Why This Approach

1. Tauri bundling can introduce issues not present in dev
2. Backend bundling (PyInstaller) behaves differently than `python main.py`
3. Real user environment = real bugs found

### What to Verify After Changes

| Change Type | Verify |
|-------------|--------|
| RAG changes | Chat works, citations appear, sources load |
| Studio changes | Quiz/visual/doc generation completes |
| API changes | Frontend can call endpoint, no CORS issues |
| Extension changes | Popup loads, side panel works, capture succeeds |
| UI changes | Renders correctly, no console errors |

### Smoke Test Checklist

```
[ ] App launches without crash
[ ] Backend starts (check port 8000)
[ ] Can create notebook
[ ] Can upload document (PDF or text)
[ ] Can chat and get response with citations
[ ] Can generate quiz in Studio
[ ] Extension popup shows notebooks (if extension changed)
```

---

## Release Process

### Quick Release

```bash
./release.sh
```

This script:
1. Builds the Tauri app
2. Creates `LocalBook-v{version}.zip`
3. Builds extension
4. Creates `LocalBook-Extension-v{version}.zip`
5. Outputs files ready for GitHub release

### Manual Release Steps

```bash
# 1. Update versions (see Version Bump Locations above)

# 2. Build app
./build.sh --rebuild

# 3. Build extension
cd extension && npm run build && cd ..

# 4. Create zips
zip -r LocalBook-v1.0.6.zip LocalBook.app
cd extension && zip -r ../LocalBook-Extension-v1.0.6.zip dist

# 5. Git tag and push
git add -A
git commit -m "Release v1.0.6"
git tag v1.0.6
git push && git push --tags
```

### GitHub Release Checklist

- [ ] Version bumped in all locations
- [ ] CHANGELOG.md updated
- [ ] App zip created and tested
- [ ] Extension zip created
- [ ] Git tagged
- [ ] Release notes written

---

## Debugging Checklist

When something doesn't work:

1. **Check backend logs**: `tail -f backend.log`
2. **Check if backend is running**: `lsof -i:8000`
3. **Check production data**: `ls ~/Library/Application\ Support/LocalBook/`
4. **Rebuild and test**: `./build.sh --rebuild`
5. **Check Ollama models**: `ollama list`

---

## Current Roadmap (as of Jan 2026)

### Completed (v1.0.x)
- ✅ Core RAG with hybrid search
- ✅ Studio (quiz, visual, document generation)
- ✅ Browser extension
- ✅ Memory system
- ✅ Entity extraction and graph
- ✅ Knowledge Constellation

### In Evaluation
- 🔄 RLM Integration (see RLM_EVALUATION_REPORT.md)
- 🔄 Batch Processing Queue (P0 priority)
- 🔄 Progressive RAG UI (P1 priority)

### Planned
- CaRR (Citation-aware retrieval) - from TECHNOLOGY_RESEARCH
- Agent Browser integration
- Graph RAG enhancements

---

## Quick Reference Commands

```bash
# Build everything
./build.sh --rebuild

# Build extension only
cd extension && npm run build

# Kill stuck backend
kill $(lsof -t -i:8000)

# Launch app
open src-tauri/target/release/bundle/macos/LocalBook.app

# Create release
./release.sh

# Check Ollama models
ollama list

# Check backend logs
tail -f backend.log
```

---

## Session Start Protocol

When starting a new session:

1. **Acknowledge** you've read this file
2. **Ask** what the user wants to work on
3. **Check** relevant READFIRST docs if needed
4. **Start working** - don't repeat explanations

**DO NOT:**
- Ask "what is LocalBook?"
- Ask about the tech stack
- Ask how to build/run
- Explain the architecture back to the user

**DO:**
- Jump straight into the task
- Reference this file and READFIRST docs as needed
- Ask clarifying questions only about the specific task

---

*Last updated: January 17, 2026*
