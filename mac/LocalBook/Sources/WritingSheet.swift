import AppKit
import LocalBookCore
import LocalBookEngines
import SwiftUI

/// The writing assistant: paste or type, pick a task and a style, and the answer model rewrites with the notebook's
/// passages as reference when asked. The result can be copied, saved as a note, or put back in the box to go again.
struct WritingSheet: View {
    let notebook: LibraryNotebookInfo?
    let library: LibraryModel
    let engine: InsightEngine
    @Environment(\.dismiss) private var dismiss
    @State private var input = ""
    @State private var output = ""
    @State private var task: WritingAssistant.Task = .improve
    @State private var format: WritingAssistant.Format = .professional
    @State private var useNotebook = true
    @State private var length = 300
    @State private var savedLine: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("Writing assistant", systemImage: "pencil.and.outline").font(.headline)
                Spacer()
                if let n = notebook { Text("Reference: \(n.title)").font(.caption).foregroundStyle(.secondary) }
            }
            HStack(spacing: 12) {
                Picker("Task", selection: $task) { ForEach(WritingAssistant.Task.allCases) { Text($0.label).tag($0) } }
                Picker("Style", selection: $format) { ForEach(WritingAssistant.Format.allCases) { Text($0.label).tag($0) } }
                Stepper("About \(length) words", value: $length, in: 50...2_000, step: 50)
            }
            Text(task.blurb).font(.callout).foregroundStyle(.secondary)
            Toggle("Use the notebook's passages as reference", isOn: $useNotebook).disabled(notebook == nil)
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Your text").font(.caption).foregroundStyle(.secondary)
                    TextEditor(text: $input).font(.body).frame(minHeight: 260)
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(output.isEmpty ? "Result" : "Result · \(WritingAssistant.wordCount(output)) words").font(.caption).foregroundStyle(.secondary)
                    TextEditor(text: $output).font(.body).frame(minHeight: 260)
                        .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
                }
            }
            if engine.working { HStack { ProgressView().controlSize(.small); Text(engine.status).font(.callout).foregroundStyle(.secondary) } }
            if let why = engine.error { Text(why).font(.callout).foregroundStyle(.orange) }
            if let savedLine { Text(savedLine).font(.callout).foregroundStyle(.secondary) }
            HStack {
                Button("Close") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Use result as text") { input = output; output = "" }.disabled(output.isEmpty)
                Button("Copy") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(output, forType: .string) }.disabled(output.isEmpty)
                Button("Save as note") { Task { await saveNote() } }.disabled(output.isEmpty || notebook == nil)
                if engine.working { Button("Stop") { engine.stop() } }
                else { Button("Write") { Task { await run() } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }
            }
        }
        .padding(20)
        .frame(width: 820, height: 560)
        .interactiveDismissDisabled(engine.working)
    }

    private func run() async {
        savedLine = nil
        if let made = await engine.write(input, task: task, format: format, maxWords: length, notebook: useNotebook ? notebook : nil, library: library) { output = made }
    }

    private func saveNote() async {
        guard let notebook else { return }
        let title = output.split(separator: "\n").first.map { String($0.trimmingCharacters(in: CharacterSet(charactersIn: "# ")).prefix(80)) } ?? "Writing"
        do {
            try await library.addNote(notebook: notebook.id, title: title.isEmpty ? "Writing" : title, text: output)
            savedLine = "Saved under Notes as “\(title)”."
            NotificationCenter.default.post(name: .librarySynced, object: nil)
        } catch { savedLine = "Could not save the note: \(error.localizedDescription)" }
    }
}
