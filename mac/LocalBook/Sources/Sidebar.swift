import SwiftUI

/// Notebooks in the desktop app's order, then the three cross-notebook places. The list is a SwiftUI `List` with
/// the sidebar style; if selection, focus or density feel wrong on the Mac it becomes an `NSOutlineView`, as the
/// plan allows, without changing anything else.
struct Sidebar: View {
    @Environment(AppModel.self) private var model
    @Binding var selection: AppModel.Destination?

    var body: some View {
        List(selection: $selection) {
            if model.notebooks.isEmpty {
                Section("Notebooks") {
                    Text(model.isPaired ? "Syncing notebooks…" : "Pair with your Mac to bring your notebooks here.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 4)
                }
            } else {
                ForEach(model.sections) { section in
                    Section(section.name) {
                        ForEach(section.notebooks) { notebook in
                            Label {
                                Text(notebook.name)
                            } icon: {
                                Image(systemName: "book.closed")
                                    .foregroundStyle(Color(hex: notebook.colorHex) ?? .accentColor)
                            }
                            .tag(AppModel.Destination.notebook(notebook.id))
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
