# 02. Gate log

Each phase ends with a measurable gate, run in CI or on Patrick's device, with the result written here. Device
gates are 30-minute scripts; before each one Patrick runs `git pull origin dev` and `git log --oneline -1`.

| Phase | Gate | How it is run | Result |
|---|---|---|---|
| 0 Set up | CI green on the package (macOS build + Swift Testing, iOS build of the shims) and the Mac app; Patrick approves `01-absorption-map.md` and `03-mac-design-profile.md` | `.github/workflows/swift.yml`; Patrick reads the two pages | **CLOSED 2026-11-13.** Patrick approved both pages ("both viewed and agreed too"). CI run 1 (d96f974): package build + 6 Swift Testing cases pass on macOS 26 (Xcode 26.6), shims build for iOS; the Mac app job failed only on macro trust (`-skipMacroValidation`). CI run 2 (46a37c4): **all three jobs green** (package build + tests 8 min, iOS shims 2 min, Mac app 8 min). |
| 1 Extract the package | TestFlight build 5 behaves identically on Patrick's phone (Ask, one of each Studio kind, a 5-minute meeting, lock mid-answer); CI green | Device script below; CI | **CLOSED 2026-11-16.** Device half: Patrick ran the script on build 5 built from `dev` and reported all testing complete (2026-11-16); the TestFlight upload is deferred by his choice ("We wont do a testflight for this yet"), so no dev → master PR for Phase 1. CI half met 2026-11-13, run 12 (aed09c0): all four jobs green — package build + 10 Swift Testing cases, shims for iOS, engines + Mac app, and the iPhone app against the package (on the runner's Xcode 26.6 only the app's iOS 27 AVFAudio calls cannot compile; `scripts/ci/ios-sdk-gap.py` lets exactly those through). |
| 2 Mac app v0 as a peer | `ParityRunner` passes at the phone's bar against the Python Mac; Patrick uses the Mac app daily for a week and lists what he reached for | Parity harness in the Mac app's Diagnostics; Patrick's list | **open** (opened 2026-11-16). All five slices on `dev` by 2026-11-16, CI run 21 (a5fd6ba) green: 1 pair + sync + Ask (fd9f4c4); 2 Sources, inspector, Studio (c79684e); 3 read-aloud, Meetings, voice notes, Notes (2df5bae); 4 LLM Studio + Diagnostics parity (d34645c); 5 drag-and-drop, Studio windows, App Intents (874f1b3). Gate steps are the Phase 2 script below; neither half run yet. |
| 3 Ingestion and library | Personal library migrated (19 notebooks, 41,970 chunks) with 95% identical citations on the fixture and every difference explained; 50-document ingestion diff; the Mac app answers with Python stopped; visuals golden set approved side by side | Migration tool report; diff scripts; Patrick | **prepared, not run** (2026-11-17): every tool is on `dev`, CI green at 2973aea (run 63, after the offline-first edits); the Phase 3 gate script below needs only Patrick's Mac |
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

## Phase 3, sitting B findings (2026-11-19, on `dev`)

B2 (Add Web Page: YouTube and arXiv), B3 (Find Sources) and the diff export passed. B1 failed again: a Safari address-bar drop reached the intake as a provider with no registered types at all ("The drop offered nothing this app can add (no types)"), so the earlier fix, which read every type the provider offered, had nothing to read. The drop handler now reads the drag pasteboard itself first (`NSPasteboard(name: .drag)`: file URLs, `public.url`, text), which always carries the types, and logs them; the providers are the fallback. B4's fixture is written beside the list file (`testdata/phase3/ingestion-fixture.json`); the plan now says so and the script prints the path.

## Phase 3, sitting A findings (2026-11-18, on `dev`)

Patrick ran sitting A of the condensed plan away from his paired Mac: files, questions, notes and export all passed; four things to fix, done in one batch:

- A new notebook lands in the section it was made from (the section header's menu and a notebook's menu pass the section; ⌘N and the empty states make it unsectioned).
- The intake's floating capsule, which sat over the Ask field, is gone. `IntakeStrip` is a slim bar docked under the pane (`safeAreaInset`, so the Ask field moves up): the notebook it is adding to, the stage in words, "n of N" and a thin bar; at the end, a green or orange result with the count, the first reason when something could not be read, a Show Sources button and a dismiss.
- The conversation is one per notebook again: the Ask pane shows only the turns asked in the notebook on screen (the old app's rule, which the running conversation across switches had broken on the Mac; the phone's Ask keeps its own behaviour for now).
- Spreadsheet SQL: the prompt says one table unless a JOIN is needed and forbids prose in the statement; a statement SQLite refuses is sent back once with its error, as the old app did. Patrick's log showed "SELECT SUM(t1.arr) FROM … assuming t1.state = t2.state", which fell through to the passages.
- From the log: the notebook pickers' first frame selected "" with no tag (a hidden "Choose a notebook" row now carries it); the other lines (linguistic-data asset locks, glass-window key warnings, a layout-recursion note from the text view, a view-bridge hint from the save panel) are macOS noise.

## Phase 3, offline-first edits (2026-11-18, on `dev`, untested on a Mac)

Patrick, away from his paired Mac, could not make a notebook: every notebook, section and source edit went to the Mac first and the two-minute timeout came back as "The Mac couldn't do that". His rule, now standing: the apps are offline-first; no local action may depend on anything remote; sync is a handshake that merges differences. What changed, in `LibraryEdits` (Engines), `LibraryStore+Edits` (Core, tested in `LibraryEditsTests`) and `MacLink`:

- Every edit is made on this device at once: notebook create/rename/color/delete, sections (by name here; the Mac gives them ids when the change reaches it), source delete/move/rename, editing a note the Mac already has (a fresh note is made and the Mac's copy of the old one is queued for deletion, because the Mac keeps the first version of a note id).
- A paired Mac hears about each change through a queue (`pending_edits`), sent oldest first the next time it answers, before a sync reads its manifest, so the Mac's list already carries the change. A change the Mac refuses (a notebook it does not share) is dropped with a trace line and shown once in Settings › Sync; a connection failure leaves the rest waiting.
- A notebook made here gets a UUID like the Mac's and a `local_notebook:` mark; when the Mac creates it, the Mac's id replaces the local one in every table, the kept-originals folder, the tabular catalog, Studio items and the queued edits. Sync never removes a notebook made here, and its notes wait to be sent until it exists on the Mac.
- Reaching the Mac is tested first with a five-second hello, so away from it the status turns orange in seconds instead of two minutes. `isUnreachable` now reads the code the link wraps into its error text (the earlier check never matched, which is why the friendly "Mac not reachable" line never appeared).
- The Mac app has no `paired` gate left on an edit: New Notebook is in the empty sidebar and the Ask pane, Move to is always on, notes can be edited after they were sent. The iOS app shares the same model code, so rename and note edits there work away from the Mac too; the phone still has no notebook-creation UI (it never had one) and still onboards by pairing, noted below as the one restriction left for Patrick to decide on.

## Phase 3, slice 8: the notes editor (2026-11-17, on `dev`, untested on a Mac)

Swift: `MarkdownEdit` (Core, pure, with `MarkdownEditTests`), `note_text` table in `LibraryStore` (a note's text as written, preferred over the joined chunks), `NoteTextView` + `NoteEditorBridge` (Mac), `NoteEditor` rebuilt around them, Edit from the Notes pane (⌘E, context menu) for notes not yet on the paired Mac, a filter field over the notes list. Device script: ⇧⌘N; type a title and a few lines; select a word and press ⌘B (stars appear dimmed, the word bold); ⇧⌘8 on two lines (bullets, tinted); Return at the end of a bullet carries the bullet on, Return on the empty bullet ends the list; the Heading menu makes the line large; ⌘K with a copied address wraps the selection as a link; type `- [ ] milk`, click the box to tick it; Save; the note reads back with the same Markdown (headings and bullets rendered); ⌘E reopens it with the text exactly as typed; after an edit, ask a question the new text answers.

## Phase 3, slice 13: site search and source discovery (2026-11-17, on `dev`, untested on a Mac)

Swift: `SiteSearch` (Core, pure, `SiteSearchTests`: each site's request and the reading of its reply on saved replies) for arXiv, Wikipedia, Hacker News, GitHub, Reddit, Stack Overflow, Semantic Scholar, OpenAlex and PubMed; `SourceDiscovery` (Core, pure, `SourceDiscoveryTests`: the old intent prompt cut to topic, purpose, terms and sites; the plan read from the model's JSON with a fallback to the sentence itself; the merge that interleaves sites and drops a repeated address or title); `SiteSearchClient` (Engines, URLSession, 12 s a site, PubMed's two calls) and `LibraryModel.discoverSources`; the Mac's File › Find Sources on the Web… (⇧⌘F, `FindSourcesSheet`) with Search and Discover modes, a checklist of hits, and Add N Sources through the link intake. YouTube and the Brave fallback wait for Settings › Keys. Device script: ⇧⌘F; Search "sparse attention" on arXiv, past year: paper titles with authors and dates, the link icon opens the PDF; All sites for "swift concurrency": rows from several sites interleaved, no address twice; Discover "Track Costco": the plan line names Costco, company research and three terms; tick two rows, Add 2 Sources: both appear in the notebook's sources and answer a question. Pass: each site returns rows or a named problem (no silent empties), and an added hit is a normal web source.

## Phase 3, slice 14: spreadsheet questions by SQL (2026-11-17, on `dev`, untested on a Mac)

Swift: `TabularSQL` (Core, pure, `TabularSQLTests`: intent, names, column typing, the SELECT check, the prompt, the answer) and `TabularStore` (Core, `tabular.sqlite` beside the library, typed tables + catalog, read-only execution with a row cap); `LibraryModel.indexTables` after a spreadsheet or CSV is read, `answerFromTables` ahead of retrieval in Ask. Device script: drop an .xlsx with a few hundred rows and a State-like column; ask "How many rows are in Texas?" (or the column's own value). Pass: the answer is a bold number computed by SQL, the status line shows the SELECT, the single citation names the file. Ask "Why …" about the same file: the passages answer as before. Delete the source: the tables go with it.

## Phase 3, slice 12: the visual golden set (2026-11-17, on `dev`, untested on a Mac)

Pick one notebook with real content. Old app's Mac: `python scripts/visual_golden_export.py <notebook id> ~/Documents/visual-golden` (the old app open; reads its per-launch token from its data folder), then open `old-app-visuals.html`. Swift app: Settings › Diagnostics › Visual golden set › choose the notebook › Draw the 15 (one Visual's time each), Open index. Pass: 15 pictures; the judgement line in `index.md` filled in: how many of the 15 pairs the Swift drawing says what the old one said, as clearly. The row goes to **done** at 12 of 15 or better with no layout always behind; a layout always behind gets a redesign before the row closes.

## Phase 3, slice 11: PDF ruled tables (2026-11-17, on `dev`, untested on a Mac)

Drop a PDF with a ruled table (an invoice, a paper with a results table). Pass: the source's text shows the table as a Markdown table (`|a|b|`, a `|---|---|` rule, one row per line) at the end of that page's text, and the cell words are not repeated in the prose above it. A page takes a few hundred milliseconds more; a page that takes over six seconds keeps its prose and no table. Compare against the old app's text for the same PDF with the ingestion diff; pymupdf4llm wrote the same table shape.

## Phase 3, slice 10: the 30-URL web diff (2026-11-17, on `dev`, untested on a Mac)

The same tool as slice 7, pointed at addresses. On the Python Mac: `cd backend && .venv/bin/python ../scripts/ingestion_diff_export.py ~/path/urls.txt` (one address per line, `#` comments; the old app's scraper reads each, chunks it as a web or YouTube source under its title). The fixture lands beside the list. In the Swift app: Settings › Diagnostics › Ingestion diff › Open fixture…, Compare now, Open report. Each address is read here with `WebExtract` and chunked the same way. Pass: the report's identical rate and the first differences, which decide whether the Readability + `PageText.js` shape needs a rule or a hatch (absorption map, web pages row). Pages that change between the two reads (news fronts, feeds) differ for that reason alone: prefer articles and documentation for the 30.

## Phase 3, slice 9: export (2026-11-17, on `dev`, untested on a Mac)

File › Export Notebook… (⇧⌘E) or a notebook's context menu › Export…: Markdown (the old app's shape), a web page, or a folder with `Sources/<name>.md`, the kept originals and `Notes/`. A source's context menu: Export Text…, Export Original…. Pass: the files open with the text as the reader shows it; the folder export carries every source.

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

## Phase 3 gate script (Mac app from `dev`, the old app on the same Mac)

Before: `git pull origin dev && git log --oneline -1`; build the Mac app. Allow about an hour; the four parts can be run on different days.

1. **Citation fixture (old app's Mac).** Quit the old app (both would hold the embedder). `cd backend && LOCALBOOK_USE_PRODUCTION_DATA=1 .venv/bin/python ../scripts/citation_gate_export.py` → `~/Desktop/localbook-parity/citation-fixture.json`: for every notebook, up to ten questions really asked there and what the old app cited (retrieval only, rule-based analysis, no Keychain, nothing written to the library). Read-only; a minute or two.
2. **Migrate.** Open the old app again. Swift app: Notebook › Move My Library Here…; wait for the report (19 notebooks, 41,970 chunks expected; the counts are in the sheet). Settings › Library shows the counts.
3. **Citation gate (Swift app).** Settings › Diagnostics › Citation gate › Open fixture… (the file from step 1) › Compare now. Each question is asked of the migrated copy with Ask's retrieval and compared with the old app's citations; a report lands under User Files/Diagnostics/ (`citation-gate-<stamp>.md`). **Pass: "Cited the same" at or above 95 %.** Below it, the report's "To explain" section lists each question with both sides' citations; every row gets a line in this log.
4. **Ingestion diff.** The 50-document folder (`scripts/ingestion_diff_export.py` on it with the old app's Python; `ingestion-fixture.json` lands beside the files) and the 30-address list (the same script on `urls.txt`); Settings › Diagnostics › Ingestion diff on each. **Pass: identical chunks on every file both sides read, every other verdict explained in this log.**
5. **Python stopped.** Quit the old app. Swift app: ask one question in three notebooks (one migrated, one with a source added here, one with a spreadsheet); make one Visual; export one notebook. **Pass: every answer cites, nothing waits on the Mac link** (the sidebar may say "Mac not reachable", which is right).
6. **Visuals.** The slice 12 script (golden set drawn by both apps, judged side by side). **Pass: 12 of 15 pairs as good or better, no layout always behind.**

Paste the three reports' verdict lines and the judgement here; the row above goes to CLOSED when all four pass.

## Phase 2 gate script (Mac app from `dev`)

1. On the Python Mac: `cd backend && LOCALBOOK_USE_PRODUCTION_DATA=1 .venv/bin/python scripts/export_cortex_parity.py` (the same fixture the phone used; it writes `~/Desktop/localbook-parity/parity_fixture.json`, read-only on the library, no Keychain).
2. In the Mac app: Settings › Diagnostics › Open fixture…; Prepare EmbeddingGemma 2 (the app's own search model is reused when it is loaded on the Neural Engine); leave "Also check this Mac's tokenizer" on; Run now. Pass = all five bars green. Save the report (it is kept under `User Files/Diagnostics/`) and paste its verdict here.
3. When the parity run passes, the next slice is the one-click library import (decision record, 2026-11-16), so the week of daily use runs on the whole library, not only the synced notebooks.
4. ~~Daily use for a week~~ Dropped by Patrick on 2026-11-16: he already has a list of tweaks after the first day, so the parity run alone closes Phase 2 and the tweaks come in as cleanup.
   Daily use for a week (as written): ask in each notebook you work in, make one Studio item, record one meeting, drop a file on a notebook, switch the answer model once in Settings › Models. Keep a list of everything you reached for that was not there or did not behave; that list closes or extends the phase.
