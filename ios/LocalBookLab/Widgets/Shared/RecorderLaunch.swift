import AppIntents
import Foundation
import Observation

/// Compiled into both the app and the widget extension. Apple requires an intent that opens the app from a control to
/// be a member of both targets; the system then runs `perform()` in the app, never in the extension.

/// A request from outside the app (home-screen or Lock Screen widget, Control Center, the Action button, Siri) to go
/// straight to the meeting recorder. The app reads it at launch to decide whether to load the answer model at all.
@MainActor @Observable
final class RecorderLaunch {
    static let shared = RecorderLaunch()
    /// Set by a launcher; the app clears it once the recorder is on screen.
    var requested = false

    /// What the widgets open. Registered as the app's URL scheme in project.yml.
    nonisolated static let url = URL(string: "localbook://record")!
    nonisolated static func isRecorderURL(_ url: URL) -> Bool { url.scheme == "localbook" && url.host() == "record" }
}

/// The one place a launcher can take you: LocalBook only offers the meeting recorder this way.
enum LocalBookLaunchScreen: String, AppEnum {
    case meetingRecorder
    static let typeDisplayRepresentation: TypeDisplayRepresentation = "LocalBook screen"
    static let caseDisplayRepresentations: [LocalBookLaunchScreen: DisplayRepresentation] = [.meetingRecorder: "Meeting recorder"]
}

/// Opens LocalBook on the meeting recorder and starts recording. The answer model is not loaded on this path.
struct RecordMeetingIntent: OpenIntent {
    static let title: LocalizedStringResource = "Record a Meeting"
    static let description = IntentDescription("Opens LocalBook straight to the meeting recorder and starts recording. Everything stays on this phone.")
    static let supportedModes: IntentModes = .foreground

    @Parameter(title: "Screen", default: .meetingRecorder)
    var target: LocalBookLaunchScreen

    init() {}

    @MainActor
    func perform() async throws -> some IntentResult {
        RecorderLaunch.shared.requested = true
        return .result()
    }
}
