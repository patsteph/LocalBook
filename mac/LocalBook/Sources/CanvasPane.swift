import AppKit
import LocalBookCore
import LocalBookEngines
import SwiftUI

/// The Canvas: the notebook's learning journey as a map. Questions, the sources they used, Studio items and insights
/// are chips; threads gather in sub-topic cards; edges say what was made from what (thin), what the user joined
/// (solid), what was auto-connected (dashed), where two sources disagree (red); dotted candidates invite a connection.
/// Gaps (questions the notebook answered weakly) and items due for review wear a badge. Ask a question about a
/// selection and the answer lands beside it: the map grows the map.
struct CanvasPane: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    @State private var engine = CanvasEngine()
    @State private var notebookId: String?
    @State private var selection = Set<String>()
    @State private var zoom = 1.0
    @State private var showCandidates = true
    @State private var showGaps = false
    @State private var reviewing = false
    @State private var question = ""
    @State private var lastAnswer: String?
    @State private var dragging: (id: String, start: CGPoint)?
    @State private var intentFor: CanvasNode?

    private var library: LibraryModel { model.library }
    private var notebook: LibraryNotebookInfo? { library.notebooks.first { $0.id == notebookId } }
    private var layout: CanvasLayout { engine.layout }
    private var picked: CanvasNode? { selection.count == 1 ? layout.node(selection.first!) : nil }
    private let pad = 160.0

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                toolbar
                Divider()
                if layout.nodes.isEmpty { empty } else { map }
                Divider()
                askBar
            }
            .frame(minWidth: 520, maxWidth: .infinity, maxHeight: .infinity)
            inspector.frame(minWidth: 260, idealWidth: 300, maxWidth: 380)
        }
        .onAppear { if notebookId == nil || notebook == nil { notebookId = library.scopeToNotebook ?? library.notebooks.first?.id } }
        .onChange(of: library.scopeToNotebook) { _, id in if let id { notebookId = id } }
        .onChange(of: notebookId, initial: true) { _, id in if let id { engine.open(notebook: id); selection = []; lastAnswer = nil } }
        .sheet(isPresented: $reviewing) { ReviewSheet(engine: engine) }
        .sheet(item: $intentFor) { node in IntentSheet(node: node) { intent in Task { await engine.elicit(node.id, intent: intent, library: library) } } }
    }

    // MARK: toolbar

    private var toolbar: some View {
        HStack(spacing: 10) {
            Button { Task { if let notebook { await engine.populate(notebook: notebook, library: library) } } } label: { Label(layout.nodes.isEmpty ? "Build the map" : "Refresh", systemImage: "arrow.triangle.2.circlepath") }
                .disabled(engine.working || notebook == nil)
            Button { engine.tidy() } label: { Label("Tidy up", systemImage: "rectangle.3.group") }.disabled(layout.nodes.isEmpty)
            Toggle(isOn: $showCandidates) { Label("Suggestions", systemImage: "circle.dotted") }.toggleStyle(.button)
            Button { engine.autoConnect() } label: { Label("Connect the confident ones", systemImage: "link") }
                .disabled(engine.candidates.filter { $0.score >= 0.7 }.isEmpty)
            Button { showGaps.toggle() } label: { Label("Gaps · \(engine.gaps.count)", systemImage: "questionmark.circle") }
                .popover(isPresented: $showGaps) { gapsList }
                .disabled(engine.gaps.isEmpty)
            Button { reviewing = true } label: { Label("Review · \(engine.due.count)", systemImage: "brain") }.disabled(engine.due.isEmpty)
            Spacer()
            if engine.working { ProgressView().controlSize(.small); Text(engine.status).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
            else if let e = engine.error { Text(e).font(.caption).foregroundStyle(.red).lineLimit(1) }
            Button { zoom = max(0.3, zoom - 0.15) } label: { Image(systemName: "minus.magnifyingglass") }
            Text("\(Int(zoom * 100))%").font(.caption.monospacedDigit()).frame(width: 40)
            Button { zoom = min(2.5, zoom + 0.15) } label: { Image(systemName: "plus.magnifyingglass") }
            Button { zoom = 1 } label: { Text("1:1") }
        }
        .controlSize(.small)
        .padding(.horizontal, 12).padding(.vertical, 8)
    }

    private var empty: some View {
        VStack(spacing: 10) {
            Image(systemName: "map").font(.system(size: 36)).foregroundStyle(.secondary)
            Text("No map yet").font(.title3)
            Text("Build the map from what you have asked in \(notebook?.title ?? "this notebook"), the sources those answers used, and what Studio and Insights made. New questions join it when you refresh.")
                .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 440)
            Button("Build the map") { Task { if let notebook { await engine.populate(notebook: notebook, library: library) } } }.disabled(engine.working || notebook == nil)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: the map

    private var bounds: CGRect {
        let xs = layout.nodes.map(\.x), ys = layout.nodes.map(\.y)
        let maxX = layout.nodes.map { $0.x + ($0.width ?? CanvasPopulate.chipWidth) }.max() ?? 0
        let maxY = layout.nodes.map { $0.y + ($0.height ?? CanvasPopulate.chipHeight) }.max() ?? 0
        return CGRect(x: (xs.min() ?? 0) - pad, y: (ys.min() ?? 0) - pad, width: maxX - (xs.min() ?? 0) + 2 * pad, height: maxY - (ys.min() ?? 0) + 2 * pad)
    }
    private func point(_ n: CanvasNode) -> CGPoint { CGPoint(x: n.x - bounds.minX, y: n.y - bounds.minY) }
    private func centre(_ n: CanvasNode) -> CGPoint {
        let p = point(n)
        return CGPoint(x: p.x + (n.width ?? CanvasPopulate.chipWidth) / 2, y: p.y + (n.isTopic ? 18 : (n.height ?? CanvasPopulate.chipHeight) / 2))
    }

    private var map: some View {
        ScrollView([.horizontal, .vertical]) {
            ZStack(alignment: .topLeading) {
                Color.clear.contentShape(Rectangle()).onTapGesture { selection = [] }
                edgesLayer
                ForEach(layout.topicCards) { card in topicCard(card).position(x: centre(card).x, y: point(card).y + (card.height ?? 300) / 2) }
                ForEach(layout.threads) { n in chip(n).position(x: centre(n).x, y: centre(n).y) }
            }
            .frame(width: bounds.width, height: bounds.height)
            .scaleEffect(zoom, anchor: .topLeading)
            .frame(width: bounds.width * zoom, height: bounds.height * zoom)
        }
        .background(Color(nsColor: .textBackgroundColor))
    }

    private var edgesLayer: some View {
        Canvas { ctx, _ in
            let nodes = Dictionary(layout.nodes.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
            for e in layout.edges {
                guard let a = nodes[e.source], let b = nodes[e.target] else { continue }
                var p = Path(); p.move(to: centre(a)); p.addLine(to: centre(b))
                switch e.state {
                case "tension": ctx.stroke(p, with: .color(.red.opacity(0.8)), style: StrokeStyle(lineWidth: 2.2))
                case "user": ctx.stroke(p, with: .color(.accentColor), style: StrokeStyle(lineWidth: 2))
                case "curator": ctx.stroke(p, with: .color(.purple.opacity(0.8)), style: StrokeStyle(lineWidth: 1.6, dash: [8, 5]))
                case "researched": ctx.stroke(p, with: .color(.green.opacity(0.8)), style: StrokeStyle(lineWidth: 1.6, dash: [3, 3]))
                default: ctx.stroke(p, with: .color(.secondary.opacity(0.35)), style: StrokeStyle(lineWidth: 1))
                }
                if !e.label.isEmpty, e.state == "user" || e.state == "tension" || e.state == "curator" {
                    let mid = CGPoint(x: (centre(a).x + centre(b).x) / 2, y: (centre(a).y + centre(b).y) / 2)
                    ctx.draw(Text(e.label).font(.caption2).foregroundStyle(.secondary), at: mid)
                }
            }
            if showCandidates {
                for c in engine.candidates {
                    guard let a = nodes[c.a], let b = nodes[c.b] else { continue }
                    var p = Path(); p.move(to: centre(a)); p.addLine(to: centre(b))
                    ctx.stroke(p, with: .color(.orange.opacity(0.35 + c.score * 0.4)), style: StrokeStyle(lineWidth: 1.4, dash: [2, 4]))
                }
            }
        }
        .allowsHitTesting(false)
    }

    private func topicCard(_ card: CanvasNode) -> some View {
        let topic = layout.topics.first { $0.id == card.id }
        return VStack(alignment: .leading, spacing: 2) {
            HStack {
                Image(systemName: "square.stack.3d.up").foregroundStyle(.secondary)
                Text(card.title).font(.headline).lineLimit(1)
                Spacer()
                Text("\(layout.members(of: card.id).count)").font(.caption).foregroundStyle(.secondary)
            }
            if let s = topic?.summary, !s.isEmpty { Text(s).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(width: card.width ?? 720, height: card.height ?? 300, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 14).fill(Color.accentColor.opacity(0.06)))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(selection.contains(card.id) ? Color.accentColor : Color.secondary.opacity(0.25), lineWidth: selection.contains(card.id) ? 2 : 1))
        .onTapGesture { select(card.id) }
        .gesture(dragGesture(card))
    }

    private func chip(_ n: CanvasNode) -> some View {
        let gap = n.snapshot.metadata?.gap
        let due = engine.due.contains { $0.id == n.id } && layout.recall[n.id] != nil
        let chosen = selection.contains(n.id)
        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: Self.symbol(n)).foregroundStyle(Self.tint(n))
                Text(Self.kindLabel(n)).font(.caption2).foregroundStyle(.secondary)
                Spacer()
                if gap != nil { Image(systemName: "questionmark.circle.fill").foregroundStyle(.orange).help(gap!) }
                if due { Image(systemName: "brain").foregroundStyle(.purple).help("Due for review") }
                if n.kind == CanvasNode.chatKind, n.topicId == nil { Image(systemName: "sparkle").foregroundStyle(.secondary).help("Not in a card yet: say what you were exploring") }
            }
            Text(n.title).font(.callout.weight(.medium)).lineLimit(2)
            if let p = n.snapshot.metadata?.preview, !p.isEmpty { Text(p).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
            else if n.kind == CanvasNode.noteKind { Text(n.snapshot.payload).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
            if let s = n.snapshot.metadata?.sources, s > 0, n.kind == CanvasNode.chatKind { Text("\(s) source\(s == 1 ? "" : "s")").font(.caption2).foregroundStyle(.tertiary) }
        }
        .padding(10)
        .frame(width: n.width ?? CanvasPopulate.chipWidth, height: n.height ?? CanvasPopulate.chipHeight, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)).shadow(color: .black.opacity(0.12), radius: 3, y: 1))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(chosen ? Color.accentColor : Self.tint(n).opacity(0.5), lineWidth: chosen ? 2 : 1))
        .onTapGesture { select(n.id) }
        .gesture(dragGesture(n))
        .contextMenu { nodeMenu(n) }
    }

    private func dragGesture(_ n: CanvasNode) -> some Gesture {
        DragGesture(minimumDistance: 3, coordinateSpace: .local)
            .onChanged { v in
                if dragging?.id != n.id { dragging = (n.id, CGPoint(x: n.x, y: n.y)) }
                guard let d = dragging, let current = layout.node(n.id) else { return }
                let nx = d.start.x + v.translation.width / zoom, ny = d.start.y + v.translation.height / zoom
                let ddx = nx - current.x, ddy = ny - current.y
                engine.move(n.id, x: nx, y: ny)
                if n.isTopic {       // the card carries its threads
                    for m in layout.members(of: n.id) { engine.move(m.id, x: m.x + ddx, y: m.y + ddy) }
                }
            }
            .onEnded { _ in dragging = nil; engine.save() }
    }

    private func select(_ id: String) {
        if NSEvent.modifierFlags.contains(.shift) || NSEvent.modifierFlags.contains(.command) { if selection.contains(id) { selection.remove(id) } else { selection.insert(id) } }
        else { selection = [id] }
    }

    @ViewBuilder private func nodeMenu(_ n: CanvasNode) -> some View {
        Button("Open") { open(n) }
        if selection.count == 2, selection.contains(n.id) {
            let other = selection.first { $0 != n.id }!
            Button("Connect these two") { engine.connect(n.id, other) }
        }
        if n.kind == CanvasNode.chatKind, n.topicId == nil { Button("What was I exploring here?…") { intentFor = n } }
        Button("Keep as finding") { keep(n) }
        Divider()
        Button("Remove from the map", role: .destructive) { engine.removeNode(n.id); selection.remove(n.id) }
    }

    // MARK: inspector

    private var inspector: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let n = picked { nodeDetail(n) }
                else if selection.count > 1 {
                    Text("\(selection.count) selected").font(.headline)
                    if selection.count == 2 { Button("Connect these two") { let ids = Array(selection); engine.connect(ids[0], ids[1]) } }
                    Text("Ask a question below and the answer will use only the sources behind these.").font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(notebook?.title ?? "Canvas").font(.headline)
                    Text("\(layout.threads.count) things on the map · \(layout.topicCards.count) cards · \(layout.edges.filter { $0.state == "user" }.count) connections you drew")
                        .font(.caption).foregroundStyle(.secondary)
                    if !layout.orphans.isEmpty { Text("\(layout.orphans.count) question\(layout.orphans.count == 1 ? "" : "s") not in a card yet. Open one and say what you were exploring; it joins or starts a card.").font(.caption).foregroundStyle(.secondary) }
                    legend
                }
                if let lastAnswer {
                    Divider()
                    Text("Answer").font(.subheadline.weight(.semibold))
                    Text(lastAnswer).font(.callout).textSelection(.enabled)
                }
            }
            .padding(12)
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var legend: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Lines").font(.caption.weight(.semibold)).padding(.top, 6)
            legendRow(.accentColor, "Drawn by you", dash: [])
            legendRow(.purple, "Connected for you", dash: [6, 4])
            legendRow(.orange, "A suggestion (click two chips, Connect)", dash: [2, 4])
            legendRow(.red, "Two sources disagree", dash: [])
            legendRow(.secondary, "Made from", dash: [])
        }
    }
    private func legendRow(_ color: Color, _ text: String, dash: [CGFloat]) -> some View {
        HStack(spacing: 8) {
            Path { p in p.move(to: CGPoint(x: 0, y: 6)); p.addLine(to: CGPoint(x: 28, y: 6)) }.stroke(color, style: StrokeStyle(lineWidth: 2, dash: dash)).frame(width: 28, height: 12)
            Text(text).font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private func nodeDetail(_ n: CanvasNode) -> some View {
        HStack { Image(systemName: Self.symbol(n)).foregroundStyle(Self.tint(n)); Text(Self.kindLabel(n)).font(.caption).foregroundStyle(.secondary) }
        Text(n.title).font(.headline)
        if n.isTopic, let t = layout.topics.first(where: { $0.id == n.id }) {
            if !t.summary.isEmpty { Text(t.summary).font(.callout) }
            Text("\(layout.members(of: n.id).count) in this card").font(.caption).foregroundStyle(.secondary)
        } else {
            Text(n.snapshot.payload.replacingOccurrences(of: "**", with: "")).font(.callout).textSelection(.enabled).lineLimit(14)
        }
        if let gap = n.snapshot.metadata?.gap { Label(gap, systemImage: "questionmark.circle").font(.caption).foregroundStyle(.orange) }
        if let intent = n.intent, !intent.isEmpty { Label(intent, systemImage: "sparkle").font(.caption).foregroundStyle(.secondary) }
        let joined = layout.edges.filter { $0.touches(n.id) && $0.state != "provenance" }
        if !joined.isEmpty {
            Text("Connections").font(.subheadline.weight(.semibold))
            ForEach(joined) { e in
                HStack {
                    Text(layout.node(e.other(than: n.id))?.title ?? "—").font(.caption).lineLimit(1)
                    Spacer()
                    Text(e.state == "tension" ? (e.meta["severity"] ?? "disagree") : (e.label.isEmpty ? e.state : e.label)).font(.caption2).foregroundStyle(e.state == "tension" ? .red : .secondary)
                    if e.state == "user" || e.state == "curator" { Button { engine.removeEdge(e.id) } label: { Image(systemName: "xmark.circle") }.buttonStyle(.borderless) }
                }
                if e.state == "tension", let a = e.meta["claim_a"], let b = e.meta["claim_b"] {
                    Text("“\(a)” vs “\(b)”").font(.caption2).foregroundStyle(.secondary).lineLimit(3)
                }
            }
        }
        HStack {
            Button("Open") { open(n) }.disabled(n.isTopic || n.kind == CanvasNode.noteKind)
            Button("Keep as finding") { keep(n) }.disabled(n.isTopic)
            if n.kind == CanvasNode.chatKind, n.topicId == nil { Button("What was I exploring?") { intentFor = n } }
        }
        .controlSize(.small)
        if n.kind == CanvasNode.chatKind || n.kind == CanvasNode.artifactKind {
            HStack(spacing: 6) {
                Text("Recall").font(.caption).foregroundStyle(.secondary)
                ForEach(CanvasRecall.Grade.allCases, id: \.rawValue) { g in Button(g.rawValue.capitalized) { engine.grade(n.id, g) } }
                if let s = layout.recall[n.id] { Text("next \(s.nextDue.formatted(date: .abbreviated, time: .omitted))").font(.caption2).foregroundStyle(.tertiary) }
            }
            .controlSize(.mini)
        }
    }

    private var gapsList: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("What to explore next").font(.headline)
            Text("Questions this notebook answered weakly.").font(.caption).foregroundStyle(.secondary)
            ForEach(engine.gaps) { g in
                VStack(alignment: .leading, spacing: 2) {
                    Text(g.question).font(.callout).lineLimit(2)
                    Text(g.reason).font(.caption).foregroundStyle(.orange)
                }
                .onTapGesture { selection = [CanvasNode.derivedId(refType: "exploration_query", refId: g.refId)]; showGaps = false }
            }
        }
        .padding(14).frame(width: 360)
    }

    // MARK: ask about a selection

    private var askBar: some View {
        HStack(spacing: 8) {
            Image(systemName: "bubble.left.and.text.bubble.right").foregroundStyle(.secondary)
            TextField(selection.isEmpty ? "Ask about this notebook; the answer joins the map" : "Ask about the \(selection.count) selected…", text: $question)
                .textFieldStyle(.plain)
                .onSubmit { ask() }
            Button("Ask") { ask() }.disabled(question.trimmingCharacters(in: .whitespaces).isEmpty || engine.working || notebook == nil)
            Button { addNote() } label: { Label("Add a note", systemImage: "note.text.badge.plus") }
        }
        .controlSize(.small)
        .padding(.horizontal, 12).padding(.vertical, 8)
    }

    private func ask() {
        guard let notebook else { return }
        let q = question.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return }
        question = ""
        Task {
            lastAnswer = await engine.chat(q, selection: Array(selection), notebook: notebook, library: library)
            if lastAnswer != nil, let made = layout.nodes.last { selection = [made.id] }
        }
    }

    private func addNote() {
        let anchor = picked.map { ($0.x, $0.y + ($0.height ?? CanvasPopulate.chipHeight) + CanvasPopulate.chipGap) } ?? (0.0, (layout.nodes.map { $0.y + ($0.height ?? CanvasPopulate.chipHeight) }.max() ?? 0) + CanvasPopulate.chipGap)
        engine.addNote("New note", near: anchor)
        if let made = layout.nodes.last { selection = [made.id] }
    }

    private func open(_ n: CanvasNode) {
        guard let notebookId else { return }
        switch n.kind {
        case CanvasNode.sourceKind:
            if let id = n.refId { openWindow(id: "source", value: SourceWindowKey(notebookId: notebookId, sourceId: id, name: n.title)) }
        case CanvasNode.artifactKind:
            if n.refType == "insight" { model.insightOpenRequest = n.refId; model.selection = .insights }
            else { model.studioOpenRequest = n.refId; model.selection = .studio }
        default:
            // A question: ask it again in the notebook's chat.
            library.query = n.title
            model.selection = .notebook(notebookId)
        }
    }

    private func keep(_ n: CanvasNode) {
        guard let notebookId else { return }
        let kind: Finding.Kind = n.kind == CanvasNode.sourceKind ? .source : (n.kind == CanvasNode.noteKind ? .note : .answer)
        FindingsStore.save(Finding(notebookId: notebookId, kind: kind, title: n.title, text: n.snapshot.payload.replacingOccurrences(of: "**", with: ""), refs: n.sourceIds))
        NotificationCenter.default.post(name: .findingsChanged, object: nil)
    }

    // MARK: looks

    static func symbol(_ n: CanvasNode) -> String {
        switch n.kind {
        case CanvasNode.sourceKind: return "doc"
        case CanvasNode.noteKind: return "note.text"
        case CanvasNode.topicKind: return "square.stack.3d.up"
        case CanvasNode.artifactKind:
            if let k = n.snapshot.metadata?.studioKind, let kind = StudioItem.Kind(rawValue: k) { return kind.symbol }
            if let k = n.snapshot.metadata?.insightKind, let kind = Insight.Kind(rawValue: k) { return kind.symbol }
            return "sparkles"
        default: return "bubble.left.and.bubble.right"
        }
    }
    static func tint(_ n: CanvasNode) -> Color {
        switch n.kind {
        case CanvasNode.sourceKind: return .gray
        case CanvasNode.noteKind: return .yellow
        case CanvasNode.artifactKind:
            if let k = n.snapshot.metadata?.studioKind { switch k { case "audio": return .green; case "video": return .pink; case "quiz": return .orange; case "visual": return .purple; case "cards": return .mint; default: return .blue } }
            if let k = n.snapshot.metadata?.insightKind, let kind = Insight.Kind(rawValue: k) { return kind.tint }
            return .blue
        default: return .secondary
        }
    }
    static func kindLabel(_ n: CanvasNode) -> String {
        switch n.kind {
        case CanvasNode.sourceKind: return "Source"
        case CanvasNode.noteKind: return "Note"
        case CanvasNode.topicKind: return "Card"
        case CanvasNode.artifactKind:
            if let k = n.snapshot.metadata?.studioKind, let kind = StudioItem.Kind(rawValue: k) { return kind.short }
            if let k = n.snapshot.metadata?.insightKind, let kind = Insight.Kind(rawValue: k) { return kind.label }
            return "Made"
        default: return n.refType == "canvas_answer" ? "Answer" : "Question"
        }
    }
}

/// Spaced-repetition review: each due chip in turn, its question first, then the answer, graded again / good / easy.
struct ReviewSheet: View {
    let engine: CanvasEngine
    @Environment(\.dismiss) private var dismiss
    @State private var index = 0
    @State private var revealed = false
    private var due: [CanvasNode] { engine.due }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack { Text("Review").font(.title2.weight(.semibold)); Spacer(); Text("\(min(index + 1, due.count)) of \(due.count)").foregroundStyle(.secondary) }
            if index < due.count {
                let n = due[index]
                Text(n.title).font(.headline)
                if revealed { ScrollView { Text(n.snapshot.payload.replacingOccurrences(of: "**", with: "")).font(.callout).textSelection(.enabled) }.frame(maxHeight: 260) }
                else { Button("Show the answer") { revealed = true }.keyboardShortcut(.space, modifiers: []) }
                HStack {
                    ForEach(CanvasRecall.Grade.allCases, id: \.rawValue) { g in
                        Button(g.rawValue.capitalized) { engine.grade(n.id, g); revealed = false; if index >= due.count { index = max(0, due.count - 1) } }
                            .keyboardShortcut(KeyEquivalent(Character(String(g == .again ? "1" : (g == .good ? "2" : "3")))), modifiers: [])
                    }
                    Spacer()
                    Button("Skip") { index = min(index + 1, due.count); revealed = false }
                }
                .disabled(!revealed)
            } else {
                Text("Nothing more is due. Well done.").foregroundStyle(.secondary)
            }
            HStack { Spacer(); Button("Done") { dismiss() }.keyboardShortcut(.cancelAction) }
        }
        .padding(20).frame(width: 520)
    }
}

/// "What were you exploring here?" on an orphan question.
struct IntentSheet: View {
    let node: CanvasNode
    let done: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("What were you exploring here?").font(.title3.weight(.semibold))
            Text(node.title).font(.callout).foregroundStyle(.secondary)
            TextField("In a few words, what this question was part of", text: $text).textFieldStyle(.roundedBorder).onSubmit { finish() }
            HStack { Spacer(); Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction); Button("Save") { finish() }.keyboardShortcut(.defaultAction).disabled(text.trimmingCharacters(in: .whitespaces).isEmpty) }
        }
        .padding(20).frame(width: 460)
    }
    private func finish() { let t = text.trimmingCharacters(in: .whitespaces); guard !t.isEmpty else { return }; done(t); dismiss() }
}
