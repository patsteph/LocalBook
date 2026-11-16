import LocalBookCore
import LocalBookEngines
import SwiftUI

/// Notebooks in the desktop app's order with its sections and colors, then the three cross-notebook places. Every
/// action the desktop sidebar has is in the context menus; each goes through the paired Mac (the system of record
/// until Phase 3) and syncs back. Sources live behind the toolbar's Sources button, not here.
struct Sidebar: View {
    @Environment(AppModel.self) private var model
    @Binding var selection: AppModel.Destination?

    var body: some View {
        VStack(spacing: 0) {
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
            Divider()
            SyncStatusDot().padding(.horizontal, 12).padding(.vertical, 6)
        }
    }

    private func notebookRow(_ notebook: LibraryNotebookInfo) -> some View {
        Label {
            Text(notebook.title).lineLimit(1)
        } icon: {
            Image(systemName: "book.closed")
                .foregroundStyle(Color(hex: notebook.color) ?? .accentColor)
        }
        .acceptsDrops(for: notebook.id)
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
}

/// One line at the foot of the sidebar: a colored dot and two words. The totals, the last sync and any failure are
/// in the tooltip, so the line never grows and never covers the list above it.
struct SyncStatusDot: View {
    @Environment(AppModel.self) private var model

    private var state: (color: Color, word: String, detail: String) {
        let library = model.library
        let totals = "\(library.notebooks.count) notebook\(library.notebooks.count == 1 ? "" : "s") · \(library.chunkCount.formatted()) passages"
        switch library.syncState {
        case .syncing(let line):
            return (.yellow, "Syncing…", line)
        case .failed(let why):
            return (.red, "Sync failed", why + "\n" + totals)
        case .idle:
            guard model.paired else { return (.gray, "Not paired", "Pair with your Mac in Settings › Sync, or move your library here.") }
            return (.green, "Synced", library.lastSyncLine + (library.lastSummary.map { " · \($0.changeLine)" } ?? "") + "\n" + totals)
        }
    }

    var body: some View {
        let s = state
        HStack(spacing: 6) {
            Circle().fill(s.color).frame(width: 8, height: 8)
            Text(s.word).font(.caption).foregroundStyle(.secondary)
            Spacer(minLength: 0)
        }
        .help(s.detail)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(s.word). \(s.detail)")
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
