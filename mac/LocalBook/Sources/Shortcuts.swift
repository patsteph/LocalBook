import LocalBookCore
import SwiftUI

/// One keyboard shortcut: what it does and which keys. `keys` is nil for a shortcut a view owns itself (Space to peek,
/// ⌘↓ to open) that the guide lists but the menu bar does not declare.
struct AppShortcut: Identifiable, Sendable {
    let title: String
    let display: String                 // "⇧⌘F", in the order macOS shows modifiers: ⌃ ⌥ ⇧ ⌘
    let key: Character?
    let modifiers: EventModifiers
    var id: String { title }

    /// A menu item's shortcut.
    init(_ title: String, _ key: Character, _ modifiers: EventModifiers = .command) {
        self.title = title; self.key = key; self.modifiers = modifiers
        self.display = Self.symbols(modifiers) + String(key).uppercased()
    }

    /// A shortcut a view binds itself; `display` is written out ("Space", "⌘↓").
    init(_ title: String, display: String) {
        self.title = title; self.display = display; self.key = nil; self.modifiers = []
    }

    /// For `.keyboardShortcut(_:)` on a menu item.
    var keys: KeyboardShortcut? { key.map { KeyboardShortcut(KeyEquivalent($0), modifiers: modifiers) } }

    /// "Find sources on the web (⇧⌘F)": the text for a button's tooltip, so hovering shows the shortcut.
    func help(_ text: String) -> String { "\(text) (\(display))" }

    private static func symbols(_ m: EventModifiers) -> String {
        var s = ""
        if m.contains(.control) { s += "⌃" }
        if m.contains(.option) { s += "⌥" }
        if m.contains(.shift) { s += "⇧" }
        if m.contains(.command) { s += "⌘" }
        return s
    }
}

/// Every keyboard shortcut in the app, in one table. The menu bar (`LocalBookCommands`) reads it, buttons append it to
/// their tooltips with `help(_:)`, and Help › Keyboard Shortcuts (⌘/) draws it, so the three never drift.
enum Shortcuts {
    struct Group: Identifiable, Sendable {
        let name: String
        let items: [AppShortcut]
        var id: String { name }
    }

    // File
    static let newNotebook = AppShortcut("New Notebook", "n")
    static let newNote = AppShortcut("New Note", "n", [.command, .shift])
    static let addSources = AppShortcut("Add Sources", "o")
    static let addWebPage = AppShortcut("Add Web Page", "l", [.command, .shift])
    static let findSources = AppShortcut("Find Sources on the Web", "f", [.command, .shift])
    static let exportNotebook = AppShortcut("Export Notebook", "e", [.command, .shift])

    // Notebook
    static let ask = AppShortcut("Ask (back to the conversation)", "l")
    static let stopAnswer = AppShortcut("Stop Answer", ".")
    static let syncNow = AppShortcut("Sync Now", "r")
    static let readAloud = AppShortcut("Read Answer Aloud", "r", [.command, .option])

    // Studio
    static let briefing = AppShortcut("Briefing", "1", [.command, .control])
    static let quiz = AppShortcut("Quiz", "2", [.command, .control])
    static let flashcards = AppShortcut("Flashcards", "3", [.command, .control])
    static let audioOverview = AppShortcut("Audio Overview", "4", [.command, .control])
    static let visual = AppShortcut("Visual", "5", [.command, .control])

    /// The Studio menu's shortcut for a kind, if it has one (Video Explainer does not).
    static func make(_ kind: StudioItem.Kind) -> AppShortcut? {
        switch kind {
        case .doc: briefing
        case .quiz: quiz
        case .cards: flashcards
        case .audio: audioOverview
        case .visual: visual
        case .video: nil
        }
    }

    // Record
    static let startRecording = AppShortcut("Start Meeting Recording", "r", [.command, .shift])
    static let voiceNote = AppShortcut("Voice Note", "v", [.command, .shift])

    // View
    static let showSources = AppShortcut("Show Sources", "1", [.command, .option])
    static let showStudio = AppShortcut("Show Studio", "2", [.command, .option])
    static let showMeetings = AppShortcut("Show Meetings", "3", [.command, .option])
    static let toggleInspector = AppShortcut("Show or Hide Inspector", "i", [.command, .option])

    // Window
    static let openInNewWindow = AppShortcut("Open in New Window", "d", [.command, .shift])

    // Help
    static let keyboardShortcuts = AppShortcut("Keyboard Shortcuts", "/")

    /// The guide, in menu order, then the shortcuts views own themselves.
    static let groups: [Group] = [
        Group(name: "File", items: [newNotebook, newNote, addSources, addWebPage, findSources, exportNotebook]),
        Group(name: "Notebook", items: [ask, stopAnswer, syncNow, readAloud,
                                        AppShortcut("Send the question", display: "Return"),
                                        AppShortcut("Add files or a web address copied in the Finder or a browser", display: "⌘V")]),
        Group(name: "Studio", items: [briefing, quiz, flashcards, audioOverview, visual,
                                      AppShortcut("Flip a flashcard, or play and pause audio", display: "Space"),
                                      AppShortcut("Previous or next flashcard", display: "← →")]),
        Group(name: "Record", items: [startRecording, voiceNote]),
        Group(name: "View", items: [AppShortcut("Show or Hide Sidebar", "s", [.command, .control]),
                                    showSources, showStudio, showMeetings, toggleInspector]),
        Group(name: "Sources", items: [AppShortcut("Quick Look the selected source", display: "Space"),
                                       AppShortcut("Open the selected source in its own window", display: "⌘↓")]),
        Group(name: "Notes", items: [AppShortcut("Edit the selected note", "e"),
                                     AppShortcut("Save the note", "s"),
                                     AppShortcut("Bold, Italic, Link", display: "⌘B  ⌘I  ⌘K"),
                                     AppShortcut("Numbered, bulleted, checklist", display: "⇧⌘7  ⇧⌘8  ⇧⌘9")]),
        Group(name: "Window", items: [openInNewWindow,
                                      AppShortcut("Settings", ","),
                                      AppShortcut("Close a sheet or popover", display: "Esc"),
                                      keyboardShortcuts]),
    ]
}

/// Help › Keyboard Shortcuts (⌘/): the whole table on one calm sheet. Every button in the app also shows its shortcut
/// in its tooltip, which the footer says.
struct ShortcutsSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text("Keyboard Shortcuts").font(.title2.weight(.semibold))
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(.horizontal, 24).padding(.top, 20).padding(.bottom, 12)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    ForEach(Shortcuts.groups) { group in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(group.name.uppercased()).font(.caption.weight(.semibold)).foregroundStyle(.secondary).kerning(0.6)
                            ForEach(group.items) { item in
                                HStack(alignment: .firstTextBaseline) {
                                    Text(item.title).font(.body)
                                    Spacer(minLength: 16)
                                    KeyCaps(item.display)
                                }
                                .padding(.vertical, 3)
                                .accessibilityElement(children: .combine)
                            }
                        }
                    }
                }
                .padding(.horizontal, 24).padding(.vertical, 18)
            }
            Divider()
            Label("Hover over any button to see its shortcut. Every menu item shows its own.", systemImage: "cursorarrow.rays")
                .font(.footnote).foregroundStyle(.secondary)
                .padding(.horizontal, 24).padding(.vertical, 12)
        }
        .frame(width: 540, height: 640)
    }
}

/// "⇧⌘F" drawn as key caps, one per key; written-out keys ("Space", "Return") are one cap. Two spaces separate groups
/// of caps ("⌘B  ⌘I  ⌘K").
private struct KeyCaps: View {
    let keys: [[String]]
    init(_ display: String) {
        keys = display.components(separatedBy: "  ").map { chord in
            // A word ("Space", "Return", "Esc") is one cap; "⇧⌘7" is a cap per key; "← →" is two caps.
            if chord.contains(" ") { return chord.split(separator: " ").map(String.init) }
            return chord.count <= 4 ? chord.map { String($0) } : [chord]
        }
    }

    var body: some View {
        HStack(spacing: 10) {
            ForEach(Array(keys.enumerated()), id: \.offset) { _, chord in
                HStack(spacing: 3) {
                    ForEach(Array(chord.enumerated()), id: \.offset) { _, k in
                        Text(k)
                            .font(.system(.callout, design: .rounded).weight(.medium)).monospacedDigit()
                            .frame(minWidth: 22).padding(.horizontal, 5).padding(.vertical, 2)
                            .background(.quaternary, in: RoundedRectangle(cornerRadius: 5, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: 5, style: .continuous).strokeBorder(.separator))
                    }
                }
            }
        }
        .foregroundStyle(.primary)
    }
}
