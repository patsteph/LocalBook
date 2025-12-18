# LocalBook

**Your documents, your AI, your machine.** A private, offline alternative to cloud-based AI assistants.

![LocalBook](https://img.shields.io/badge/Platform-macOS-blue) ![Python](https://img.shields.io/badge/Python-3.10+-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## What is LocalBook?

LocalBook lets you **chat with your documents** using AI — completely offline and private. Upload PDFs, Word docs, web pages, or YouTube videos, then ask questions and get answers with exact citations.

- 🔒 **100% Private** — Everything runs on your Mac
- 📚 **Your Documents** — AI answers from YOUR files with citations
- � **Knowledge Constellation** — 3D visualization of concepts across documents
- 🧠 **Memory System** — AI remembers your preferences and past conversations
- 🎙️ **Podcast Generator** — Turn documents into audio discussions
- � **Auto-Updates** — Check for updates from GitHub (**under construction for packaged `.app` installs**)

---

## Quick Start

```bash
# Clone and build (~15-20 min first time)
git clone https://github.com/patsteph/LocalBook.git
cd LocalBook
./build.sh

# Install
cp -r LocalBook.app /Applications/
```

The build script installs everything: Homebrew, Python, Node.js, Rust, Ollama, AI models (~10GB), and all dependencies.

Note: `./build.sh` performs network downloads and may install system dependencies. It typically requires an admin-enabled Mac and may prompt for permissions.

---

## Requirements

### System
- **macOS** (required for audio generation)
- **16GB+ RAM** recommended (8GB minimum)
- **~15GB storage** for models and app
- **Apple Silicon** recommended (Intel works but slower)

### System Dependencies
| Dependency | Purpose | Install |
|------------|---------|--------|
| **Ollama** | Local LLM inference | `brew install ollama` |
| **ffmpeg** | Audio/video transcription | `brew install ffmpeg` |
| **Python 3.10+** | Backend | `brew install python@3.11` |
| **Node.js 18+** | Frontend build | `brew install node` |
| **git** | Updates | Pre-installed on macOS |

> The `build.sh` script installs all of these automatically.

### AI Models (pulled by build script)
```bash
ollama pull phi4:14b           # System 2: Main model (~9GB)
ollama pull llama3.2:3b        # System 1: Fast model (~2GB)
ollama pull nomic-embed-text   # Embeddings (~300MB)
```

---

## Features

### Core Features
| Feature | Description |
|---------|-------------|
| 💬 **Chat** | Ask questions, get answers with citations |
| 📄 **Multi-format** | PDF, Word, PowerPoint, Excel, web pages, YouTube |
| 🔍 **Web Search** | Optionally supplement with real-time web results |
| 📅 **Timeline** | Auto-extract and visualize dates/events |

### Latest Features (v0.2.0)
| Feature | Description |
|---------|-------------|
| 🌌 **3D Constellation** | Interactive 3D knowledge graph with clustering and color-coded themes |
| 🎯 **Key Themes** | Auto-discovered topic clusters from your documents |
| 📊 **Top Concepts** | See your most frequently referenced concepts |
| 🔄 **Real-time Updates** | Constellation and themes update live during builds |
| ⚡ **Embedding Options** | Choose from multiple Ollama embedding models (Nomic, MixedBread, MiniLM, Snowflake) |
| 🧠 **Memory** | AI remembers facts about you across sessions |
| 📅 **Timeline** | Auto-extract and visualize dates/events from documents |

### ⚠️ Upgrading from v0.1.x (IMPORTANT)
If upgrading from v0.1.x, your data is stored inside the app bundle and **will be lost** if you simply replace the app.

**Before replacing LocalBook.app, run this migration script:**
```bash
curl -sL https://raw.githubusercontent.com/patsteph/LocalBook/master/migrate_data.sh | bash
```

Or manually copy your data:
```bash
cp -R "/Applications/LocalBook.app/Contents/Resources/resources/backend/localbook-backend/_internal/data/"* ~/Library/Application\ Support/LocalBook/
```

**After upgrading:**
1. **Re-embed documents** — The default embedding model changed. Go to Settings → Embedding Model and click "Re-embed All Documents".
2. **Rebuild Constellation** — Click Reset → Build Constellation to regenerate the knowledge graph.

**v0.2.2+ users:** Future updates are seamless via Settings → Updates → Download & Install.

---

## Development

```bash
# Run in development mode with hot-reload
./start.sh
```

### Project Structure
```
LocalBook/
├── backend/           # Python FastAPI backend
│   ├── api/          # API endpoints
│   ├── services/     # Business logic (RAG, memory, knowledge graph)
│   └── storage/      # Database and vector storage
├── src/              # React frontend
├── src-tauri/        # Tauri desktop app
└── data/             # Local data (gitignored)
```

### API Docs
When running: http://localhost:8000/docs

---

## Configuration

### Settings (in-app)
- **API Keys**: Brave Search, OpenAI, Anthropic
- **Memory**: View/manage AI memory
- **Updates**: Check for new versions

### Environment (`backend/.env`)
```bash
OLLAMA_MODEL=phi4:14b              # System 2: Main reasoning model
OLLAMA_FAST_MODEL=llama3.2:3b      # System 1: Fast responses
EMBEDDING_MODEL=nomic-embed-text   # Document embeddings via Ollama
CHUNK_SIZE=1000
CHUNK_OVERLAP=200
```

---

## Data Storage

All data stored locally in `data/` (gitignored):
- `data/uploads/` — Your documents
- `data/lancedb/` — Vector embeddings
- `data/memory/` — AI memory (persists across updates)
- `data/audio/` — Generated podcasts

---

## Troubleshooting

### Ollama Issues
```bash
curl http://localhost:11434/api/tags  # Check if running
ollama serve                           # Start if not
ollama list                            # Verify models
```

### Clean Rebuild
```bash
./build.sh --rebuild

# If you still have issues, do a full clean wipe rebuild:
rm -rf src-tauri/resources/backend/ src-tauri/target/ node_modules/ backend/.venv/
./build.sh
```

### Memory Not Working
Restart the backend after updating. Memory is extracted from chat conversations automatically.

---

## License

MIT — See LICENSE file.

---

## Acknowledgments

Inspired by Google's NotebookLM, built for privacy-conscious users who want local document AI.
