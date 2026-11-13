# LocalBook goes native Swift

The Mac app is being rebuilt in Swift around the engine the iPhone already runs, sharing one package between the
two apps. The Python backend stays untouched as the system of record until the Swift app passes the phone's
parity harness and the three-Macs-plus-iPhone sync checklist. Nothing is deferred or retired: every Mac feature
has a named Swift approach and a phase.

| File | What it is |
|---|---|
| `00-decision.md` | The decision record: why, the research, the check results, the fixed toolchain, the escape-hatch log |
| `01-absorption-map.md` | Every Mac feature with its Swift approach, phase and status. The working copy |
| `02-gate-log.md` | Each phase's gate, how it is run, and the result when it closed |
| `03-mac-design-profile.md` | The one-page Mac design profile: windows, sidebar, toolbar, menu bar map, keyboard map |

Code:

| Path | What it is |
|---|---|
| `swift/LocalBookCore` | The shared package: `LocalBookCore` (pure engine) and `LocalBookEngines` (MLX, Core ML, FluidAudio) |
| `mac/LocalBook` | The Mac app (xcodegen project) |
| `ios/LocalBookLab` | The iPhone app and Lab; depends on the package from Phase 1 |
| `.github/workflows/swift.yml` | macOS CI: package build and Swift Testing, iOS build of the pure library, Mac app build |

The full plan and the three research documents behind it live in the project's shared folder under
`research/2026-11-13-*.md`; the decision record summarises them.

Standing rules that apply to all of this work: diff-only sync; search scoped to one notebook (only the curator
crosses notebooks); the answer model loads at open and stays loaded; cancel generation the instant the phone
leaves the foreground; everything generates on the phone, not the Mac; never run the Python test suite or any
Keychain-touching test on Patrick's Macs (CI and the cloud run them); `dev` stays shippable for TestFlight at
every commit.
