import LocalBookCore
import LocalBookEngines
import SwiftUI

/// Notebooks in the desktop app's order with its sections and colors, then the three cross-notebook places. A SwiftUI
/// `List` with the sidebar style; if selection, focus or density feel wrong on the Mac it becomes an `NSOutlineView`,
/// as the plan allows, without changing anything else.
struct Sidebar: View {
    @Environment(AppModel.self) private var model
    @Binding var selection: AppModel.Destination?

    var body: some View {
        List(selection: $selection) {
            if model.library.notebooks.isEmpty {
                Section("Notebooks") {
                    Text(model.paired ? "Syncing notebooks…" : "Pair with your Mac to bring your notebooks here.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 4)
                }
            } else {
                ForEach(model.sections) { section in
                    Section(section.name) {
                        ForEach(section.notebooks) { notebook in
                            Label {
                                Text(notebook.title)
                            } icon: {
                                Image(systemName: "book.closed")
                                    .foregroundStyle(Color(hex: notebook.color) ?? .accentColor)
                            }
                            .tag(AppModel.Destination.notebook(notebook.id))
                            .badge(notebook.chunks)
                            .help("\(notebook.chunks) passages")
                            .acceptsDrops(for: notebook.id)
                        }
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
