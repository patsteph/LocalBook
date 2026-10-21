import SwiftUI

extension Color {
    /// The desktop stores a notebook's colour as "#RRGGBB".
    init?(hex: String?) {
        guard var h = hex?.trimmingCharacters(in: .whitespaces), !h.isEmpty else { return nil }
        if h.hasPrefix("#") { h.removeFirst() }
        guard h.count == 6, let v = UInt32(h, radix: 16) else { return nil }
        self.init(red: Double((v >> 16) & 255) / 255, green: Double((v >> 8) & 255) / 255, blue: Double(v & 255) / 255)
    }
}

/// The desktop's list: unsectioned notebooks first, then each section (folded as the Mac has it), each
/// notebook with its colour. Tap a notebook to ask in it.
struct NotebooksView: View {
    let library: LibraryModel
    let onAsk: () -> Void
    @State private var search = ""
    @State private var toggled: Set<String> = []        // sections the user flipped on this phone, relative to the Mac's state

    var body: some View {
        NavigationStack {
            List {
                ForEach(groups) { group in
                    if let name = group.section {
                        Section {
                            if !folded(group) { rows(group.items) }
                        } header: {
                            Button { flip(group) } label: {
                                HStack(spacing: 6) {
                                    Image(systemName: "chevron.right").font(.caption.weight(.semibold))
                                        .rotationEffect(.degrees(folded(group) ? 0 : 90))
                                    Text(name)
                                    Text("\(group.items.count)").foregroundStyle(.tertiary)
                                }
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("\(name), \(folded(group) ? "folded" : "open"), \(group.items.count) notebooks")
                        }
                    } else {
                        Section("Notebooks") { rows(group.items) }
                    }
                }
                if library.notebooks.isEmpty {
                    Section { Text("Nothing here yet. Pull down to copy your library from your Mac.").foregroundStyle(.secondary) }
                } else if groups.isEmpty {
                    Section { Text("No notebook matches “\(search)”.").foregroundStyle(.secondary) }
                }
                Section {
                    switch library.syncState {
                    case .idle:
                        Button("Sync now") { Task { await library.sync() } }
                    case .syncing(let line):
                        HStack { ProgressView(); Text(line).font(.footnote) }
                    case .failed:
                        Label("Mac not reachable. Showing the copy on this phone.", systemImage: "wifi.slash").font(.footnote).foregroundStyle(.orange)
                        Button("Try again") { Task { await library.sync() } }
                    }
                    Text(library.lastSyncLine).font(.footnote).foregroundStyle(.secondary)
                } header: { Text("Sync") } footer: {
                    Text("Your Mac must be running LocalBook with Sync on, on the same network.")
                }
            }
            .searchable(text: $search, prompt: "Search notebooks")
            .refreshable { await library.sync() }
            .navigationTitle("Notebooks")
        }
    }

    private var groups: [NotebookGroups.Group] {
        let q = search.trimmingCharacters(in: .whitespaces)
        let all = NotebookGroups(library.notebooks).groups
        guard !q.isEmpty else { return all }
        return all.compactMap { g in
            let items = g.items.filter { $0.title.localizedCaseInsensitiveContains(q) }
            return items.isEmpty ? nil : NotebookGroups.Group(id: g.id, section: g.section, items: items)
        }
    }

    private func key(_ g: NotebookGroups.Group) -> String { g.section ?? "" }
    private func folded(_ g: NotebookGroups.Group) -> Bool {
        if !search.isEmpty { return false }
        let mac = g.items.first?.sectionCollapsed ?? false
        return toggled.contains(key(g)) ? !mac : mac
    }
    private func flip(_ g: NotebookGroups.Group) {
        if toggled.contains(key(g)) { toggled.remove(key(g)) } else { toggled.insert(key(g)) }
    }

    @ViewBuilder private func rows(_ items: [LibraryNotebookInfo]) -> some View {
        ForEach(items) { n in
            Button {
                library.scopeToNotebook = n.id
                UserDefaults.standard.set(n.id, forKey: "lastNotebook")
                onAsk()
            } label: {
                HStack(spacing: 10) {
                    Circle().fill(Color(hex: n.color) ?? .accentColor).frame(width: 10, height: 10)
                    Text(n.title).foregroundStyle(.primary)
                    Spacer()
                    if n.id == library.scopeToNotebook { Image(systemName: "checkmark").foregroundStyle(.tint) }
                    Text("\(n.chunks)").font(.footnote).foregroundStyle(.secondary)
                }
            }
            .accessibilityHint("Ask in this notebook")
        }
    }
}
