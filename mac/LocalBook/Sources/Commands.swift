import AppKit
import SwiftUI

/// The menu bar. Every action the app will have is here from day one, with its keyboard shortcut, so the map in
/// `03-mac-design-profile.md` and the app never drift. Items whose feature arrives in a later phase are disabled
/// until then; enabling one is the last line of that feature's work.
struct LocalBookCommands: Commands {
    var body: some Commands {
        // File
        CommandGroup(replacing: .newItem) {
            Button("New Notebook…") {}.keyboardShortcut("n").disabled(true)                       // Phase 3
            Button("New Note") {}.keyboardShortcut("n", modifiers: [.command, .shift]).disabled(true) // Phase 3
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
            Button("Ask") { NotificationCenter.default.post(name: .focusComposer, object: nil) }
                .keyboardShortcut("l")
            Button("Stop Answer") {}.keyboardShortcut(".").disabled(true)                         // Phase 2
            Divider()
            Button("Sync Now") {}.keyboardShortcut("r").disabled(true)                            // Phase 2
            Button("Pair with a Mac…") {}.disabled(true)                                          // Phase 2
            Divider()
            Button("Read Answer Aloud") {}.keyboardShortcut("r", modifiers: [.command, .option]).disabled(true) // Phase 2
            Divider()
            Button("Keep Local") {}.disabled(true)                                                 // Phase 4
            Button("Sync Everywhere") {}.disabled(true)                                           // Phase 4
        }

        // Studio
        CommandMenu("Studio") {
            Button("Briefing") {}.keyboardShortcut("1", modifiers: [.command, .control]).disabled(true)
            Button("Quiz") {}.keyboardShortcut("2", modifiers: [.command, .control]).disabled(true)
            Button("Flashcards") {}.keyboardShortcut("3", modifiers: [.command, .control]).disabled(true)
            Button("Audio Overview") {}.keyboardShortcut("4", modifiers: [.command, .control]).disabled(true)
            Button("Visual") {}.keyboardShortcut("5", modifiers: [.command, .control]).disabled(true)
            Divider()
            Button("Video Explainer") {}.disabled(true)
            Button("Image…") {}.disabled(true)                                                    // Phase 5
            Button("Canvas") {}.disabled(true)                                                    // Phase 5
            Button("Constellation") {}.disabled(true)                                             // Phase 5
        }

        // Record
        CommandMenu("Record") {
            Button("Start Meeting Recording") {}.keyboardShortcut("r", modifiers: [.command, .shift]).disabled(true) // Phase 2
            Button("Voice Note") {}.keyboardShortcut("v", modifiers: [.command, .shift]).disabled(true)              // Phase 2
        }

        // View
        SidebarCommands()
        CommandGroup(after: .sidebar) {
            Button("Show Sources") {}.keyboardShortcut("1", modifiers: [.command, .option]).disabled(true) // Phase 2
            Button("Show Studio") {}.keyboardShortcut("2", modifiers: [.command, .option]).disabled(true)
            Button("Show Meetings") {}.keyboardShortcut("3", modifiers: [.command, .option]).disabled(true)
            Divider()
            Button("Show Inspector") {}.keyboardShortcut("i", modifiers: [.command, .option]).disabled(true) // Phase 2
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
