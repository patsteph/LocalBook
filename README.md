# LocalBook

**Your documents, your AI, your machine.** A private, offline alternative to cloud-based AI assistants.

[![Version](https://img.shields.io/badge/version-1.0.3-blue.svg)](https://github.com/patsteph/LocalBook/releases)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://github.com/patsteph/LocalBook)
[![Python](https://img.shields.io/badge/python-3.11-green.svg)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

---

## What is LocalBook?

Chat with your documents using AI — completely offline and private. Upload PDFs, Word docs, web pages, or YouTube videos, then ask questions and get answers with exact citations.

- 🔒 **100% Private** — Everything runs locally on your Mac
- 📚 **Cited Answers** — AI answers from YOUR files with source citations
- 🌌 **Knowledge Constellation** — 3D visualization of concepts across documents
- 🧠 **Memory System** — AI remembers your preferences across sessions
- 🎙️ **Podcast Generator** — Turn documents into audio discussions
- 🧩 **Browser Extension** — Research companion for web browsing

---

## 🎉 What's New in v1.0.5

### Visual Studio Improvements
| Feature | Description |
|---------|-------------|
| 🎨 **AI Visual Generator** | Options to choose from with intelligent type selection |
| 🔍 **Lightbox View** | Click diagrams to view full-size with export options |
| 🌈 **Vibrant Theming** | Beautiful color palette for light and dark modes |
| 📊 **Horizontal Steps** | New template for simple step sequences |

### Performance & Reliability
| Feature | Description |
|---------|-------------|
| ⚡ **Mermaid Prewarm** | Instant diagram rendering (no cold start) |
| 💾 **Metrics Persistence** | Query stats survive restarts |
| 🔧 **Code Auto-fix** | Malformed LLM output automatically corrected |

See [CHANGELOG.md](CHANGELOG.md) for full release history.

---

## v1.0.0 Highlights

The foundation release includes:

### Browser Extension: LocalBook Companion
| Feature | Description |
|---------|-------------|
| 🧩 **Side Panel** | AI assistant available while browsing any website |
| 📝 **Page Summarization** | One-click summaries with key points and concepts |
| 💬 **Chat with Pages** | Ask questions about any webpage you're viewing |
| 📥 **Quick Capture** | Save pages directly to your notebooks |

### Studio: Content Generation
| Feature | Description |
|---------|-------------|
| 📝 **Quiz Generator** | Create quizzes from notebook content with topic focus |
| 🎨 **Visual Summaries** | Generate Mermaid diagrams, timelines, concept maps |
| ✍️ **Writing Assistant** | AI-powered document drafting |

### Enhanced RAG Engine
| Feature | Description |
|---------|-------------|
| 🎯 **Query Orchestrator** | Complex queries auto-decompose into sub-questions |
| 📖 **Parent Document Retrieval** | Retrieves surrounding context for better answers |
| 🔀 **Hybrid Search** | Vector + BM25 keyword search combined |
| 📊 **FlashRank Reranking** | Cross-encoder reranking for better retrieval |
| 🔄 **Corrective RAG** | Query reformulation when initial retrieval fails |

### New Capabilities
| Feature | Description |
|---------|-------------|
| 🔐 **Credential Locker** | Encrypted storage for site credentials |
| 🌐 **Site Search** | Deep search across entire websites |
| 🎤 **Voice Input** | Dictate questions using speech-to-text |
| 🕸️ **Entity Graph** | Track people, metrics, relationships across documents |
| 🔍 **Contradiction Detection** | Find conflicting information in your sources |

See [CHANGELOG.md](CHANGELOG.md) for full release notes.

---

## Requirements

| Requirement | Details |
|-------------|---------|
| **macOS** | 12.0+ (Apple Silicon recommended, Intel supported) |
| **Python** | 3.11 required (openai-whisper dependency) |
| **RAM** | 16GB+ recommended (8GB minimum) |
| **Storage** | ~15GB for models and app |
| **Ollama** | Local LLM runtime ([ollama.ai](https://ollama.ai)) |

### System Dependencies

The build script installs these automatically, or install manually:

```bash
brew install ollama ffmpeg tesseract python@3.11 node
```

---

## Quick Start

### Option 1: Download Release (Recommended)

1. Download `LocalBook-v1.0.3.zip` from [Releases](https://github.com/patsteph/LocalBook/releases)
2. Unzip and drag `LocalBook.app` to `/Applications`
3. Launch LocalBook — it will download required AI models on first run

### Option 2: Build from Source

**⚠️ Requires Python 3.11** (not 3.12+). The `openai-whisper` dependency does not support newer Python versions.

```bash
# Ensure Python 3.11 is installed
brew install python@3.11

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
# Required models (~6GB total)
ollama pull olmo-3:7b-instruct      # Main reasoning model (64K context)
ollama pull phi4-mini               # Fast model for quick responses
ollama pull snowflake-arctic-embed2 # Embeddings (1024 dims)
```

---

## Browser Extension

The **LocalBook Companion** extension lets you use LocalBook while browsing the web.

### Installation

1. Download `LocalBook-Extension-v1.0.0.zip` from [Releases](https://github.com/patsteph/LocalBook/releases)
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
| **Images** | `.png`, `.jpg`, `.jpeg` | OCR text extraction (requires Tesseract) |
| **Web** | URLs | Full page capture and parsing |
| **YouTube** | URLs | Automatic transcript extraction |
| **RTF** | `.rtf` | Rich Text Format |
| **OpenDocument** | `.odt` | LibreOffice/OpenOffice |

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
OLLAMA_MODEL=olmo-3:7b-instruct       # Main reasoning model
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
./release.sh 1.0.0  # Creates versioned archives for distribution
```

---

## Upgrading

### From v0.6.x
Automatic upgrade. Replace the app and restart.

### From v0.5.x or earlier
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
