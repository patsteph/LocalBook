# 04. Gap list: what the old app does that the Swift Mac app does not yet (2026-11-27)

*Built on 2026-11-27 from `01-absorption-map.md`, the gate log, the old app's React views (`src/components`), its
API routers (`backend/api`) and the Swift tree (`swift/LocalBookCore`, `mac/LocalBook`). Patrick is away for the
holiday; the visuals close-out sitting is paused, not dropped. This list drives the "build flights" that follow;
each flight lands as one push with one CI run, and the whole set gets one combined test sitting at the end.*

Rule kept from the plan: nothing leaves the absorption map. Every row here is a row there; this file only orders
them and says what each one needs.

## A. Already in the Swift app (for orientation)

Ask with citations, streaming, answer length, per-notebook chat and answer boundary, read-aloud; hybrid search +
reranker; notebooks, sections, colours, folds, edits queued offline; sources pane, Quick Look, export; TextKit notes
editor; every reader of 4b (PDF incl. scanned and ruled tables, Office old and new, EPUB, Jupyter, SVG, images, web
pages, YouTube, watched folders, spreadsheets by SQL); Studio (briefing, quiz, flashcards, audio, video, visuals with
the new engine: 17 idioms, 4 themes, critic, Klein/schnell backdrops, inline visuals, PNG/SVG/PDF share); meetings,
dictation, voice notes; LLM Studio (locker, browse, evaluator, hardware); site search and source discovery; one-way
library import; the iPhone host (pairing, sync, notes, Studio both ways); keyboard map, slash actions, App Intents.

## B. Gaps, grouped by flight

### Flight 1 (this push): agents reach the Swift app, and the Swift app reaches across notebooks

| Absorption-map row | Gap | Swift approach |
|---|---|---|
| 74 MCP server, agent keys, `.mcpb` bridge | Nothing listens on 127.0.0.1:8000; Jocasta, Claude Code and Claude Desktop cannot connect | `LocalAPI` (Core, pure): Streamable HTTP MCP at `/mcp` (JSON-RPC: initialize, tools/list, tools/call, ping; DELETE ends a session) behind the same companion keys as the old app (`CompanionKeys`, same `companion_keys.json` layout, `sha256(salt+token)`, `lb-` prefix, the six scopes), the same tool names and reply shapes (`list_notebooks`, `search_notebook`, `search_notebooks`, `ask_notebook`, `list_sources`, `get_source`, `list_notes`, `get_note`, `list_recent_notes`, `fetch_page`, `web_search`, `youtube_search`, `scholarly_search`, `propose_note`, `memory_search`, `memory_add`, `approval_queue`; the Curator-only tools answer "not in this version yet"). `HTTPServer` gains a plain (no TLS) loopback mode; `mcp-bridge` and `docs/MCP.md` need no change |
| 75 OpenAI-compatible `/v1` | Companions that speak OpenAI (the meeting-notes tool) have nothing to call | `/v1/models`, `/v1/chat/completions` (streamed as one SSE body or JSON) on the same server, scope `llm`, served by the loaded answer model |
| 76 Browser extension endpoints | The shipped extension finds no backend | `/auth/bootstrap` (pinned extension origin → app token), `/browser/status`, `/browser/notebooks`, `/browser/exists`, `/browser/capture`, `/browser/capture/selection`, `/browser/capture/youtube`, `/browser/capture-status/...` over `LibraryModel.ingestLink/ingestText`; pending-scrapes answers empty (the Swift app renders pages itself) |
| 15 @curator cross-notebook search | The only cross-notebook search is missing | `CrossNotebook` (Core: merge and context rules) + `LibraryModel.askAcrossNotebooks`: embed once, the top 3 passages of every notebook (`LibraryStore.search` per notebook), merged by cosine, cited with the notebook named; `@curator …` in the Ask field and `/curator` in the slash menu; the notebook chat itself stays notebook-scoped |
| 21 Memory system | No persistent memory; `memory_search`/`memory_add` have nowhere to go | `MemoryStore` (Core, SQLite `memory.sqlite`): core facts by category, archival memories with keyword search, tagged with who wrote them; Settings › Agents lists and deletes them; the chat does not read memory yet (that is the Phase 5 prompt port) |
| 64 Quality signals / audit | Agent calls were listed in Settings | `CompanionAudit` (Core): one row per call (tool, hashed args, ms, outcome) in `companion_calls.json`, shown and purgeable in Settings › Agents |
| 78 Settings (Agents tab) | Placeholder | Settings › Agents: agent keys (issue once, scopes, revoke), ready-to-paste Claude Code / Claude Desktop / JSON setup, the call log, memory, proposals from agents (approve → note, dismiss) |

### Flight 2: own your data (Phase 4 security rows that need no second Mac) — built 2026-11-27, untested on a Mac

| Row | Gap | Swift approach |
|---|---|---|
| 73 Backup and restore | None | `BackupArchive` (Core): the old `.lbbackup` envelope (magic `LBBK1\n`, JSON header as AAD, AES-GCM, device and recovery recipients) over a ustar tar (`Tar.swift`, byte-compatible with Python's) of the Swift app's folder with `data/MANIFEST.json` (sha256 per file, SQLite copied with the backup API, models and media left out unless asked). `BackupRestore` verifies, stages under `Restore-pending/` and swaps at the next launch (`AppModel.start`), keeping what it replaced in `Restore-replaced-<when>/`. Settings › Security: folder (security-scoped bookmark), Back up now, keep count, daily, the folder's archives with Restore, Open a backup… (asks for the phrase when this Mac's key does not open it). An old-app archive opens and verifies but is refused for restore (its layout is the Python app's); the import brings that library over |
| 72 Recovery phrase | None | BIP-39 (24 words, the English list as source in `BIP39Words.swift`) → PBKDF2 seed → X25519 recovery key exactly as the old `keyvault._recovery_private_key`, held to `Fixtures/backup_golden.json`; shown once; Settings › Security. The old app's phrase opens the new app's backups and the other way round |
| 72 Encrypted volume, Touch ID, credential locker | None | Deferred: the Swift app's folder is in the sandbox container and FileVault covers the disk; the hdiutil volume waits until Phase 4 proper. Credential locker (site logins) ports with the People profiler (Phase 5) |
| 20 Embedding selector, model catalog | Models tab has MLX repos only | Settings › Models: the embedding model is shown and fixed (EmbeddingGemma 2 is what the phone and the recipe need); the catalog beyond MLX waits |
| 46 Podcast voice roster | Audio overview has two accents | Done: the audio sheet picks Alex's and Sam's voices and the video sheet its narrator from the 20 Kokoro voices Read aloud offers (`StudioEngine.make(voices:)`) |
| 13 Voice modifier | None | Done: `VoiceModifier` (Core) with `voice_modifier.py`'s family texts, applied in `generationSetup` to answers only; Settings › General › "Keep the tone even across models" |

### Flight 3: intelligence that is prompt + SQLite (Phase 5 rows with no new engine) — built 2026-11-27, untested on a Mac

| Row | Gap | Swift approach |
|---|---|---|
| 62 Timeline, key dates | None | Done: `InsightEngine.makeTimeline` reads the whole notebook in windows of 8 passages (sampled evenly past 192) with `structured_llm.extract_timeline`'s prompt, `DateKey` orders the dates a model writes (ISO, month-year, quarters, decades, FY), events merge by title and date. Insights pane › Timeline. Key dates from the web (collector) stay with flight 4 |
| 62 Knowledge graph, entities, contradictions, comparison, synthesis, findings, writing assistant | None | Done in the Insights pane (sidebar › Insights, ⌥⌘4, the Insights menu): knowledge graph (`entity_extractor`'s prompt per window, nodes merged by name, edges by shared passage, relations named for the top 30 pairs, force-layout canvas); contradictions (`contradiction_detector`'s two prompts: claims per window, candidate pairs across sources by the search model's vectors above 0.72, up to 24 pairs checked, dismiss kept); comparison (`compare_documents`' prompt over two sources' text with an optional focus); perspectives (`topic_perspectives`: a take and claims per source, then consensus and contested); findings (`FindingsStore`: Keep as finding under an answer or an insight, tags and stars); writing assistant (`WritingAssistant`: the old tasks and formats, notebook passages as reference, Save as note, ⌥⌘W). Parsers tested in `InsightTests`; no old-app golden exists for these shapes, so the readers are tested on the shapes the old prompts specify |
| 61 Topic modeling | Tag cloud only | Done without a new engine: `TextClusters` k-means (k-means++ seeded, fixed seed) over the notebook's chunk vectors, class-based TF-IDF keywords (BERTopic's first naming step), the model names each group. Insights › Topics. Leiden/BERTopic itself not ported |
| 49 Canvas, 50 Constellation | None | Not started: the knowledge graph's canvas is the seed for both; the old Canvas document (clusters, tensions, gaps, idle research) and the SceneKit constellation wait for a flight of their own |

### Flight 4: the agents (Phase 5 rows that need schedules and the web)

Curator (brief, weekly wrap, patterns, devil's advocate, brain), Collector (RSS, sites, approval queue, auto-tagging),
Correspondent (Mail.app or IMAP path, Patrick chooses), People profiler, intent routers, research jobs. Each ships with
a golden from today's output; the MCP tools that name them (`curator_insights`, `todays_digest`, `start_research`,
`events_since`) switch from "not yet" to real as each lands.

### Later, with a second Mac on hand

Mac-to-Mac journal sync and Ed25519 mTLS (rows 70, 71), the 2026-10-23 sync checklist with three Macs, the encrypted
volume (72), health portal and updates (77), the Tauri shell duties (79), the scripts (80), image generation UI (48).

## C. What each flight needs from Patrick

Nothing until the combined sitting: flights push to `dev` with CI green and no device gate. The sitting plan names
the commit to pull, every fixture file, and one pass line per check.
