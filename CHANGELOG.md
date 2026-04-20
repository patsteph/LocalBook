# Changelog

All notable changes to LocalBook will be documented in this file.

## v1.8.0 — Sidecar Lifecycle + One-Click Bonsai Swap (Phase 2)

### Sidecar process management
- **`services/sidecar_manager.py`** — New `SidecarManager` singleton that spawns `llama-server` as a child process, polls `/health` until ready (45 s default timeout), and terminates cleanly on shutdown (SIGTERM → SIGKILL fallback). Layered config: env vars → `user_preferences.json → sidecar` → built-in defaults (binary at `~/src/llama.cpp/build/bin/llama-server` or PATH, model at `~/.localbook/models/bonsai/Bonsai-8B-Q1_0.gguf`, port 8090).
- **Binary + model auto-discovery** — Checks source-built llama.cpp location first (needed for Q1_0 since Homebrew's formula lags), falls back to `/opt/homebrew/bin/llama-server`, then `PATH`.
- **Foreign-process detection** — If the configured port already answers `/health`, the manager adopts the existing sidecar rather than refusing to start. Status API reports `owned: false` so the UI can disable the Stop button for sidecars launched outside the backend.
- **FastAPI lifespan integration** — Auto-starts the sidecar in a background task when the active `main_model` or `fast_model` is a `llama_server`-provider model (or `sidecar.auto_start=true` in prefs); stops it during graceful shutdown. Never blocks backend boot — spawn failures are logged, not fatal.

### One-click swap from Locker / LLMSelector
- **`LLMLocker.analyze_swap()`** — Dropped the Phase 1 `LOCALBOOK_ALLOW_SIDECAR_SWAP` env gate. Sidecar models are now first-class swap targets. Retained a fast `/health` pre-check so a ghost request can't silently swap to a dead backend.
- **`POST /evaluator/swap`** — When the target is a sidecar model, auto-invokes `sidecar_manager.ensure_started()` with a 45 s timeout, invalidates the provider health cache, and only then executes the swap. User flow becomes: click **Use** → sidecar warms up → model becomes active → evaluator picks it up from `config.settings`.
- **`LLMSelector`** (frontend) — Removed the Phase 1 "Labs"/disabled state. Sidecar models are selectable; ⚗ Sidecar badge remains so users know what they're picking. Button tooltip explains the 10–20 s warmup on first use.

### Lifecycle control surface
- **`GET /evaluator/sidecar/status`** — Reports `running`, `owned`, `healthy`, `pid`, `uptime_seconds`, `binary_path`, `model_path`, `model_exists`, `port`, `last_error`.
- **`POST /evaluator/sidecar/start`** — Ensures the sidecar is up (blocks up to 45 s). Returns 503 with structured error detail if Metal init / model load fails.
- **`POST /evaluator/sidecar/stop`** — Graceful SIGTERM, 5 s grace, then SIGKILL. Idempotent. Skipped if the process is foreign (not owned by us).

### Health Portal Locker UI
- **Sidecar status card** — New compact card above the Locker grid. Status dot (green / yellow / grey), model filename, uptime, `owned` vs `external process` label. Start/Stop buttons wired to the lifecycle endpoints. Refreshes the locker model list after any state change so Bonsai becomes selectable / de-selectable in sync.
- **Stop button gating** — Disabled with tooltip for foreign sidecars (launched via `scripts/start_bonsai_sidecar.sh` instead of the backend) so the UI never claims power it doesn't have.

### Tests
- `python3 -m services.llm_provider` now also validates `SidecarManager.resolve_config()` and `.status()` without spawning a subprocess, keeping the smoke suite hermetic.

### What Phase 2 delivers end-to-end
The user story `Bonsai benchmark in five clicks` now works:

1. Open Health Portal → **Locker** tab → Bonsai appears in **Main Reasoning Models** (if sidecar healthy) or greyed (if stopped).
2. Click **Start** on the Sidecar status card → llama-server spins up in ~10–20 s.
3. Click **Set as Main** on Bonsai → backend confirms sidecar health, swaps `settings.ollama_model`.
4. Switch to **Evaluator** tab → click **Run** → benchmark runs against Bonsai via the translator built in Phase 1.
5. (Optional) **Save Current as Default** → next boot auto-spawns the sidecar before the first request.

### Deliberately **not** in Phase 2
- Memory/perf metrics in the sidecar status card (planned for Phase 3 dedicated tab).
- Model picker in the sidecar card (Phase 1 registers exactly one sidecar model — Bonsai; picker only matters once there are multiple).
- Evaluator per-run model override (today you must swap first; a future "run with" dropdown would let you benchmark without touching the active config).

---

## v1.7.0 — Multi-Provider LLM Infrastructure (Phase 1)

### Foundation for non-Ollama backends
- **`services/llm_provider.py`** — New routing layer with a `Provider` enum (`ollama`, `llama_server`), a `ProviderRoute` dataclass, async/sync health checks with a 10-second TTL cache, and an Ollama↔OpenAI payload translator covering generate + chat, streaming + non-streaming, token usage, and stop sequences. Unknown models fall back to the Ollama route byte-for-byte, so existing behavior is preserved.
- **`ModelInfo.provider`** — New registry field on entries in `known_models.json` (default `"ollama"`). Entries can now be tagged `"provider": "llama_server"` to route them through a locally running sidecar that speaks the OpenAI chat API.
- **`model_registry.refresh_installed_status()`** — Ollama models still checked via `/api/tags`; sidecar models are now marked installed iff the llama-server `/health` endpoint returns 200.

### Call sites threaded through the resolver
- **`services/ollama_service.py`** — `generate`, `chat`, and `stream_generate` now resolve the provider first; Ollama-backed models keep the existing `/api/generate` / `/api/chat` paths, sidecar-backed models translate to `/v1/chat/completions` with streaming SSE parsed back into Ollama-shape dicts so existing callers read `response["message"]["content"]` / `response["response"]` unchanged.
- **`services/rag_llm.stream_ollama()`** — Same routing; token-economy metrics and stop sequences work on either path.
- **`services/model_warmup.py`** — Skips Ollama keep-alive pings for models served by a sidecar (llama-server is always resident).

### Bonsai-8B registry entry
- Added `bonsai-8b` to `known_models.json` (8B params, 1-bit Q1_0 GGUF, 1.16 GB disk, 4 GB RAM min, Apache-2.0, US-origin, `"provider": "llama_server"`). Tagged `experimental` and `sidecar` so it's clearly distinguishable in the UI.

### Evaluator + settings APIs
- **`GET /evaluator/providers`** — New endpoint reporting per-provider health (`ollama`, `llama_server`) with base URL and live status.
- **`GET /settings/ollama/models`** — Now appends registered sidecar models to the returned list when the sidecar `/health` probe succeeds; each row carries a `provider` field for the UI. Uncached response shape is backward-compatible.

### Safety — Phase 1 keeps sidecar models inert in the user UI
- **`LLMLocker.analyze_swap()`** — Rejects any swap to a `llama_server`-provider model unless `LOCALBOOK_ALLOW_SIDECAR_SWAP=1` is set in the environment. When allowed, a live sidecar `/health` check is required before the swap proceeds. Phase 2 (Labs toggle) will flip this gate under UI control.
- **`LLMSelector`** (frontend) — Sidecar models now render with a ⚗ **Sidecar** badge; the Use button shows as disabled "Labs" with a Phase 2 tooltip. Ollama models are unchanged.

### Developer tooling
- **`backend/scripts/start_bonsai_sidecar.sh`** — Convenience launcher. Prefers a source-built `llama-server` at `~/src/llama.cpp/build/bin/llama-server` (needed for Q1_0 since Homebrew's formula lags), falls back to PATH. Supports `--bg` for background mode with logs under `/tmp/bonsai-server.{log,err}`. Reads `BONSAI_MODEL_PATH`, `BONSAI_PORT`, `BONSAI_CTX_SIZE`, `BONSAI_NGL` for overrides.
- **Smoke tests** — `python3 -m services.llm_provider` runs in-memory assertions for the resolver fallback, provider enum parsing, and all four translator functions (generate/chat × stream/non-stream). No pytest dependency introduced.

### Architectural intent
Phase 1 delivers infrastructure only. No user-visible behavior changes on the default Ollama path. Bonsai-8B is wired end-to-end so the Evaluator can benchmark it, but the Locker UI keeps it gated pending Phase 2's Labs toggle + automated sidecar lifecycle.

---

## v1.6.2

### Upload Experience
- **Granular Ingestion Progress** — The file upload progress bar now streams stage-by-stage updates instead of jumping from 0% to 100%; users see the full RAG journey as it happens (receive → detect format → extract text → analyze → chunk → summarize → HyDE questions → embed → index → tag)
- **"Show journey" Expander** — Optional checklist view reveals every stage with a plain-English description of what's happening and why (e.g. "HyDE questions — generating synthetic questions each chunk answers to boost recall at query time")
- **Per-File Progress** — Each file in a multi-file upload gets its own progress bar, stage label, and completion state; overall bar averages across all files
- **New SSE Endpoint** `POST /sources/upload/stream` — Backward-compatible addition; the existing `POST /sources/upload` is unchanged so agent tools, browser extension captures, and direct API callers are unaffected
- **Reusable ProgressReporter** — New `backend/services/progress_reporter.py` threads optional progress events through `document_processor.process()` and `rag_engine.ingest_document()` with a zero-cost no-op fallback for existing callers

---

## v1.6.1

### Chat Agents
- **Multi-Intent Messages** — `@collector`, `@curator`, `@research`, and `@studio` can now handle compound requests in a single message (e.g. "add this URL and set my focus to X"); the classifier decomposes the message and each action runs in sequence
- **Smarter Compound Routing** — Messages like "scrape this video, add the channel, collect daily" now correctly subscribe to the channel, schedule daily collection, and ingest the video in one turn
- **Schedule Keyword Fallback** — "daily", "hourly", and "weekly" in a message are honored even when the LLM classifier doesn't extract them into params

### Sources
- **Consistent YouTube / arXiv Labels** — YouTube videos and arXiv papers now display as `▶️ YOUTUBE` / `ARXIV` regardless of how they were added (chat, browser extension capture, feed-page article, or agent tool); previously some paths mislabeled them as generic `WEB`
- **Full Ingest Pipeline for Chat Adds** — Sources added via `@collector` now run the same pipeline as direct captures, including auto-tagging, content-date extraction, and `document_captured` event logging

---

## v1.6.0

- **YouTube Sources** — YouTube videos now ingest with a full-transcript summary for better retrieval and display as `▶️ YOUTUBE` throughout the app
- **LLM Locker Improvements** — Smarter RAM estimation eliminates false memory rejections; per-model tuning profiles added to the registry
- **Labs Toggle** — Experimental features (LLM Evaluator, Locker) now live behind a toggle in the Health Portal
- **Release Pipeline** — Version badge, download links, and CHANGELOG are all auto-updated on each release
- **Quiz Enhancements** — Studio quizzes now use RAG chunk retrieval for higher-quality questions drawn from the full document corpus; five question types supported (Multiple Choice, True/False, Fill in the Blank, Short Answer, Spot the Error) selectable before generation; choice questions reveal instantly on click with A/B/C/D prefixes matching the Feynman curriculum experience; open-ended answers graded by LLM with partial credit and feedback

---

## v1.5.2

### Audio Processor
- **Main Model Narration** — Video narration now uses the main model instead of phi4-mini for richer spoken language
- **Sentence-Count Guidance** — Replaced explicit word count targets with sentence-count guidance

### Adaptive Collection
- **Stagnation Detection** — Detects when a notebook's collection hasn't found new content in 5+ days (mild → moderate → plateau tiers)
- **Auto-Expand Search** — Automatically widens search queries, lowers confidence floor, and seeds from cross-notebook shared entities
- **Collection Tombstone** — Prominent banner surfaces pending approval items and expansion mode status
- **Morning Brief Integration** — Stagnation status appears in morning briefs and Curator chat
- **Plateau Frequency Reduction** — After 15+ days of stagnation, collection frequency is automatically halved
- **Rejection Reason Tracking** — Collection history now records why items were rejected
- **Auto-Expand Toggle** — Per-notebook toggle to enable/disable adaptive expansion

---

## v1.5

### Video Explainers
- **Video Generation** — Generate narrated explainer videos from notebooks with auto-storyboarding
- **Visual Styles** — Multiple slide styles: classic, dark, whiteboard, and more
- **TTS Narration** — Natural voice narration via Kokoro-82M TTS (50+ voices, 9 languages)

### Feynman Learning Suite
- **Feynman Curriculum** — 4-part progressive learning: Foundation → Building → First Principles → Mastery
- **Teaching Podcasts** — Dedicated teacher/learner audio format (up to 45 min)
- **Learning Visuals** — Progression flowcharts, knowledge maps, and misconception diagrams
- **Self-Tests** — Integrated quiz generation at multiple difficulty levels

### Studio & Content Generation
- **Outline-First Documents** — Multi-step pipeline for deep dives, debates, and curricula
- **Completion Verification** — Post-generation gate ensures all required sections are present
- **Chain-of-Density Audio** — Running summaries between podcast sections prevent topic repetition

### Weekly Wrap-Up & Curator
- **Weekly Wrap-Up** — Monday morning summary of all research activity across notebooks
- **Feed Page Detection** — Collector auto-detects index/listing pages and extracts article links
- **RSS & Feed Pages** — Recurring collection from RSS feeds and content index pages

### Chat & Rendering
- **Markdown Chat** — Chat messages now render full Markdown with inline citations
- **Consistent Formatting** — Unified Markdown rendering across chat, canvas, curator, and all panels
- **Adaptive Response Format** — Auto-detects list, table, step-by-step, and code queries

---

## v1.3

- Flexible drawer panels — Sources and Collector fill available space
- Citation popup portals — tooltips never clipped by sidebar overflow
- Compact chat input, reliable Studio drawer expand/collapse
- Collector: expanded frequencies (2h, 8h, twice daily, every 3 days), full Curator pipeline, frequency picker wizard

---

## v1.20

- People Profiler with coaching notes, goals, and social platform integration
- Curator Agent for cross-notebook intelligence, morning briefs, config inference
- Knowledge Constellation v2: dynamic zoom, concentric rings, tag-based edges, smart labels
- Parallel sub-queries, response format detection, content date extraction
- Memory v2: deep consolidation, user signals, search miss tracking, daily summaries

---

## v1.10

- AI Visual Generator with intelligent type selection and lightbox view
- Horizontal Steps template, vibrant theming for light/dark modes
- Mermaid prewarm for instant rendering, metrics persistence across restarts
- Auto-fix for malformed LLM diagram output

---

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
