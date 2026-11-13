# 01. Absorption map: every Mac feature, its Swift approach, its phase, its status

*Copied from the approved plan (`/mnt/project-files/research/2026-11-13-native-swift-plan.md`, revision 2, 2026-11-13) with a Status column. This is the working copy: a row changes here first, and the gate log (`02-gate-log.md`) records when a phase gate closes. Status values: **not started**, **in progress**, **done** (side-by-side check passed and the Python counterpart may be switched off), **hatched** (shipping behind one of the two pre-approved escape hatches; the replacement date is in `00-decision.md`).*

Rule: nothing leaves this table. If a feature turns up that is not here, it is added with an approach and a phase before any phase gate closes.

Legend for "How": **Direct** = same design, Swift frameworks; **Port** = rewrite of Python logic with golden tests; **Replace** = different mechanism, same behaviour for the user, with the equivalence stated; **Hatch** = in-process Python or Rust static library, time-boxed.

### 4a. Ask, library and retrieval

| Feature today | How | Swift approach | Phase | Status |
|---|---|---|---|---|
| Ask with citations, streaming, answer length, voice modifier | Direct | `AnswerRecipe` + Gemma via MLX Swift; `MLXGuidedGeneration` for structured calls | 2 | not started |
| Hybrid search (BM25 + vectors) + FlashRank reranker | Direct | Phone's exact BM25, Accelerate flat scan (0.29 ms at the largest notebook), Core ML MiniLM | 2 | not started |
| @curator cross-notebook search | Replace | Flat scan across all notebooks (1 ms for the whole library today); USearch index added only past ~1M chunks | 2 | not started |
| Notebooks, folders, desktop ordering | Direct | SQLite, SwiftUI sidebar (AppKit outline view) | 2 | not started |
| Sources list, source viewer, articles per source, export | Direct | SwiftUI + Quick Look previews, PDFKit viewer, export via `NSSavePanel` and Share | 2 to 3 | not started |
| Rich note editor, canvas notes | Direct | TextKit 2 (`NSTextView`) with Markdown round-trip | 3 | not started |
| Chunking, synthetic questions, parent text, re-index, repair zero vectors | Port | Same algorithm as `phone_recipe.py`; maintenance commands in Settings > Library | 3 | not started |
| Embedding selector, LLM Studio, model catalog, HF downloads, hardware profiler | Direct | `swift-huggingface` (phone uses it), MLX model registry, `ProcessInfo`/IOKit for hardware | 2 | not started |
| Memory system (persistent preferences, consolidation, memory bridge for companions) | Port | SQLite store + prompt ports; bridge endpoints on the local HTTP server | 4 | not started |
| RAG health, rag profiles, evaluator capability probes, feature parity runner | Port | Fixtures become Swift Testing + the parity harness; a Settings > Diagnostics screen shows the same numbers | 4 | not started |

### 4b. Ingestion and sources

| Feature today | How | Swift approach | Phase | Status |
|---|---|---|---|---|
| PDF (PyMuPDF + pymupdf4llm) | Replace | PDFKit text + Vision `RecognizeDocumentsRequest` (paragraphs, lists, ruled tables, OCR) + font-position heading pass. Equivalence: chunk-text diff on a 50-document sample; **Hatch** pymupdf4llm for any PDF class that regresses | 3 | not started |
| DOCX, DOC, RTF, ODT, HTML, webarchive | Direct | AppKit document importer (tables included; verified) | 3 | not started |
| XLSX / XLS / CSV / tabular store queries | Replace | CoreXLSX; libxls (C, BSD) compiled in for .xls; CSV native; tabular queries on SQLite (DuckDB Swift if analytics outgrow it) | 3 | not started |
| PPTX / PPT | Replace | Own OOXML slide reader; .ppt via Spotlight's Office importer with a 300-line TextCharsAtom/TextBytesAtom reader as fallback. Better than today's olefile scrape | 3 | not started |
| EPUB, Jupyter, images, HEIC, OCR (Vision + tesseract + OpenCV) | Direct | zip+XML, JSON, ImageIO, Vision, Core Image preprocessing | 3 | not started |
| Web pages (trafilatura, Playwright for JS), site search, source discovery, web fallback | Replace | WKWebView render + Readability (fixture-passing Swift port or Mozilla JS in JavaScriptCore) + SwiftSoup rules; site search and discovery are HTTP + extraction. Equivalence: text diff on 30 library URLs | 3 | not started |
| YouTube transcripts | Direct | swift-youtube-transcript or own Innertube client; paste fallback | 3 | not started |
| Folder watcher | Direct | `FSEvents` / `DispatchSource` | 3 | not started |
| Scan pipeline (capture sessions, image upload, WebSocket push from phone) | Direct | Phone already captures; Mac receives over the sync link; Vision OCR | 3 | not started |
| Voice notes (Whisper) | Direct | SpeechAnalyzer + FluidAudio Parakeet | 2 | not started |
| Audio and video transcription (mlx-whisper, parakeet-mlx, PyAV decode) | Direct | AVFoundation decode + SpeechAnalyzer; WhisperKit via the Argmax SDK for the Whisper-specific path if a language needs it | 3 | not started |
| Multimodal extractor (images in documents through the vision model) | Direct | MLXVLM (Gemma 4 vision) | 3 | not started |

### 4c. Studio and media

| Feature today | How | Swift approach | Phase | Status |
|---|---|---|---|---|
| Briefing, quiz, flashcards, audio overview, video explainer | Direct | `StudioEngine`, FluidAudio Kokoro, AVAssetWriter (phone has all of it) | 2 | not started |
| Podcast generator (multi-voice, styles) | Direct | Audio overview's four styles and two accents already cover it; add the Mac's voice roster | 2 | not started |
| Visuals: v1 smart stream, v2 compose with critic, infographic, SVG templates, Mermaid | Replace | `VisualSpec` drawn natively (five layouts) extended with the v2 layouts users actually get (infographic stickers become SF Symbols and Swift Charts); Mermaid in sources rendered by swift-mermaid or BeautifulMermaidSwift; export PNG/PDF/SVG via `ImageRenderer`. Equivalence: the same 15-prompt golden set the audit proposed, judged side by side by Patrick | 3 | not started |
| Image generation (mflux FLUX.2 Klein) | Replace, with Hatch | flux-2-swift-mlx / mlx-swift-image behind the same UI; mflux in-process until the Swift port matches on the golden prompts | 5 | not started |
| Canvas (xyflow) with action popover, generate-visual-to-canvas | Port | Own SwiftUI canvas (nodes, edges, pan, zoom, drag) with the same JSON document format so existing canvases open | 5 | not started |
| Knowledge Constellation (three.js, WebSocket) | Port | SceneKit/RealityKit scene fed by the same graph and topic data | 5 | not started |
| Read-aloud, OpenAI-compatible `/v1/audio/speech` and `/transcriptions` | Direct | Kokoro and SpeechAnalyzer behind the local HTTP server | 4 | not started |

### 4d. Agents and intelligence

| Feature today | How | Swift approach | Phase | Status |
|---|---|---|---|---|
| Curator (morning brief, weekly wrap, cross-notebook intelligence, event bus, judge, synthesize, patterns) | Port | Prompt and state ports as dispatch tables; golden outputs with a stub model before switching; scheduling via `BGTaskScheduler`-style timers in the app and a launch agent for "while the app is closed" | 5 | not started |
| Collector (RSS, sites, auto-tagging, morning brief feed) | Port | FeedKit/XMLParser + the web extractor above | 5 | not started |
| Correspondent (IMAP ingestion, SMTP confirmations, weekly auto-journal) | Replace | **Two paths, user picks in Settings:** (1) Mail.app's local store (Envelope Index + .emlx) after a one-time Full Disk Access grant, sending through Mail via Scripting Bridge; (2) SwiftMail (IMAP + SMTP on Apple's swift-nio-imap) for accounts not in Mail.app. Same rules, same confirmation replies, same journal. **Hatch** imap-tools if SwiftMail misses a server quirk | 5 | not started |
| People profiler (coaching notebooks, social collector via Playwright, coaching insights) | Replace | WKWebView with a persistent `WKWebsiteDataStore` for logged-in collection (replaces Playwright); insights prompts ported; the Playwright utilities become WKWebView helpers | 5 | not started |
| Topic modeling (BERTopic) | Replace | kNN graph + Leiden with c-TF-IDF keywords, centroid assignment and LLM names (same outputs the graph and coaching consume). **Exact-parity alternative** if clusters must match: the pure-Rust `hdbscan` crate behind a C ABI | 5 | not started |
| Knowledge graph, entity extraction, timeline, contradictions, synthesis (perspectives), comparison, RLM notebook-scale analysis, exploration (learning journey), findings, writing assistant, skills | Port | Prompt-driven features: port prompts and SQLite schemas one by one, each with a golden test from today's output; `MLXGuidedGeneration` enforces the JSON shapes that llguidance enforces today | 5 | not started |
| Chat agents `_stream_correspondent/_collector/_curator` (intent chains) | Port | One `IntentRouter` with a handler per intent; the audit's characterization tests become Swift Testing cases | 5 | not started |
| Jobs queue, incidents (GitHub incident filing), quality signals ("rough edges") | Port | Swift actors + SQLite; incident filing via the GitHub REST API | 4 | not started |

### 4e. Platform, security, integration

| Feature today | How | Swift approach | Phase | Status |
|---|---|---|---|---|
| Sync (HLC, journal, merge, scope, retention, blobs, discovery, phone library/notes/personal/studio) | Port | Module-by-module with the 16 test files ported first; the Python pytest suite runs as a black-box conformance suite against the Swift host | 4 | not started |
| Mac-to-Mac mTLS (Ed25519) and phone link (P-256) | Direct | swift-nio-ssl listener with `.ed25519` in `verifySignatureAlgorithms` (verified); Network.framework for the phone port | 4 | not started |
| Encrypted volume (create, migrate, compact, open at login), staged restore, recovery phrase (BIP-39), K-1 keyvault (X25519/HKDF/AES-GCM), credential locker, Touch ID | Direct | `hdiutil` via `Process`; CryptoKit + Secure Enclave; LocalAuthentication; a one-time key migration from today's keyvault | 4 | not started |
| Backup and restore | Port | Same archive format so old backups restore | 4 | not started |
| MCP server, agent keys, `.mcpb` bridge, companions (install, connect, wire up) | Port | Official Swift MCP SDK (stdio + Streamable HTTP); bridge unchanged | 4 | not started |
| OpenAI-compatible `/v1` (chat, embeddings, audio) | Port | Local HTTP server (Hummingbird or NIO); same routes and JSON | 4 | not started |
| Browser extension endpoints (capture, transform, agent browser automation) | Port | Same HTTP contract so the shipped extension keeps working unchanged; agent-browser automation moves from Playwright to WKWebView | 4 | not started |
| Health portal, system info, engine truth, updates | Port | Settings > Diagnostics with the same numbers; Sparkle or App Store for updates | 4 | not started |
| Settings (all tabs), themes, encryption prompts, volume recovery | Direct | SwiftUI Settings scenes; system appearance plus the existing accent themes | 2 to 4 | not started |
| Tauri shell duties (backend watchdog, port cleanup, crash log, token file) | Replace | None needed: there is no second process. Crash reporting via `MXMetricManager` and the app's log | 2 | not started |
| Scripts (phone readiness, re-embed, backfills, benchmarks) | Port | Swift command-line targets in the package, run from Terminal or Settings > Library | 3 to 5 | not started |

Nothing in the Python app is outside this table. If a feature turns up that is not here, it is added to the table with a phase before any phase gate closes.

