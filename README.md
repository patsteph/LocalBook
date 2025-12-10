# LocalBook

**Your documents, your AI, your machine.** A private, offline alternative to cloud-based AI assistants.

![LocalBook](https://img.shields.io/badge/Platform-macOS-blue) ![Python](https://img.shields.io/badge/Python-3.11+-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

![LocalBook Screenshot](docs/screenshot.png)

## What is LocalBook?

LocalBook lets you **chat with your documents** using AI — completely offline and private. Upload PDFs, Word docs, web pages, or YouTube videos, then ask questions and get answers with exact citations.

**Think of it like ChatGPT, but:**
- 🔒 **100% Private** — Everything runs on your Mac, nothing sent to the cloud
- 📚 **Your Documents** — AI answers come from YOUR files, not the internet
- 🎙️ **Podcast Generator** — Turn your documents into conversational audio podcasts
- 💰 **Free** — No subscriptions, no API costs (uses free local AI models)

### What Can You Do?

| Feature | Description |
|---------|-------------|
| 💬 **Ask Questions** | Chat with your documents in plain English, get answers with page citations |
| 📄 **Upload Anything** | PDFs, Word, PowerPoint, Excel, web pages, YouTube videos |
| 🎙️ **Generate Podcasts** | Create two-host audio discussions from your documents |
| 🔍 **Web Search** | Optionally combine your docs with real-time web results |
| 📅 **Timeline View** | Automatically extract and visualize dates/events |

---

## Installation

> **⏱️ Build time: ~15-20 minutes** (first build only, mostly downloading dependencies and AI models)

### Build & Install

```bash
# Clone the repository
git clone https://github.com/patsteph/LocalBook.git
cd LocalBook

# Build the app (installs all dependencies automatically)
./build.sh

# Install (either way works)
cp -r LocalBook.app /Applications/
# OR open the DMG installer:
open src-tauri/target/release/bundle/dmg/LocalBook_*.dmg
```

That's it! The build script automatically installs:
- ✅ Homebrew (if needed)
- ✅ Python, Node.js, Rust (if needed)
- ✅ Ollama + AI models (~10GB)
- ✅ All project dependencies
- ✅ Builds the complete app

### Running the App

Just launch **LocalBook** from Applications — it automatically starts Ollama if needed.

---

### Development Mode

For development with hot-reload:

```bash
./start.sh
```

---

### (Optional) Install Premium Voices for Audio

For the best audio podcast quality, install premium macOS voices:

1. Open **System Settings** → **Accessibility** → **Spoken Content**
2. Click **System Voice** → **Manage Voices**
3. Download these recommended voices (~100-200MB each):
   - **Ava (Premium)** - Female US voice
   - **Evan (Enhanced)** - Male US voice
   - **Zoe (Premium)** - Female US voice (variety)
   - **Tom (Enhanced)** - Male US voice (variety)

Verify installed voices:
```bash
say -v '?'
# Look for "Ava (Premium)", "Evan (Enhanced)", etc.
```

### (Optional) Set Up Web Search

LocalBook supports web search to supplement your local documents:

- **Brave Search API** (Recommended): 2,000 free queries/month
- **DuckDuckGo** (Fallback): Free but rate-limited

To set up Brave Search:
1. Get a free API key at https://brave.com/search/api/
2. Open LocalBook → Settings (gear icon) → Add your Brave API key

## Usage Guide

### Creating a Notebook
1. Click "New Notebook" in the sidebar
2. Give it a name and description
3. Start adding sources

### Adding Sources
- **Upload Files**: PDF, DOCX, PPTX, XLSX
- **Add Web Page**: Paste a URL to extract content
- **Add YouTube Video**: Paste a YouTube URL for transcript extraction

### Asking Questions
1. Select a notebook
2. Type your question in the chat
3. Toggle "Web Search" for real-time information
4. Get answers with citations and sources

### Generating Audio
1. Go to "Audio Studio" in a notebook
2. Choose topic and duration (5-30 minutes)
3. Select voice preferences (gender, accent)
4. Generate and download MP3 podcast

### Timeline View
1. Navigate to "Timeline" in a notebook
2. See all extracted dates and events
3. Click events to see source documents

### Custom Skills
1. Go to "Skills" in the sidebar
2. Create custom prompts for specialized tasks
3. Use skills when generating audio or content

---

## Tech Stack

<details>
<summary><strong>Click to expand technical details</strong></summary>

### Frontend
- **Framework**: React 19 + TypeScript
- **UI**: TailwindCSS + Lucide Icons
- **Desktop**: Tauri 2 (native desktop app)
- **State Management**: Zustand
- **Build Tool**: Vite

### Backend
- **API**: FastAPI (Python)
- **Vector Database**: LanceDB
- **Embeddings**: sentence-transformers
- **LLM Support**: Ollama, OpenAI, Anthropic
- **Audio**: macOS Say command with premium voices
- **Document Processing**: PyMuPDF, python-docx, pdfplumber, trafilatura
- **Video**: moviepy, youtube-transcript-api
- **Audio Transcription**: faster-whisper

</details>

## Configuration

### LLM Model Selection

**Progressive Response Architecture:**
- **phi4-mini** (fast): Generates quick 2-3 sentence summary (~3s)
- **mistral-nemo** (main): Streams detailed, well-cited answer after
- **phi4-mini** (parallel): Generates follow-up questions while main answer streams

This architecture provides immediate value with a quick summary, then streams the detailed answer for users who want more depth.

**Main Model: mistral-nemo:12b-instruct-2407-q4_K_M**
- **Why**: Best balance of speed, quality, and local privacy
- **Performance**: Quick summary in ~3s, detailed answer in ~12-20s
- **GPU Usage**: 100% utilization on Apple Silicon

**Model Choice Reasoning:**
- ✅ **mistral-nemo:12b-instruct-2407-q4_K_M**: Q4_K_M quantization for better quality (main detailed answers)
- ✅ **phi4-mini**: Fast and lightweight (quick summaries + follow-up questions)
- ❌ **gemma3**: Fast (~10s) but poor answer quality and citation accuracy
- ❌ **ministral-3:8b**: Very slow (60-114s) - not recommended
- ❌ **minitron**: Unstable - hangs indefinitely

### Performance Tuning for Different Models

If you want to experiment with other Ollama models, modify these files:

1. **Change the model** (`backend/services/llm_service.py:12`):
```python
def __init__(self, model_name: str = "your-model-name", provider: str = "ollama"):
```

2. **Adjust generation parameters** (`backend/services/llm_service.py:89-93`):
```python
options = {
    'temperature': 0.4,  # Lower = more focused (0.3-0.5)
    'top_p': 0.9,        # Nucleus sampling (0.85-0.95)
    # No num_predict limit for quality (add 400-700 for speed)
}
```

3. **Tune citation count** (`backend/models/chat.py:11`):
```python
top_k: Optional[int] = 5  # Lower = faster (3-7 recommended)
```

**Performance Tips:**
- **Smaller models**: Add `num_predict: 400` for speed, but expect shorter answers
- **Larger models**: Remove `num_predict` limit, increase `top_k` to 7-10
- **Speed vs Quality**: Lower `top_k` (fewer citations) = faster but less context

### Advanced Performance Tuning

Configuration can be customized via `backend/.env` (copy from `.env.example`):

```bash
# Key settings in backend/.env
CHUNK_SIZE=1000        # Smaller = more precise retrieval, larger = more context per chunk
CHUNK_OVERLAP=200      # Higher = better context continuity, but more storage
EMBEDDING_MODEL=all-MiniLM-L6-v2        # Fastest option (change requires re-indexing)
OLLAMA_MODEL=mistral-nemo:12b-instruct-2407-q4_K_M  # Main LLM model (Q4_K_M quantization)
```

**Chunking Strategy:**
| Use Case | CHUNK_SIZE | CHUNK_OVERLAP | Notes |
|----------|------------|---------------|-------|
| Technical docs | 800 | 150 | Smaller chunks for precise code/API answers |
| Long narratives | 1200 | 250 | Larger chunks preserve story context |
| Mixed content | 1000 | 200 | Default balanced setting |

**First Query Performance:**
- LocalBook automatically keeps models warm in memory, eliminating cold start delays
- Models stay loaded for 30 minutes between queries
- First query after app start: ~5s (models pre-loaded)
- Subsequent queries: Quick summary in ~3s, detailed answer streams after

**Memory Optimization:**
- Each Ollama model uses 4-8GB VRAM
- Running both `mistral-nemo` and `phi4-mini` requires ~10GB
- On systems with <16GB RAM, consider using only `mistral-nemo` (edit `rag_engine.py` to use same model for follow-ups)

### LLM Providers
Configure in Settings:
- **Ollama** (default): Local, private, free
  - Main model: `mistral-nemo:12b-instruct-2407-q4_K_M` (detailed answers)
  - Fast model: `phi4-mini` (quick summaries + follow-ups)
- **OpenAI**: GPT-4, GPT-3.5
  - Requires API key
- **Anthropic**: Claude models
  - Requires API key

### Audio Voices
Premium macOS voices rotate automatically for variety. Download in System Settings → Accessibility → Spoken Content:

**US Voices:**
- Ava (Premium), Zoe (Premium), Samantha - Female
- Evan (Enhanced), Tom (Enhanced), Alex - Male

**UK Voices:**
- Jamie (Premium) - Female
- Daniel (Enhanced) - Male

### Supported File Types

**Documents:**
- PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx, .xls), CSV
- Plain text (.txt, .md), HTML, Code files (.py, .js, .ts, etc.)

**Media (transcribed via Whisper):**
- Audio: MP3, WAV, M4A, OGG, FLAC, AAC, WMA
- Video: MP4, MOV, AVI, MKV, WEBM

**Web:**
- Any URL (content extracted automatically)
- YouTube videos (transcript extraction)

## Data Storage

All data is stored locally:
- **Documents**: `data/uploads/`
- **Vector Database**: `data/lancedb/`
- **Audio Files**: `data/audio/`
- **Database**: `data/structured.db` (SQLite)

## Development

### Project Structure
```
LocalBook/
├── backend/              # Python FastAPI backend
│   ├── api/             # API endpoints
│   ├── services/        # Business logic
│   ├── storage/         # Database and vector storage
│   ├── models/          # Pydantic models
│   └── utils/           # Utility functions
├── src/                 # React frontend
├── src-tauri/           # Tauri desktop app
└── data/                # Local data storage
```

### API Documentation
When the backend is running, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### Building for Production

Create a distributable `.app` and `.dmg`:

```bash
# Build everything (backend binary + Tauri app)
./build.sh

# Or rebuild backend binary from scratch
./build.sh --rebuild
```

**Output files:**
- `src-tauri/target/release/bundle/macos/LocalBook.app`
- `src-tauri/target/release/bundle/dmg/LocalBook_*.dmg`

**Note:** The built app still requires users to have Ollama installed with the AI models:
```bash
brew install ollama
ollama pull mistral-nemo:12b-instruct-2407-q4_K_M
ollama pull phi4-mini
```

### Development Mode

For development with hot-reload:

```bash
./start.sh
```

This starts the backend externally and runs `npm run tauri dev`.

## Requirements

### Python Dependencies
See `backend/requirements.txt` for complete list. Key dependencies:
- fastapi, uvicorn
- sentence-transformers, torch
- lancedb, pyarrow
- ollama, openai, anthropic
- PyMuPDF, python-docx, pdfplumber
- faster-whisper, pydub
- youtube-transcript-api

### System Requirements
- **macOS**: Required for audio generation (macOS Say command)
- **Memory**: 16GB+ RAM recommended (8GB minimum, but may be slow)
- **Storage**: ~5GB for models + document storage
  - Ollama models: ~4GB each for mistral-nemo
  - Embedding model: ~100MB (downloads on first use)
  - Documents: varies by usage
- **GPU**: Apple Silicon recommended for best performance
  - Intel Macs work but are significantly slower

## Troubleshooting

### Audio Generation Issues
- Ensure you've downloaded premium voices in System Settings > Accessibility > Spoken Content
- Available voices: `say -v '?'` in terminal

### Ollama Connection Failed
```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags

# Restart Ollama
ollama serve

# Verify models are installed
ollama list
# Should show: mistral-nemo, phi4-mini
```

### Slow First Query
The first query after starting is slow because the embedding model downloads and loads:
```bash
# Pre-warm the embedding model by making a test query
# Or wait ~30-60s on first query - subsequent queries will be faster
```

### Model Not Found Error
```bash
# If you see "model not found" errors, pull the required models:
ollama pull mistral-nemo
ollama pull phi4-mini
```

### Out of Memory
- Close other applications using GPU memory
- Use a smaller model: `ollama pull mistral:7b` and update `backend/config.py`
- Reduce `top_k` in queries (fewer citations = less context to process)

### Port Already in Use
```bash
# Backend (default: 8000)
python -m uvicorn main:app --reload --port 8001

# Frontend will auto-detect backend port
```

### Pip Install Fails (torch/dependency conflicts)
If you see errors about torch versions or dependency conflicts:
```bash
# Option 1: Install torch first, then other requirements
pip install torch
pip install -r requirements.txt

# Option 2: Create a fresh virtual environment
cd backend
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Privacy & Security

- **100% Local Processing**: All document processing happens on your machine
- **Secure Storage**: API keys stored in system keyring (not in files)
- **No Telemetry**: No usage tracking or data collection
- **Optional Web Search**: Web search is opt-in per query

## License

See LICENSE file for details.

## Contributing

Contributions welcome! Please open an issue or PR.

## Acknowledgments

Built with inspiration from Google's NotebookLM, designed for privacy-conscious users who want local document AI without cloud dependencies.
