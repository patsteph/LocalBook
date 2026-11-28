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
| 72 Encrypted volume, Touch ID, credential locker | None | Flight 8 (2026-11-28): see below. Credential locker (site logins) became the People profiler's own WebKit cookie store (flight 7) |
| 20 Embedding selector, model catalog | Models tab has MLX repos only | Settings › Models: the embedding model is shown and fixed (EmbeddingGemma 2 is what the phone and the recipe need); the catalog beyond MLX waits |
| 46 Podcast voice roster | Audio overview has two accents | Done: the audio sheet picks Alex's and Sam's voices and the video sheet its narrator from the 20 Kokoro voices Read aloud offers (`StudioEngine.make(voices:)`) |
| 13 Voice modifier | None | Done: `VoiceModifier` (Core) with `voice_modifier.py`'s family texts, applied in `generationSetup` to answers only; Settings › General › "Keep the tone even across models" |

### Flight 3: intelligence that is prompt + SQLite (Phase 5 rows with no new engine) — built 2026-11-27, untested on a Mac

| Row | Gap | Swift approach |
|---|---|---|
| 62 Timeline, key dates | None | Done: `InsightEngine.makeTimeline` reads the whole notebook in windows of 8 passages (sampled evenly past 192) with `structured_llm.extract_timeline`'s prompt, `DateKey` orders the dates a model writes (ISO, month-year, quarters, decades, FY), events merge by title and date. Insights pane › Timeline. Key dates from the web (collector) stay with flight 4 |
| 62 Knowledge graph, entities, contradictions, comparison, synthesis, findings, writing assistant | None | Done in the Insights pane (sidebar › Insights, ⌥⌘4, the Insights menu): knowledge graph (`entity_extractor`'s prompt per window, nodes merged by name, edges by shared passage, relations named for the top 30 pairs, force-layout canvas); contradictions (`contradiction_detector`'s two prompts: claims per window, candidate pairs across sources by the search model's vectors above 0.72, up to 24 pairs checked, dismiss kept); comparison (`compare_documents`' prompt over two sources' text with an optional focus); perspectives (`topic_perspectives`: a take and claims per source, then consensus and contested); findings (`FindingsStore`: Keep as finding under an answer or an insight, tags and stars); writing assistant (`WritingAssistant`: the old tasks and formats, notebook passages as reference, Save as note, ⌥⌘W). Parsers tested in `InsightTests`; no old-app golden exists for these shapes, so the readers are tested on the shapes the old prompts specify |
| 61 Topic modeling | Tag cloud only | Done without a new engine: `TextClusters` k-means (k-means++ seeded, fixed seed) over the notebook's chunk vectors, class-based TF-IDF keywords (BERTopic's first naming step), the model names each group. Insights › Topics. Leiden/BERTopic itself not ported |
| 49 Canvas, 50 Constellation | None | Moved to flight 4 (below) |

### Flight 4: the Canvas and the Constellation — built 2026-11-27, untested on a Mac

| Row | Gap | Swift approach |
|---|---|---|
| 49 Canvas | None | Done: `CanvasPane` over `CanvasEngine`. The journey is recorded as each turn finishes (`JourneyStore`, the old exploration store's fields); Build the map makes a chip per learning question (the old noise filter keeps agent admin chatter off), per source an answer used, per Studio item and insight; threads are embedded with the search model and gathered into sub-topic cards at the old thresholds, with the answer model naming new cards; edges in the old six states (made-from, drawn, auto-connected, researched, tension from the contradictions insight, dotted candidates from 0.5 concept + 0.35 embed + 0.15 shared source); gaps as badges and a list; SM-2 review; "What were you exploring here?" on orphans; ask about a selection and the answer lands on the map; notes; Tidy up. The document is the old `GET /canvas/layout` JSON (one file per notebook under `Canvas/`), so an old canvas opens |
| 50 Constellation | None | Done: `ConstellationPane` in SceneKit over the knowledge-graph insight: communities by label propagation, each on its patch of sky with the most connected star at the centre, stars sized by connections and mentions, lines by weight, billboard names, click to read and to dim the rest, a source filter, a cluster legend. No WebSocket: the Swift app builds in the foreground and draws when done |
| 49 Idle research on gaps, floating windows | None | Not ported: idle research waits for the Collector (flight 5); the react-flow floating windows have no place in a native pane |

### Flight 5: the agents (Phase 5 rows that need schedules and the web) — built 2026-11-27, untested on a Mac

| Row | Gap | Swift approach |
|---|---|---|
| 57 Curator | Only the `@curator` question existed | Done: `CuratorEngine` over `Curator.swift` (Core). Sidebar › Curator (⌥⌘7): the morning **Brief** (what changed since the last one, from the event log: sources added, questions asked, Studio and insights made, per notebook), the **Week** in review, **Patterns** (passages that say the same thing across two notebooks: search-model vectors above 0.74, up to 12 pairs, each named by the model), **Devil's advocate** (argue against a thesis from every notebook), a **Digest** per notebook. The old prompts and the three voices (smart colleague, executive brief, conversational analyst) are ported; the profile (name, personality, voice, notebooks left out) is `CuratorProfile`, kept in `Curator/profile.json`. Pieces are kept, can be saved as notes, marked useful, dismissed. `/brief` in the slash menu and ⌥⌘B. The old "brain" (judge, synthesize) stays the `@curator` answer path |
| 58 Collector | None | Done: `CollectorEngine` over `Collector.swift` (Core, the old `collector_config` keys so an old config reads). Sidebar › Collector (⌥⌘8), one per notebook: Setup (name, subject, intent, focus areas, excluded topics, approval mode trust me / show me / mixed, how often, relevance floor), Sources (feeds by `FeedParser` on `XMLParser`: RSS 2.0 and Atom, a page that advertises a feed becomes the feed; watched pages re-read and diffed by hash; keywords searched on Hacker News, Reddit and Wikipedia each run), Queue (approve → the item becomes a source, dismiss with a reason, expire after 7 days), History (runs with examined / added / queued / skipped, source health degraded at 1 failure, failing at 3, dead at 6). Scoring is the old 0.5 relevance (search model, clamp((cos − 0.3) / 0.6)) + 0.3 trust + 0.2 freshness, auto-approve at 0.85 in mixed mode. Runs by hand (Collect now, `/collect`) and on a 10-minute timer that fires the notebooks whose schedule is due while the app is open; nothing runs while it is closed (no launch agent) |
| 63 Research jobs, intent routers | None | Done for research: `ResearchEngine` (Collector › Research, `/research <topic>`): web search (the sites `SiteSearchClient` knows, the model picks which), site search, deep dive (reads the top pages, `ResearchQuality` scores length, links, recency and topical fit, the model summarizes the keepers); results land in that notebook's Collector queue. The intent routers are not ported: the Swift app's slash commands are the routing |
| 64 Event bus | None | Done: `EventLog` (Core, `Events/events.jsonl`): source_added, question_asked, studio_made, insight_made, collected, approved, research_done; read by the Brief and by the MCP tools |
| 74 Curator-only MCP tools | Answered "not in this version yet" | Done: `events_since`, `event_kinds`, `curator_insights`, `todays_digest`, `start_research`, `get_job` are real in `LocalAPIBridge`; `MCPCatalog.notYet` is empty |

### Flight 6: the Correspondent — built 2026-11-27, untested on a Mac

Patrick's decision (2026-11-27): both paths built in; which one the user has is a setting in their profile (Correspondent › Inboxes › Your mail setup), and every inbox added follows it.

| Row | Gap | Swift approach |
|---|---|---|
| 59 Correspondent | None | Done: `CorrespondentEngine` over `Correspondent.swift` (Core) and one `Mailbox` protocol with two implementations, the user's choice kept as `CorrespondentState.mailPath`. **Mail on this Mac** (`MailAppStore` + `MailAppBridge`): reads Mail's Envelope Index read-only (`immutable=1`) and the `.emlx` files under the chosen `~/Library/Mail` (security-scoped bookmark, Full Disk Access asked for once), and asks Mail by Apple event to mark read, move (creating the mailbox first) and send; entitlements `automation.apple-events` + the Mail exception. **IMAP account** (`IMAPClient` + `SMTPClient` on Network.framework, no dependency): implicit TLS, LOGIN with a non-synchronising literal, UID SEARCH UNSEEN from the last UID, BODY.PEEK fetch, STORE, MOVE or COPY + \\Deleted + EXPUNGE, CREATE; SMTP AUTH PLAIN or LOGIN on 465 (STARTTLS not supported; plain only to loopback for the test server). Passwords live in the Keychain (`MailSecrets`), never in `Mail/correspondent.json`. Rules ported verbatim: the three-way classification prompt (personal is the safe default, never ingested, moved to `LocalBook/Personal`; transactional marked read), forwards by two of three signals with `#slug` routing, the digest router (cosine ≥ 0.75 routes, else the waiting list; sender bias +0.25 from approvals, capped 0.5), Message-ID and SHA-256 dedupe, delete after ingest (Gmail → `[Gmail]/Trash`, iCloud → `Deleted Messages`), the "Got it — ingested into …" confirmation, the weekly journal (07–11 UTC, ≥ 7 days, from the event log, never empty). Sidebar › Correspondent (⌥⌘9): Waiting, Inboxes, Journal, History; Check mail now (⌥⇧⌘M, `/mail`); a 30-minute timer polls each inbox every 8 hours while the app is open. `MailParser` (MIME, RFC 2047, quoted-printable, base64, HTML to text), `IMAPWire`/`SMTPWire` (protocol text) and the rules are tested in `MailTests`. Fixtures: `testdata/flights/mail/` + `scripts/flights_mailserver.py` |

### Flight 7: the People profiler — built 2026-11-28, untested on a Mac

| Row | Gap | Swift approach |
|---|---|---|
| 60 People profiler | None | Done: `PeopleEngine` over `People.swift` (Core: `PersonProfile`, `PeopleConfig` with the old snake_case keys so an old `people_config` reads, `ActivityAnalysis`, `ProfileChanges`, `ProfileText`, `PeoplePrompts`). Sidebar › People (⌥⌘0), one team per notebook: add people with their public page addresses (LinkedIn, X, GitHub, Instagram, a personal site); **Read pages** (⌥⇧⌘P, `/people`, or a daily / weekly timer while the app is open) loads each page in WebKit through `PageLoader`, signed in where the site needs it (the user signs in once in a sheet; the cookies live in LocalBook's own persistent `WKWebsiteDataStore`, replacing the Fernet-encrypted Playwright sessions), and the answer model turns the text into fields (the old `_llm_extract` prompt, widened to experience, education, posts, repositories). Then the old pipeline: activity frequency and content types from the post dates, a two-sentence focus line, the profile and the private notes indexed as sources of the notebook (`people-profile-<id>`, `people-notes-<id>`, replaced on every read) so Ask can answer about a person, the coaching insights prompt verbatim with its grade-calibration rules and the notebook's passages, topics, findings and recent questions as context, and change detection (role, company, headline, location, skills, post counts) into the Changes tab and the event log. No Playwright, no Chromium download, nothing sent anywhere but the page loads |

### Flight 8: the lock, encryption at rest, and running while the window is closed — built 2026-11-28, untested on a Mac

| Row | Gap | Swift approach |
|---|---|---|
| 72 Touch ID | None | Done: `AppLock` (`Protection.swift`). Settings › Security › Lock: ask for Touch ID or the login password to open LocalBook (LocalAuthentication `deviceOwnerAuthentication`, so Apple Watch and the password work where there is no Touch ID), and again after the app has been away for 1 / 5 / 15 / 30 / 60 minutes or when the screen locks; Lock now. `LockScreen` covers the whole window (the content is hidden, not dimmed); the agents and the local API keep working behind it |
| 72 Encrypted volume | None | **Decided, not built:** a sandboxed app cannot mount its own encrypted disk image: `hdiutil attach` needs the disk-image daemon (`hdiejectd`), which the sandbox will not start ("Device not configured" / "Operation not permitted"; Apple DTS says not to try). The library is in the app's container, so FileVault is the layer at rest. Settings › Security › Encryption at rest reads `fdesetup status` and says whether FileVault is on, with a button to System Settings when it is off, and says plainly why there is no volume. Backups stay encrypted on their own with the recovery phrase |
| 57/58/59/60 "while the app is closed" | Schedules ran only with the window open | Done: closing the window never quit the app (SwiftUI on the Mac); what was missing was a sign it is alive and a way to start it. `MenuBarExtra` ("Keep LocalBook in the menu bar when its window is closed", on by default): what is running, how many items wait for approval, Open LocalBook, Check Mail Now / Collect Now / Read Profiles Now, Lock, Quit. "Open LocalBook when I log in" through `SMAppService.mainApp` (System Settings › Login Items lists it; the sheet points there when macOS wants approval). No launch agent and no second process: the one app, with its models, is the agent |

### Flight 9: Mac-to-Mac sync — built 2026-11-28, untested (needs a second Mac)

| Row | Gap | Swift approach |
|---|---|---|
| 70 Mac-to-Mac sync (HLC journal, merge) | Two Swift Macs could not share a library; only the phone could pair | **Decided: the phone link is the Mac link.** A second Swift Mac pairs with the Mac that holds the library exactly as the iPhone does (`MacLink.requestPairing` on the companion ↔ `PhoneHost.handlePair` on the library Mac: the same `/pair/hello`, six-digit code, P-256 keys and signed sync port), pulls the notebooks it is given diff by diff (`LibrarySync`: manifest, sources, chunk pages with vectors, recipe, notes, Studio), and sends its own notes and Studio items (`note_sent`) and its notebook, section and source edits (`LibraryEdits` queue) back. Hub and spoke: the library Mac is the hub, every other device a spoke, and two spokes see each other's changes through the hub on their next sync. The old app's HLC journal and three-way merge are not ported: the spoke model has no concurrent writers to the same row (each spoke's edits are applied on the hub in the order they arrive), and Patrick's three Macs have always had one library Mac. Settings › Sync says which this Mac is ("The Mac that holds your library"), and the paired-devices list shows a Mac with a laptop icon (`mac-` device ids) |
| 71 Ed25519 mTLS for Macs | None | **Decided, not built:** the P-256 link the phone uses carries the Mac too (Secure Enclave key where the Mac has one, `PhoneIdentity.keyIsHardware`), so a second TLS stack for Macs buys nothing. Same pinning, LBP2 signatures, nonce cache and failure limiter |
| `roles.py` (one Mac runs scheduled collections) | Two paired Macs would both poll the same feeds and inboxes | Done: `SyncRole` (Core): the library Mac runs the Collector, Correspondent and People schedules; a companion Mac does not, unless Settings › Sync › Agent schedules › "Run the agents' schedules on this Mac too" is on (`Agents.schedulesRunHere`, checked in each engine's `runDue`). The Now buttons always work. The role follows the pairing: pair, and the clocks stop here; remove the pairing, and they start |

### Later, with a second Mac on hand

The 2026-10-23 sync checklist with three Macs (now runnable on the Swift app: sitting section K), health portal and updates (77), the Tauri shell duties (79), the scripts (80), image generation UI (48).

## C. What each flight needs from Patrick

Nothing until the combined sitting: flights push to `dev` with CI green and no device gate. The sitting plan names
the commit to pull, every fixture file, and one pass line per check.
