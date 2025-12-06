# LocalBook

A privacy-first, local alternative to Google's NotebookLM. Chat with your documents using RAG, get cited answers, generate podcasts, and more — all running locally on your machine.

![LocalBook](https://img.shields.io/badge/Platform-macOS-blue) ![Python](https://img.shields.io/badge/Python-3.11+-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

### Core Capabilities
- **RAG (Retrieval Augmented Generation)**: Ask questions about your documents with accurate citations
- **Multi-Format Support**: PDF, DOCX, PPTX, XLSX, web pages, and YouTube videos
- **Web Search Integration**: Combine local document knowledge with real-time web search
- **Audio/Podcast Generation**: Convert your documents into conversational podcasts with natural-sounding voices
- **Timeline Visualization**: Automatically extract and visualize dates and events from your documents
- **Smart Document Chunking**: Intelligent chunking for better context preservation
- **Multi-LLM Support**: Use Ollama (local), OpenAI, or Anthropic models

### Document Processing
- **PDFs**: Full text extraction with page numbers and metadata
- **Office Documents**: Word (DOCX), PowerPoint (PPTX), Excel (XLSX)
- **Web Pages**: Extract and process web content with metadata
- **YouTube Videos**: Automatic transcript extraction and processing
- **Tables**: Smart table extraction and processing

### AI-Powered Features
- **Conversational Chat**: Ask questions in natural language
- **Citation Tracking**: Every answer includes sources with page numbers and snippets
- **Web Search**: Optional web search to supplement local knowledge
- **Suggested Questions**: Auto-generated starter questions for each notebook
- **Custom Skills**: Create custom prompts for specialized tasks
- **Export Options**: Export notebooks and conversations

### Audio Studio
- **Podcast Generation**: Create conversational two-host podcasts from your documents
- **Premium Voices**: High-quality macOS voices (Ava Premium, Evan Enhanced)
- **Voice Customization**: Choose gender and accent (US/UK) for each host
- **Background Processing**: Audio generation happens in the background

## Tech Stack

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

## Quick Start

### Prerequisites
- **macOS** (required for audio generation features)
- **Python 3.11+** (3.13 recommended)
- **Node.js 18+**
- **Rust** (for Tauri desktop app)
- **Ollama** (for local LLM inference)
- **ffmpeg** (required for audio/video transcription)

### Step 1: Install System Dependencies

```bash
# Install Homebrew if not already installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Ollama for local LLM
brew install ollama

# Install Rust (required for Tauri)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# Install Node.js (if not installed)
brew install node

# Install ffmpeg (required for audio/video transcription)
brew install ffmpeg
```

### Step 2: Set Up Ollama

```bash
# Start Ollama service (keep this running in a terminal)
ollama serve

# In a new terminal, pull the required models
ollama pull mistral-nemo  # Main LLM for answers
ollama pull phi4-mini     # Fast model for follow-up questions
```

### Step 3: Clone and Set Up Backend

```bash
# Clone the repository
git clone https://github.com/yourusername/LocalBook.git
cd LocalBook

# Create and activate Python virtual environment
cd backend
python3 -m venv .venv
source .venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# (Optional) Copy and customize environment config
cp .env.example .env

# Start the backend server (production)
python main.py

# Or for development with auto-reload:
# python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Step 4: Set Up Frontend

```bash
# In a new terminal, from the LocalBook root directory
cd LocalBook

# Install Node.js dependencies
npm install

# Run the desktop app in development mode
npm run tauri dev
```

### Step 5: (Optional) Install Premium Voices for Audio

For the best audio podcast quality, install premium macOS voices:

1. Open **System Settings** → **Accessibility** → **Spoken Content**
2. Click **System Voice** → **Manage Voices**
3. Download these recommended voices:
   - **Ava (Premium)** - Female US voice
   - **Evan (Enhanced)** - Male US voice
   - **Zoe (Premium)** - Female US voice (variety)
   - **Tom (Enhanced)** - Male US voice (variety)

You can verify installed voices with:
```bash
say -v '?'
```

### Step 6: (Optional) Set Up Web Search

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

## Configuration

### LLM Model Selection

**Dual-Model Architecture:**
- **mistral-nemo** (main): Generates detailed, well-cited answers
- **phi4-mini** (fast): Generates follow-up questions in parallel

This dual-model approach speeds up the overall response by running follow-up question generation concurrently with the main answer.

**Main Model: mistral-nemo**
- **Why**: Best balance of speed, quality, and local privacy
- **Performance**: ~14-18s per query with detailed, well-cited answers
- **GPU Usage**: 100% utilization on Apple Silicon

**Model Choice Reasoning:**
- ✅ **mistral-nemo**: Optimal quality/speed balance (recommended for main answers)
- ✅ **phi4-mini**: Fast and lightweight (used for follow-up questions)
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
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5  # Change requires re-indexing documents
OLLAMA_MODEL=mistral-nemo               # Main LLM model
```

**Chunking Strategy:**
| Use Case | CHUNK_SIZE | CHUNK_OVERLAP | Notes |
|----------|------------|---------------|-------|
| Technical docs | 800 | 150 | Smaller chunks for precise code/API answers |
| Long narratives | 1200 | 250 | Larger chunks preserve story context |
| Mixed content | 1000 | 200 | Default balanced setting |

**First Query Performance:**
- The first query after starting the backend takes longer (~30-60s) because:
  1. Embedding model downloads on first use (~100MB)
  2. Model loads into memory
- Subsequent queries are much faster (~14-18s)

**Memory Optimization:**
- Each Ollama model uses 4-8GB VRAM
- Running both `mistral-nemo` and `phi4-mini` requires ~10GB
- On systems with <16GB RAM, consider using only `mistral-nemo` (edit `rag_engine.py` to use same model for follow-ups)

### LLM Providers
Configure in Settings:
- **Ollama** (default): Local, private, free
  - Main model: `mistral-nemo`
  - Follow-up model: `phi4-mini`
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
```bash
# Build desktop app
npm run tauri:build

# Output in src-tauri/target/release/
```

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
