# LocalBook for Mac

The native Swift Mac app, built around the engine the iPhone app runs (`swift/LocalBookCore`). Plan, absorption
map and gate log: `docs/swift-migration/`.

```bash
brew install xcodegen          # once
cd mac/LocalBook
xcodegen generate
open LocalBook.xcodeproj       # pick your team under Signing & Capabilities, Run
```

Phase 0 is the shell: window, sidebar, Ask view, the full menu bar map (items enable as their phase lands) and
Settings. It does not touch the Python app's files: its data lives under
`~/Library/Application Support/LocalBook (Swift)/` while the two run side by side.

Requires macOS 26 and stable Xcode 27.2. `project.yml` never carries a team id.
