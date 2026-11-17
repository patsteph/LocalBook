import AppKit
import Combine
import LocalBookCore
import LocalBookEngines
import SwiftUI
import UniformTypeIdentifiers

/// File › Export Notebook… (⇧⌘E) and Export… on a source. The notebook goes out as the old app's Markdown or as a page
/// (print it to PDF from the browser, as the old app's PDF did), or as a folder holding every source's text and kept
/// original; a single source goes out as its text or its original. All through the standard save panel.
@MainActor
enum Exporter {
    enum NotebookFormat: String, CaseIterable, Identifiable {
        case markdown = "Markdown", html = "Web page", folder = "Folder of files"
        var id: String { rawValue }
    }

    /// The accessory under the save panel: the format and whether source text goes along.
    final class Options: ObservableObject {
        @Published var format: NotebookFormat = .markdown
        @Published var includeContent = true
    }

    struct Accessory: View {
        @ObservedObject var options: Options
        var body: some View {
            VStack(alignment: .leading, spacing: 8) {
                Picker("Format:", selection: $options.format) {
                    ForEach(NotebookFormat.allCases) { Text($0.rawValue).tag($0) }
                }
                .frame(width: 280)
                Toggle("Include the text of each source", isOn: $options.includeContent)
                    .disabled(options.format == .folder)
            }
            .padding(12)
        }
    }

    /// Everything the export needs, gathered before the panel so the file is written at once on Save.
    static func gather(_ notebook: LibraryNotebookInfo, library: LibraryModel) async -> (NotebookExport.Notebook, [(LibraryStore.SourceInfo, String)]) {
        var sources: [NotebookExport.Source] = []
        var texts: [(LibraryStore.SourceInfo, String)] = []
        for s in await library.sources(in: notebook.id) {
            let text = await library.sourceText(notebook: notebook.id, sourceId: s.id)
            let url = await library.sourceURL(notebook: notebook.id, sourceId: s.id)
            sources.append(.init(name: s.name, format: s.type, chunks: s.pieces, characters: s.characters, url: url, text: text))
            texts.append((s, text))
        }
        let notes = await library.localNotes().filter { $0.notebookId == notebook.id }.map { NotebookExport.Note(title: $0.title, text: $0.text) }
        let exchanges = library.history.filter { $0.notebookId == notebook.id && !$0.answer.isEmpty }.map { t in
            NotebookExport.Exchange(question: t.question, answer: t.answer,
                                    citations: t.sources.enumerated().map { .init(number: $0.offset + 1, filename: $0.element.filename, snippet: $0.element.text) })
        }
        return (NotebookExport.Notebook(title: notebook.title, sources: sources, notes: notes, exchanges: exchanges), texts)
    }

    /// The save panel for `notebook`; writes on Save and returns a problem, if any.
    static func exportNotebook(_ notebook: LibraryNotebookInfo, library: LibraryModel) async -> String? {
        let (export, texts) = await gather(notebook, library: library)
        let options = Options()
        let panel = NSSavePanel()
        panel.title = "Export Notebook"
        panel.nameFieldStringValue = NotebookExport.fileName(notebook.title) + ".md"
        panel.allowedContentTypes = [UTType(filenameExtension: "md") ?? .plainText, .html, .folder]
        panel.canCreateDirectories = true
        panel.allowsOtherFileTypes = true
        panel.accessoryView = NSHostingView(rootView: Accessory(options: options))
        panel.isExtensionHidden = false
        // The panel's name follows the format, so what the Finder shows is what was chosen.
        let watch = options.$format.sink { f in
            let base = (panel.nameFieldStringValue as NSString).deletingPathExtension
            switch f {
            case .markdown: panel.nameFieldStringValue = base + ".md"
            case .html: panel.nameFieldStringValue = base + ".html"
            case .folder: panel.nameFieldStringValue = base
            }
        }
        defer { watch.cancel() }
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        do {
            switch options.format {
            case .markdown:
                try NotebookExport.markdown(export, includeContent: options.includeContent).write(to: url, atomically: true, encoding: .utf8)
            case .html:
                try NotebookExport.html(export, includeContent: options.includeContent).write(to: url, atomically: true, encoding: .utf8)
            case .folder:
                try writeFolder(url, export: export, texts: texts)
            }
            NSWorkspace.shared.activateFileViewerSelecting([url])
            return nil
        } catch {
            return "The export could not be written: \(error.localizedDescription)"
        }
    }

    /// A folder: `<notebook>.md` (the summary without the texts), `Sources/<name>.md` for every source's text, the kept
    /// original beside it when there is one, and `Notes/<title>.md`.
    static func writeFolder(_ folder: URL, export: NotebookExport.Notebook, texts: [(LibraryStore.SourceInfo, String)]) throws {
        let fm = FileManager.default
        try fm.createDirectory(at: folder, withIntermediateDirectories: true)
        try NotebookExport.markdown(export, includeContent: false).write(to: folder.appending(path: NotebookExport.fileName(export.title) + ".md"), atomically: true, encoding: .utf8)
        if !texts.isEmpty {
            let sources = folder.appending(path: "Sources", directoryHint: .isDirectory)
            try fm.createDirectory(at: sources, withIntermediateDirectories: true)
            var used: Set<String> = []
            for (s, text) in texts {
                let base = unique(NotebookExport.fileName((s.name as NSString).deletingPathExtension), in: &used)
                try text.write(to: sources.appending(path: base + ".md"), atomically: true, encoding: .utf8)
                if let file = s.file {
                    let original = LibraryModel.originalURL(file)
                    let copy = sources.appending(path: base + "." + original.pathExtension)
                    if fm.fileExists(atPath: original.path), !fm.fileExists(atPath: copy.path) { try? fm.copyItem(at: original, to: copy) }
                }
            }
        }
        if !export.notes.isEmpty {
            let notes = folder.appending(path: "Notes", directoryHint: .isDirectory)
            try fm.createDirectory(at: notes, withIntermediateDirectories: true)
            var used: Set<String> = []
            for n in export.notes {
                try n.text.write(to: notes.appending(path: unique(NotebookExport.fileName(n.title), in: &used) + ".md"), atomically: true, encoding: .utf8)
            }
        }
    }

    static func unique(_ name: String, in used: inout Set<String>) -> String {
        var candidate = name, n = 2
        while used.contains(candidate.lowercased()) { candidate = "\(name) \(n)"; n += 1 }
        used.insert(candidate.lowercased())
        return candidate
    }

    /// Export… on one source: its text as Markdown, or the kept original when `original` is set.
    static func exportSource(_ source: LibraryStore.SourceInfo, original: Bool, library: LibraryModel) async -> String? {
        let panel = NSSavePanel()
        panel.title = original ? "Export Original" : "Export Source"
        panel.canCreateDirectories = true
        let originalURL = source.file.map(LibraryModel.originalURL)
        if original, let originalURL {
            panel.nameFieldStringValue = originalURL.lastPathComponent
            if let type = UTType(filenameExtension: originalURL.pathExtension) { panel.allowedContentTypes = [type] }
        } else {
            panel.nameFieldStringValue = NotebookExport.fileName((source.name as NSString).deletingPathExtension) + ".md"
            panel.allowedContentTypes = [UTType(filenameExtension: "md") ?? .plainText, .plainText]
        }
        let text = original ? "" : await library.sourceText(notebook: source.notebookId, sourceId: source.id)
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        do {
            if original, let originalURL {
                if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
                try FileManager.default.copyItem(at: originalURL, to: url)
            } else {
                try text.write(to: url, atomically: true, encoding: .utf8)
            }
            NSWorkspace.shared.activateFileViewerSelecting([url])
            return nil
        } catch {
            return "The export could not be written: \(error.localizedDescription)"
        }
    }
}
