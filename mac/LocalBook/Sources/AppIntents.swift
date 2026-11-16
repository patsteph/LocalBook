import AppIntents
import Foundation
import LocalBookCore

/// Siri, Spotlight and Shortcuts on the Mac: the same three asks the phone answers, here opening the window and
/// handing the request to it. Everything stays on this Mac.
struct LocalBookShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: RecordMeetingIntent(),
                    phrases: ["Record a meeting in \(.applicationName)", "Start a \(.applicationName) meeting recording"],
                    shortTitle: "Record Meeting", systemImageName: "waveform")
        AppShortcut(intent: AskLocalBookIntent(),
                    phrases: ["Ask \(.applicationName)", "Ask \(.applicationName) a question"],
                    shortTitle: "Ask", systemImageName: "text.bubble")
        AppShortcut(intent: NewNoteIntent(),
                    phrases: ["New note in \(.applicationName)", "Add a note to \(.applicationName)"],
                    shortTitle: "New Note", systemImageName: "square.and.pencil")
    }
}

struct RecordMeetingIntent: AppIntent {
    static let title: LocalizedStringResource = "Record a Meeting"
    static let description = IntentDescription("Opens LocalBook to the Meetings pane and starts recording. Everything stays on this Mac.")
    static let openAppWhenRun = true

    @MainActor
    func perform() async throws -> some IntentResult {
        LaunchRequests.shared.post(.startMeetingRecording)
        return .result()
    }
}

struct AskLocalBookIntent: AppIntent {
    static let title: LocalizedStringResource = "Ask LocalBook"
    static let description = IntentDescription("Asks the current notebook a question in LocalBook and shows the answer.")
    static let openAppWhenRun = true

    @Parameter(title: "Question", requestValueDialog: "What would you like to ask?")
    var question: String

    static var parameterSummary: some ParameterSummary { Summary("Ask \(\.$question)") }

    @MainActor
    func perform() async throws -> some IntentResult {
        LaunchRequests.shared.post(.askQuestion, object: question)
        return .result()
    }
}

struct NewNoteIntent: AppIntent {
    static let title: LocalizedStringResource = "New Note"
    static let description = IntentDescription("Opens a new note in the current LocalBook notebook, with the text you give it.")
    static let openAppWhenRun = true

    @Parameter(title: "Text", default: "")
    var text: String

    @MainActor
    func perform() async throws -> some IntentResult {
        LaunchRequests.shared.post(.newNote, object: text.isEmpty ? nil : text)
        return .result()
    }
}

/// An intent or a `localbook://` link can arrive before the window is on screen. Requests wait here until the main
/// window says it is ready, then go out as the same notifications the menu bar posts.
@MainActor
final class LaunchRequests {
    static let shared = LaunchRequests()
    private var pending: [(Notification.Name, Any?)] = []
    private var ready = false

    func post(_ name: Notification.Name, object: Any? = nil) {
        if ready { NotificationCenter.default.post(name: name, object: object) } else { pending.append((name, object)) }
    }

    func windowReady() {
        ready = true
        let queue = pending; pending = []
        for (name, object) in queue { NotificationCenter.default.post(name: name, object: object) }
    }

    /// localbook://record · localbook://ask?q=… · localbook://note?text=…
    func open(_ url: URL) {
        guard url.scheme == "localbook" else { return }
        let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
        func value(_ key: String) -> String? { parts?.queryItems?.first { $0.name == key }?.value }
        switch url.host() ?? url.path() {
        case "record": post(.startMeetingRecording)
        case "ask": if let q = value("q"), !q.isEmpty { post(.askQuestion, object: q) } else { post(.focusComposer) }
        case "note": post(.newNote, object: value("text"))
        default: break
        }
    }
}
