import AppKit
import LocalBookCore
import SwiftUI

/// The menu bar. Every action the app will have is here from day one, with its keyboard shortcut from the `Shortcuts`
/// table (which Help › Keyboard Shortcuts draws and tooltips quote), so the map in `03-mac-design-profile.md`, the
/// guide and the app never drift. Items whose feature arrives in a later phase are disabled
/// until then; enabling one is the last line of that feature's work.
struct LocalBookCommands: Commands {
    private func make(_ kind: StudioItem.Kind) {
        NotificationCenter.default.post(name: .makeStudio, object: kind.rawValue)
    }

    var body: some Commands {
        // File
        CommandGroup(replacing: .newItem) {
            Button("New Notebook…") { NotificationCenter.default.post(name: .newNotebook, object: nil) }.keyboardShortcut(Shortcuts.newNotebook.keys)
            Button("New Note") { NotificationCenter.default.post(name: .newNote, object: nil) }
                .keyboardShortcut(Shortcuts.newNote.keys)
            Divider()
            Button("Add Sources…") { NotificationCenter.default.post(name: .addSources, object: nil) }.keyboardShortcut(Shortcuts.addSources.keys)
            Button("Add Web Page…") { NotificationCenter.default.post(name: .addWebPage, object: nil) }.keyboardShortcut(Shortcuts.addWebPage.keys)
            Button("Watch Folder…") { NotificationCenter.default.post(name: .watchFolder, object: nil) }
            Button("Find Sources on the Web…") { NotificationCenter.default.post(name: .findSources, object: nil) }.keyboardShortcut(Shortcuts.findSources.keys)
        }
        CommandGroup(after: .importExport) {
            Button("Export Notebook…") { NotificationCenter.default.post(name: .exportNotebook, object: nil) }.keyboardShortcut(Shortcuts.exportNotebook.keys)
        }

        // Notebook
        CommandMenu("Notebook") {
            Button("Ask") { NotificationCenter.default.post(name: .focusComposer, object: nil) }   // also returns to the conversation
                .keyboardShortcut(Shortcuts.ask.keys)
            Button("Stop Answer") { NotificationCenter.default.post(name: .stopAnswer, object: nil) }
                .keyboardShortcut(Shortcuts.stopAnswer.keys)
            Divider()
            Button("Sync Now") { NotificationCenter.default.post(name: .syncNow, object: nil) }
                .keyboardShortcut(Shortcuts.syncNow.keys)
            Button("Pair with a Mac…") { NotificationCenter.default.post(name: .pairWithMac, object: nil) }
            Button("Move My Library Here…") { NotificationCenter.default.post(name: .importLibrary, object: nil) }
            Divider()
            Button("Read Answer Aloud") { NotificationCenter.default.post(name: .readAnswerAloud, object: nil) }
                .keyboardShortcut(Shortcuts.readAloud.keys)
            Divider()
            Button("Keep Local") {}.disabled(true)                                                 // Phase 4
            Button("Sync Everywhere") {}.disabled(true)                                           // Phase 4
        }

        // Studio
        CommandMenu("Studio") {
            Button("Briefing") { make(.doc) }.keyboardShortcut(Shortcuts.briefing.keys)
            Button("Quiz") { make(.quiz) }.keyboardShortcut(Shortcuts.quiz.keys)
            Button("Flashcards") { make(.cards) }.keyboardShortcut(Shortcuts.flashcards.keys)
            Button("Audio Overview") { make(.audio) }.keyboardShortcut(Shortcuts.audioOverview.keys)
            Button("Visual") { make(.visual) }.keyboardShortcut(Shortcuts.visual.keys)
            Divider()
            Button("Video Explainer") { make(.video) }
            Button("Image…") {}.disabled(true)                                                    // Phase 5
            Button("Canvas") {}.disabled(true)                                                    // Phase 5
            Button("Constellation") {}.disabled(true)                                             // Phase 5
        }

        // Insights
        CommandMenu("Insights") {
            ForEach(Insight.Kind.allCases) { kind in
                Button(kind.label) { NotificationCenter.default.post(name: .makeInsight, object: kind.rawValue) }
            }
            Divider()
            Button("Writing Assistant…") { NotificationCenter.default.post(name: .writingAssistant, object: nil) }
                .keyboardShortcut(Shortcuts.writingAssistant.keys)
        }

        // Agents
        CommandMenu("Agents") {
            Button("Morning Brief") { NotificationCenter.default.post(name: .curatorAction, object: CuratorPiece.Kind.brief.rawValue) }
                .keyboardShortcut(Shortcuts.morningBrief.keys)
            Button("Week in Review") { NotificationCenter.default.post(name: .curatorAction, object: CuratorPiece.Kind.weekly.rawValue) }
            Button("Discover Patterns") { NotificationCenter.default.post(name: .curatorAction, object: CuratorPiece.Kind.patterns.rawValue) }
            Divider()
            Button("Collect Now") { NotificationCenter.default.post(name: .collectorAction, object: "collect") }
            Button("Research…") { NotificationCenter.default.post(name: .collectorAction, object: "research:") }
            Divider()
            Button("Check Mail Now") { NotificationCenter.default.post(name: .correspondentAction, object: "check") }
                .keyboardShortcut(Shortcuts.checkMail.keys)
            Divider()
            Button("Read Profiles Now") { NotificationCenter.default.post(name: .peopleAction, object: "collect") }
                .keyboardShortcut(Shortcuts.readProfiles.keys)
        }

        // Record
        CommandMenu("Record") {
            Button("Start Meeting Recording") { NotificationCenter.default.post(name: .startMeetingRecording, object: nil) }
                .keyboardShortcut(Shortcuts.startRecording.keys)
            Button("Voice Note") { NotificationCenter.default.post(name: .voiceNote, object: nil) }
                .keyboardShortcut(Shortcuts.voiceNote.keys)
        }

        // View
        SidebarCommands()
        CommandGroup(after: .sidebar) {
            Button("Show Sources") { NotificationCenter.default.post(name: .showSources, object: nil) }
                .keyboardShortcut(Shortcuts.showSources.keys)
            Button("Show Studio") { NotificationCenter.default.post(name: .showStudio, object: nil) }
                .keyboardShortcut(Shortcuts.showStudio.keys)
            Button("Show Meetings") { NotificationCenter.default.post(name: .showMeetings, object: nil) }
                .keyboardShortcut(Shortcuts.showMeetings.keys)
            Button("Show Insights") { NotificationCenter.default.post(name: .showInsights, object: nil) }
                .keyboardShortcut(Shortcuts.showInsights.keys)
            Button("Show Canvas") { NotificationCenter.default.post(name: .showCanvas, object: nil) }
                .keyboardShortcut(Shortcuts.showCanvas.keys)
            Button("Show Constellation") { NotificationCenter.default.post(name: .showConstellation, object: nil) }
                .keyboardShortcut(Shortcuts.showConstellation.keys)
            Button("Show Curator") { NotificationCenter.default.post(name: .showCurator, object: nil) }
                .keyboardShortcut(Shortcuts.showCurator.keys)
            Group {
            Button("Show Collector") { NotificationCenter.default.post(name: .showCollector, object: nil) }
                .keyboardShortcut(Shortcuts.showCollector.keys)
            Button("Show Correspondent") { NotificationCenter.default.post(name: .showCorrespondent, object: nil) }
                .keyboardShortcut(Shortcuts.showCorrespondent.keys)
            Button("Show People") { NotificationCenter.default.post(name: .showPeople, object: nil) }
                .keyboardShortcut(Shortcuts.showPeople.keys)
            Divider()
            Button("Show Inspector") { NotificationCenter.default.post(name: .toggleInspector, object: nil) }
                .keyboardShortcut(Shortcuts.toggleInspector.keys)
            }
        }

        // Window and Help, grouped: a Commands body holds ten entries at most.
        Group {
        CommandGroup(after: .windowArrangement) {
            Button("Open in New Window") { NotificationCenter.default.post(name: .detachItem, object: nil) }
                .keyboardShortcut(Shortcuts.openInNewWindow.keys)
        }

        // Help
        CommandGroup(replacing: .help) {
            Button("LocalBook Help") {}.disabled(true)
            Button("Keyboard Shortcuts") { NotificationCenter.default.post(name: .showShortcuts, object: nil) }
                .keyboardShortcut(Shortcuts.keyboardShortcuts.keys)
            Divider()
            Button("Privacy") {
                if let url = URL(string: "https://www.patrickjstephens.com/projects/localbook/privacy") {
                    NSWorkspace.shared.open(url)
                }
            }
        }
        }
    }
}
