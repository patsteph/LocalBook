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
| Xcode | 27.2 stable | 27.0 beta rejects swift-collections 1.7.6; CI selects the newest stable 27.x it has |
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
| 2026-11-13 | `AppLifecycle.backgroundWithdrawsGPU` is true on iOS and false on macOS; the Mac may keep generating when not in front | The lock rule exists because iOS withdraws the GPU; the Mac does not. Phase 2 decides the Mac's policy with that fact, not by inheriting the phone's |
