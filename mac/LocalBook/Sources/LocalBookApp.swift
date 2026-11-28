import SwiftUI
import LocalBookCore

/// LocalBook for Mac. Phase 2: a sync peer. The window, sidebar, Ask view, menu bar and Settings are laid out as
/// `docs/swift-migration/03-mac-design-profile.md` describes, connected to the package's engine: this Mac pairs with
/// the Python Mac over the phone-class link, copies the notebooks it shares, and searches and answers them itself.
@main
struct LocalBookApp: App {
    @State private var model = AppModel()
    @AppStorage(BackgroundMenu.keepRunningKey) private var keepRunning = true

    init() { SoakTrace.startSession("LocalBook for Mac") }

    var body: some Scene {
        WindowGroup("LocalBook", id: "main") {
            MainWindow()
                .environment(model)
                .frame(minWidth: 820, minHeight: 540)
        }
        .defaultSize(width: 1180, height: 760)
        .commands { LocalBookCommands() }

        // A source in its own window (from a citation popover or a source row: ⌘↓ / Open in Window).
        WindowGroup("Source", id: "source", for: SourceWindowKey.self) { $key in
            if let key { SourceWindow(key: key).environment(model) }
        }
        .defaultSize(width: 720, height: 620)

        // A Studio item in its own window (Open in Window from the shelf, or Window › Open in New Window).
        WindowGroup("Studio", id: "studio", for: StudioWindowKey.self) { $key in
            if let key { StudioWindow(key: key).environment(model) }
        }
        .defaultSize(width: 820, height: 640)

        Settings {
            SettingsView()
                .environment(model)
        }

        // The menu bar item that keeps the agents company while the window is closed (Settings › Security).
        MenuBarExtra("LocalBook", systemImage: "book.closed", isInserted: $keepRunning) {
            BackgroundMenu().environment(model)
        }
    }
}
