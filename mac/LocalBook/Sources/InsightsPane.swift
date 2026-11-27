import AppKit
import LocalBookCore
import LocalBookEngines
import SwiftUI

extension Insight.Kind {
    var tint: Color {
        switch self { case .timeline: .teal; case .graph: .purple; case .contradictions: .orange; case .comparison: .blue; case .perspectives: .green; case .topics: .pink }
    }
}

/// Insights: the timeline, knowledge graph, contradictions, comparisons, perspectives and topics made from one notebook
/// by the answer model (`InsightEngine`), plus the findings kept from it and the writing assistant. The shelf on the
/// left, the open item on the right, like Studio.
struct InsightsPane: View {
    @Environment(AppModel.self) private var model
    @State private var engine = InsightEngine()
    @State private var items: [Insight] = []
    @State private var findings: [Finding] = []
    @State private var notebookId: String?
    @State private var making: Insight.Kind?
    @State private var selected: Selection?
    @State private var writing = false

    enum Selection: Hashable { case insight(String), findings }

    private var library: LibraryModel { model.library }
    private var notebook: LibraryNotebookInfo? { library.notebooks.first { $0.id == notebookId } }
    private var opened: Insight? { if case .insight(let id)? = selected { return items.first { $0.id == id } } else { return nil } }

    var body: some View {
        HSplitView {
            shelf.frame(minWidth: 280, idealWidth: 340, maxWidth: 480)
            detail.frame(minWidth: 420, maxWidth: .infinity, maxHeight: .infinity)
        }
        .onAppear { if notebookId == nil || notebook == nil { notebookId = library.scopeToNotebook ?? library.notebooks.first?.id } }
        .onChange(of: library.scopeToNotebook) { _, id in if let id { notebookId = id } }
        .onChange(of: notebookId) { _, _ in reload() }
        .onChange(of: model.insightRequest) { _, kind in
            guard let kind else { return }
            model.insightRequest = nil
            if notebook != nil { making = kind }
        }
        .onChange(of: model.writingRequest) { _, on in if on { model.writingRequest = false; writing = true } }
        .task { reload() }
        .onReceive(NotificationCenter.default.publisher(for: .findingsChanged)) { _ in reload() }
        .sheet(item: $making) { kind in
            if let notebook {
                InsightMakeSheet(kind: kind, notebook: notebook, library: library, engine: engine) { made in
                    reload(); selected = .insight(made.id)
                }
            }
        }
        .sheet(isPresented: $writing) { WritingSheet(notebook: notebook, library: library, engine: engine) }
    }

    private func reload() {
        items = InsightStore.all(notebook: notebookId)
        findings = FindingsStore.all(notebook: notebookId)
        if case .insight(let id)? = selected, !items.contains(where: { $0.id == id }) { selected = nil }
    }

    // MARK: shelf

    private var shelf: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                Picker("Notebook", selection: $notebookId) {
                    ForEach(library.notebooks) { Text($0.title).tag(Optional($0.id)) }
                }
                .labelsHidden()
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
                    ForEach(Insight.Kind.allCases) { kind in
                        Button { making = kind } label: {
                            VStack(spacing: 6) {
                                Image(systemName: kind.symbol).font(.title3).foregroundStyle(kind.tint)
                                Text(kind.label).font(.caption.weight(.medium)).foregroundStyle(.primary).lineLimit(1).minimumScaleFactor(0.8)
                            }
                            .frame(maxWidth: .infinity).padding(.vertical, 10)
                            .background(kind.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
                        }
                        .buttonStyle(.plain)
                        .help(kind.blurb)
                        .disabled(notebook == nil || engine.working)
                    }
                }
                HStack {
                    Button { writing = true } label: { Label("Writing assistant", systemImage: "pencil.and.outline") }
                        .disabled(engine.working)
                    Spacer()
                    Button { selected = .findings } label: { Label("Findings (\(findings.count))", systemImage: "star") }
                }
                .buttonStyle(.link).font(.callout)
            }
            .padding(12)
            Divider()
            if items.isEmpty {
                ContentUnavailableView("No insights yet", systemImage: "lightbulb", description: Text("Pick one above: the model reads this notebook and writes it up. Everything stays on this Mac."))
            } else {
                List(selection: $selected) {
                    ForEach(items) { item in
                        HStack(spacing: 10) {
                            Image(systemName: item.kind.symbol).foregroundStyle(item.kind.tint).frame(width: 20)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.title).lineLimit(1)
                                Text("\(item.kind.label) · \(item.created.formatted(date: .abbreviated, time: .shortened))").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .tag(Selection.insight(item.id))
                        .contextMenu {
                            Button("Keep as finding") { keepAsFinding(item) }
                            Button("Delete", role: .destructive) { InsightStore.delete(item); reload() }
                        }
                    }
                }
                .listStyle(.inset)
            }
        }
    }

    private func keepAsFinding(_ item: Insight) {
        FindingsStore.save(Finding(notebookId: item.notebookId, kind: .insight, title: item.title, text: InsightText.summary(of: item), refs: [item.id]))
        reload()
    }

    // MARK: detail

    @ViewBuilder private var detail: some View {
        if engine.working {
            VStack(spacing: 14) {
                ProgressView(value: engine.progress).frame(maxWidth: 360)
                Text(engine.status).font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
                Button("Stop") { engine.stop() }
            }
            .padding(40)
        } else if selected == .findings {
            InsightFindingsView(findings: findings, reload: reload)
        } else if let item = opened {
            InsightDetail(item: item, library: library, onChange: reload)
        } else {
            ContentUnavailableView("Insights", systemImage: "lightbulb", description: Text(engine.error ?? "Choose an insight on the left, or make one."))
        }
    }
}

/// Confirms what will be made (and asks for the two sources or the topic where needed), then shows progress.
struct InsightMakeSheet: View {
    let kind: Insight.Kind
    let notebook: LibraryNotebookInfo
    let library: LibraryModel
    let engine: InsightEngine
    let onMade: (Insight) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var sources: [LibraryStore.SourceInfo] = []
    @State private var sourceA: String?
    @State private var sourceB: String?
    @State private var focus = ""
    @State private var topic = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label(kind.label, systemImage: kind.symbol).font(.headline).foregroundStyle(kind.tint)
            if engine.working {
                ProgressView(value: engine.progress)
                Text(engine.status).font(.callout).foregroundStyle(.secondary)
            } else {
                Text(blurb).font(.callout).foregroundStyle(.secondary)
                if kind == .comparison {
                    Picker("First", selection: $sourceA) { Text("Choose a source").tag(String?.none); ForEach(sources) { Text($0.name).tag(Optional($0.id)) } }
                    Picker("Second", selection: $sourceB) { Text("Choose a source").tag(String?.none); ForEach(sources) { Text($0.name).tag(Optional($0.id)) } }
                    TextField("Focus (optional)", text: $focus, prompt: Text("e.g. the methods, the numbers, the recommendations"))
                }
                if kind == .perspectives {
                    TextField("Topic", text: $topic, prompt: Text("What should each source be asked about?")).onSubmit { Task { await make() } }
                }
                if let why = engine.error { Text(why).font(.callout).foregroundStyle(.orange) }
            }
            HStack {
                Spacer()
                if engine.working { Button("Stop") { engine.stop() } }
                else {
                    Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                    Button("Make") { Task { await make() } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(!ready)
                }
            }
        }
        .padding(20)
        .frame(width: 460)
        .task { if kind == .comparison, let store = library.store { sources = await store.sources(notebook: notebook.id) } }
        .interactiveDismissDisabled(engine.working)
    }

    private var ready: Bool {
        switch kind {
        case .comparison: sourceA != nil && sourceB != nil && sourceA != sourceB
        case .perspectives: !topic.trimmingCharacters(in: .whitespaces).isEmpty
        default: true
        }
    }

    private var blurb: String {
        switch kind {
        case .timeline: "Reads the whole notebook “\(notebook.title)” for dates and events and puts them in order. A large notebook takes a few minutes."
        case .graph: "Finds the people, organizations, places, products and ideas in “\(notebook.title)” and how they connect."
        case .contradictions: "Lists the checkable claims in “\(notebook.title)”, finds the ones from different sources that talk about the same thing, and asks whether they disagree."
        case .comparison: "Compares two sources of “\(notebook.title)”: what they share, where they differ, what only one says."
        case .perspectives: "Asks each source of “\(notebook.title)” what it says about a topic, then what they agree on and where they part."
        case .topics: "Groups the passages of “\(notebook.title)” by what they are about (no model needed for the grouping) and names each group."
        }
    }

    private func make() async {
        let made: Insight?
        switch kind {
        case .timeline: made = await engine.makeTimeline(in: notebook, library: library)
        case .graph: made = await engine.makeGraph(in: notebook, library: library)
        case .contradictions: made = await engine.makeContradictions(in: notebook, library: library)
        case .topics: made = await engine.makeTopics(in: notebook, library: library)
        case .perspectives: made = await engine.makePerspectives(in: notebook, topic: topic, library: library)
        case .comparison:
            guard let a = sources.first(where: { $0.id == sourceA }), let b = sources.first(where: { $0.id == sourceB }) else { return }
            made = await engine.makeComparison(in: notebook, sourceA: a, sourceB: b, focus: focus, library: library)
        }
        if let made { onMade(made); dismiss() }
    }
}

/// An insight on screen, by kind.
struct InsightDetail: View {
    let item: Insight
    let library: LibraryModel
    let onChange: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Label(item.title, systemImage: item.kind.symbol).font(.title3.weight(.semibold)).lineLimit(1)
                Spacer()
                Text(item.created.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                Button { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(InsightText.markdown(of: item), forType: .string) } label: { Label("Copy", systemImage: "doc.on.doc") }
                ShareLink(item: InsightText.markdown(of: item)) { Label("Share", systemImage: "square.and.arrow.up") }
                Button {
                    FindingsStore.save(Finding(notebookId: item.notebookId, kind: .insight, title: item.title, text: InsightText.summary(of: item), refs: [item.id]))
                    NotificationCenter.default.post(name: .findingsChanged, object: nil)
                } label: { Label("Keep as finding", systemImage: "star") }
            }
            .buttonStyle(.borderless).font(.callout)
            .padding(14)
            Divider()
            ScrollView {
                Group {
                    switch item.kind {
                    case .timeline: if let t = item.decode(TimelineInsight.self) { InsightTimelineView(timeline: t) }
                    case .graph: if let g = item.decode(GraphInsight.self) { InsightGraphView(graph: g) }
                    case .contradictions: if let c = item.decode(ContradictionsInsight.self) { ContradictionsView(item: item, report: c, onChange: onChange) }
                    case .comparison: if let c = item.decode(ComparisonInsight.self) { InsightComparisonView(comparison: c) }
                    case .perspectives: if let p = item.decode(PerspectivesInsight.self) { InsightPerspectivesView(perspectives: p) }
                    case .topics: if let t = item.decode(TopicsInsight.self) { InsightTopicsView(topics: t) }
                    }
                }
                .padding(18)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

struct InsightTimelineView: View {
    let timeline: TimelineInsight

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if !timeline.timeSpan.isEmpty || !timeline.context.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    if !timeline.timeSpan.isEmpty { Text(timeline.timeSpan).font(.headline) }
                    if !timeline.context.isEmpty { Text(timeline.context).font(.callout).foregroundStyle(.secondary) }
                }
            }
            ForEach(timeline.events) { e in
                HStack(alignment: .top, spacing: 12) {
                    Text(e.date.isEmpty ? "—" : e.date).font(.callout.monospacedDigit().weight(.medium)).frame(width: 120, alignment: .trailing).foregroundStyle(.secondary)
                    Circle().fill(color(e.importance)).frame(width: 10, height: 10).padding(.top, 4)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(e.title).font(.body.weight(.semibold))
                        if !e.description.isEmpty { Text(e.description).font(.callout).foregroundStyle(.secondary) }
                        if let f = e.filename { Text(f).font(.caption).foregroundStyle(.tertiary) }
                    }
                }
            }
            Text("\(timeline.events.count) events · \(timeline.events.filter { $0.sortKey == nil }.count) without a readable date, listed last.").font(.caption).foregroundStyle(.tertiary)
        }
    }

    private func color(_ importance: String) -> Color { importance == "high" ? .orange : (importance == "low" ? .gray : .teal) }
}

/// The graph drawn on a canvas: a few hundred rounds of a simple force layout at first appearance, then still.
struct InsightGraphView: View {
    let graph: GraphInsight
    @State private var positions: [String: CGPoint] = [:]
    @State private var picked: String?

    private static let typeColors: [String: Color] = ["person": .blue, "company": .purple, "location": .green, "product": .orange, "concept": .pink, "metric": .teal, "date": .gray]

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            GeometryReader { geo in
                Canvas { ctx, size in
                    for e in graph.edges {
                        guard let a = positions[e.a], let b = positions[e.b] else { continue }
                        var p = Path(); p.move(to: scaled(a, size)); p.addLine(to: scaled(b, size))
                        ctx.stroke(p, with: .color(.secondary.opacity(0.25 + min(0.5, Double(e.weight) * 0.1))), lineWidth: 1 + min(3, CGFloat(e.weight) * 0.5))
                    }
                    for n in graph.nodes {
                        guard let p = positions[n.id] else { continue }
                        let c = scaled(p, size)
                        let r = 6 + min(14, CGFloat(n.mentions).squareRoot() * 2)
                        let color = Self.typeColors[n.type] ?? .gray
                        ctx.fill(Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2)), with: .color(picked == n.id ? color : color.opacity(0.8)))
                        ctx.draw(Text(n.name).font(.caption2).foregroundStyle(.primary), at: CGPoint(x: c.x, y: c.y + r + 8))
                    }
                }
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                .onTapGesture { at in
                    picked = graph.nodes.min { hypot(scaled(positions[$0.id] ?? .zero, geo.size).x - at.x, scaled(positions[$0.id] ?? .zero, geo.size).y - at.y)
                                                < hypot(scaled(positions[$1.id] ?? .zero, geo.size).x - at.x, scaled(positions[$1.id] ?? .zero, geo.size).y - at.y) }?.id
                }
            }
            .frame(minHeight: 420)
            .onAppear { if positions.isEmpty { positions = ForceLayout.layout(nodes: graph.nodes.map(\.id), edges: graph.edges.map { ($0.a, $0.b) }) } }
            VStack(alignment: .leading, spacing: 8) {
                if let id = picked, let n = graph.nodes.first(where: { $0.id == id }) {
                    Text(n.name).font(.headline)
                    Text("\(n.type) · \(n.mentions) mentions").font(.caption).foregroundStyle(.secondary)
                    if !n.context.isEmpty { Text(n.context).font(.callout) }
                    let links = graph.edges.filter { $0.a == id || $0.b == id }
                    if !links.isEmpty {
                        Text("Connected to").font(.caption.weight(.semibold)).padding(.top, 6)
                        ForEach(links) { e in
                            let other = e.a == id ? e.b : e.a
                            Text("\(graph.nodes.first { $0.id == other }?.name ?? other)\(e.relation.isEmpty ? "" : " — \(e.relation)")").font(.callout)
                        }
                    }
                } else {
                    Text("\(graph.nodes.count) entities, \(graph.edges.count) connections").font(.headline)
                    Text("Click a dot to see what it is and what it connects to. Size is how often it is mentioned.").font(.callout).foregroundStyle(.secondary)
                    ForEach(Self.typeColors.sorted { $0.key < $1.key }, id: \.key) { type, color in
                        if graph.nodes.contains(where: { $0.type == type }) { Label(type, systemImage: "circle.fill").foregroundStyle(color).font(.caption) }
                    }
                }
            }
            .frame(width: 220, alignment: .leading)
        }
    }

    private func scaled(_ p: CGPoint, _ size: CGSize) -> CGPoint { CGPoint(x: 30 + p.x * (size.width - 60), y: 24 + p.y * (size.height - 48)) }
}

/// A plain force layout in the unit square: springs along edges, repulsion between all nodes, a pull to the middle.
enum ForceLayout {
    static func layout(nodes: [String], edges: [(String, String)], rounds: Int = 300) -> [String: CGPoint] {
        guard !nodes.isEmpty else { return [:] }
        var pos: [String: CGPoint] = [:]
        for (i, n) in nodes.enumerated() {
            let a = Double(i) / Double(nodes.count) * 2 * .pi
            pos[n] = CGPoint(x: 0.5 + 0.35 * cos(a), y: 0.5 + 0.35 * sin(a))
        }
        let index = Set(nodes)
        let valid = edges.filter { index.contains($0.0) && index.contains($0.1) }
        var step = 0.02
        for _ in 0..<rounds {
            var force: [String: CGPoint] = [:]
            for i in nodes.indices { for j in nodes.indices where j > i {
                let a = pos[nodes[i]]!, b = pos[nodes[j]]!
                var dx = a.x - b.x, dy = a.y - b.y
                var d = max(0.001, hypot(dx, dy))
                dx /= d; dy /= d
                let rep = 0.0025 / (d * d)
                d = min(rep, 0.05)
                force[nodes[i], default: .zero].x += dx * d; force[nodes[i], default: .zero].y += dy * d
                force[nodes[j], default: .zero].x -= dx * d; force[nodes[j], default: .zero].y -= dy * d
            } }
            for (a, b) in valid {
                let pa = pos[a]!, pb = pos[b]!
                let dx = pb.x - pa.x, dy = pb.y - pa.y
                let d = hypot(dx, dy)
                let pull = (d - 0.18) * 0.5
                force[a, default: .zero].x += dx * pull; force[a, default: .zero].y += dy * pull
                force[b, default: .zero].x -= dx * pull; force[b, default: .zero].y -= dy * pull
            }
            for n in nodes {
                var p = pos[n]!
                let f = force[n] ?? .zero
                p.x += (f.x + (0.5 - p.x) * 0.05) * step; p.y += (f.y + (0.5 - p.y) * 0.05) * step
                pos[n] = CGPoint(x: min(1, max(0, p.x)), y: min(1, max(0, p.y)))
            }
            step *= 0.995
        }
        return pos
    }
}

struct ContradictionsView: View {
    let item: Insight
    let report: ContradictionsInsight
    let onChange: () -> Void
    @State private var showDismissed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("\(report.claimsChecked) claims listed, \(report.pairsChecked) pairs from different sources checked, \(report.items.filter { !$0.dismissed }.count) contradictions.")
                .font(.callout).foregroundStyle(.secondary)
            if report.items.isEmpty { Text("The sources agree on everything they both talk about.").font(.body) }
            ForEach(report.items.filter { showDismissed || !$0.dismissed }) { c in
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(c.severity.capitalized).font(.caption.weight(.semibold)).padding(.horizontal, 8).padding(.vertical, 3)
                            .background(color(c.severity).opacity(0.18), in: Capsule()).foregroundStyle(color(c.severity))
                        Text(c.type).font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button(c.dismissed ? "Bring back" : "Dismiss") { toggle(c) }.buttonStyle(.link).font(.caption)
                    }
                    claim(c.claimA, c.sourceA)
                    claim(c.claimB, c.sourceB)
                    if !c.explanation.isEmpty { Text(c.explanation).font(.callout) }
                    if let hint = c.resolutionHint, !hint.isEmpty { Text("Possible reason: \(hint)").font(.callout).foregroundStyle(.secondary) }
                }
                .padding(12)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                .opacity(c.dismissed ? 0.6 : 1)
            }
            if report.items.contains(where: \.dismissed) { Toggle("Show dismissed", isOn: $showDismissed).font(.caption) }
        }
    }

    private func claim(_ text: String, _ source: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("“\(text)”").font(.body)
            Text(source).font(.caption).foregroundStyle(.tertiary)
        }
    }

    private func color(_ s: String) -> Color { s == "high" ? .red : (s == "low" ? .gray : .orange) }

    private func toggle(_ c: ContradictionsInsight.Item) {
        var r = report
        if let i = r.items.firstIndex(where: { $0.id == c.id }) { r.items[i].dismissed.toggle() }
        var copy = item; copy.body = Insight.encode(r)
        InsightStore.save(copy); onChange()
    }
}

struct InsightComparisonView: View {
    let comparison: ComparisonInsight

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let f = comparison.focus { Text("Focus: \(f)").font(.callout).foregroundStyle(.secondary) }
            if !comparison.synthesis.isEmpty { Text(comparison.synthesis).font(.body).padding(12).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10)) }
            HStack(alignment: .top, spacing: 16) {
                column("Only in \(comparison.titleA)", comparison.uniqueToA)
                column("Only in \(comparison.titleB)", comparison.uniqueToB)
            }
            column("Both say", comparison.similarities)
            column("They differ on", comparison.differences)
        }
    }

    private func column(_ title: String, _ points: [String]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.headline)
            if points.isEmpty { Text("Nothing noted.").font(.callout).foregroundStyle(.tertiary) }
            ForEach(points, id: \.self) { Text("• " + $0).font(.callout) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct InsightPerspectivesView: View {
    let perspectives: PerspectivesInsight

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(perspectives.topic).font(.headline)
            if !perspectives.synthesis.isEmpty { Text(perspectives.synthesis).font(.body).padding(12).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10)) }
            HStack(alignment: .top, spacing: 16) {
                list("Agreed", perspectives.consensus, .green)
                list("Contested", perspectives.contested, .orange)
            }
            ForEach(perspectives.perspectives) { p in
                VStack(alignment: .leading, spacing: 6) {
                    Text(p.filename).font(.callout.weight(.semibold))
                    if !p.take.isEmpty { Text(p.take).font(.body) }
                    ForEach(p.claims, id: \.self) { Text("• " + $0).font(.callout).foregroundStyle(.secondary) }
                }
                .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
            }
        }
    }

    private func list(_ title: String, _ points: [String], _ tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: "circle.fill").font(.headline).foregroundStyle(tint)
            if points.isEmpty { Text("Nothing noted.").font(.callout).foregroundStyle(.tertiary) }
            ForEach(points, id: \.self) { Text("• " + $0).font(.callout) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct InsightTopicsView: View {
    let topics: TopicsInsight

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("\(topics.topics.count) topics across \(topics.chunksClustered) passages.").font(.callout).foregroundStyle(.secondary)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: 12)], alignment: .leading, spacing: 12) {
                ForEach(topics.topics) { t in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(t.name).font(.headline)
                        Text("\(t.chunkCount) passages · \(t.sources.count) source\(t.sources.count == 1 ? "" : "s")").font(.caption).foregroundStyle(.secondary)
                        FlowChips(words: t.keywords)
                        Text(t.sources.prefix(4).joined(separator: ", ") + (t.sources.count > 4 ? ", …" : "")).font(.caption).foregroundStyle(.tertiary).lineLimit(2)
                    }
                    .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                }
            }
        }
    }
}

struct FlowChips: View {
    let words: [String]
    var body: some View {
        HStack(spacing: 4) {
            ForEach(words.prefix(6), id: \.self) { w in
                Text(w).font(.caption2).padding(.horizontal, 6).padding(.vertical, 2).background(Color.accentColor.opacity(0.12), in: Capsule())
            }
        }
    }
}

/// The findings kept from this notebook: starred first, tags editable in place.
struct InsightFindingsView: View {
    let findings: [Finding]
    let reload: () -> Void
    @State private var editingTags: Finding?
    @State private var tagText = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack { Text("Findings").font(.title3.weight(.semibold)); Spacer(); Text("\(findings.count)").font(.caption).foregroundStyle(.secondary) }.padding(14)
            Divider()
            if findings.isEmpty {
                ContentUnavailableView("No findings yet", systemImage: "star", description: Text("Keep an answer with “Keep as finding” under it, or an insight from its header. Findings are yours to tag and star."))
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(findings) { f in
                            VStack(alignment: .leading, spacing: 6) {
                                HStack {
                                    Button { var c = f; c.starred.toggle(); FindingsStore.save(c); reload() } label: { Image(systemName: f.starred ? "star.fill" : "star").foregroundStyle(f.starred ? .yellow : .secondary) }
                                        .buttonStyle(.borderless)
                                    Text(f.title).font(.callout.weight(.semibold)).lineLimit(1)
                                    Text(f.kind.rawValue).font(.caption).foregroundStyle(.tertiary)
                                    Spacer()
                                    Text(f.created.formatted(date: .abbreviated, time: .omitted)).font(.caption).foregroundStyle(.tertiary)
                                    Button(role: .destructive) { FindingsStore.delete(f); reload() } label: { Image(systemName: "trash") }.buttonStyle(.borderless)
                                }
                                Text(f.text).font(.callout).lineLimit(6).textSelection(.enabled)
                                HStack(spacing: 6) {
                                    FlowChips(words: f.tags)
                                    Button(f.tags.isEmpty ? "Add tags" : "Edit tags") { editingTags = f; tagText = f.tags.joined(separator: ", ") }.buttonStyle(.link).font(.caption)
                                }
                            }
                            .padding(12)
                            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                        }
                    }
                    .padding(14)
                }
            }
        }
        .alert("Tags", isPresented: Binding(get: { editingTags != nil }, set: { if !$0 { editingTags = nil } })) {
            TextField("Tags, separated by commas", text: $tagText)
            Button("Save") {
                if var f = editingTags {
                    f.tags = tagText.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
                    FindingsStore.save(f); reload()
                }
                editingTags = nil
            }
            Button("Cancel", role: .cancel) { editingTags = nil }
        }
    }
}

/// Text forms of an insight: a summary for a finding, Markdown for the clipboard.
enum InsightText {
    static func summary(of item: Insight) -> String {
        switch item.kind {
        case .timeline: return (item.decode(TimelineInsight.self)?.events.prefix(12).map { "\($0.date): \($0.title)" } ?? []).joined(separator: "\n")
        case .graph: return (item.decode(GraphInsight.self)?.nodes.prefix(15).map { "\($0.name) (\($0.type))" } ?? []).joined(separator: ", ")
        case .contradictions: return (item.decode(ContradictionsInsight.self)?.items.filter { !$0.dismissed }.prefix(5).map { "\($0.sourceA) vs \($0.sourceB): \($0.explanation)" } ?? []).joined(separator: "\n")
        case .comparison: return item.decode(ComparisonInsight.self)?.synthesis ?? ""
        case .perspectives: return item.decode(PerspectivesInsight.self)?.synthesis ?? ""
        case .topics: return (item.decode(TopicsInsight.self)?.topics.map { "\($0.name) (\($0.chunkCount))" } ?? []).joined(separator: ", ")
        }
    }

    static func markdown(of item: Insight) -> String {
        var out = "# \(item.title)\n\n"
        switch item.kind {
        case .timeline:
            guard let t = item.decode(TimelineInsight.self) else { break }
            if !t.timeSpan.isEmpty { out += "_\(t.timeSpan)_\n\n" }
            for e in t.events { out += "- **\(e.date)** — \(e.title)\(e.description.isEmpty ? "" : ": \(e.description)")\(e.filename.map { " _(\($0))_" } ?? "")\n" }
        case .graph:
            guard let g = item.decode(GraphInsight.self) else { break }
            out += "## Entities\n" + g.nodes.map { "- \($0.name) (\($0.type), \($0.mentions))\($0.context.isEmpty ? "" : ": \($0.context)")" }.joined(separator: "\n")
            let names = Dictionary(uniqueKeysWithValues: g.nodes.map { ($0.id, $0.name) })
            out += "\n\n## Connections\n" + g.edges.map { "- \(names[$0.a] ?? $0.a) — \(names[$0.b] ?? $0.b)\($0.relation.isEmpty ? "" : ": \($0.relation)") (\($0.weight))" }.joined(separator: "\n")
        case .contradictions:
            guard let c = item.decode(ContradictionsInsight.self) else { break }
            for i in c.items where !i.dismissed { out += "- **\(i.severity)** \(i.type)\n  - \(i.sourceA): “\(i.claimA)”\n  - \(i.sourceB): “\(i.claimB)”\n  - \(i.explanation)\n" }
        case .comparison:
            guard let c = item.decode(ComparisonInsight.self) else { break }
            out += "\(c.synthesis)\n\n## Both say\n" + c.similarities.map { "- " + $0 }.joined(separator: "\n")
            out += "\n\n## They differ on\n" + c.differences.map { "- " + $0 }.joined(separator: "\n")
            out += "\n\n## Only in \(c.titleA)\n" + c.uniqueToA.map { "- " + $0 }.joined(separator: "\n")
            out += "\n\n## Only in \(c.titleB)\n" + c.uniqueToB.map { "- " + $0 }.joined(separator: "\n")
        case .perspectives:
            guard let p = item.decode(PerspectivesInsight.self) else { break }
            out += "\(p.synthesis)\n\n## Agreed\n" + p.consensus.map { "- " + $0 }.joined(separator: "\n") + "\n\n## Contested\n" + p.contested.map { "- " + $0 }.joined(separator: "\n")
            for s in p.perspectives { out += "\n\n### \(s.filename)\n\(s.take)\n" + s.claims.map { "- " + $0 }.joined(separator: "\n") }
        case .topics:
            guard let t = item.decode(TopicsInsight.self) else { break }
            for x in t.topics { out += "- **\(x.name)** (\(x.chunkCount) passages): \(x.keywords.joined(separator: ", "))\n" }
        }
        return out
    }
}
