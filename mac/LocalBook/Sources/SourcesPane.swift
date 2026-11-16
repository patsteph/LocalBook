import AppKit
import LocalBookCore
import LocalBookEngines
import SwiftUI

/// What a source is called and looks like in a list, the same words the phone uses.
enum SourceKinds {
    static func label(_ type: String) -> String {
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

    static func symbol(_ type: String) -> String {
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

    static func detail(_ s: LibraryStore.SourceInfo) -> String {
        let words = s.characters / 6
        let size = words >= 1000 ? "\(words / 1000)k words" : "\(max(1, words)) words"
        var parts = [label(s.type)]
        if let d = StudioPull.parseDate(s.created) { parts.append(d.formatted(date: .abbreviated, time: .omitted)) }
        parts.append(size)
        if s.isLocal { parts.append("made on this Mac") }
        return parts.joined(separator: " · ")
    }
}

/// Names a source for its own window. Strings only, so the window can be restored after a relaunch.
struct SourceWindowKey: Codable, Hashable {
    let notebookId: String
    let sourceId: String
    let name: String
}

/// Everything the selected notebook holds, like the desktop's Sources list: the list on the left, the source on the
/// right; filter by kind, search, order. Reading only in this slice; adding sources is Phase 3 (queued to the Python
/// host until then).
struct SourcesPane: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    @State private var inbox: [URL] = []
    @State private var sources: [LibraryStore.SourceInfo] = []
    @State private var loaded = false
    @State private var search = ""
    @State private var kind: String?
    @State private var newestFirst = true
    @State private var selected: String?

    private var notebook: LibraryNotebookInfo? { model.selectedNotebook }

    private var kinds: [(label: String, count: Int)] {
        Dictionary(grouping: sources, by: { SourceKinds.label($0.type) }).map { ($0.key, $0.value.count) }.sorted { $0.count > $1.count }
    }

    private var shown: [LibraryStore.SourceInfo] {
        let kept = sources.filter { s in
            (kind == nil || SourceKinds.label(s.type) == kind)
                && (search.isEmpty || s.name.localizedCaseInsensitiveContains(search))
        }
        return newestFirst ? kept : kept.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    var body: some View {
        HSplitView {
            list.frame(minWidth: 260, idealWidth: 320, maxWidth: 460)
            detail.frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity)
        }
        .task(id: notebook?.id) { await reload(); inbox = notebook.map { IntakeModel.queued($0.id) } ?? [] }
        .onReceive(NotificationCenter.default.publisher(for: .librarySynced)) { _ in Task { await reload(); inbox = notebook.map { IntakeModel.queued($0.id) } ?? [] } }
        .onReceive(NotificationCenter.default.publisher(for: .detachItem)) { _ in
            if let s = shown.first(where: { $0.id == selected }) { openWindow(id: "source", value: key(s)) }
        }
    }

    private var list: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                TextField("Filter sources", text: $search).textFieldStyle(.roundedBorder)
                Menu {
                    Picker("Order", selection: $newestFirst) {
                        Text("Newest first").tag(true)
                        Text("A to Z").tag(false)
                    }
                    .pickerStyle(.inline)
                } label: { Image(systemName: "arrow.up.arrow.down") }
                    .menuStyle(.borderlessButton).fixedSize()
                    .help("Order")
            }
            .padding(10)
            if kinds.count > 1 {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        chip("All", count: sources.count, on: kind == nil) { kind = nil }
                        ForEach(kinds, id: \.label) { k in chip(k.label, count: k.count, on: kind == k.label) { kind = (kind == k.label ? nil : k.label) } }
                    }
                    .padding(.horizontal, 10).padding(.bottom, 8)
                }
            }
            Divider()
            List(selection: $selected) {
                if loaded && shown.isEmpty {
                    Text(sources.isEmpty ? "This notebook has no sources on this Mac yet." : "Nothing matches.")
                        .foregroundStyle(.secondary).font(.callout)
                }
                ForEach(shown) { s in
                    HStack(spacing: 10) {
                        Image(systemName: SourceKinds.symbol(s.type)).foregroundStyle(.tint).frame(width: 22)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(s.name).lineLimit(2)
                            Text(SourceKinds.detail(s)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                    }
                    .tag(s.id)
                    .contextMenu { SourceContextMenu(source: s) }
                }
                if !inbox.isEmpty {
                    Section {
                        ForEach(inbox, id: \.self) { url in
                            HStack(spacing: 10) {
                                Image(systemName: "tray.and.arrow.down").foregroundStyle(.orange).frame(width: 22)
                                Text(url.lastPathComponent).lineLimit(1)
                            }
                            .contextMenu {
                                Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([url]) }
                                Button("Remove", role: .destructive) { try? FileManager.default.removeItem(at: url); inbox = notebook.map { IntakeModel.queued($0.id) } ?? [] }
                            }
                        }
                    } header: { Text("Inbox · waiting to become sources") } footer: {
                        Text("Dropped files this Mac cannot read yet. They are added as sources when this Mac hosts the library (Phase 3).").font(.caption)
                    }
                }
            }
            .listStyle(.inset)
        }
    }

    @ViewBuilder private var detail: some View {
        if let s = sources.first(where: { $0.id == selected }) {
            SourceDetail(source: s)
                .id(s.id)
        } else {
            ContentUnavailableView {
                Label(notebook?.title ?? "Sources", systemImage: "doc.text")
            } description: {
                Text(sources.isEmpty ? "Sources arrive with the next sync." : "Choose a source to read it. Press ⌘↓ to open it in its own window.")
            }
        }
    }

    private func key(_ s: LibraryStore.SourceInfo) -> SourceWindowKey {
        SourceWindowKey(notebookId: s.notebookId, sourceId: s.id, name: s.name)
    }

    private func chip(_ title: String, count: Int, on: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text("\(title) \(count)").font(.footnote.weight(.medium))
                .padding(.horizontal, 10).padding(.vertical, 4)
                .background(on ? Color.accentColor : Color.secondary.opacity(0.15), in: Capsule())
                .foregroundStyle(on ? Color.white : Color.primary)
        }.buttonStyle(.plain)
    }

    private func reload() async {
        guard let id = notebook?.id else { sources = []; loaded = true; return }
        sources = await model.library.sources(in: id)
        if selected != nil, !sources.contains(where: { $0.id == selected }) { selected = nil }
        loaded = true
    }
}

/// Opens the source in its own window (⌘↓ from a row).
struct SourceOpenButton: View {
    let key: SourceWindowKey
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        Button { openWindow(id: "source", value: key) } label: { Label("Open in Window", systemImage: "macwindow") }
            .keyboardShortcut(.downArrow, modifiers: .command)
    }
}

/// One source, readable, with rename, copy and share. Notes made on this Mac that have not reached the paired Mac yet
/// can be edited (the same rule as the phone).
struct SourceDetail: View {
    let source: LibraryStore.SourceInfo
    @Environment(AppModel.self) private var model
    @State private var text = ""
    @State private var renaming = false
    @State private var newName = ""
    @State private var shownName: String?
    @State private var renameError: String?
    @State private var busy = false

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: SourceKinds.symbol(source.type)).foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: 1) {
                    Text(shownName ?? source.name).font(.headline).lineLimit(1)
                    Text(SourceKinds.detail(source)).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if busy { ProgressView().controlSize(.small) }
                Button { newName = shownName ?? source.name; renaming = true } label: { Image(systemName: "pencil") }
                    .help("Rename")
                Button { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string) } label: { Image(systemName: "doc.on.doc") }
                    .help("Copy text")
                ShareLink(item: text) { Image(systemName: "square.and.arrow.up") }.help("Share")
                SourceOpenButton(key: SourceWindowKey(notebookId: source.notebookId, sourceId: source.id, name: source.name))
                    .labelStyle(.iconOnly).help("Open in Window (⌘↓)")
            }
            .buttonStyle(.borderless)
            .padding(12)
            Divider()
            if let renameError { Text(renameError).font(.footnote).foregroundStyle(.orange).padding([.horizontal, .top]) }
            ScrollView {
                Text(text.isEmpty ? "…" : text).textSelection(.enabled).lineSpacing(3)
                    .frame(maxWidth: 760, alignment: .leading).padding(20).frame(maxWidth: .infinity)
            }
        }
        .task { text = await model.library.sourceText(notebook: source.notebookId, sourceId: source.id) }
        .alert("Rename", isPresented: $renaming) {
            TextField("Name", text: $newName)
            Button("Save") { Task {
                renameError = nil; busy = true
                renameError = await model.library.renameSource(source, to: newName)
                busy = false
                if renameError == nil { shownName = newName.trimmingCharacters(in: .whitespacesAndNewlines) }
            } }
            Button("Cancel", role: .cancel) {}
        }
    }
}

/// A source chosen in the sidebar: the same reader as the Sources pane, in the window's detail.
struct SourceScreen: View {
    let key: SourceWindowKey
    @Environment(AppModel.self) private var model
    @State private var source: LibraryStore.SourceInfo?
    @State private var looked = false

    var body: some View {
        Group {
            if let source {
                SourceDetail(source: source).id(source.id)
            } else if looked {
                ContentUnavailableView("This source is gone", systemImage: "doc.text", description: Text("It is no longer in \(model.library.notebooks.first { $0.id == key.notebookId }?.title ?? "the notebook")."))
            } else {
                ProgressView()
            }
        }
        .task(id: key) { await find() }
        .onReceive(NotificationCenter.default.publisher(for: .librarySynced)) { _ in Task { await find() } }
    }

    private func find() async {
        source = await model.library.sources(in: key.notebookId).first { $0.id == key.sourceId }
        looked = true
    }
}

/// The source viewer: one source in its own window, from a citation or a source row.
struct SourceWindow: View {
    let key: SourceWindowKey
    @Environment(AppModel.self) private var model
    @State private var text = ""

    var body: some View {
        ScrollView {
            Text(text.isEmpty ? "…" : text).textSelection(.enabled).lineSpacing(3)
                .frame(maxWidth: 760, alignment: .leading).padding(24).frame(maxWidth: .infinity)
        }
        .navigationTitle(key.name)
        .navigationSubtitle(model.library.notebooks.first { $0.id == key.notebookId }?.title ?? "")
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string) } label: { Label("Copy", systemImage: "doc.on.doc") }
                ShareLink(item: text) { Label("Share", systemImage: "square.and.arrow.up") }
            }
        }
        .task {
            await model.library.open()
            text = await model.library.sourceText(notebook: key.notebookId, sourceId: key.sourceId)
        }
        .frame(minWidth: 480, minHeight: 360)
    }
}

/// The inspector (⌥⌘I): the current notebook's sources in a compact list. Selecting sources to scope a question
/// arrives with the retrieval work later in Phase 2.
struct InspectorView: View {
    @Environment(AppModel.self) private var model
    @State private var sources: [LibraryStore.SourceInfo] = []

    private var notebook: LibraryNotebookInfo? { model.selectedNotebook }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(notebook?.title ?? "Sources").font(.headline).lineLimit(1)
                Spacer()
                Text("\(sources.count)").font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
            .padding(12)
            Divider()
            List {
                if sources.isEmpty {
                    Text(notebook == nil ? "Choose a notebook." : "No sources on this Mac yet.").font(.callout).foregroundStyle(.secondary)
                }
                ForEach(sources) { s in
                    Label {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(s.name).lineLimit(2)
                            Text(SourceKinds.label(s.type)).font(.caption2).foregroundStyle(.secondary)
                        }
                    } icon: { Image(systemName: SourceKinds.symbol(s.type)) }
                    .contextMenu { SourceContextMenu(source: s) }
                }
            }
            .listStyle(.sidebar)
            Divider()
            Button("Show All Sources") { model.selection = .sources }
                .buttonStyle(.link).font(.callout).padding(10)
                .disabled(notebook == nil)
        }
        .task(id: notebook?.id) { await reload() }
        .onReceive(NotificationCenter.default.publisher(for: .librarySynced)) { _ in Task { await reload() } }
    }

    private func reload() async {
        guard let id = notebook?.id else { sources = []; return }
        sources = await model.library.sources(in: id)
    }
}

extension Notification.Name {
    /// Posted by `AppModel` when a sync finishes, so open lists reload.
    static let librarySynced = Notification.Name("LocalBook.librarySynced")
}
