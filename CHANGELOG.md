# Changelog

All notable changes to LocalBook will be documented in this file.

## [1.0.3] - 2025-01-14

### ✨ New Features

#### Visual Studio Enhancements
- **AI Visual Generator** — Intelligent visual type selection with 3 options to choose from
- **Horizontal Steps Template** — New simple left-to-right step sequence visualization
- **Lightbox View** — Click any diagram to view full-size in a modal overlay
- **Vibrant Theming** — New color palette that works beautifully in light and dark modes
- **Smart Regeneration** — Clear UX hint to edit input and regenerate visuals

#### Performance & Reliability
- **Mermaid Prewarm** — Renderer preloads on app start for instant diagram generation
- **Metrics Persistence** — Query stats (24h count, avg latency) now persist across restarts
- **Graceful Shutdown** — Metrics auto-save when backend stops

### 🔧 Improvements

- **Mermaid Code Cleaning** — Auto-fix malformed LLM output (single-line code, markdown fences)
- **Template Diversity** — Visual generator ensures different diagram types in options
- **Export in Lightbox** — Copy/PNG/SVG buttons available in expanded view

### 🐛 Bug Fixes

- Fixed Mermaid rendering failures from LLM outputting single-line code
- Fixed query stats resetting to 0 after every rebuild
- Fixed visual panel not stripping citation markers from chat content

---

## [1.0.2] - 2025-01-12

### 🔧 Improvements
- Health portal smoke screen enhancements
- Reranker and main model health check repairs
- Console auto-load with countdown timer
- FlashRank reranker persistent cache fix

---

## [1.0.1] - 2025-01-10

### 🔧 Improvements
- Web multimodal capture implementation
- Notebook list UI fixes (star and source count)
- "Create Visual from this" button in chat

---

## [1.0.0] - 2025-01-09

### 🎉 First Stable Release

LocalBook v1.0.0 represents our first production-ready release with a complete feature set for private, offline document AI.

### ✨ New Features

#### Browser Extension: LocalBook Companion
- **Side Panel Interface** — Browse the web with AI assistance always available
- **Page Summarization** — One-click summaries with key points and concepts
- **Chat with Page Context** — Ask questions about any webpage you're viewing
- **Quick Capture** — Save pages directly to your notebooks
- **Web Search Integration** — Research topics with AI-powered search

#### Quiz & Visual Generation (Studio)
- **AI Quiz Generator** — Create quizzes from your notebook content with customizable difficulty
- **Topic Focus** — Generate quizzes or visuals focused on specific topics
- **Visual Summaries** — Create Mermaid diagrams, timelines, and concept maps

#### Voice & Audio
- **Voice Input** — Dictate questions using speech-to-text
- **Podcast Generation** — Turn documents into audio discussions (enhanced)

#### Credential Locker
- **Secure Storage** — Encrypted storage for site credentials
- **Auto-fill Support** — Credentials available for authenticated content capture

#### Site Search
- **Deep Site Search** — Search across entire websites, not just single pages
- **Crawl Management** — Control depth and scope of site indexing

### 🔧 Improvements

#### RAG Engine v2
- **Query Orchestrator** — Complex queries auto-decompose into sub-questions
- **Parent Document Retrieval** — Retrieves surrounding context for better answers
- **Hybrid Search** — Vector + BM25 keyword search combined
- **FlashRank Reranking** — Cross-encoder reranking for better retrieval
- **Corrective RAG** — Query reformulation when initial retrieval fails

#### Knowledge Graph
- **Entity Extraction** — Automatic extraction of people, organizations, metrics
- **Relationship Mapping** — Track connections between entities across documents
- **3D Constellation** — Interactive visualization of your knowledge network

#### Memory System
- **Persistent Memory** — AI remembers facts about you across sessions
- **Memory Management** — View, edit, and delete stored memories
- **Context-Aware Responses** — Personalized answers based on your history

#### Performance
- **Snowflake Arctic Embed2** — Upgraded to 1024-dim frontier embeddings
- **Phi-4 Mini** — Faster responses with Microsoft's latest small model
- **OLMo-3 7B** — Main reasoning model with 64K context window

### 📦 Document Support

Full support for:
- PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx)
- EPUB, Jupyter Notebooks (.ipynb)
- Images with OCR (requires Tesseract)
- YouTube videos (transcript extraction)
- Web pages and entire websites
- RTF, ODT (OpenDocument)

### 🔒 Privacy

- **100% Local** — All processing on your machine
- **No Cloud Required** — Works completely offline
- **No Telemetry** — Zero data collection

### 🛠️ Technical

- Built with Tauri 2.0 (Rust + React)
- Python FastAPI backend bundled via PyInstaller
- LanceDB for vector storage
- Ollama for local LLM inference

---

## [0.6.x] - Previous Releases

### [0.6.6]
- Bug fixes for document processing
- Improved error handling

### [0.6.5]
- Query Orchestrator for complex queries
- Parent Document Retrieval
- Entity Graph tracking

### [0.6.0]
- Migration Manager for seamless upgrades
- Snowflake embeddings upgrade
- Phi-4 Mini integration

### [0.5.x]
- Adaptive RAG with two-tier model routing
- Hybrid search (Vector + BM25)
- FlashRank reranking
- Improved prompt engineering

### [0.2.x - 0.4.x]
- Initial public releases
- Core RAG functionality
- Basic document support

---

## Upgrade Notes

### From v0.6.x
Automatic upgrade. Just replace the app and restart.

### From v0.5.x or earlier
Documents will be re-indexed with new embeddings on first launch. This is automatic but may take a few minutes depending on notebook size.

### From v0.1.x
Data was stored inside the app bundle. Run the migration script BEFORE replacing the app:
```bash
curl -sL https://raw.githubusercontent.com/patsteph/LocalBook/master/migrate_data.sh | bash
```
