import LocalBookCore
import LocalBookEngines
import AppIntents

/// Siri, Spotlight and the Action button ("Shortcut") can start a meeting recording without opening the app first.
/// App Shortcuts appear on their own once the app is installed; nothing to set up.
struct LocalBookShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: RecordMeetingIntent(),
            phrases: [
                "Record a meeting in \(.applicationName)",
                "Start a \(.applicationName) meeting recording",
                "\(.applicationName) record meeting",
            ],
            shortTitle: "Record Meeting",
            systemImageName: "waveform"
        )
    }
}
