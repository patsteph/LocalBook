import LocalBookCore
import SwiftUI

/// One thing the composer can do besides asking: a Studio kind, a note, a web search, a recording, and later an
/// agent. Typing `/` in the Ask field lists them; the chosen one sits in the field as a chip and the text that
/// follows is handed to it. New entries go in `SlashCommands.all`; nothing else in the app needs to change.
struct SlashCommand: Identifiable {
    enum Group: String, CaseIterable, Sendable {
        case action = "Actions"
        case agent = "Agents"
    }

    /// The word typed after the slash, lower case, no spaces: "quiz".
    let name: String
    let title: String
    let symbol: String
    /// What the rest of the line is for, shown as the field's hint while the chip is in place.
    let hint: String
    let group: Group
    /// Runs on the main actor with the text typed after the chip (trimmed, possibly empty).
    let run: @MainActor (String, AppModel) -> Void

    var id: String { name }
}

enum SlashCommands {
    /// The registry, in menu order. A Studio kind opens its make sheet with the text as the subject; the other
    /// actions reach the same code paths as the menu bar items of the same name.
    @MainActor static let all: [SlashCommand] = [
        studio(.doc, "briefing", hint: "Subject, or leave empty for the whole notebook"),
        studio(.quiz, "quiz", hint: "Subject, or leave empty for the whole notebook"),
        studio(.cards, "flashcards", hint: "Subject, or leave empty for the whole notebook"),
        studio(.audio, "audio", hint: "Subject, or leave empty for the whole notebook"),
        studio(.visual, "visual", hint: "Describe the picture you want"),
        studio(.video, "video", hint: "Subject, or leave empty for the whole notebook"),
        SlashCommand(name: "note", title: "New Note", symbol: "square.and.pencil", hint: "First line of the note, or leave empty", group: .action) { text, model in
            NotificationCenter.default.post(name: .newNote, object: text.isEmpty ? nil : text)
        },
        SlashCommand(name: "web", title: "Find Sources on the Web", symbol: "magnifyingglass", hint: "What to search for", group: .action) { text, model in
            model.findSourcesQuery = text
            model.showFindSources = true
        },
        SlashCommand(name: "sources", title: "Add Sources", symbol: "plus.rectangle.on.folder", hint: "Press Return to choose files", group: .action) { _, _ in
            NotificationCenter.default.post(name: .addSources, object: nil)
        },
        SlashCommand(name: "record", title: "Start Meeting Recording", symbol: "record.circle", hint: "Press Return to open the recorder", group: .action) { _, _ in
            NotificationCenter.default.post(name: .startMeetingRecording, object: nil)
        },
        SlashCommand(name: "sync", title: "Sync Now", symbol: "arrow.triangle.2.circlepath", hint: "Press Return to sync", group: .action) { _, model in
            model.syncNow()
        },
        // Agents, with `group: .agent`; the menu grows an Agents section on its own.
        SlashCommand(name: "curator", title: "Curator", symbol: "sparkles", hint: "A question for every notebook at once", group: .agent) { text, model in
            model.askCurator(text)
        },
        SlashCommand(name: "brief", title: "Morning Brief", symbol: "sunrise", hint: "Press Return for what changed since the last brief", group: .agent) { _, model in
            model.selection = .curator; model.curatorRequest = .brief
        },
        SlashCommand(name: "research", title: "Research", symbol: "magnifyingglass.circle", hint: "A topic to research into this notebook's Collector queue", group: .agent) { text, model in
            model.selection = .collector; model.collectorRequest = .research(text)
        },
        SlashCommand(name: "collect", title: "Collect Now", symbol: "binoculars", hint: "Press Return to run this notebook's Collector", group: .agent) { _, model in
            model.selection = .collector; model.collectorRequest = .collect
        },
    ]

    /// The commands whose name starts with what was typed after the slash (everything, for an empty prefix).
    @MainActor static func matching(_ prefix: String) -> [SlashCommand] {
        let p = prefix.lowercased()
        return all.filter { p.isEmpty || $0.name.hasPrefix(p) || $0.title.lowercased().hasPrefix(p) }
    }

    /// The command named exactly `name`, if any.
    @MainActor static func named(_ name: String) -> SlashCommand? {
        all.first { $0.name == name.lowercased() }
    }

    /// Splits "/quiz the water cycle" into the command and the text after it. Nil when the field does not start
    /// with a slash, or the word after it is not a command.
    @MainActor static func parse(_ text: String) -> (SlashCommand, String)? {
        guard text.hasPrefix("/") else { return nil }
        let body = text.dropFirst()
        let word = body.prefix { !$0.isWhitespace && !$0.isNewline }
        guard let command = named(String(word)) else { return nil }
        return (command, String(body.dropFirst(word.count)).trimmingCharacters(in: .whitespacesAndNewlines))
    }

    @MainActor private static func studio(_ kind: StudioItem.Kind, _ name: String, hint: String) -> SlashCommand {
        SlashCommand(name: name, title: kind.label, symbol: kind.symbol, hint: hint, group: .action) { text, model in
            model.make(kind, subject: text)
        }
    }
}
