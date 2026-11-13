# 02. Gate log

Each phase ends with a measurable gate, run in CI or on Patrick's device, with the result written here. Device
gates are 30-minute scripts; before each one Patrick runs `git pull origin dev` and `git log --oneline -1`.

| Phase | Gate | How it is run | Result |
|---|---|---|---|
| 0 Set up | CI green on the package (macOS build + Swift Testing, iOS build of the shims) and the Mac app; Patrick approves `01-absorption-map.md` and `03-mac-design-profile.md` | `.github/workflows/swift.yml`; Patrick reads the two pages | **CLOSED 2026-11-13.** Patrick approved both pages ("both viewed and agreed too"). CI run 1 (d96f974): package build + 6 Swift Testing cases pass on macOS 26 (Xcode 26.6), shims build for iOS; the Mac app job failed only on macro trust (`-skipMacroValidation`). CI run 2 (46a37c4): **all three jobs green** (package build + tests 8 min, iOS shims 2 min, Mac app 8 min). |
| 1 Extract the package | TestFlight build 5 behaves identically on Patrick's phone (Ask, one of each Studio kind, a 5-minute meeting, lock mid-answer); CI green | Device script below; CI | **open** (opened 2026-11-13) |
| 2 Mac app v0 as a peer | `ParityRunner` passes at the phone's bar against the Python Mac; Patrick uses the Mac app daily for a week and lists what he reached for | Parity harness in the Mac app's Diagnostics; Patrick's list | not started |
| 3 Ingestion and library | Personal library migrated (19 notebooks, 41,970 chunks) with 95% identical citations on the fixture and every difference explained; 50-document ingestion diff; the Mac app answers with Python stopped; visuals golden set approved side by side | Migration tool report; diff scripts; Patrick | not started |
| 4 Become the host | The 2026-10-23 sync checklist passes with the Swift app hosting three Macs and the iPhone; the shipped browser extension works unchanged; Jocasta and Claude Desktop connect over MCP; the Python host stays off on the personal MBP for a week | Checklist; extension; MCP clients; a week | not started |
| 5 Agents, intelligence, media | Every row of the absorption map marked done; a month of daily use with Python uninstalled on the personal MBP; then tag the last Python release and archive `backend/`, `src/`, `src-tauri/` | Absorption map; a month | not started |
| 6 Award-level polish | Patrick's sign-off per tweak; a beta to the existing testers | Per tweak | not started |

Stop rule: if the Phase 2 parity gate fails twice, stop and reassess before any ingestion or migration work.

## Phase 1 device script (TestFlight build 5)

1. Open the app; the answer model loads at open as before.
2. Ask one question in a synced notebook; citations open; answer length and read-aloud work.
3. Make one of each Studio kind: briefing, quiz, flashcards, audio overview, visual.
4. Record a five-minute meeting from the Lock Screen widget; review; save in a notebook.
5. Ask again and lock the phone mid-answer; unlock; the app is still responsive and the partial answer kept.
6. Sync once more; nothing duplicates.
