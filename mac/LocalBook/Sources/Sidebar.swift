import LocalBookCore
import LocalBookEngines
import SwiftUI

/// Notebooks in the desktop app's order with its sections and colors, each opening to its sources, then the three
/// cross-notebook places. Every action the desktop sidebar has is in the context menus; each goes through the paired
/// Mac (the system of record until Phase 3) and syncs back.
struct Sidebar: View {
    @Environment(AppModel.self) private var model
    @Binding var selection: AppModel.Destination?
    @State private var expanded: Set<String> = []
    @State private var sources: [String: [LibraryStore.SourceInfo]] = [:]

    var body: some View {
        List(selection: $selection) {
            if model.library.notebooks.isEmpty {
                Section("Notebooks") {
                    Text(model.paired ? "Syncing notebooks…" : "Pair with your Mac to bring your notebooks here.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 4)
                    if !model.paired {
                        Button("Move my library here…") { model.showImport = true }.buttonStyle(.link).font(.callout)
                    }
                }
            } else {
                ForEach(model.sections) { section in
                    Section {
                        ForEach(section.notebooks) { notebook in notebookRow(notebook) }
                    } header: {
                        Text(section.name).contextMenu { sectionMenu(section) }
                    }
                }
            }
            Section("Library") {
                Label("Studio", systemImage: "sparkles").tag(AppModel.Destination.studio)
                Label("Meetings", systemImage: "waveform").tag(AppModel.Destination.meetings)
                Label("Notes", systemImage: "note.text").tag(AppModel.Destination.notes)
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom) { SyncStatusLine().padding(.horizontal, 12).padding(.vertical, 8) }
        .onChange(of: expanded) { _, now in Task { await load(now) } }
        .onReceive(NotificationCenter.default.publisher(for: .librarySynced)) { _ in Task { await load(expanded, fresh: true) } }
    }

    private func notebookRow(_ notebook: LibraryNotebookInfo) -> some View {
        DisclosureGroup(isExpanded: Binding(get: { expanded.contains(notebook.id) },
                                            set: { if $0 { expanded.insert(notebook.id) } else { expanded.remove(notebook.id) } })) {
            if let list = sources[notebook.id] {
                if list.isEmpty {
                    Text(notebook.chunks == 0 ? "Nothing copied yet" : "No sources").font(.callout).foregroundStyle(.tertiary)
                }
                ForEach(list) { s in
                    Label {
                        Text(s.name).lineLimit(1)
                    } icon: { Image(systemName: SourceKinds.symbol(s.type)) }
                    .tag(AppModel.Destination.source(SourceWindowKey(notebookId: s.notebookId, sourceId: s.id, name: s.name)))
                    .help(SourceKinds.detail(s))
                    .contextMenu { SourceContextMenu(source: s) }
                }
            } else {
                ProgressView().controlSize(.small)
            }
        } label: {
            Label {
                Text(notebook.title)
            } icon: {
                Image(systemName: "book.closed")
                    .foregroundStyle(Color(hex: notebook.color) ?? .accentColor)
            }
            .badge(notebook.chunks)
            .help("\(notebook.chunks) passages")
            .acceptsDrops(for: notebook.id)
        }
        .tag(AppModel.Destination.notebook(notebook.id))
        .contextMenu { NotebookContextMenu(notebook: notebook) }
    }

    @ViewBuilder private func sectionMenu(_ section: AppModel.NotebookSection) -> some View {
        if let s = model.macSections.first(where: { $0.name == section.name }) {
            Button("Rename Section…") { model.prompts.text = s.name; model.prompts.renameSection = s }
            Button("Delete Section…", role: .destructive) { model.prompts.deleteSection = s }
            Divider()
        }
        Button("New Section…") { model.prompts.text = ""; model.prompts.newSection = true }
        Button("New Notebook…") { model.prompts.text = ""; model.prompts.newNotebook = true }
    }

    /// The sources of every open notebook; only the ones not loaded yet unless `fresh`.
    private func load(_ ids: Set<String>, fresh: Bool = false) async {
        for id in ids where fresh || sources[id] == nil {
            sources[id] = await model.library.sources(in: id)
        }
    }
}

/// What the copy on this Mac holds and what sync is doing, at the foot of the sidebar: the answer to "is it done?"
/// without opening Settings. Progress while a sync runs, the failure when it fails, the totals otherwise.
struct SyncStatusLine: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let library = model.library
        VStack(alignment: .leading, spacing: 4) {
            switch library.syncState {
            case .syncing(let line):
                HStack(spacing: 6) { ProgressView().controlSize(.mini); Text("Syncing…").font(.caption.weight(.semibold)) }
                Text(line).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
            case .failed(let why):
                Label("Sync failed", systemImage: "exclamationmark.triangle.fill").font(.caption.weight(.semibold)).foregroundStyle(.orange)
                Text(why).font(.caption2).foregroundStyle(.secondary).lineLimit(3)
            case .idle:
                if model.paired {
                    Text("\(library.notebooks.count) notebook\(library.notebooks.count == 1 ? "" : "s") · \(library.chunkCount.formatted()) passages")
                        .font(.caption.weight(.semibold))
                    Text(library.lastSyncLine + (library.lastSummary.map { " · \($0.changeLine)" } ?? "")).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

extension Color {
    /// `#RRGGBB` or `RRGGBB`, the form the desktop app stores notebook colors in.
    init?(hex: String?) {
        guard let hex else { return nil }
        var s = Substring(hex)
        if s.hasPrefix("#") { s = s.dropFirst() }
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return nil }
        self.init(red: Double((v >> 16) & 0xFF) / 255, green: Double((v >> 8) & 0xFF) / 255, blue: Double(v & 0xFF) / 255)
    }
}
