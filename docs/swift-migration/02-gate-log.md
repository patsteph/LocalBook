# 02. Gate log

Each phase ends with a measurable gate, run in CI or on Patrick's device, with the result written here. Device
gates are 30-minute scripts; before each one Patrick runs `git pull origin dev` and `git log --oneline -1`.

| Phase | Gate | How it is run | Result |
|---|---|---|---|
| 0 Set up | CI green on the package (macOS build + Swift Testing, iOS build of the shims) and the Mac app; Patrick approves `01-absorption-map.md` and `03-mac-design-profile.md` | `.github/workflows/swift.yml`; Patrick reads the two pages | **CLOSED 2026-11-13.** Patrick approved both pages ("both viewed and agreed too"). CI run 1 (d96f974): package build + 6 Swift Testing cases pass on macOS 26 (Xcode 26.6), shims build for iOS; the Mac app job failed only on macro trust (`-skipMacroValidation`). CI run 2 (46a37c4): **all three jobs green** (package build + tests 8 min, iOS shims 2 min, Mac app 8 min). |
| 1 Extract the package | TestFlight build 5 behaves identically on Patrick's phone (Ask, one of each Studio kind, a 5-minute meeting, lock mid-answer); CI green | Device script below; CI | **CLOSED 2026-11-16.** Device half: Patrick ran the script on build 5 built from `dev` and reported all testing complete (2026-11-16); the TestFlight upload is deferred by his choice ("We wont do a testflight for this yet"), so no dev → master PR for Phase 1. CI half met 2026-11-13, run 12 (aed09c0): all four jobs green — package build + 10 Swift Testing cases, shims for iOS, engines + Mac app, and the iPhone app against the package (on the runner's Xcode 26.6 only the app's iOS 27 AVFAudio calls cannot compile; `scripts/ci/ios-sdk-gap.py` lets exactly those through). |
| 2 Mac app v0 as a peer | `ParityRunner` passes at the phone's bar against the Python Mac; Patrick uses the Mac app daily for a week and lists what he reached for | Parity harness in the Mac app's Diagnostics; Patrick's list | **open** (opened 2026-11-16). All five slices on `dev` by 2026-11-16, CI run 21 (a5fd6ba) green: 1 pair + sync + Ask (fd9f4c4); 2 Sources, inspector, Studio (c79684e); 3 read-aloud, Meetings, voice notes, Notes (2df5bae); 4 LLM Studio + Diagnostics parity (d34645c); 5 drag-and-drop, Studio windows, App Intents (874f1b3). Gate steps are the Phase 2 script below; neither half run yet. |
| 3 Ingestion and library | Personal library migrated (19 notebooks, 41,970 chunks) with 95% identical citations on the fixture and every difference explained; 50-document ingestion diff; the Mac app answers with Python stopped; visuals golden set approved side by side | Migration tool report; diff scripts; Patrick | not started |
| 4 Become the host | The 2026-10-23 sync checklist passes with the Swift app hosting three Macs and the iPhone; the shipped browser extension works unchanged; Jocasta and Claude Desktop connect over MCP; the Python host stays off on the personal MBP for a week | Checklist; extension; MCP clients; a week | not started |
| 5 Agents, intelligence, media | Every row of the absorption map marked done; a month of daily use with Python uninstalled on the personal MBP; then tag the last Python release and archive `backend/`, `src/`, `src-tauri/` | Absorption map; a month | not started |
| 6 Award-level polish | Patrick's sign-off per tweak; a beta to the existing testers | Per tweak | not started |

Stop rule: if the Phase 2 parity gate fails twice, stop and reassess before any ingestion or migration work.

## Phase 1 device script (TestFlight build 5)

Build number 5 is set on `dev` (app and widget targets together). Patrick pulled `dev`, built to the phone from Xcode and ran the script (passed 2026-11-16). The TestFlight upload (dev → master PR, archive from master) is deferred until Patrick asks for one.

1. Open the app; the answer model loads at open as before.
2. Ask one question in a synced notebook; citations open; answer length and read-aloud work.
3. Make one of each Studio kind: briefing, quiz, flashcards, audio overview, visual.
4. Record a five-minute meeting from the Lock Screen widget; review; save in a notebook.
5. Ask again and lock the phone mid-answer; unlock; the app is still responsive and the partial answer kept.
6. Sync once more; nothing duplicates.

## Phase 2 result: PASS (2026-11-16)

Patrick ran the parity fixture (500 chunks, 50 questions from the production library) in Settings › Diagnostics on the MBP (M5 Pro, 48 GB, macOS 27.3), compute GPU, one pass. Report: `reports/2026-11-16-mac-parity-report.json`.

| Bar | Result |
|---|---|
| Mean cosine ≥ 0.999 | 0.99995 |
| Worst cosine ≥ 0.998 | 0.99975 |
| Top-1 same ≥ 95 % / top-5 overlap ≥ 90 % | 98 % / 97 % |
| Errors | 0 |
| Over the 512-token cap | 0 |
| Tokenizer identical | 497 of 497 |

497 items in 39 s, 0.15 ms per token. The ten top-5 disagreements are order swaps among the same passages (ranks 4 and 5), the same pattern the phone showed. The week of daily use was dropped by Patrick the same day; Phase 2 is CLOSED and the cleanup list he sends replaces it.

## Phase 3, slice 8: the notes editor (2026-11-17, on `dev`, untested on a Mac)

Swift: `MarkdownEdit` (Core, pure, with `MarkdownEditTests`), `note_text` table in `LibraryStore` (a note's text as written, preferred over the joined chunks), `NoteTextView` + `NoteEditorBridge` (Mac), `NoteEditor` rebuilt around them, Edit from the Notes pane (⌘E, context menu) for notes not yet on the paired Mac, a filter field over the notes list. Device script: ⇧⌘N; type a title and a few lines; select a word and press ⌘B (stars appear dimmed, the word bold); ⇧⌘8 on two lines (bullets, tinted); Return at the end of a bullet carries the bullet on, Return on the empty bullet ends the list; the Heading menu makes the line large; ⌘K with a copied address wraps the selection as a link; type `- [ ] milk`, click the box to tick it; Save; the note reads back with the same Markdown (headings and bullets rendered); ⌘E reopens it with the text exactly as typed; after an edit, ask a question the new text answers.

## Phase 3, slice 7: the ingestion diff tool (2026-11-17, on `dev`, untested on a Mac)

Python: `scripts/ingestion_diff_export.py` (read-only: the old app's readers and chunker over one folder, writing `ingestion-fixture.json` into it; no notebook, no Keychain). Swift: `IngestionDiff` (Core, pure, with `IngestionDiffTests`), `LibraryModel.runIngestionDiff` (Engines), Settings › Diagnostics › Ingestion diff. Device script: put 10 to 50 documents of mixed kinds in one folder (PDFs with and without a text layer, Word, .xlsx, .pptx, .csv, Markdown, an .epub, an .xls); on the Python Mac `cd backend && .venv/bin/python ../scripts/ingestion_diff_export.py <folder>`; in the Mac app Settings › Diagnostics › Ingestion diff › Open fixture… (the JSON in that folder), Compare now; Open report. The gate wants every file that is not "identical" explained in the report's Differences section (a PDF heading the font pass missed, a table pymupdf4llm drew, a Word paragraph break). The summary line's identical rate is the number to carry into the gate record.

## Phase 3, slice 6: originals, watched folders, Settings › Library (2026-11-17, on `dev`, untested on a Mac)

Swift: `LibraryStore` (`source_text.file`, `watched_folders`, `watched_files`, counts, empty vectors), `LibraryModel.ingestFile(keep:)`, `LibraryMaintenance.swift` (re-index, repair, watch, scan), `WatchFolderSheet`, `LibrarySettings`, Quick Look in `SourceDetail`. Device script: drop a PDF, select it under Sources and press Space (Quick Look opens the kept copy), click the folder button (Finder shows `Sources/<notebook>/`); File › Watch Folder…, pick a folder with two readable files, Watch, expect both under Sources within a minute; copy a third file into the folder and expect it within five minutes (or Settings › Library › Scan Now); Settings › Library shows the counts and the folder's last scan; Re-index with the rewrite toggle off finishes with "Done: N".

## Phase 3, slice 5: the remaining readers (2026-11-17, on `dev`, no device gate by Patrick's choice)

Swift: `OLE2`, `XLSReader`, `PPTLegacyReader`, `JupyterText` (Core) with `XLSReaderTests` (`Fixtures/xls_golden.json` from `scripts/ci/xls_golden.py`) and `JupyterTextTests` (`Fixtures/jupyter_golden.json` from `scripts/ci/jupyter_golden.py`); `EPUBReader`, `XHTMLText`, `ODSReader`, `SVGText` (Engines). When Patrick gets to it: drop an .xls, a .ppt, an .epub, an .ipynb, an .ods and an .svg on a notebook; each should become a source rather than wait in the Inbox, and the .xls should read like the same book saved as .xlsx.

## Phase 3, slice 4: web pages and YouTube (2026-11-17, on `dev`, untested on a Mac)

Swift: `WebExtract`, `PageLoader` (WebKit + `Resources/Readability.js` + `Resources/PageText.js`), `YouTubeTranscript` (Engines); `LibraryModel.ingestLink`; the Mac's `IntakeModel.take(links:)`, `AddLinkSheet`, ⌘V with a copied address, File › Add Web Page… (⇧⌘L). Device script: drag a link from Safari's address bar onto a notebook and expect the status capsule to walk through loading, cutting, summarising, questions and embedding, then the page's title under Sources with its text in the reader (one block per line, image captions at the end); ⇧⌘L, paste a YouTube address, Add, and expect the video's real title and a transcript; drop a link to a PDF and expect the file read as a PDF. Add the same page in the old app and compare the passage counts; a gap of more than a few passages is a text diff to explain.

## Phase 3, slice 3: scanned PDFs, spreadsheets, PowerPoint (2026-11-17, on `dev`, untested on a Mac)

Swift: `TextExtract` (its own file; async; heading pass from fonts; Vision for pages with no text layer), `TabularText` + `CSV` (Core) with `TabularTextTests` against `Fixtures/tabular_golden.json` (written by `scripts/ci/tabular_golden.py` with pandas and the old app's function), `XLSXReader` and `PPTXReader` over ZIPFoundation (Engines). Device script: drop a scanned PDF (a photographed page saved as PDF) and expect text under Sources; drop an .xlsx with two sheets and expect "=== Sheet:" markers and "Row N:" lines in the reader, then ask a question a row answers; drop a .pptx with speaker notes and expect "=== Slide N ===" and "Speaker Notes:" in the reader. For each, drop the same file on the old app and compare the passage counts.

## Phase 3, slice 2: the chunker and the first readers (2026-11-16, on `dev`, untested on a Mac)

Swift: `Chunker`, `TokenBudget`, `PyText` (Core, pure), `ChunkerTests` against `Fixtures/chunk_golden.json`; `TextExtract` and `LibraryModel.ingestFile/ingestText` (Engines); `LibraryStore.addLocalSource`; the Mac's `IntakeModel` sends PDF, Word, RTF, ODT and HTML through it and drains the Inbox. Device script: drop a PDF on a notebook; expect the status capsule to walk through reading, cutting, summarising, questions and embedding, then the file under Sources with its page count of passages and the summary line; ask a question only that PDF answers and expect it cited. Drop the same PDF on the old app and compare the passage count (Sources panel); a difference is a chunk-text diff to explain.

## Phase 3, slice 1: Move my library here (2026-11-16, on `dev`, untested on a Mac)

Backend: `backend/api/migrate.py` (loopback + app token, read-only; tests in `tests/test_migrate.py`). Swift: `LibraryImport` (Engines), `LibrarySync.run(store:call:origin:)`, `source_text` and `imported_notes` tables, `ImportSheet` (Settings › Sync, Notebook › Move My Library Here…, the pairing sheet and the empty sidebar). Device script: open the old LocalBook; in the new app choose Move My Library Here…, pick `~/Library/Application Support/LocalBook`, Move; expect every notebook (device-only ones included) in the sidebar, full text in a source's reader, the old notes under Notes, Studio items on the shelf. Run it twice: the second run copies nothing.

## Phase 2 gate script (Mac app from `dev`)

1. On the Python Mac: `cd backend && LOCALBOOK_USE_PRODUCTION_DATA=1 .venv/bin/python scripts/export_cortex_parity.py` (the same fixture the phone used; it writes `~/Desktop/localbook-parity/parity_fixture.json`, read-only on the library, no Keychain).
2. In the Mac app: Settings › Diagnostics › Open fixture…; Prepare EmbeddingGemma 2 (the app's own search model is reused when it is loaded on the Neural Engine); leave "Also check this Mac's tokenizer" on; Run now. Pass = all five bars green. Save the report (it is kept under `User Files/Diagnostics/`) and paste its verdict here.
3. When the parity run passes, the next slice is the one-click library import (decision record, 2026-11-16), so the week of daily use runs on the whole library, not only the synced notebooks.
4. ~~Daily use for a week~~ Dropped by Patrick on 2026-11-16: he already has a list of tweaks after the first day, so the parity run alone closes Phase 2 and the tweaks come in as cleanup.
   Daily use for a week (as written): ask in each notebook you work in, make one Studio item, record one meeting, drop a file on a notebook, switch the answer model once in Settings › Models. Keep a list of everything you reached for that was not there or did not behave; that list closes or extends the phase.
