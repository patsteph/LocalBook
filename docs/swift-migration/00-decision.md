# 00. Decision record

*Opened 2026-11-13. Patrick approved the plan (revision 2) the same day and asked to start.*

## The decision

Build a native SwiftUI/AppKit Mac app around the engine the iPhone already runs, sharing one Swift package
(`swift/LocalBookCore`) between the two apps. The Tauri/React/Python Mac app keeps running unchanged as the
system of record until the Swift app passes the phone's `ParityRunner` bar and the 2026-10-23 sync checklist.
Every Mac feature is absorbed (`01-absorption-map.md`); when Python retires nothing is left on a someday list.

Not chosen: staying on Tauri and simplifying (the audit's three waves); a Swift shell over the Python backend
(keeps the second process, PyInstaller and the dual engine); a Rust core (only if LanceDB had to stay, and it
does not). The audit's Wave 2 and 3 refactors are cancelled; Python gets bug fixes only from today.

## Why

- The phone app, built in four weeks on a 11.9k-line Swift tree, "works incredible in practice" (Patrick). About
  8 to 9k of those lines compile on macOS with little or no change.
- The Mac app today is a web view in a Tauri window over a 170k-line Python backend: a second process, PyInstaller,
  an 11-second startup floor, and two engines to keep in parity forever.
- The apps Apple gives Mac awards to share seven traits (one idea done completely, platform conventions as
  features, speed you can feel, one codebase per Apple device, Apple frameworks as leverage, craft in small
  moments, AppKit where SwiftUI falls short). A Tauri shell gets none of them for free.

## What the six Mac checks settled (2026-11-13, Patrick's M5 Pro MBP, macOS 27.3, Xcode 27.2)

| Check | Result | Consequence |
|---|---|---|
| Ed25519 mutual TLS with swift-nio-ssl | Pass, both directions | No re-pair of any Mac. Needs `verifySignatureAlgorithms` to include `.ed25519` and `.noHostnameVerification` on the server |
| Vision `RecognizeDocumentsRequest` on PDFs | Pass | Paragraphs, lists, ruled tables and OCR from one API; 110 to 450 ms a page warm, 17 to 26 s cold, so warm it at launch and run it off the main actor with a timeout |
| Office import without Python | Pass (.docx, .doc, .pptx), partial (.xlsx) | AppKit reads Word with tables; Spotlight's importer reads PowerPoint text; spreadsheets get their own reader (CoreXLSX, libxls) |
| USearch | Pass with two Xcode 27 caveats | Not needed below about a million chunks; known fallback |
| Mail.app store | Blocked by TCC | Correspondent offers two paths: Full Disk Access to Mail's store, or SwiftMail IMAP |
| Brute-force vector scan | Pass | 0.29 ms a query at the largest notebook (11,451 rows); the phone's SQLite + Accelerate design is right for the Mac |

Library today: 19 LanceDB tables, 41,970 rows, 768-d f32 vectors.

## Toolchain, fixed

| Item | Value | Why |
|---|---|---|
| Xcode | 27.2 stable on Patrick's Macs; CI uses the newest stable 27.x the runner has, else the image default (Xcode 26.6, Swift 6.3.3 on the macos-26 image as of 2026-11-13) | 27.0 beta rejects swift-collections 1.7.6; GitHub's runner images had no Xcode 27 yet on 2026-11-13 |
| Swift language mode | 6 | strict concurrency from the first line |
| Minimum OS | macOS 26, iOS 27 | Vision document reader and SpeechAnalyzer need 26; the phone app is already on 27 |
| swift-collections | 1.3.0 exact | 1.7.x needs an experimental feature flag |
| mlx-swift | 0.31.x | the phone's pin |
| mlx-swift-lm | 3.31.4 exact | 3.32.x adds a FoundationModels adapter that does not compile against the iOS 27 SDK |
| FluidAudio | 0.17.6+ | Kokoro and Parakeet |
| SwiftPM build system | `--build-system native` where Xcode 27's new one misbehaves | USearch and NumKong failed to link under the new one |

Pins live in `swift/LocalBookCore/Package.swift` and `ios/LocalBookLab/project.yml`; change both or neither.

## Escape hatches (pre-approved, time-boxed, logged here)

1. In-process Python (PythonKit + Python-Apple-support) behind a Swift protocol.
2. A Rust static library behind a one-function C ABI.

| Date opened | Feature | Hatch | Why | Replace by | Closed |
|---|---|---|---|---|---|
| none yet | | | | | |

## Decisions taken while building

| Date | Decision | Why |
|---|---|---|
| 2026-11-13 | The package has two libraries, `LocalBookCore` (pure) and `LocalBookEngines` (MLX, Core ML, FluidAudio) | So CI can build and test the pure parts in minutes without compiling MLX, and so the iOS build of the shims runs on a runner that cannot run MLX |
| 2026-11-13 | Platform differences go through `Sources/LocalBookCore/Platform/` shims (`AppLifecycle`, `KeepAwake`, `PlatformImage`, `AppFolders`, `DeviceDescription`, `Haptics`); nothing else in the package imports UIKit or AppKit | One engine, two apps; the lock rule stays expressed once |
| 2026-11-13 | The Mac app's data lives under `~/Library/Application Support/LocalBook (Swift)/` until Phase 4 | The Python app's `LocalBook/` folder is never touched while the two run side by side; Phase 3's migration is one-way and the original stays |
| 2026-11-13 | The Mac app's bundle id is `com.patsteph.localbook.mac`; the iPhone keeps `com.patsteph.localbook.ios` | Separate from the Tauri app's id so both can be installed |
| 2026-11-13 | The Mac app's version starts at 3.0.0 | The Python app is on 2.5.x; the iPhone app follows the Mac's marketing version |
| 2026-11-13 | On the iPhone the package keeps the Lab's folder names (`Library/`, `LocalBookLab/`) | Phase 1 moves code, not data; TestFlight build 5 must find everything where build 4 left it |
| 2026-11-13 | The menu bar carries every planned action from day one, disabled until its phase lands | So the keyboard map and the app never drift; enabling the item is the last line of each feature's work |
| 2026-11-13 | `xcodebuild` in CI passes `-skipMacroValidation` as well as `-skipPackagePluginValidation` | mlx-swift-lm ships a Swift macro (`MLXHuggingFaceMacros`); an unattended build cannot click "Trust" |
| 2026-11-13 | `AppLifecycle.backgroundWithdrawsGPU` is true on iOS and false on macOS; the Mac may keep generating when not in front | The lock rule exists because iOS withdraws the GPU; the Mac does not. Phase 2 decides the Mac's policy with that fact, not by inheriting the phone's |
| 2026-11-13 | `LibraryModel` (the phone's orchestration: library, embedder, reranker, answer model, sync) lives in `LocalBookEngines`, not in the app | It is the engine the Mac app reuses in Phase 2; only its views stay per app |
| 2026-11-13 | Phase 1 leaves in the iOS app what is iOS-only or UI: all views, `MeetingRecorder`, `ReadAloud`, `SpeechAudio`, the audio-overview player, `ParityModel`/`BackgroundParity`, `GemmaBench`, the Speech lab, the widgets | Phase 1 moves the engine with no behaviour change; the Mac grows its own views and audio session handling in Phase 2 |
| 2026-11-13 | Moved code was made `public` with `scripts/swift/make_public.py` (type- and module-scope declarations only), then hand-fixed where the compiler asked | The phone's code was one module with internal access everywhere; the script is the audit trail of what became API |
| 2026-11-13 | Public structs the apps construct get an explicit `public init` with the memberwise shape; public value types the apps pass across actors are explicitly `Sendable`; a conformance a package type needs (`Identifiable`, …) is declared in the package, never retroactively in an app | Across a module boundary Swift gives public types neither a public memberwise init nor implicit `Sendable`; `public init() {}` also removes the memberwise init; retroactive conformances cannot satisfy public requirements |
| 2026-11-13 | The iOS app CI job runs `scripts/ci/ios-sdk-gap.py` when `xcodebuild` fails: it passes when every error is one of the app's known iOS 27 AVFAudio calls that the runner's Xcode 26.6 lacks, and fails on anything else | GitHub had no Xcode 27 runner image on 2026-11-13; the job's purpose is to prove the app builds against the extracted package. Remove the step when the runner ships Xcode 27 (the build then simply succeeds and the step is never reached) |
| 2026-11-16 | The Mac app pairs with the Python Mac as a phone-class peer: `PhoneIdentity` gives it a `mac-` device id and the Mac's own name, and the Python host lists it as "<name> (phone)" | The host's phone routes are the proven link; the host keys nothing on the id prefix. The host's label is cosmetic and changes when the Swift app becomes the host in Phase 4 |
| 2026-11-16 | On macOS every Keychain call in `PhoneIdentity` sets `kSecUseDataProtectionKeychain` | The sandboxed Mac app uses a Secure Enclave key, which lives only in the data-protection Keychain; the legacy file Keychain would refuse it (-34018/-50). iOS has only the one Keychain, so nothing changes there |
| 2026-11-16 | `GPUGuard.protect`, `GPUGuard.canStart` and `GPUGate.installReopenOnActive` do nothing on a platform where `AppLifecycle.backgroundWithdrawsGPU` is false | The Mac keeps answering behind other windows; switching apps must not stop an answer. The phone's behaviour is unchanged |
| 2026-11-16 | `AnswerText` (the answer renderer, `[n]` citations as `lb-source://n` links) lives in `LocalBookCore` | Both apps render an answer the same way |
| 2026-11-16 | The Mac app's reranker resources are the iPhone app's files, referenced by relative path in `mac/LocalBook/project.yml`; CI strips the two optional entries as the iOS job does | One copy of the model and tokenizer; the model stays uncommitted (64 MB) |
| 2026-11-16 | Phase 2 lands in slices on `dev`, each one Patrick can build and use; the first is pair, sync and Ask | Patrick's week of daily use needs a usable app early; the gate closes on the whole phase |
| 2026-11-16 | Sources on the Mac are a pane (View › Show Sources, ⌥⌘1) and an inspector (⌥⌘I), not a sidebar row; a source opens in its own window (⌘↓) via `WindowGroup(for: SourceWindowKey.self)` | The design profile's map: panels become places. Quick Look (Space) waits for Phase 3, when sources have files on this Mac rather than text in the library |
| 2026-11-16 | The Mac app's Studio pane is the phone's Studio code moved over (shelf left, item right); items it makes are sent to the Python host over the phone route like the phone's, and Mac-made items are pulled the same way | One engine, one sync path until Phase 4; the Canvas on the Python host sees everything the peer makes |
| 2026-11-16 | Meetings, Notes and read-aloud on the Mac are the phone's `MeetingSession`, `Dictation`, `MediaTranscriber`, `NoteEditor` and `ReadAloud` moved over, minus the audio session, interruptions, lock-screen controls and model pausing | The Mac keeps the answer model loaded while a meeting is written up; it has no audio session or lock screen. Recordings live in `~/Library/Application Support/LocalBook (Swift)/User Files/Meetings` |
| 2026-11-16 | The Mac app links FluidAudio directly (same pin as the package) for the voice and the speaker models, as the iPhone app does | `KokoroAneManager` and `OfflineDiarizerManager` are used from the app's own views, not only inside the package |
| 2026-11-16 | The answer model is chosen through `AnswerModels` (UserDefaults `answerModelId`, default `mlx-community/gemma-4-e4b-it-4bit`); `LibraryModel` loads `AnswerModels.configuration` everywhere it used the registry entry | LLM Studio on the Mac can switch models without touching the engine; the phone keeps the default and is unchanged |
| 2026-11-16 | Models live in the Hub cache the MLX loader uses (`Library/Caches/huggingface/hub` in the sandbox); the Locker lists that cache and Browse downloads into it with the loader's own file patterns | One copy of every model, found by both the loader and the Studio |
| 2026-11-16 | "Will it fit" = weights + max(2 GB, 20 %) against Metal's recommended working set less 1.5 GB for the search model, reranker and the app | The Python app's GPU budget bar, reduced to one line per model; a 48 GB Mac and an 18 GB Mac get different answers for the same reasons |
| 2026-11-16 | Drag and drop in Phase 2 goes through the phone's note route: text, text files, pictures (Vision), audio and video (transcript) and web addresses become notes; PDF, Word, EPUB and the rest wait in `User Files/Inbox/<notebook>/`, listed in the Sources pane, for Phase 3 to turn into sources | The Python Mac stays the system of record for sources; nothing dropped is lost, and nothing pretends to be indexed when it is not |
| 2026-11-16 | Studio items open in their own window like sources (`WindowGroup(for: StudioWindowKey.self)`, Open in Window / Window › Open in New Window ⇧⌘D); App Intents (Record a Meeting, Ask LocalBook, New Note) and `localbook://record|ask?q=|note?text=` queue through `LaunchRequests` until the main window is up | The design profile's "detach" and the phone's intents, on the Mac |
| 2026-11-16 | The one-way library import moves up: it is the first slice after the Phase 2 parity gate passes, not a Phase 3 item. One click in the Mac app ("Move my library here") finds the Python LocalBook on the same Mac, copies every notebook including local-only ones and the source files, and leaves the original untouched; pairing with the Python app is a development path only | Patrick's point: no user would pair two apps on one Mac to migrate. The import copies existing chunks and vectors, so it does not depend on the Swift chunker or the 95 % citation gate, which still guard Phase 3's own ingestion |
| 2026-11-16 | Edits a peer makes to the library (delete and move sources; new, rename, colour, section and delete notebooks; sections) go through new signed phone-link routes that run the desktop's own handlers (`services/sync/phone_edits.py`), only on notebooks the peer can see; the Swift app changes its copy after the Mac says yes, then syncs | Patrick's first-day list: the sidebar had lost the desktop's abilities. The Python Mac stays the system of record, so the Swift app asks it rather than writing LanceDB; the same routes serve the phone |
| 2026-11-16 | "Move my library here" (Phase 3, slice 1): the Swift app reads the old app's `.app_token` from the folder the user points it at once, then runs the same sync as the phone link against the old app's loopback `/migrate/…` routes for every notebook, device-only ones included, and adds each source's whole text, the Canvas notes and the Studio items. Notebooks the link's manifest never lists are marked `imported:<id>` so a later link sync keeps them | Patrick: no user migrates by pairing two apps on one Mac. The old app stays the system of record and untouched; the copy is one-way and can be re-run (diff-only). Lance is not read directly: the old app already knows how to send its chunks and vectors |
| 2026-11-16 | Sidebar notebook sections fold: click a section header to collapse it, the fold is remembered on this Mac (`sidebar.collapsedSections`), and a section never touched here follows the old app's fold for it | Patrick: not every notebook should be visible at once. Writing the fold back to the old app waits; in Phase 4 the Swift app owns it |
| 2026-11-16 | Phase 3, slice 2: a dropped PDF, Word, RTF, OpenDocument or HTML file becomes a source on this Mac (`LibraryModel.ingestFile`), cut by a line-for-line Swift port of the old app's chunker and token budget, summarised and questioned (HyDE) by the answer model with the old app's prompts, embedded with the document template, and stored as a local source (`local:file-…`, with its whole text) that sync never removes. The port is proved by a golden fixture the Python functions write (`scripts/ci/chunk_golden.py`), not by reading the code | Identical chunking is the Phase 3 gate's first condition: a citation has to land on the same passage on every device. The Python Mac still owns sources until Phase 4, so sources made here stay here until then, as phone notes did |
| 2026-11-16 | Three quiet ways to add files besides dropping them on a notebook or the conversation: File › Add Sources… (⌘O, the standard open panel), ⌘V with files copied in the Finder, and "Open with LocalBook" / a drop on the Dock icon (the app declares the readable kinds as documents at Alternate rank, so it never becomes a file's default app). Files that arrive without a notebook on screen get one small sheet: a notebook picker and Add | Patrick: another way to upload that is intuitive but not bold. Each of the three is a Mac convention the user already knows, with no new control in the window |
| 2026-11-17 | Phase 3, slice 4: a web address becomes a source. The page loads in an off-screen `WKWebView` (scripts run, cookies die with the load) and Mozilla's `Readability.js`, shipped as a package resource under its Apache licence, finds the article; `PageText.js` writes it in trafilatura's shape with the old image-reference section, and keeps the whole page when the article is under a fifth of it. YouTube gives its transcript through the same Innertube calls the old library made. Links arrive by drop, ⌘V from a browser, and File › Add Web Page… (⇧⌘L); one this Mac cannot read stays as a note holding the address | The old app's Playwright + trafilatura pair had no Swift twin; WebKit is the renderer the Mac already has, and Readability is the extractor Safari's Reader descends from. Equivalence is by shape, not bytes: the chunker sees the same block-per-line text, and the 30-URL diff in the absorption map decides whether a hatch is needed |
| 2026-11-17 | Phase 3, slice 5: the remaining readers land without a device gate. `OLE2`, `XLSReader` and `PPTLegacyReader` live in LocalBookCore (pure Foundation) so Swift Testing can prove them; `XLSReader` is held to xlrd + pandas by `xls_golden.json` (xlwt writes the books), `JupyterText` to nbformat by `jupyter_golden.json`. EPUB, ODS and SVG readers sit in Engines over ZIPFoundation with no fixture | Patrick (2026-11-17): rare formats do not need his sign-off at ~90 % confidence; he tries them later. A golden test where Python can write the input is the stand-in for his test; the zip-based readers follow the proven patterns of the OOXML readers |
| 2026-11-17 | Phase 3, slice 6: a source read on this Mac keeps its file (`Sources/<notebook>/<id> <name>` under the app's files; a watched folder's file stays put and is remembered by path), shown with Quick Look and the Finder. Watched folders are polled every five minutes with a `(mtime, size)` ledger, as the old app did, with a security-scoped bookmark per folder; nothing is ever written into them. Settings › Library gets the counts, the folders, Re-index and Repair empty vectors. Audio and video drops become sources (transcript) rather than notes | The gate needs the Mac to own its sources: a file the user can open again, an index the user can rebuild, and the old app's linked folders. Polling over FSEvents for the reasons `folder_watcher.py` gives |
| 2026-11-17 | Phase 3, slice 7: the gate's 50-document ingestion diff is a tool, not a one-off. `scripts/ingestion_diff_export.py` records what the old app's readers and chunker make of a folder (`ingestion-fixture.json`, beside the files); Settings › Diagnostics › Ingestion diff reads the same files here with `TextExtract` and `Chunker`, compares line bags and exact chunks (`IngestionDiff`, pure, tested), and writes a JSON and a Markdown report under `User Files/Diagnostics/` with the differences first | The gate asks for every difference explained, which needs a report a person reads, repeatable after each reader fix. Lines are compared as bags with whitespace folded so that spacing never counts; chunks must match byte for byte, since a citation lands on a chunk |
