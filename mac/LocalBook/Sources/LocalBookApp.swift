import SwiftUI
import LocalBookCore

/// LocalBook for Mac. Phase 0: the shell. The window, sidebar, Ask view, menu bar and Settings are laid out as
/// `docs/swift-migration/03-mac-design-profile.md` describes; Phase 2 connects them to the engine as a sync peer.
@main
struct LocalBookApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup("LocalBook") {
            MainWindow()
                .environment(model)
                .frame(minWidth: 820, minHeight: 540)
        }
        .defaultSize(width: 1180, height: 760)
        .commands { LocalBookCommands() }

        Settings {
            SettingsView()
                .environment(model)
        }
    }
}
