# Changelog

All notable changes to LocalBook will be documented in this file.

## Unreleased — v2.0.0 work-in-progress (Information Cortex)

Tag gated on Phase 14 (article depth) per 2026-06-10 plan. Everything below
is code-complete and user-verified unless explicitly noted as "awaiting test".

### Highlights
- **Universal Canvas** — single artifact spec + renderer registry routes markdown / strict HTML / interactive HTML (iframe sandbox) / SVG / Mermaid / Klein / `json:<kind>` through one dispatch. Mixed-medium documents interleave prose + Recharts + SVG via gemma4 `VISUAL_INTERLEAVE` injection + post-processor.
- **Correspondent (email cortex)** — IMAP poller (Gmail / Fastmail / iCloud+ / Outlook via app password), tool-less LLM classification, cross-notebook auto-routing via embedding similarity, sister-newsletter auto-subscribe through Collector queue, reply-to-ingest, outbound SMTP via `aiosmtplib`, newsletter HTML rendered in source viewer.
- **Synthesis layer** — Curator HTML morning brief with consensus detection + deep-read auto-trigger, interactive HTML artifacts (iframe `sandbox="allow-scripts"` + postMessage), cross-source perspectives view (consensus vs contested), per-notebook dashboards, entity-anchored topic deep-dives, source-graph entity proposals, weekly auto-journal via SMTP.
- **Correspondent Tier 2 (all 10 capabilities)** — per-article extraction + summary + RAG indexing, hot/cold clusters via embedding agglomeration, deep-read with newsletter context, cross-notebook entity tagging at ingest, per-newsletter scorecard, RFC 2369 List-Unsubscribe one-click POST with two-step confirmation, frequency tuner, smart digest grouping, effectiveness dashboard, routing histogram with auto/manual/queued series.
- **Gemma4 flip** — `ollama_model` default switched from `olmo-3:7b-instruct` to `gemma4:e4b`. Native vision absorbs the vision slot on 16 GB Macs.

### Phase 14 — Article Depth (2026-06-10)

The final pre-tag block. Until Phase 14, each newsletter was treated as
one source — a 12-article TLDR contributed a single signal to the
cortex. Phase 14 turns each content article into a first-class citizen
across entities, sections, brain events, and the consensus detector.

- **P14.A — Article skip classifier** (`backend/services/article_classifier.py`) — single phi4-mini JSON call per article returns `{kind ∈ content/sponsor/ad/jobs/navigation, reason, confidence}`. Three new columns on `articles`: `kind`, `kind_reason`, `kind_confidence`. Non-content articles stay in the DB (audit trail) but are skipped by the summary / embed / RAG-index / entity / sectioning / event-emission passes. Idempotent — `_summarize_articles_background` skips already-classified articles via the `confidence > 0` check. `list_recent` / `list_by_sender` / `list_with_embeddings` default to content-only with an `include_non_content` opt-in. Article-rag indexer adds a defense-in-depth kind gate. **Fixture-validated**: 5/5 of TLDR-style sample articles classified correctly at 0.95–1.00 confidence.
- **P14.B — Per-article entity extraction → notebook KG** (`backend/services/correspondent_processor.py:_summarize_articles_background`) — each content article runs `entity_extractor.extract_from_text` independently using the article's synthetic `art-{uuid}` source_id, with title + summary + body[:2500] as input (LLM-clean summary informs the input, not just noisy body). The newsletter-level entity extraction in `ingest_newsletter` + `ingest_forward` skips when ≥2 articles were extracted (per-article passes own it; avoids double-counting). Source cascade-delete extended in `source_store.py` so per-article entities are cleaned up when the parent newsletter is removed. **Fixture-validated**: 10 entities landed on a 3-article fixture, all correctly tied to article `art-` IDs (`person: Eric Glyman`, `company: Ramp/DoorDash/Shopify/Stripe`, …).
- **P14.C — Per-article curator brain events** (`backend/services/correspondent_processor.py`, `services/consensus_detector.py`, `services/weekly_journal.py`) — each content article emits an `article_ingested` event via `event_bus.emit_now` with the same payload shape as `source_ingested` (so `consensus_detector._coerce_event` works unchanged). Parent `source_ingested` for the wrapping newsletter omits the summary when ≥2 articles were extracted — the brain's dispatch handlers (mark_notebook_dirty / mental-model trigger / stance scoring / anticipatory drafts) still fire once per newsletter while consensus + journal skip the parent and only count per-article events. `consensus_detector.detect_consensus` now consumes both action types; `weekly_journal.compose_journal_html` reads them separately so the headline can frame "N articles from your newsletters plus M other sources" instead of the old "N new sources" understatement. Also fixed in-memory dict mirror bug — `a["summary"]` / `a["topic_tags"]` now propagate after the summary pass so downstream passes see new values. **Fixture-validated**: 2/3 events emitted on a 3-article fixture (sponsor correctly skipped), summaries populated, synthetic source_ids correct.
- **P14.D — Per-article notebook section assignment** (`backend/services/article_sectioner.py`, `backend/storage/article_section_store.py`) — new `article_sections` table (notebook-scoped, distinct from the global left-nav `notebook_sections`). Three new `articles` columns: `section_id`, `section_proposal`, `section_confidence`. Single phi4-mini JSON call per content article: picks an existing section by id OR proposes a new section name (short noun phrase). Three paths: existing match + confidence ≥ 0.40 → assign + increment count; new proposal + confidence ≥ 0.85 → auto-create (idempotent on name → prevents sprawl from LLM hiccups) + assign; otherwise store proposal text for later review. Validates `match_existing_id` against the real ID list (LLM hallucination guard). The `article_ingested` event payload carries `section_id` so consensus + journal can group by section. **Fixture-validated**: 2 sections auto-created on a 4-article fixture ("AI Accounting", "Claude Innovations"), both at 0.90 confidence — clean noun-phrase names, no sentence fragments.

### Polish + bug fixes (Q-batch, 2026-06-10)
- **Q1 — Article titles** — Newsletter article cards were saving paragraph fragments / chrome / URLs / HTML soup as titles for ~90% of articles. Five iterations landed at the right architecture: the LLM-written summary's first sentence IS the title. Backend refresh handler + ingest pass + frontend renderer all prefer the summary unconditionally when summary is non-HTML non-chrome prose. Strict body-extraction gate is now the fallback. User-verified: 49/80 cards cleaned in the refresh run.
- **Q2 — `refresh_titles` chat intent** — `@correspondent refresh titles` walks every article through the new logic. Handles `refresh titles / fix article titles / rebuild titles`.
- **Q3 — Entity denylist expansion** — locations + newsletter chrome ("Sign Up", "View Online", country names) filtered at read-time so existing noisy rows clear without a purge.
- **Q4 — Cluster fence rendering** — `whats_hot deep` + `cluster_deep_read` replies prefix the `json-correspondent-hot-clusters` fence with `\n\n`.
- **Q5 — Sender-diversity gate** — Deep-read CTA threshold relaxed from `≥3` to `≥2` unique senders.
- **Q6 — Manual approves logged to routing telemetry** — `approve_queued` records `decision_verb='manual_route'` + `bias_applied='user-override'`. Histogram gained a third "manual approve" series.
- **Q7 — Dashboard empty states** — Per-tile fallback copy ("populates after next poll", "learns from your manual approves") + 5-bullet "why some tiles show —" explainer with `@correspondent sync now` trigger.
- **Article click 404 fix** — `ChatInterface.tsx` listener was throwing away the `notebookId` from `lb:openSource` events; cross-notebook cluster clicks now open the article's actual notebook.

### Process / docs
- **Session housekeeping rules** added to `CLAUDE.md` — STATUS.md / capability docs / CHANGELOG / READFIRST memory are first-class deliverables, updated as milestones land, not at release-tag time.

### Known gaps — closed by Phase 14 above
*(Phase 14 was originally listed here as gating the tag; all four items shipped and are now logged under Phase 14.)*

### Pre-tag punch list (small, not blocking)
- `pip-compile` regen of `requirements.txt` for new deps (`imap-tools`, `mail-parser`, `aiosmtplib`).
- Weekly journal toggle checkbox in `CorrespondentSettings.tsx`.
- Version bumps in `package.json` / `tauri.conf.json` / `Cargo.toml` / `README.md`.

### Deferred to post-v2.0
- **F. Always-on synthesizer sidebar** — needs a new real-time UI surface; conscious scope cut.
- Nightly cluster recompute scheduler, per-notebook entity denylist UI, Settings UI for unsubscribe blocklist, persistent entity watchlist.

---

## v1.8.0 — Studio UI Redesign, iPhone Scan Capture, Sidecar Lifecycle, Multi-Provider LLM

### Highlights
- **Studio redesigned** — One unified drawer + two slim entry bars (chat-area slim strip + LeftNav rainbow) replaces the old 9-pill ActionBar and tabbed Studio panel. 6 generation types including Cards (Flash Cards).
- **Studio documents now read like the chat** — Explicit markdown presentation brief (headers, bold, lists, tables, scan-ability) added to every doc generation prompt.
- **iPhone Scan Capture** — Continuity Camera fully integrated in-process via `AVCaptureDevice` + multi-page Scan Documents sessions. Portrait preview by default, rotate button, Return-to-capture.
- **Memory steward** — New module that owns Ollama RAM hygiene, evicts non-essential models before each scan so the vision working set fits a 16 GB box.
- **Signed + notarized releases** — `release.sh` now does codesign + `xcrun notarytool` + stapler + Gatekeeper verify end-to-end with safe identity injection (never committed).
- **Multi-provider LLM foundation** — New `services/llm_provider.py` routes models to Ollama or `llama_server`; Ollama↔OpenAI translator covers generate + chat + streaming.
- **Sidecar lifecycle + one-click swap** — `SidecarManager` spawns `llama-server` as a child process with health polling + graceful shutdown. Bonsai-8B is selectable from the Health Portal Locker.
- **Library main view** — New type-grouped accordion + universal Download per kind. Per-item PDF / PPTX export menu on canvas cards.
- **Main nav redesign** — Word-button strip (Chat / Library / Constellation / Timeline / Curator) + ⌘1-⌘5 / ⌘K command palette + pulse-on-change.
- **Voice Profile robust to LLM shape drift** — Settings → Voice no longer crashes when the LLM that builds the profile returns an object where the UI expected a string (React #31).

---

### Studio UI redesign — chat-area slim bar, LeftNav rainbow, canvas-contained drawer
- **`src/components/studio/StudioLauncher.tsx` + `StudioDrawer.tsx`** — Replaced the 1437-line `ChatActionBar` 9-pill row, the inline per-type popovers, and the tabbed `Studio.tsx` panel with one unified drawer + two slim entry bars. Chat-area variant is a near-flush `⌃ Studio ⌃` strip above the chat input; LeftNav variant has a rainbow accent line (animates during generation) + STUDIO label + 5 tab icons. Drawer mounts inside `CanvasPanel` with `absolute` positioning so it overlays only the canvas — LeftNav and top nav stay interactive.
- **6th drawer type: Cards (Flash Cards)** — Layers icon, fuchsia accent. Per-type config: count slider, difficulty, tutor gender, tutor accent (us/uk), autoplay toggle, include-visuals toggle. Drops a `flashcards` canvas item; `FlashcardsCanvasTile` self-generates the deck on mount.
- **Audio: "Accent" → "Accent / Language"** — Dropdown contains real languages, not just US/UK accents. Restored Hindi (regression from earlier rewrite), added German. Extracted to a shared `ACCENT_LANGUAGE_OPTIONS` constant.
- **Video: Accent/Language + Narrator gender** — Previously hardcoded `accent: 'us'` with no UI for narrator gender. Both controls now in the drawer, persisted to localStorage.
- **Per-item export menu on canvas cards** — `⋯` menu in the expanded header of `document` / `note` / `chat-response` cards: Download PDF (`contentService.downloadAsPDF`, layout='clean') and Export to Slides (dispatches `openExportModal`). Replaces the old standalone PPTX + PDF pills with a contextual affordance.
- **`createFlashcardsDeck` listener restored in `App.tsx`** — Silent regression: `FlashcardsCanvasTile` dispatches this event for gap-analysis "Quiz me on this" follow-on decks, but the listener died with `ChatActionBar`. New listener mounted next to `lb:openLibraryItem`.
- **Library main view (`src/components/Library.tsx` + `quiz_store.py` + `visual_store.py`)** — Type-grouped accordion, click-to-open canvas tombstones, universal Download per kind.
- **Main nav redesign (`src/components/layout/MainNav.tsx`)** — Replaced view-selector dropdown with word-button strip (Chat | Library | Constellation | Timeline | Curator). Added ⌘1-⌘5 / ⌘[ / ⌘K shortcuts, command palette, subtle pulse-on-change.
- **Save-as-Note refactor** — Collapsed the parallel Findings surface into Sources/Notes with a one-time migration so saved items are visible + RAG-indexed.

### Studio document output quality — explicit markdown presentation brief
- **`backend/services/output_templates.py`** — Added `PRESENTATION_QUALITY` constant: ~13-line brief covering `##` / `###` headers, `**bold**` for key terms, `*italics*` sparingly, lists for 3+ enumerated items, tables for side-by-side comparisons, blockquotes for sourced quotes, `---` between major sections only, `` `inline code` `` for literals, short paragraphs, one-line takeaway-then-evidence section openings, no preamble lede, scan-test. Injected into `build_document_prompt` (both template + no-template paths) — covers every `DOCUMENT_TEMPLATES` doc type automatically.
- **`backend/api/content.py`** — Imported `PRESENTATION_QUALITY` and injected into both custom-skill fallback paths (streaming + non-streaming). Root cause: chat-side `rag_generation.py` has had an explicit `PRESENTATION QUALITY` block for a while, which is why chat answers read like well-formatted docs; Studio's prompt builder was missing the equivalent so flat-prose-with-no-headings became the failure mode. 7B models reliably follow EXPLICIT format instructions; they unreliably infer them from voice exemplars.

### Voice Profile defensive coercion — fix React #31 from LLM shape drift
- **`src/components/settings/VoiceProfileSection.tsx`** — Settings → Voice was crashing on one of three machines because the LLM that builds the profile (`phi4-mini` in `voice_engine.py`) produced an object (e.g. `{"vocabulary": {"type": "academic"}}`) where the UI expected a string. `json.loads()`-only validation let it through; React refused to render the object. Frontend now types every field as `unknown`, coerces via `renderField()` (strings pass, objects `JSON.stringify`, nullish → fallback) and `renderInterests()` (flattens any shape into `string[]`, peels common object keys: `name` / `topic` / `interest` / `label`). UI is robust to any backend shape.

### Sprint 9.10 — Vision pipeline reliability (memory steward + working vision default)
- **Diagnosed silent capture failure** — Captures uploaded from the QR-flow iPhone page were getting stuck at "processing" with `ollama ps` never showing the vision model. Root cause was twofold: (a) `ibm/granite3.3-vision:2b` reliably segfaults the Ollama 0.23 llama-runner on Apple Silicon (same fault address `0x18c0a25e8` every call → it's a model-runner / model-file bug, not OOM), and (b) when running the original `granite3.2-vision` flow alongside `olmo-3:7b-instruct + phi4-mini + snowflake-arctic-embed2` the runner did legitimately OOM because the working set exceeds ~13 GB.
- **`backend/services/memory_steward.py`** — New module that owns Ollama RAM hygiene. Three coroutines: `loaded_ollama_models()` (queries `/api/ps`), `unload_ollama_model()` (POST `/api/generate` with `keep_alive: 0`), and `free_for_pipeline(keep, *, reason)` which evicts everything outside the caller-supplied keep set. Tolerant of the `:latest` tag (so config-style names match Ollama's reported names), guarded by an `asyncio.Lock`, all errors swallowed-and-logged so a memory-mgmt failure never breaks the scan pipeline.
- **`backend/services/scan_pipeline.py`** — `classify_and_ocr` now calls `free_for_pipeline({vision, DOC_CLEANUP_MODEL, PHOTO_ENRICH_MODEL, embedding_model})` once at the top, before the first vision call. Effect on the typical 16 GB box: the chat main model (~6 GB) gets evicted, leaving comfortable room for vision (~4-5 GB). Working set is computed dynamically via the new `_ocr_working_set()` helper so a future config where main IS the vision model (e.g. `gemma4:e4b`) collapses to a single-model resident state with zero churn.
- **`backend/config.py`** — Default `vision_model` switched from `ibm/granite3.3-vision:2b` to `granite3.2-vision:2b` (3.5 GB, `/api/generate` API, the official Ollama-team build). Diagnosis: the IBM 3.3 community tag reliably crashes the Ollama 0.23 llama-runner on first inference (same fault address every call → upstream model-file bug, not OOM). Verified granite 3.2 returns proper vision descriptions in 5 s end-to-end with the full chat working set resident. With the default `olmo-3:7b-instruct` main model, `memory_steward` evicts olmo (~6.3 GB) before each scan so the OCR working set (vision 3.5 GB + phi4-mini cleanup 3.1 GB + embed 1.1 GB ≈ 7.7 GB) sits well under a 16 GB ceiling. Tradeoff: one-time ~3-5 s reload of olmo on the user's next chat after a scan. The IBM Granite 3.3 entries stay in the registry so users can opt back in once the upstream Ollama bug is fixed.
- **`src/components/ScanQRBadge.tsx`** — `page_error` events now stash `event.error` in a `lastError` state, auto-expand the dropdown, and render a red `AlertCircle` banner with the actual backend message. When the error contains "model runner" we add a one-liner hint pointing the user at closing other Ollama-using apps. Replaces the previous behaviour where the QR badge would just show a small red counter and the user had no idea what failed.
- **`src/components/ScanQRBadge.tsx` — `PageStatus.error?: string`** added so per-page errors can be surfaced later in the page-grid view if we want.

### Sprint 7 — Continuity Camera sidecar
- **`src-tauri/tools/continuity-camera/`** — New Swift CLI sidecar (`ContinuityCamera.swift`) that captures one image from a paired iPhone and returns the path as JSON. After two dead Apple API paths on macOS 26 Tahoe (`NSPerformService "Capture.ImportImage"` removed; `ICDeviceBrowser` no longer enumerates Continuity iPhones — confirmed via `pbs -dump_services` and Image Capture.app), settled on **AVCaptureDevice `.continuityCamera`** as the only public path that works.
- **Tauri wiring** — `tauri.conf.json` registers the sidecar as `externalBin`; `lib.rs` exposes `trigger_continuity_camera` command that spawns it and parses JSON. `Info.plist` declares `NSCameraUsageDescription`.
- **Entitlements / signing** — `com.apple.security.device.camera` on the sidecar; dual-mode `build.sh` signs with Developer ID when available, adhoc otherwise. Deployment target bumped to macOS 14 (required for `.continuityCamera`).
- **Tahoe Info.plist compliance** — `NSCameraUseContinuityCameraDeviceType` added to the main app's `Info.plist` and embedded into the sidecar binary via `-sectcreate __TEXT __info_plist` (without this key, AVCaptureDevice silently returns zero Continuity iPhones on macOS 14+). New `ContinuityCamera-Info.plist` carries the bundle id, usage description, and the opt-in key for the sidecar.

### Sprint 9.1 — Continuity Camera moved in-process (sidecar deleted)
- **Why the sidecar had to go** — Even with the right `importFromDeviceIdentifier` placeholder and a correctly-installed `NSServicesMenuRequestor`, the Swift sidecar would auto-populate the AppKit menu with iPhone capture items but tapping one did nothing: the captured image data never reached our `readSelection:`. Root cause: macOS's pasteboard-services routing keys off Launch Services-registered `.app` bundles, and a single-file adhoc-signed sidecar binary isn't registered as one. LocalBook.app *is* registered, so the only reliable fix is hosting the responder inside LocalBook itself.
- **`src-tauri/src/continuity.rs`** — New module (~480 lines) implementing the full Insert-from-iPhone flow in Rust via `objc2 0.6` + `objc2-app-kit 0.3`. Defines `LBContinuityResponder` (an `NSResponder` subclass conforming to `NSServicesMenuRequestor`), installs it into the main window's responder chain (between `contentView` and the window), pops an `NSMenu` containing the magic `NSMenuItem` placeholder, and bridges back to tokio via a `oneshot::channel` with a 3-minute capture budget.
- **`src-tauri/src/lib.rs`** — `trigger_continuity_camera` is now a thin wrapper around `continuity::trigger(app)`; the legacy `camera_id` / `include_non_continuity` params are accepted-and-ignored so the frontend doesn't need to change. `tauri_plugin_shell::process::CommandEvent` import dropped.
- **Sidecar fully removed** — Deleted `src-tauri/tools/continuity-camera/` (Swift source, build script, entitlements, Info.plist), both pre-built `src-tauri/binaries/continuity-camera-*-apple-darwin` binaries, the `externalBin` entry in `src-tauri/tauri.conf.json`, the Step-2 sidecar build in `build.sh`, and the matching initial-install + rebuild blocks in `install.sh`. `release.sh` comment updated. Net effect: one fewer signed binary to ship, one fewer entitlements file to maintain, zero IPC.
- **Bonus bug fixes** — The old "10 dismissals to close" bug (recursive auto-repop on menu cancel) is gone; cancel now just resolves the oneshot once and restores the responder chain. Multi-page Scan Documents from the iPhone returns N image paths in a single command response, which `RichNoteEditor.runInlineOcr` already handles via `/scan/ocr-batch`.
- **New deps (macOS-only)** — `objc2 = "0.6"`, `objc2-app-kit = "0.3"`, `objc2-foundation = "0.3"` added to `Cargo.toml` under `[target.'cfg(target_os = "macos")'.dependencies]`. All three were already in the transitive graph via `window-vibrancy 0.6` so the lockfile barely changes.

### Sprint 9.9b — Continuity Camera UX pass (rotation + keyboard)
- **Portrait preview by default** — iPhone sensor streams landscape natively; 9.9's window displayed the video sideways when users held the phone the way most people actually do for doc capture. Capture window is now portrait-shaped (540×780) and both the preview layer connection and the photo output connection call `setVideoRotationAngle(90)` via the AVCaptureConnection modern rotation API, so the saved JPEG is oriented correctly too.
- **Rotate button** — A `Rotate` button on the left of the button row cycles `0° → 90° → 180° → 270° → 0°`. Applies to both the preview and the photo output in one shot, so turning the phone sideways for a long whiteboard works without editing the file after the fact.
- **Return/Enter captures** — Capture button is now the window's default button; pressing Return fires it. No reaching for the mouse.
- **Diagnostic logs** — Rotation support/failure (`isVideoRotationAngleSupported`) is now logged per connection so users can see if their Continuity device refuses a given angle.

### Sprint 9.9 — Continuity Camera pivot to AVCaptureDevice (the right architecture)
- **Why we pivoted** — Eight sprints chasing `NSMenuItemImportFromDeviceIdentifier` proved Apple's menu-substitution machinery only fires from the menu-bar tracking loop. The "right" way for a button-driven Tauri app is the same mechanism browsers use via `getUserMedia`: enumerate the iPhone as an external camera (`AVCaptureDeviceTypeContinuityCamera`) and drive the capture session ourselves.
- **`src-tauri/src/continuity.rs`** — Full rewrite (~650 lines). Discovers Continuity Camera devices via `AVCaptureDeviceDiscoverySession`, builds an `AVCaptureSession` with `AVCapturePhotoOutput`, and shows a native 720×540 NSWindow centred on screen with `AVCaptureVideoPreviewLayer` (live iPhone feed) plus `Capture` and `Cancel` buttons. `LBCaptureController` (NSObject subclass) owns the window/session and handles button actions; `LBCapturePhotoDelegate` (AVCapturePhotoCaptureDelegate) writes the resulting JPEG to `~/Library/.../scans/continuity/` via `NSData.writeToFile:atomically:` and hops back to the main thread via `performSelectorOnMainThread:` to close the window and send on the existing oneshot channel.
- **`src-tauri/Cargo.toml`** — Added `objc2-av-foundation`, `objc2-core-media`, `objc2-quartz-core` (all 0.3); trimmed AppKit features to the new UI surface (`NSButton`, `NSColor`, `NSScreen`, `NSTextField`); dropped `NSMenu*`, `NSResponder`, `NSPasteboard*`, `NSEvent` features that the menu approach required.
- **`src/components/RichNoteEditor.tsx`** — Toast now reads "Opening capture window… frame the page on your iPhone, then click Capture." matching the new programmatic flow.
- **Sprints 9.1–9.8 retired** — `LBContinuityResponder`, `NSServicesMenuRequestor` impl, menu placeholder injection, services-type registration, all gone. The `trigger_continuity_camera` Tauri command surface is unchanged so the JS frontend keeps working without edits.

### Sprint 9.8 — Continuity Camera: Edit menu integration (dead-end, see 9.9)
- **Root cause of Sprints 9.1–9.7 failures** — `importFromDeviceIdentifier` substitution ONLY fires during the menu-bar tracking loop (user physically clicks a menu bar item). `popUpContextMenu`, `sendEvent`, and standalone `popUpMenuPositioningItem` never trigger it. Every native macOS app (TextEdit, Notes, Pages, Keynote, Finder) uses the menu bar.
- **`src-tauri/src/continuity.rs`** — Injects the placeholder into the Edit menu bar submenu (matching TextEdit/Notes pattern). No more programmatic popups. Registers image return types (`public.tiff`, `public.png`, `public.jpeg`, `com.adobe.pdf`, `public.file-url`) with services system. Responder stays as `firstResponder` awaiting `readSelectionFromPasteboard`. (Replaced in 9.9.)
- **`src/components/RichNoteEditor.tsx`** — Toast updated to direct user: "Click Edit in the menu bar, then choose your device under Import from iPhone or iPad." (Replaced in 9.9.)

### Sprint 9.3 — Continuity Camera identifier fix (menu was greyed-out)
- **Root cause** — The placeholder menu item's identifier was hardcoded as the literal string `"NSMenuItemImportFromDeviceIdentifier"`, but the actual runtime value of the AppKit extern constant is an opaque string that does NOT equal the symbol name. AppKit never recognised the placeholder → Continuity Camera substitution never engaged → menu showed a greyed-out "Insert from iPhone" label with no device submenu.
- **`src-tauri/src/continuity.rs`** — Now imports the real `NSMenuItemImportFromDeviceIdentifier` constant from `objc2-app-kit`. Also broadened `validRequestor` to return `self` for ANY non-nil returnType when sendType is nil (covers Take Photo images, Scan Documents PDFs, and Add Sketch). Added diagnostic log of the actual identifier value.

### Sprint 9 — iPhone-side capture + inline OCR insertion
- **iPhone owns the capture experience** — `src-tauri/tools/continuity-camera/ContinuityCamera.swift` rewritten on top of Apple's documented `NSMenuItem.importFromDeviceIdentifier` flow. The Mac shows a tiny "Connecting to iPhone…" launcher; AppKit auto-pops a contextual menu populated with iPhone-side capture modes (Take Photo, Scan Documents, Add Sketch); the user picks once and then drives the rest from their iPhone screen — shutter, framing, multi-page batching all happen there. Multi-page Scan Documents returns N images in a single sidecar invocation. Replaces the old AVCaptureDevice live-preview window. Two non-obvious correctness requirements kept biting us before this stuck: (1) the menu must contain an `NSMenuItem` whose `identifier` is `NSMenuItem.importFromDeviceIdentifier` — AppKit *replaces* this placeholder with iPhone items at open time, so an empty menu silently falls back to generic Services items that never reach the iPhone; (2) the receiving `NSResponder` (an `NSView` subclass conforming to `NSServicesMenuRequestor`) must be the key window's `firstResponder`, otherwise AppKit's responder-chain walk never hits our `validRequestor`.
- **Frontend collapsed to one menu entry** — The Mac-side scan menu used to expose two iPhone variants (Scan Documents / Take Photo). Now a single "📱 Insert from iPhone…" entry; the iPhone screen makes the same choice with a much better UI.
- **Inline OCR → open note** — Captures and file-picker scans now OCR through the new `POST /scan/ocr-batch` endpoint (`backend/api/scan.py`) and append the merged markdown into the *currently-open* note at the cursor position via `editor.getTextCursorPosition()`, instead of forking a separate scan note. Backend pipeline refactored: `services/scan_pipeline.py` extracted `_ocr_pages_and_merge()` shared core, added `process_batch_inline()` returning `{merged_text, page_texts, total_pages, chars}`.
- **Frontend consolidation** — `RichNoteEditor.runInlineOcr()` is now the single source of truth used by the file picker, single-shot Continuity capture, and the multi-page session-finish handler. `ScanSessionPanel` gained an optional `processBatch` prop so its progress bar drives the same pipeline. `scanService.ocrBatchWithProgress()` shares the SSE reader with `processBatchWithProgress` via a typed helper.
- **Dead code removed** — `CameraPickerModal.tsx` deleted (AppKit now picks the iPhone). Tauri command `list_continuity_cameras` and the `ContinuityCameraInfo` Rust struct removed from `src-tauri/src/lib.rs`. The legacy `--camera` / `--include-non-continuity` sidecar flags are accepted-and-ignored for backward compat. The old `--list` mode stays — still useful for diagnostics.

### Sprint 8 — Multi-page scan sessions
- **`src/components/ScanSessionPanel.tsx`** — Thumbnail grid with reorder/delete/retry, drives the batch flow from the note editor.
- **`src/services/scanSession.ts`** — Versioned `localStorage` persistence so a session survives reload.
- **`src/services/scanService.ts`** — SSE client mirroring `uploadWithProgress` for per-page progress.
- **Backend `POST /scan/process-batch`** — New SSE endpoint in `backend/api/scan.py` + `process_batch` in `scan_pipeline.py`. Merges pages with `--- PAGE N ---` markers. Fixed duplicate-class bug and a broken `ws_manager` import (→ `broadcast_update`) picked up along the way.

### Signing + notarization pipeline
- **`release.sh`** — Pre-flight checks for `APPLE_SIGNING_IDENTITY` / `APPLE_TEAM_ID`; dynamically injects the signing identity into `tauri.conf.json` at build time (never committed); adds notarization (`xcrun notarytool submit --keychain-profile localbook-notary`) + stapling + Gatekeeper verify. Old standalone `scripts/build-signed-release.sh` removed.
- **`backend/build_backend.sh`** — Dual-mode: Developer ID + hardened runtime + entitlements when the env var is set, adhoc fallback otherwise. Signs the PyInstaller bundle and every nested binary.
- **`build.sh` / `install.sh`** — Both now invoke the sidecar build before `npm run tauri build` so end-user installs produce a working (adhoc-signed) sidecar without requiring a Developer Program membership.
- **Build robustness** — `install.sh`'s rebuild path was missing the sidecar build entirely (it was only in the initial-install branch), and Tauri build failures were being coerced to warnings, masking sidecar problems. Both paths now build the sidecar, both unmute its stderr, and both fail loudly with a clear pointer to the manual recovery command. `build.sh` and `install.sh` now also hard-fail with a clear message if `src-tauri/binaries/continuity-camera-*` are missing before invoking `tauri build`. Fixed a pre-existing latent bug where `install.sh`'s rebuild error path called a nonexistent `error` helper (real helper is `fail`).
- **Skip DMG in dev/install builds** — `build.sh` and `install.sh` now pass `--bundles app` to `tauri build`. Tauri's `bundle_dmg.sh` uses AppleScript to set DMG window metadata and fails on machines without Automation permission. End-user installs only need the `.app`; `release.sh` continues to build the full DMG for notarized distribution.
- **`.gitignore`** — Blocks `.signing.env`, certificates, provisioning profiles, notarization zips, and sidecar build outputs from ever reaching the repo.

### Sprint 8 follow-up bug fixes
- **Scan button no longer overlapped by close-X** — `RichNoteEditor` header row now reserves `pr-10` so the parent's absolute-positioned close button (`top-3 right-3`) doesn't sit on top of the Scan button.
- **Scan session no longer leaks across notes** — `ScanSessionState` now carries `noteId`; `loadSession(noteId)` filters by it. Schema bumped to `v2` (legacy `v1` payloads are dropped on first read). Symptom was: any abandoned scan session resurrected itself on every freshly-created `+Note`, even when the user just wanted to type a manual note.
- **Sidecar: strict Continuity-only selection + richer diagnostics** — Removed the silent fallback to `devices.first` that let stranded virtual cameras (mmhmm, OBS, etc.) hijack the session. Discovery now also enumerates `.builtInWideAngleCamera` purely for diagnostics, prints every camera with manufacturer / modelID / uniqueID to stderr, and the failure path returns a multi-line checklist that varies based on whether AVFoundation saw 0 cameras (permission / Info.plist issue) vs. saw cameras but no Continuity device (Continuity itself off, or stale DAL plugin to remove from `/Library/CoreMediaIO/Plug-Ins/DAL/`).

### Curator + sources fixes
- **Weekly-wrap single-flight** — `CuratorAgent.generate_weekly_wrap_up()` (and `generate_morning_brief()`) are now wrapped in async lock + 5-minute / 90-second cache. Symptom was: a long, populated wrap the user was reading would get clobbered by a near-empty second wrap from a concurrent caller (chat intent + UI poll, or scheduler + manual). The save endpoint also gained a guardrail that refuses to overwrite a same-day wrap with non-empty narrative using one whose narrative is empty.
- **Source move-between-notebooks rewrite** — Old endpoint hard-failed with "Source has no content to re-index" for any source whose text lived in LanceDB but not `source.content` (web captures, collector items, sources processed via background path). New endpoint: (1) preserves *every* metadata field (tags, url, author, dates, notes, web/collector provenance, content_date) instead of dropping all but type/format/content; (2) verifies target notebook exists; (3) treats empty-content sources as a metadata-only move with `status=needs_reindex` instead of erroring; (4) if re-ingest fails after the move, returns 200 with a `reindex_error` field so the source ends up in the target notebook with `status=failed` instead of being lost altogether; (5) uses `INSERT OR REPLACE` to flip `notebook_id` atomically — no window where the row exists in both notebooks or neither.
- **Source organization (NotebookLM-style sort)** — New sort dropdown above each notebook's source list, persisted to `localStorage` (`localbook.sourcesList.sort`). Modes: Recently added (default), Oldest first, Title A→Z, Title Z→A, By type (groups note / collected / format), Largest first. Implemented client-side in `SourcesList.tsx` so it's instant; backend already returned `created_at` so no server change was needed beyond surfacing the field on the `Source` TS type.
- **Continuity Camera: per-machine camera picker** — When more than one camera is available (paired iPhone + iPad + built-in FaceTime + leftover virtual cams), the user can now pick which one to scan with. New sidecar `--list` mode enumerates every device AVFoundation sees as JSON; new `--camera <uniqueID>` flag forces a specific device; new `--include-non-continuity` flag lets a built-in / external camera be used as a fallback when no iPhone is around. New Tauri command `list_continuity_cameras` and an updated `trigger_continuity_camera(camera_id, include_non_continuity)`. New React `CameraPickerModal` with iconography per device type, a "Remember this choice" toggle, and persistence to `localStorage` (`localbook.continuityCamera.preferredId`) so subsequent scans skip the picker. Strict iPhone-only behaviour is preserved when no camera is explicitly chosen — virtual / built-in cams are still excluded by default.

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

### Foundation for non-Ollama backends (multi-provider LLM)
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
