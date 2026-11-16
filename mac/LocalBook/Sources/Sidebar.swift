import LocalBookCore
import LocalBookEngines
import SwiftUI

/// Notebooks in the desktop app's order with its sections and colors, then the three cross-notebook places. Every
/// action the desktop sidebar has is in the context menus; each goes through the paired Mac (the system of record
/// until Phase 3) and syncs back. Sources live behind the toolbar's Sources button, not here.
struct Sidebar: View {
    @Environment(AppModel.self) private var model
    @Binding var selection: AppModel.Destination?
    @State private var expanded: Set<String> = []
    @State private var topics: [String: [LibraryStore.TopicInfo]] = [:]
    @State private var summaries: [String: String] = [:]

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
        .task { await loadSummaries() }
        .onChange(of: expanded) { _, now in Task { await loadTopics(now) } }
        .onChange(of: model.library.topicsVersion) { _, _ in Task { await loadTopics(expanded, fresh: true) } }
        .onReceive(NotificationCenter.default.publisher(for: .librarySynced)) { _ in Task { await loadSummaries() } }
    }

    /// The color mark and the title; the chevron opens the notebook's topics. Counts live in the tooltip.
    private func notebookRow(_ notebook: LibraryNotebookInfo) -> some View {
        DisclosureGroup(isExpanded: Binding(get: { expanded.contains(notebook.id) },
                                            set: { if $0 { expanded.insert(notebook.id) } else { expanded.remove(notebook.id) } })) {
            TopicChips(notebook: notebook, topics: topics[notebook.id])
        } label: {
            Label {
                Text(notebook.title).lineLimit(1)
            } icon: {
                Image(systemName: "book.closed")
                    .foregroundStyle(Color(hex: notebook.color) ?? .accentColor)
            }
            .help(summaries[notebook.id] ?? notebook.title)
            .acceptsDrops(for: notebook.id)
        }
        .tag(AppModel.Destination.notebook(notebook.id))
        .contextMenu { NotebookContextMenu(notebook: notebook) }
    }

    private func loadTopics(_ ids: Set<String>, fresh: Bool = false) async {
        for id in ids where fresh || topics[id] == nil {
            topics[id] = await model.library.topics(in: id)
        }
    }

    private func loadSummaries() async {
        for nb in model.library.notebooks {
            let s = await model.library.notebookSummary(nb.id)
            var line = "\(s.sources) source\(s.sources == 1 ? "" : "s") · \(nb.chunks.formatted()) passages"
            if let last = s.lastAdded, let date = ISO8601DateFormatter().date(from: last) ?? Self.looseDate(last) {
                line += " · last added \(date.formatted(date: .abbreviated, time: .omitted))"
            }
            summaries[nb.id] = line
        }
    }

    /// The Mac stores "2026-11-14 09:12:33" as often as a full ISO stamp.
    private static func looseDate(_ s: String) -> Date? {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f.date(from: String(s.prefix(19))) ?? { f.dateFormat = "yyyy-MM-dd"; return f.date(from: String(s.prefix(10))) }()
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

/// A notebook's topics as chips, biggest first; clicking one asks the notebook about it. Nothing modelled yet is
/// said plainly, since the Mac computes topics in its own time.
struct TopicChips: View {
    let notebook: LibraryNotebookInfo
    let topics: [LibraryStore.TopicInfo]?
    @Environment(AppModel.self) private var model
    @State private var showAll = false
    @State private var untagged = 0

    var body: some View {
        if let topics {
            if let why = model.library.topicProblems[notebook.id] {
                Text(why.contains("404") ? "The Mac's LocalBook is older than this app: restart it from the current code." : "Couldn't get topics from the Mac: \(why)")
                    .font(.caption).foregroundStyle(.orange).padding(.vertical, 2)
            } else if topics.isEmpty {
                Text(model.paired ? "No topics yet: tag this notebook's sources in the Mac's Sources panel." : "No topics came over for this notebook.")
                    .font(.caption).foregroundStyle(.tertiary).padding(.vertical, 2)
            } else {
                let shown = showAll ? topics : Array(topics.prefix(8))
                VStack(alignment: .leading, spacing: 4) {
                    ChipFlow(spacing: 5) {
                        ForEach(shown) { t in
                            Button {
                                model.selection = .notebook(notebook.id)
                                model.pendingQuestion = "What do my sources say about \(t.name)?"
                            } label: {
                                HStack(spacing: 4) {
                                    Text(t.name).lineLimit(1)
                                    Text(t.sources.formatted()).foregroundStyle(.secondary).monospacedDigit()
                                }
                            }
                            .buttonStyle(.chip)
                            .help("\(t.sources) source\(t.sources == 1 ? "" : "s") tagged \(t.name). Click to ask about it.")
                        }
                        if topics.count > 8 {
                            Button(showAll ? "Fewer" : "\(topics.count - 8) more…") { showAll.toggle() }.buttonStyle(.chipQuiet)
                        }
                    }
                    if untagged > 0 {
                        Text("\(untagged) untagged").font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                .padding(.vertical, 2)
                .task(id: topics.count) { untagged = await model.library.untaggedCount(in: notebook.id) }
            }
        } else {
            ProgressView().controlSize(.small)
        }
    }
}

/// Chips that wrap onto new lines within the sidebar's width.
struct ChipFlow: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 200
        return CGSize(width: width, height: place(in: width, subviews: subviews).height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let frames = place(in: bounds.width, subviews: subviews).frames
        for (i, f) in frames.enumerated() {
            subviews[i].place(at: CGPoint(x: bounds.minX + f.minX, y: bounds.minY + f.minY), proposal: ProposedViewSize(f.size))
        }
    }

    private func place(in width: CGFloat, subviews: Subviews) -> (frames: [CGRect], height: CGFloat) {
        var frames: [CGRect] = []
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        for v in subviews {
            let size = v.sizeThatFits(.unspecified)
            if x > 0, x + size.width > width { x = 0; y += rowHeight + spacing; rowHeight = 0 }
            frames.append(CGRect(origin: CGPoint(x: x, y: y), size: size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return (frames, y + rowHeight)
    }
}

struct ChipButtonStyle: ButtonStyle {
    var quiet = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.caption)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(quiet ? AnyShapeStyle(.clear) : AnyShapeStyle(.quaternary), in: Capsule())
            .overlay(Capsule().strokeBorder(.separator, style: StrokeStyle(lineWidth: 1, dash: quiet ? [3, 2] : [])))
            .foregroundStyle(quiet ? .secondary : .primary)
            .opacity(configuration.isPressed ? 0.6 : 1)
            .contentShape(Capsule())
    }
}

extension ButtonStyle where Self == ChipButtonStyle {
    static var chip: ChipButtonStyle { ChipButtonStyle() }
    static var chipQuiet: ChipButtonStyle { ChipButtonStyle(quiet: true) }
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

    @State private var showing = false

    var body: some View {
        let s = state
        Button {
            showing.toggle()
        } label: {
            HStack(spacing: 6) {
                Circle().fill(s.color).frame(width: 8, height: 8)
                Text(s.word).font(.caption).foregroundStyle(.secondary)
                Spacer(minLength: 0)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(s.detail)
        .popover(isPresented: $showing, arrowEdge: .top) {
            VStack(alignment: .leading, spacing: 6) {
                Text(s.word).font(.headline)
                ForEach(s.detail.components(separatedBy: "\n"), id: \.self) { line in
                    Text(line).font(.callout).foregroundStyle(.secondary)
                }
                if model.paired { Button("Sync Now") { showing = false; model.syncNow() }.controlSize(.small).padding(.top, 2) }
            }
            .padding(12)
            .frame(minWidth: 220, maxWidth: 320, alignment: .leading)
        }
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
