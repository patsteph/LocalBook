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

Requires macOS 26 and stable Xcode 27.2. `project.yml` never carries a team id, but a team is required: the pairing key lives in the Secure Enclave, which macOS only opens to an app signed with an App ID. With no team ("Sign to Run Locally") pairing fails with `-34018`. Set the team in Xcode, or put `DEVELOPMENT_TEAM` in your local, never-pushed copy of `project.yml` so `xcodegen generate` keeps it. `project.yml` also asks for Keychain Sharing (our own group only), which makes Xcode embed the provisioning profile that authorises the App ID; a team without it still fails. Check with `codesign -d --entitlements - <path to LocalBook.app> | grep application-identifier`.
