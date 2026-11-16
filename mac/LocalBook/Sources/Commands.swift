import AppKit
import LocalBookCore
import SwiftUI

/// The menu bar. Every action the app will have is here from day one, with its keyboard shortcut, so the map in
/// `03-mac-design-profile.md` and the app never drift. Items whose feature arrives in a later phase are disabled
/// until then; enabling one is the last line of that feature's work.
struct LocalBookCommands: Commands {
    private func make(_ kind: StudioItem.Kind) {
        NotificationCenter.default.post(name: .makeStudio, object: kind.rawValue)
    }

    var body: some Commands {
        // File
        CommandGroup(replacing: .newItem) {
            Button("New Notebook…") { NotificationCenter.default.post(name: .newNotebook, object: nil) }.keyboardShortcut("n")
            Button("New Note") { NotificationCenter.default.post(name: .newNote, object: nil) }
                .keyboardShortcut("n", modifiers: [.command, .shift])
            Divider()
            Button("Add Sources…") {}.keyboardShortcut("o").disabled(true)                        // Phase 3
            Button("Add Web Page…") {}.keyboardShortcut("l", modifiers: [.command, .shift]).disabled(true)
            Button("Watch Folder…") {}.disabled(true)
        }
        CommandGroup(after: .importExport) {
            Button("Export Notebook…") {}.keyboardShortcut("e", modifiers: [.command, .shift]).disabled(true) // Phase 3
        }

        // Notebook
        CommandMenu("Notebook") {
            Button("Ask") { NotificationCenter.default.post(name: .focusComposer, object: nil) }   // also returns to the conversation
                .keyboardShortcut("l")
            Button("Stop Answer") { NotificationCenter.default.post(name: .stopAnswer, object: nil) }
                .keyboardShortcut(".")
            Divider()
            Button("Sync Now") { NotificationCenter.default.post(name: .syncNow, object: nil) }
                .keyboardShortcut("r")
            Button("Pair with a Mac…") { NotificationCenter.default.post(name: .pairWithMac, object: nil) }
            Divider()
            Button("Read Answer Aloud") { NotificationCenter.default.post(name: .readAnswerAloud, object: nil) }
                .keyboardShortcut("r", modifiers: [.command, .option])
            Divider()
            Button("Keep Local") {}.disabled(true)                                                 // Phase 4
            Button("Sync Everywhere") {}.disabled(true)                                           // Phase 4
        }

        // Studio
        CommandMenu("Studio") {
            Button("Briefing") { make(.doc) }.keyboardShortcut("1", modifiers: [.command, .control])
            Button("Quiz") { make(.quiz) }.keyboardShortcut("2", modifiers: [.command, .control])
            Button("Flashcards") { make(.cards) }.keyboardShortcut("3", modifiers: [.command, .control])
            Button("Audio Overview") { make(.audio) }.keyboardShortcut("4", modifiers: [.command, .control])
            Button("Visual") { make(.visual) }.keyboardShortcut("5", modifiers: [.command, .control])
            Divider()
            Button("Video Explainer") { make(.video) }
            Button("Image…") {}.disabled(true)                                                    // Phase 5
            Button("Canvas") {}.disabled(true)                                                    // Phase 5
            Button("Constellation") {}.disabled(true)                                             // Phase 5
        }

        // Record
        CommandMenu("Record") {
            Button("Start Meeting Recording") { NotificationCenter.default.post(name: .startMeetingRecording, object: nil) }
                .keyboardShortcut("r", modifiers: [.command, .shift])
            Button("Voice Note") { NotificationCenter.default.post(name: .voiceNote, object: nil) }
                .keyboardShortcut("v", modifiers: [.command, .shift])
        }

        // View
        SidebarCommands()
        CommandGroup(after: .sidebar) {
            Button("Show Sources") { NotificationCenter.default.post(name: .showSources, object: nil) }
                .keyboardShortcut("1", modifiers: [.command, .option])
            Button("Show Studio") { NotificationCenter.default.post(name: .showStudio, object: nil) }
                .keyboardShortcut("2", modifiers: [.command, .option])
            Button("Show Meetings") { NotificationCenter.default.post(name: .showMeetings, object: nil) }
                .keyboardShortcut("3", modifiers: [.command, .option])
            Divider()
            Button("Show Inspector") { NotificationCenter.default.post(name: .toggleInspector, object: nil) }
                .keyboardShortcut("i", modifiers: [.command, .option])
        }

        // Window
        CommandGroup(after: .windowArrangement) {
            Button("Open in New Window") { NotificationCenter.default.post(name: .detachItem, object: nil) }
                .keyboardShortcut("d", modifiers: [.command, .shift])
        }

        // Help
        CommandGroup(replacing: .help) {
            Button("LocalBook Help") {}.disabled(true)
            Button("Privacy") {
                if let url = URL(string: "https://www.patrickjstephens.com/projects/localbook/privacy") {
                    NSWorkspace.shared.open(url)
                }
            }
        }
    }
}
