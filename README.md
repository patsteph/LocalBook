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
- � **Auto-Updates** — Check for and pull updates from GitHub

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
ollama pull mistral-nemo:12b-instruct-2407-q4_K_M  # Main model (~7GB)
ollama pull phi4-mini                               # Fast model (~2GB)
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

### New in v0.1.0
| Feature | Description |
|---------|-------------|
| 🌌 **Constellation** | 3D knowledge graph showing concept relationships |
| 🧠 **Memory** | AI remembers facts about you across sessions |
| 🎨 **Notebook Colors** | Color-code notebooks for organization |
| 🔄 **Updates** | Check for updates in Settings → Updates |

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
OLLAMA_MODEL=mistral-nemo:12b-instruct-2407-q4_K_M
OLLAMA_FAST_MODEL=phi4-mini
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
