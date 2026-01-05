# LocalBook

**Your documents, your AI, your machine.** A private, offline alternative to cloud-based AI assistants.

![LocalBook](https://img.shields.io/badge/Platform-macOS-blue) ![Python](https://img.shields.io/badge/Python-3.10+-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

---

## What is LocalBook?

Chat with your documents using AI — completely offline and private. Upload PDFs, Word docs, web pages, or YouTube videos, then ask questions and get answers with exact citations.

- 🔒 **100% Private** — Everything runs locally on your Mac
- 📚 **Cited Answers** — AI answers from YOUR files with source citations
- 🌌 **Knowledge Constellation** — 3D visualization of concepts across documents
- 🧠 **Memory System** — AI remembers your preferences across sessions
- 🎙️ **Podcast Generator** — Turn documents into audio discussions

---

## Requirements

| Requirement | Details |
|-------------|---------|
| **macOS** | Required (Apple Silicon recommended, Intel supported) |
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

```bash
git clone https://github.com/patsteph/LocalBook.git
cd LocalBook
./build.sh
cp -r LocalBook.app /Applications/
```

Build takes ~15-20 minutes on first run (downloads models, installs dependencies).

### ⚡ Speed Up First Launch

Pre-download AI models before building to save time on first startup:

```bash
# Required models (~6GB total)
ollama pull olmo-3:7b-instruct      # Main reasoning model
ollama pull phi4-mini               # Fast model
ollama pull snowflake-arctic-embed2 # Embeddings (1024 dims)
```

---

## Features

| Feature | Description |
|---------|-------------|
| 💬 **Chat** | Ask questions, get answers with citations |
| 📄 **Multi-format** | PDF, Word, PowerPoint, Excel, EPUB, Jupyter, Images (OCR), YouTube |
| 🔍 **Web Search** | Supplement answers with real-time web results |
| 📅 **Timeline** | Auto-extract and visualize dates/events |
| 🌌 **Constellation** | 3D knowledge graph with clustering |
| 🧠 **Memory** | AI remembers facts about you across sessions |
| 🎙️ **Podcasts** | Generate audio discussions from documents |

### What's New in v0.6

| Feature | Description |
|---------|-------------|
| 🎯 **Query Orchestrator** | Complex queries auto-decompose into sub-questions |
| 📖 **Parent Document Retrieval** | Retrieves surrounding context for better answers |
| 🕸️ **Entity Graph** | Tracks people, metrics, and relationships |
| 🔄 **Migration Manager** | Seamless upgrades with progress notifications |
| ❄️ **Snowflake Embeddings** | Upgraded to 1024-dim frontier embeddings |
| ⚡ **Phi-4 Mini** | Faster responses with Microsoft's latest small model |

### What's New in v0.5

| Feature | Description |
|---------|-------------|
| 🎯 **Adaptive RAG** | Two-tier model routing (fast vs deep thinking) |
| 🔀 **Hybrid Search** | Vector + BM25 keyword search combined |
| 📊 **FlashRank Reranking** | Cross-encoder reranking for better retrieval |
| ✨ **Cleaner Answers** | Improved prompt engineering, no artifacts |

---

## Upgrading

### From v0.5
Automatic incremental upgrade. Just replace the app and restart.

### From v0.2/v0.3
Automatic migration on first launch. Documents will be re-indexed with new embeddings.

### From v0.1.x
Data was stored inside the app bundle. Run this **before** replacing the app:
```bash
curl -sL https://raw.githubusercontent.com/patsteph/LocalBook/master/migrate_data.sh | bash
```

---

## Configuration

### In-App Settings
- **API Keys** — Brave Search, OpenAI, Anthropic (optional)
- **Memory** — View/manage what AI remembers
- **Updates** — Check for new versions

### Environment (`backend/.env`)
```bash
OLLAMA_MODEL=olmo-3:7b-instruct       # Main reasoning (64K context)
OLLAMA_FAST_MODEL=phi4-mini           # Fast responses
EMBEDDING_MODEL=snowflake-arctic-embed2  # 1024-dim embeddings
```

---

## Data Storage

All data stored in `~/Library/Application Support/LocalBook/`:

| Directory | Contents |
|-----------|----------|
| `uploads/` | Your documents |
| `lancedb/` | Vector embeddings |
| `memory/` | AI memory (persists across updates) |
| `audio/` | Generated podcasts |
| `backups/` | Pre-migration backups |

---

## Development

```bash
./start.sh  # Run with hot-reload
```

API docs available at http://localhost:8000/docs when running.

### Project Structure
```
LocalBook/
├── backend/           # Python FastAPI
│   ├── api/          # REST endpoints
│   ├── services/     # RAG, memory, knowledge graph
│   └── storage/      # LanceDB, file storage
├── src/              # React frontend
└── src-tauri/        # Tauri desktop wrapper
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

### Clean Rebuild
```bash
rm -rf src-tauri/resources/backend/ src-tauri/target/ node_modules/ backend/.venv/
./build.sh
```

---

## License

MIT — See LICENSE file.

---

Built for privacy-conscious users who want local document AI. Inspired by Google's NotebookLM.
