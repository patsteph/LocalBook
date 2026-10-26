import SwiftUI

/// Everything a notebook holds, like the Mac's Sources list: search it, filter by kind, open a source to read it.
/// Notes made on this phone that haven't reached the Mac yet can be edited here.
struct SourcesView: View {
    let library: LibraryModel
    let notebook: LibraryNotebookInfo
    @Environment(\.dismiss) private var dismiss
    @State private var sources: [LibraryStore.SourceInfo] = []
    @State private var loaded = false
    @State private var search = ""
    @State private var kind: String?

    private static func label(_ type: String) -> String {
        switch type.lowercased() {
        case "pdf": "PDFs"
        case "web", "url", "article", "webpage": "Web pages"
        case "youtube", "video": "Videos"
        case "audio": "Audio"
        case "image": "Pictures"
        case "note", "text", "markdown", "md": "Notes"
        case "docx", "doc", "pptx", "xlsx", "csv": "Documents"
        case "": "Other"
        default: type.capitalized
        }
    }

    private static func symbol(_ type: String) -> String {
        switch type.lowercased() {
        case "pdf": "doc.richtext"
        case "web", "url", "article", "webpage": "globe"
        case "youtube", "video": "play.rectangle"
        case "audio": "waveform"
        case "image": "photo"
        case "note", "text", "markdown", "md": "note.text"
        default: "doc.text"
        }
    }

    private var kinds: [(label: String, count: Int)] {
        Dictionary(grouping: sources, by: { Self.label($0.type) }).map { ($0.key, $0.value.count) }.sorted { $0.count > $1.count }
    }

    private var shown: [LibraryStore.SourceInfo] {
        sources.filter { s in
            (kind == nil || Self.label(s.type) == kind)
                && (search.isEmpty || s.name.localizedCaseInsensitiveContains(search))
        }
    }

    var body: some View {
        NavigationStack {
            List {
                if kinds.count > 1 {
                    Section {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 8) {
                                chip("All", count: sources.count, on: kind == nil) { kind = nil }
                                ForEach(kinds, id: \.label) { k in chip(k.label, count: k.count, on: kind == k.label) { kind = (kind == k.label ? nil : k.label) } }
                            }
                        }
                        .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
                        .listRowBackground(Color.clear)
                    }
                }
                if loaded && shown.isEmpty {
                    Text(sources.isEmpty ? "This notebook has no sources on this phone yet." : "Nothing matches.").foregroundStyle(.secondary)
                }
                ForEach(shown) { s in
                    NavigationLink {
                        SourceDetail(library: library, source: s)
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: Self.symbol(s.type)).foregroundStyle(.tint).frame(width: 26)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(s.name).lineLimit(2)
                                Text(detail(s)).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .searchable(text: $search, prompt: "Search sources")
            .navigationTitle(notebook.title).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } } }
            .task { await reload() }
            .refreshable { await reload() }
        }
    }

    private func detail(_ s: LibraryStore.SourceInfo) -> String {
        let words = s.characters / 6
        let size = words >= 1000 ? "\(words / 1000)k words" : "\(max(1, words)) words"
        return Self.label(s.type) + " · " + size + (s.isLocal ? " · made on this phone" : "")
    }

    private func chip(_ title: String, count: Int, on: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text("\(title) \(count)").font(.footnote.weight(.medium))
                .padding(.horizontal, 12).padding(.vertical, 6)
                .background(on ? Color.accentColor : Color.secondary.opacity(0.15), in: Capsule())
                .foregroundStyle(on ? Color.white : Color.primary)
        }.buttonStyle(.plain)
    }

    private func reload() async {
        sources = await library.sources(in: notebook.id)
        loaded = true
    }
}

private struct SourceDetail: View {
    let library: LibraryModel
    let source: LibraryStore.SourceInfo
    @State private var text = ""
    @State private var editing = false
    @State private var canEdit = false

    var body: some View {
        ScrollView {
            Text(text.isEmpty ? "…" : text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding()
        }
        .navigationTitle(source.name).navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if canEdit { ToolbarItem(placement: .primaryAction) { Button("Edit") { editing = true } } }
            ToolbarItem(placement: .secondaryAction) { ShareLink(item: text) }
        }
        .task { await load() }
        .sheet(isPresented: $editing, onDismiss: { Task { await load() } }) {
            SourceEditor(library: library, source: source, text: text)
        }
    }

    private func load() async {
        text = await library.sourceText(notebook: source.notebookId, sourceId: source.id)
        // Only notes still waiting on this phone can be changed here; once the Mac has them they live there.
        canEdit = source.isLocal && (await library.noteSentSourceId(source.id)) == nil
    }
}

private struct SourceEditor: View {
    let library: LibraryModel
    let source: LibraryStore.SourceInfo
    @State var text: String
    @State private var title: String
    @State private var saving = false
    @State private var error: String?
    @Environment(\.dismiss) private var dismiss

    init(library: LibraryModel, source: LibraryStore.SourceInfo, text: String) {
        self.library = library; self.source = source
        _text = State(initialValue: text); _title = State(initialValue: source.name)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section { TextField("Title", text: $title) }
                Section { TextEditor(text: $text).frame(minHeight: 300) }
                if let error { Section { Text(error).font(.footnote).foregroundStyle(.orange) } }
            }
            .navigationTitle("Edit").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }
                        .disabled(saving || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
        .interactiveDismissDisabled(saving)
    }

    private func save() async {
        saving = true; defer { saving = false }
        do {
            try await library.addNote(notebook: source.notebookId, title: title, text: text, kind: source.type.isEmpty ? "note" : source.type, replacing: source.id)
            dismiss()
        } catch { self.error = "Couldn't save: \(error.localizedDescription)" }
    }
}
