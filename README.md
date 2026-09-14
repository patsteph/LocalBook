# LocalBook

**Your documents, your AI, your machine.** A private, offline alternative to cloud-based AI assistants.

[![Version](https://img.shields.io/badge/version-2.1.1-blue.svg)](https://github.com/patsteph/LocalBook/releases)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://github.com/patsteph/LocalBook)
[![Python](https://img.shields.io/badge/python-3.12+-green.svg)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

---

## What is LocalBook?

Chat with your documents using AI — completely offline and private. Upload PDFs, Word docs, web pages, or YouTube videos, then ask questions and get answers with exact citations.

- 🔒 **100% Private** — Everything runs locally on your Mac
- 📚 **Cited Answers** — AI answers from YOUR files with source citations
- 🌌 **Knowledge Constellation** — 3D visualization of concepts across documents
- 🧠 **Memory System** — AI remembers your preferences across sessions
- 🎙️ **Podcast Generator** — Turn documents into audio discussions
- 🎬 **Video Explainers** — Generate narrated slide videos from your research
- 🧩 **Browser Extension** — Research companion for web browsing
- 👥 **People Profiler** — Coaching and team management with social integration
- 🤖 **Curator Agent** — Morning briefs, weekly wrap-ups, and cross-notebook intelligence

---

## 🎉 What's New in v2.3.0
**LocalBook no longer uses Ollama.** One in-process MLX engine now serves every role — chat,
vision, image generation, and embeddings — so there is no second inference server to install,
start, or keep in sync. Models are managed inside the app.
Users upgrading from v2.1.1 also receive everything from the v2.2.0 development line, which was
never tagged; its notes are kept in full below.
### Added
- **Model Browser + download manager** in LLM Studio: live Hugging Face search filtered to MLX
  models, sortable by trending / downloads / likes / recency, with a fit badge computed from the
  checkpoint's real weight size against this Mac's addressable GPU working set, a capability and
  role read (main / fast / vision / embedding / image), a model-card popup, and an in-app download
  queue.
- **Origin labelling.** Every model shows who published it and what its weights derive from, as
  two separate facts. A publisher country is claimed only for accounts we can actually identify;
  lineage is resolved from the model architecture, so a fine-tune republished under another
  account is still attributed to its base. An optional origin filter is offered. **Nothing is
  hidden and no download is blocked** — the browser shows what exists and the user decides.
### Changed
- **One model per role**, each holding an MLX checkpoint id: `main_model`, `fast_model`,
  `vision_model`, `image_model`, `embedding_model`. The paired Ollama/MLX settings and the
  per-role engine flags are gone. Preferences migrate automatically.
- The **llama-server sidecar is removed**, along with the Ollama transport, startup pre-flight,
  health checks, warmup, capability probing, and the model pulls that ran after every build.
- Startup no longer requires any model to be present, and nothing is auto-downloaded.
- EPUB books are read in **spine (reading) order** with their heading structure preserved, rather
  than in manifest order and flattened. DRM-protected files are detected and declined.
### Fixed
- **HTTPS failed entirely on networks that inspect TLS.** Such a network re-signs every
  connection with its own root certificate; macOS trusts it, so Safari and the browser extension
  work, but Python verified against a bundle of public roots only and rejected all of them. The
  visible symptom was the model browser reporting "Could not reach Hugging Face" on a Mac with a
  working connection, but model downloads, the embedding checkpoint, the reranker and article
  fetching were affected the same way. TLS is now verified against the system trust store, the
  same one `curl` uses. Untrusted and expired certificates are still rejected.
- **Every evaluation run failed immediately** with `name '_mlx_embed' is not defined`. The config
  collapse removed the per-role engine setting but left one reader behind, in the gate that used
  to decide whether to probe Ollama for an embeddings endpoint. There is no second engine to
  probe now, so the gate is gone.
- **Mermaid diagrams could not be rendered to images** — the shared-browser refactor removed the
  render page but not the code reading it, so PPTX and image export raised on every diagram. The
  page is rebuilt from the vendored copy of mermaid.js, which keeps it working offline and inside
  the app bundle.
- **Bulk-approving correspondent queue items failed** — a missing type import left the request
  model unbuildable.
- **`/system/model-readiness` could report ready while the engine was dead.** The same config
  collapse orphaned a name in the engine check, and the error was swallowed by the surrounding
  `except`. This is the endpoint the troubleshooting docs reach for first.
- Diagnostic logging in the health portal referenced a logger that was never defined, so several
  repair and check paths raised instead of reporting.
- **A failed model browse said "check your connection" no matter what went wrong.** Rate limiting,
  a refused request and a rejected certificate now each say so, and the log keeps the underlying
  error rather than only its type — the two failures that matter most are indistinguishable by
  type alone.
- **The app could not launch** — a startup banner printed a setting deleted in the config
  collapse, which raised inside a background task, so the backend served HTTP but never reported
  ready and the shell restarted it every ~30s, with a clean log. Now covered by a static check
  that resolves every `settings.<attr>` against the model, including in `main.py`.
- **Bulk embedding exceeded the Metal buffer cap.** Attention is O(batch × seq²) and every
  sequence in a batch is padded to the longest, so one long document could ask for tens of GB and
  fail the whole batch. Embedding batches are now grouped by attention cost against a
  working-set-derived budget. Vectors are unchanged, so no re-indexing is required.
- The canvas silently fell back to a flat grid whenever that embedding failure occurred, because
  a failed embed reads as "no topics" and no topics reads as "use the grid". Clustering is
  restored.
- The config collapse had disabled **every** embedding call; 148 zero vectors written during that
  window were repaired.
- Model weight sizing missed multi-component diffusion layouts and `.npz`/`.bin` checkpoints, so
  downloaded models could report as absent.
- Deep-dive source quality scoring parsed LLM JSON by hand and fell back silently on any
  malformation; it now goes through the shared repair path.
- `build.sh` stages the app and swaps it atomically, restoring the previous build on failure, and
  syncs frontend dependencies before the typecheck so a pull that adds one doesn't break the
  build. `install.sh --branch <name>` now fetches the branch it is asked to track.
### Removed
- `ebooklib` (AGPL-3.0), which was shipping inside the signed app. EPUB is read with the standard
  library and `lxml`.

---
---

## v1.8.0 — Studio Redesign, iPhone Scan Capture, Sidecar Lifecycle, Multi-Provider LLM

- **Studio redesigned** — one unified drawer + two slim entry bars replace the old 9-pill action bar; 6 generation types including Flash Cards. Studio documents now read like the chat (explicit markdown presentation brief).
- **iPhone Scan Capture** — Continuity Camera integrated in-process via `AVCaptureDevice` with multi-page Scan Documents sessions, portrait preview, and rotate.
- **Memory steward** — owns Ollama RAM hygiene, evicting non-essential models before each scan so the vision working set fits a 16 GB Mac.
- **Signed + notarized releases** — `release.sh` now does codesign + `notarytool` + stapler + Gatekeeper verify end-to-end (identity injected, never committed).
- **Multi-provider LLM foundation** — `llm_provider.py` routes models to Ollama or a `llama-server` sidecar (one-click Bonsai-8B swap from the Health Portal).
- **Library main view + main nav redesign** — type-grouped accordion with universal Download/export; word-button nav (Chat / Library / Constellation / Timeline / Curator) + ⌘1-⌘5 / ⌘K command palette.

---

<details>
<summary><strong>v1.6.0 – v1.6.2</strong> — Granular ingestion progress, multi-intent chat, YouTube sources</summary>

- **Granular ingestion progress** — the upload bar streams stage-by-stage (receive → detect → extract → analyze → chunk → summarize → HyDE → embed → index → tag) with an optional "Show journey" expander; per-file progress in multi-file uploads
- **Multi-intent chat** — `@collector` / `@curator` / `@research` / `@studio` handle compound requests in one message ("add this URL and set my focus to X")
- **YouTube sources** — ingest with a full-transcript summary; consistent `▶️ YOUTUBE` / `ARXIV` labels across every add path (chat, extension, feed, agent)
- **LLM Locker improvements** — smarter RAM estimation eliminates false memory rejections; per-model tuning profiles in the registry
- **Quiz enhancements** — RAG-retrieval-backed questions, five question types, instant click-to-reveal, LLM-graded open answers with partial credit
</details>

## v1.5

### Video Explainers
| Feature | Description |
|---------|-------------|
| 🎬 **Video Generation** | Generate narrated explainer videos from notebooks with auto-storyboarding |
| 🎨 **Visual Styles** | Multiple slide styles: classic, dark, whiteboard, and more |
| 🎤 **TTS Narration** | Natural voice narration via Kokoro-82M TTS (50+ voices, 9 languages) with per-chunk progress tracking |

### Feynman Learning Suite
| Feature | Description |
|---------|-------------|
| 🧠 **Feynman Curriculum** | 4-part progressive learning: Foundation → Building → First Principles → Mastery |
| 🎙️ **Teaching Podcasts** | Dedicated teacher/learner audio format (up to 45 min) |
| 📊 **Learning Visuals** | Progression flowcharts, knowledge maps, and misconception diagrams |
| 🧪 **Self-Tests** | Integrated quiz generation at multiple difficulty levels |

### Studio & Content Generation
| Feature | Description |
|---------|-------------|
| 📝 **Outline-First Documents** | Multi-step pipeline for deep dives, debates, and curricula — eliminates cutoffs and repetition |
| 🔁 **Completion Verification** | Post-generation gate ensures all required sections are present |
| 🎧 **Chain-of-Density Audio** | Running summaries between podcast sections prevent topic repetition |

### Weekly Wrap-Up & Curator
| Feature | Description |
|---------|-------------|
| 📅 **Weekly Wrap-Up** | Monday morning summary of all research activity across notebooks |
| 🔍 **Feed Page Detection** | Collector auto-detects index/listing pages and extracts article links |
| 📰 **RSS & Feed Pages** | Recurring collection from RSS feeds and content index pages |

### Chat & Rendering
| Feature | Description |
|---------|-------------|
| 💬 **Markdown Chat** | Chat messages now render full Markdown (headings, bold, lists, tables, code) with inline citations |
| 📐 **Consistent Formatting** | Unified Markdown rendering across chat, canvas, curator, and all panels |
| ⚡ **Adaptive Response Format** | Auto-detects list, table, step-by-step, and code queries for optimized formatting |

See [CHANGELOG.md](CHANGELOG.md) for full release history.

---

## Previous Releases

<details>
<summary><strong>v1.3</strong> — Flexible drawers, citation popups, collector enhancements</summary>

- Flexible drawer panels — Sources and Collector fill available space
- Citation popup portals — tooltips never clipped by sidebar overflow
- Compact chat input, reliable Studio drawer expand/collapse
- Collector: expanded frequencies (2h, 8h, twice daily, every 3 days), full Curator pipeline, frequency picker wizard
</details>

<details>
<summary><strong>v1.20</strong> — People Profiler, Curator Agent, Constellation v2, Memory v2</summary>

- People Profiler with coaching notes, goals, and social platform integration
- Curator Agent for cross-notebook intelligence, morning briefs, config inference
- Knowledge Constellation v2: dynamic zoom, concentric rings, tag-based edges, smart labels
- Parallel sub-queries, response format detection, content date extraction
- Memory v2: deep consolidation, user signals, search miss tracking, daily summaries
</details>

<details>
<summary><strong>v1.10</strong> — Visual Studio, Mermaid prewarm, metrics persistence</summary>

- AI Visual Generator with intelligent type selection and lightbox view
- Horizontal Steps template, vibrant theming for light/dark modes
- Mermaid prewarm for instant rendering, metrics persistence across restarts
- Auto-fix for malformed LLM diagram output
</details>

<details>
<summary><strong>v1.0.0</strong> — Browser Extension, Studio, RAG v2, Voice, Credentials</summary>

- LocalBook Companion browser extension (summarize, capture, chat with pages)
- Studio content generation (quizzes, visual summaries, writing assistant)
- Query Orchestrator, Parent Document Retrieval, Hybrid Search, FlashRank reranking, Corrective RAG
- Credential Locker, Site Search, Voice Input, Entity Graph, Contradiction Detection
- Snowflake Arctic Embed2 (1024-dim), Phi-4 Mini, OLMo-3 7B
</details>

<details>
<summary><strong>v0.2 – v0.6</strong> — Foundation releases</summary>

- 3D Constellation, Key Themes, persistent memory, auto-upgrade
- BERTopic topic modeling, migration manager, embedding upgrades
- Adaptive RAG, hybrid search, FlashRank reranking
</details>

---

## Requirements

| Requirement | Details |
|-------------|---------|
| **macOS** | 12.0+ (Apple Silicon required — M1/M2/M3/M4) |
| **Python** | 3.12+ required |
| **RAM** | 16GB+ recommended (8GB minimum) |
| **Storage** | ~20GB for models and app |
| **Engine** | Apple MLX — runs in-process, no separate server to install or start |

### System Dependencies

The build script installs these automatically, or install manually:

```bash
brew install ffmpeg tesseract espeak-ng python@3.12 node
```

---

## Quick Start

### Option 1: One-Line Install (Recommended)

Clones the repo, builds the app, pulls the AI models, and installs to `/Applications` — one command:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/patsteph/LocalBook/master/install.sh)"
```

To upgrade an existing install, append `-- --upgrade`.

### Option 2: Download Release

1. Download `LocalBook-v2.3.0.zip` from [Releases](https://github.com/patsteph/LocalBook/releases)
2. Unzip and drag `LocalBook.app` to `/Applications`
3. Launch LocalBook — it will download required AI models on first run

### Option 3: Build from Source

**⚠️ Requires Python 3.12+**

```bash
# Ensure Python 3.12+ is installed
brew install python@3.12

# Clone and build
git clone https://github.com/patsteph/LocalBook.git
cd LocalBook
./build.sh
cp -r LocalBook.app /Applications/
```

Build takes ~15-20 minutes on first run (downloads models, installs dependencies).

### ⚡ Speed Up First Launch

Pre-download AI models before launching:

Models are MLX checkpoints from Hugging Face, managed in-app from **LLM Studio**. The
installer fetches them on first run; nothing is downloaded at launch, so a fresh start never
stalls behind a multi-GB pull.

| Role | Model | Size |
|---|---|---|
| Main + Vision | `mlx-community/gemma-4-e4b-it-4bit` | 4.8 GB |
| Fast | `mlx-community/Phi-4-mini-instruct-4bit` | 2.0 GB |
| Embeddings | `mlx-community/snowflake-arctic-embed-l-v2.0-bf16` | 1.1 GB |
| Image | `Runpod/FLUX.2-klein-4B-mflux-4bit` | 4.3 GB |

The Kokoro-82M TTS model (~348MB) downloads automatically on first use. If the automatic download fails (e.g. SSL certificate issues on macOS), you can download it manually:

```bash
bash backend/scripts/download_kokoro_model.sh
```

---

## Browser Extension

The **LocalBook Companion** extension lets you use LocalBook while browsing the web.

### Installation

1. Download `LocalBook-Extension-v2.3.0.zip` from [Releases](https://github.com/patsteph/LocalBook/releases)
2. Unzip to a folder (e.g., `~/LocalBook-Extension`)
3. Open Chrome/Edge and go to `chrome://extensions`
4. Enable **Developer mode** (toggle in top right)
5. Click **Load unpacked** and select the extension folder
6. Pin the extension to your toolbar for easy access

### Features

- **Summarize** — Get AI summaries of any webpage
- **Capture** — Save pages to your LocalBook notebooks
- **Chat** — Ask questions about the page you're viewing
- **Research** — Web search with AI-powered results

> **Note:** The extension requires LocalBook app to be running (it connects to the local backend).

---

## Document Support

LocalBook supports a wide range of document formats:

| Format | Extensions | Notes |
|--------|------------|-------|
| **PDF** | `.pdf` | Full text extraction |
| **Word** | `.docx` | Microsoft Word 2007+ |
| **PowerPoint** | `.pptx` | Slide text extraction |
| **Excel** | `.xlsx`, `.xls` | Spreadsheet data |
| **EPUB** | `.epub` | E-books |
| **Jupyter** | `.ipynb` | Notebooks with code/markdown |
| **Images** | `.png`, `.jpg`, `.jpeg`, `.webp` | OCR text extraction (requires Tesseract) |
| **Apple Photos** | `.heic`, `.heif` | OCR text extraction |
| **SVG** | `.svg` | Text extraction from vector graphics |
| **Video** | `.mp4`, `.mov`, `.m4v`, `.mkv` | Audio transcript extraction |
| **Audio** | `.mp3`, `.wav`, `.m4a`, `.ogg` | Speech-to-text transcription |
| **Web** | URLs | Full page capture and parsing |
| **YouTube** | URLs | Automatic transcript extraction |
| **RTF** | `.rtf` | Rich Text Format |
| **OpenDocument** | `.odt`, `.ods` | Text and spreadsheet formats |

---

## Core Features

### 💬 Chat with Documents
Ask questions about your uploaded documents. LocalBook retrieves relevant passages and generates answers with citations pointing to exact sources.

### 🌌 Knowledge Constellation
Interactive 3D visualization of concepts and entities across all your documents. See how ideas connect, discover clusters, and explore your knowledge graph.

### 🧠 Persistent Memory
LocalBook remembers facts about you, your preferences, and your research context. Memory persists across sessions and can be managed in Settings.

### 🎙️ Podcast Generation
Transform your documents into engaging audio discussions. Great for learning on the go or reviewing content in a new format.

### 📅 Timeline Extraction
Automatically extract dates and events from documents, visualized on an interactive timeline.

---

## Configuration

### In-App Settings
- **LLM Studio** — pick the model for each role (main, fast, vision, embeddings) from the
  models downloaded on this Mac. Everything runs in-process on Apple MLX.
- **API Keys** — Brave Search (for web search)
- **Memory** — View, edit, and manage AI memory

### Environment Variables (`backend/.env`)
```bash
# LLM Configuration
OLLAMA_MODEL=gemma4:e4b               # Main model (chat + native vision)
OLLAMA_FAST_MODEL=phi4-mini           # Fast responses
EMBEDDING_MODEL=snowflake-arctic-embed2  # 1024-dim embeddings

# Optional API Keys (can also set in app)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
BRAVE_API_KEY=BSA...
```

---

## Data Storage

All data is stored locally in `~/Library/Application Support/LocalBook/`:

| Directory | Contents |
|-----------|----------|
| `uploads/` | Your uploaded documents |
| `lancedb/` | Vector embeddings database |
| `memory/` | AI memory (persists across updates) |
| `audio/` | Generated podcasts |
| `credentials/` | Encrypted site credentials |
| `backups/` | Pre-migration backups |

---

## Development

### Running in Development Mode

```bash
./start.sh  # Starts backend + frontend with hot-reload
```

API documentation available at http://localhost:8000/docs when running.

### Project Structure

```
LocalBook/
├── backend/           # Python FastAPI backend
│   ├── api/          # REST endpoints (29 modules)
│   ├── services/     # Core services (RAG, memory, knowledge graph)
│   ├── storage/      # Data persistence (LanceDB, file storage)
│   └── agents/       # LangGraph agent workflows
├── src/              # React TypeScript frontend
│   ├── components/   # UI components
│   └── services/     # API client services
├── extension/        # Browser extension (Plasmo)
└── src-tauri/        # Tauri desktop wrapper (Rust)
```

### Building a Release

```bash
./release.sh 1.5  # Creates versioned archives for distribution
```

---

## Upgrading

### From v1.x
Automatic upgrade. Replace the app and restart. All data is preserved.

### From v0.6.x or earlier
Documents will be re-indexed with new embeddings on first launch. This is automatic but may take a few minutes.

### From v0.1.x
Data was stored inside the app bundle. Run this **before** replacing:
```bash
curl -sL https://raw.githubusercontent.com/patsteph/LocalBook/master/migrate_data.sh | bash
```

---

## Troubleshooting

### Models Not Loading
The engine runs in-process — there is no server to start. Check which models the app can
actually see:
```bash
curl -s localhost:8000/system/model-readiness | python3 -m json.tool
```

### Models Missing
Open **LLM Studio** and download the model for the role that is missing — it lists only what
is actually present on this Mac. To see what the app thinks is missing:
```bash
curl -s localhost:8000/system/model-readiness | python3 -m json.tool
```

### Extension Not Connecting
1. Make sure LocalBook app is running
2. Check that backend is accessible at http://localhost:8000
3. Reload the extension in `chrome://extensions`

### Clean Rebuild
```bash
rm -rf src-tauri/resources/backend/ src-tauri/target/ node_modules/ backend/.venv/
./build.sh --clean
```

### OCR Not Working
```bash
brew install tesseract  # Install Tesseract for image OCR
```

---

## Privacy & Security

- **100% Local Processing** — All AI inference runs on your machine via Ollama
- **No Cloud Required** — Works completely offline after initial setup
- **No Telemetry** — Zero data collection or tracking
- **Encrypted Credentials** — Site credentials stored with Fernet encryption
- **Open Source** — Full source code available for audit

---

## License

MIT — See [LICENSE](LICENSE) file.

---

## Acknowledgments

- [Ollama](https://ollama.ai) — Local LLM runtime
- [LanceDB](https://lancedb.com) — Vector database
- [Tauri](https://tauri.app) — Desktop app framework
- [LangChain](https://langchain.com) / [LangGraph](https://langchain-ai.github.io/langgraph/) — Agent orchestration

---

**Built for privacy-conscious users who want local document AI.**

*Inspired by Google's NotebookLM, but running entirely on your machine.*
