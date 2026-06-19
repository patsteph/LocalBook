# LocalBook

**Your documents, your AI, your machine.** A private, offline alternative to cloud-based AI assistants.

[![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)](https://github.com/patsteph/LocalBook/releases)
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

## 🎉 What's New in v2.0.0
LocalBook's largest release: an Information Cortex that turns documents and
forwarded email into a synthesizing knowledge system — one artifact/renderer
spec (Universal Canvas), an email cortex (Correspondent), and a cross-source
synthesis layer. Highlights below.
### Highlights
- **Universal Canvas** — single artifact spec + renderer registry routes markdown / strict HTML / interactive HTML (iframe sandbox) / SVG / Mermaid / Klein / `json:<kind>` through one dispatch. Mixed-medium documents interleave prose + Recharts + SVG via gemma4 `VISUAL_INTERLEAVE` injection + post-processor.
- **Correspondent (email cortex)** — IMAP poller (Gmail / Fastmail / iCloud+ / Outlook via app password), tool-less LLM classification, cross-notebook auto-routing via embedding similarity, sister-newsletter auto-subscribe through Collector queue, reply-to-ingest, outbound SMTP via `aiosmtplib`, newsletter HTML rendered in source viewer.
- **Synthesis layer** — Curator HTML morning brief with consensus detection + deep-read auto-trigger, interactive HTML artifacts (iframe `sandbox="allow-scripts"` + postMessage), cross-source perspectives view (consensus vs contested), per-notebook dashboards, entity-anchored topic deep-dives, source-graph entity proposals, weekly auto-journal via SMTP.
- **Correspondent Tier 2 (all 10 capabilities)** — per-article extraction + summary + RAG indexing, hot/cold clusters via embedding agglomeration, deep-read with newsletter context, cross-notebook entity tagging at ingest, per-newsletter scorecard, RFC 2369 List-Unsubscribe one-click POST with two-step confirmation, frequency tuner, smart digest grouping, effectiveness dashboard, routing histogram with auto/manual/queued series.
- **Gemma4 flip** — `ollama_model` default switched from `olmo-3:7b-instruct` to `gemma4:e4b`. Native vision absorbs the vision slot on 16 GB Macs.

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
| **Ollama** | Local LLM runtime ([ollama.ai](https://ollama.ai)) |

### System Dependencies

The build script installs these automatically, or install manually:

```bash
brew install ollama ffmpeg tesseract espeak-ng python@3.12 node
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

1. Download `LocalBook-v2.0.0.zip` from [Releases](https://github.com/patsteph/LocalBook/releases)
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

```bash
# The one-line installer pulls these for you; to pre-pull manually:
ollama pull gemma4:e4b              # Main model — chat + native vision (~9.6GB)
ollama pull phi4-mini               # Fast model for quick responses
ollama pull snowflake-arctic-embed2 # Embeddings (1024 dims)
ollama pull granite3.2-vision:2b    # Vision fallback
```

The Kokoro-82M TTS model (~348MB) downloads automatically on first use. If the automatic download fails (e.g. SSL certificate issues on macOS), you can download it manually:

```bash
bash backend/scripts/download_kokoro_model.sh
```

---

## Browser Extension

The **LocalBook Companion** extension lets you use LocalBook while browsing the web.

### Installation

1. Download `LocalBook-Extension-v2.0.0.zip` from [Releases](https://github.com/patsteph/LocalBook/releases)
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
- **LLM Provider** — Choose between Ollama, OpenAI, or Anthropic
- **API Keys** — Brave Search (for web search), OpenAI, Anthropic
- **Memory** — View, edit, and manage AI memory
- **Embeddings** — Choose embedding model

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

### Ollama Not Running
```bash
ollama serve          # Start Ollama
ollama list           # Verify models installed
```

### Models Missing
```bash
ollama pull olmo-3:7b-instruct
ollama pull phi4-mini
ollama pull snowflake-arctic-embed2
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
