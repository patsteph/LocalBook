import Foundation

/// Builds the canvas from what the notebook already holds: the journey's questions, the sources those questions used,
/// the Studio items and insights made from the notebook. Pure, so it is tested in CI; the engine does the reads, the
/// embedding and the naming. Populating again keeps every node where the user left it and adds only what is new, as
/// the old app's populate did.
public enum CanvasPopulate {
    public static let columnWidth = 340.0
    public static let rowHeight = 200.0
    static let sourcesGroup = "__sources__"

    // MARK: learning versus noise (the old app's deterministic filter)

    static let adminAgents = ["curator", "collector", "correspondent"]
    static let adminPhrases: [String] = [
        "brain status", "show status", "show profile", "show pending", "show history", "show queue", "show accounts", "show senders",
        "show subscriptions", "show scorecards", "show routing", "show blocklist", "show digest", "show draft", "show recent",
        "show articles", "show entities", "show score", "show voice", "source health", "collection schedule", "collection status",
        "morning brief", "weekly wrap", "weekly wrap-up", "devil's advocate", "devils advocate", "discover patterns", "show themes",
        "note themes", "approve connection", "dismiss connection", "approve all", "set name", "set personality", "set voice",
        "set focus", "set intent", "set subject", "set mode", "set approval", "set schedule", "set filters", "toggle overwatch",
        "exclude notebook", "include notebook", "sync now", "digest mode", "live mode", "forget sender", "refresh titles",
        "diagnose extraction", "pipeline status", "backfill status", "unsubscribe candidates", "scorecard", "routing histogram"]
    static let adminCommands: Set<String> = ["status", "profile", "pending", "history", "config", "settings", "overwatch", "pause", "resume",
        "sync", "queue", "accounts", "senders", "subscriptions", "scorecards", "routing", "blocklist", "backfill", "reprocess",
        "reextract", "re-extract", "help"]

    static func agentMention(_ lowered: String) -> String? {
        for name in adminAgents + ["research"] where lowered.contains("@" + name) { return name }
        return nil
    }
    static func hasAdminSignal(_ text: String) -> Bool {
        let s = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if adminCommands.contains(s) { return true }
        return adminPhrases.contains { s.contains($0) }
    }

    /// True when the question was learning (a real question, a research pull, a source add), false for agent admin and
    /// status chatter. An empty question is nothing. Errs toward keeping.
    public static func isLearning(_ question: String) -> Bool {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return false }
        let lowered = text.lowercased()
        let agent = agentMention(lowered)
        if agent == "research" { return true }
        if let agent, adminAgents.contains(agent) {
            let rest = lowered.replacingOccurrences(of: "@" + agent, with: "").trimmingCharacters(in: .whitespaces)
            return !hasAdminSignal(rest)
        }
        return !hasAdminSignal(lowered)
    }

    // MARK: nodes from capture

    public struct SourceRef: Sendable, Hashable { public let id: String; public let title: String; public let created: String?
        public init(id: String, title: String, created: String? = nil) { self.id = id; self.title = title; self.created = created } }
    public struct ArtifactRef: Sendable, Hashable {
        public let id: String; public let kind: String; public let title: String; public let preview: String; public let created: Date
        public let sourceIds: [String]; public let studioKind: String?; public let insightKind: String?
        public init(id: String, kind: String, title: String, preview: String, created: Date, sourceIds: [String] = [], studioKind: String? = nil, insightKind: String? = nil) {
            self.id = id; self.kind = kind; self.title = title; self.preview = preview; self.created = created; self.sourceIds = sourceIds; self.studioKind = studioKind; self.insightKind = insightKind
        }
    }

    static func truncate(_ s: String, _ n: Int = 60) -> String {
        let t = s.replacingOccurrences(of: "\n", with: " ").trimmingCharacters(in: .whitespacesAndNewlines)
        return t.count <= n ? t : String(t.prefix(n - 1)).trimmingCharacters(in: .whitespaces) + "…"
    }

    /// Un-positioned nodes: one per learning question, one per source a question actually used (a file nobody asked
    /// about stays off the map), one per Studio item or insight. Returned with the group each one seeds into.
    public static func buildNodes(turns: [JourneyTurn], sources: [SourceRef], artifacts: [ArtifactRef]) -> [(node: CanvasNode, group: String)] {
        var out: [(node: CanvasNode, group: String)] = []
        var referenced = Set<String>()
        let fmt = ISO8601DateFormatter()
        for t in turns where isLearning(t.question) {
            referenced.formUnion(t.sourcesUsed)
            let preview = t.answerPreview.trimmingCharacters(in: .whitespaces)
            let meta = CanvasNode.Meta(sources: t.sourcesUsed.count, answered: !preview.isEmpty, preview: truncate(preview, 180),
                                       topics: Array(t.filenames.prefix(3)), sourceIds: t.sourcesUsed, gap: CanvasGaps.reason(for: t))
            let node = CanvasNode(id: CanvasNode.derivedId(refType: "exploration_query", refId: t.id), kind: CanvasNode.chatKind,
                                  refType: "exploration_query", refId: t.id, title: truncate(t.question),
                                  snapshot: .init(payload: "**Q:** \(t.question)\n\n\(preview)".trimmingCharacters(in: .whitespacesAndNewlines), metadata: meta),
                                  createdAt: fmt.string(from: t.timestamp))
            out.append((node, t.filenames.first ?? ""))
        }
        let titles = Dictionary(sources.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        for id in referenced.sorted() {
            guard let s = titles[id] else { continue }
            out.append((CanvasNode(id: CanvasNode.derivedId(refType: "source", refId: id), kind: CanvasNode.sourceKind, refType: "source", refId: id,
                                   title: truncate(s.title), snapshot: .init(payload: "**Source:** \(s.title)", metadata: .init(sourceIds: [id])), createdAt: s.created), sourcesGroup))
        }
        for a in artifacts {
            let meta = CanvasNode.Meta(sources: a.sourceIds.count, answered: true, preview: truncate(a.preview, 180), sourceIds: a.sourceIds, studioKind: a.studioKind, insightKind: a.insightKind)
            out.append((CanvasNode(id: CanvasNode.derivedId(refType: a.kind, refId: a.id), kind: CanvasNode.artifactKind, refType: a.kind, refId: a.id,
                                   title: truncate(a.title), snapshot: .init(payload: "**\(a.kind.capitalized):** \(a.title)\n\n\(a.preview)", metadata: meta),
                                   createdAt: fmt.string(from: a.created)), "__artifacts__"))
        }
        return out
    }

    /// Seed positions: one column per group, sources last, artifacts before them.
    public static func seedLayout(_ built: [(node: CanvasNode, group: String)], startingAt origin: (x: Double, y: Double) = (0, 0)) -> [CanvasNode] {
        var groups: [String: [CanvasNode]] = [:]
        var order: [String] = []
        for (n, g) in built { if groups[g] == nil { order.append(g) }; groups[g, default: []].append(n) }
        let sorted = order.sorted { a, b in
            let ra = a == sourcesGroup ? 2 : (a == "__artifacts__" ? 1 : 0), rb = b == sourcesGroup ? 2 : (b == "__artifacts__" ? 1 : 0)
            return ra != rb ? ra < rb : a < b
        }
        var out: [CanvasNode] = []
        for (col, g) in sorted.enumerated() {
            for (row, var n) in (groups[g] ?? []).enumerated() {
                n.x = origin.x + Double(col) * columnWidth; n.y = origin.y + Double(row) * rowHeight; n.z = 0
                out.append(n)
            }
        }
        return out
    }

    /// Merge: nodes already on the map keep their place, size and card; new ones are placed below the lowest existing
    /// node so they are found at once. Derived nodes whose thing is gone (a deleted source) are removed; user-made
    /// notes always stay. Edges that touch a removed node go with it.
    public static func merge(_ layout: CanvasLayout, with built: [(node: CanvasNode, group: String)], removeMissing: Bool = true) -> CanvasLayout {
        var out = layout
        let existing = Dictionary(layout.nodes.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        let incoming = Set(built.map(\.node.id))
        if removeMissing {
            let gone = layout.nodes.filter { !$0.isTopic && $0.kind != CanvasNode.noteKind && $0.refType != "canvas_answer" && !incoming.contains($0.id) }.map(\.id)
            for id in gone { out.remove(node: id) }
        }
        let fresh = built.filter { existing[$0.node.id] == nil }
        let bottom = (out.nodes.map { $0.y + ($0.height ?? 120) }.max() ?? -rowHeight) + rowHeight / 2
        let placed = seedLayout(fresh, startingAt: (0, bottom))
        for var n in placed { n.createdAt = n.createdAt ?? CanvasStore.stamp(); n.updatedAt = CanvasStore.stamp(); out.nodes.append(n) }
        // Refresh the snapshot of what stayed (an answer may have been re-asked), keeping geometry and card.
        for (n, _) in built { if let i = out.nodes.firstIndex(where: { $0.id == n.id }) { out.nodes[i].snapshot = n.snapshot; out.nodes[i].title = n.title } }
        return out
    }

    // MARK: sub-topic cards (accretive, as the old canvas_subtopics)

    public static let assignThreshold: Float = 0.58
    public static let seedThreshold: Float = 0.66
    public static let minNewTopicSize = 2

    public struct Assignment: Sendable, Equatable {
        public var assigned: [String: String]        // node id → topic id
        public var newTopics: [(members: [String], centroid: [Float])]
        public static func == (a: Assignment, b: Assignment) -> Bool { a.assigned == b.assigned && a.newTopics.map(\.members) == b.newTopics.map(\.members) }
    }

    /// Assigns each thread to the nearest existing card (cosine ≥ assign threshold), seeds new cards from the unmatched
    /// threads that cluster together (≥ seed threshold, at least two), leaves lone ones as orphans. Nodes already on a
    /// card stay there: identity is worth more than a slightly better fit.
    public static func assignTopics(nodes: [(id: String, vector: [Float], topicId: String?)], topics: [CanvasTopic]) -> Assignment {
        var assigned: [String: String] = [:]
        var unmatched: [(id: String, vector: [Float])] = []
        for n in nodes {
            if let t = n.topicId, topics.contains(where: { $0.id == t }) { assigned[n.id] = t; continue }
            guard !n.vector.isEmpty else { continue }
            var best: (id: String, score: Float)?
            for t in topics where t.centroid.count == n.vector.count {
                let s = VectorMath.dot(VectorMath.normalized(n.vector), VectorMath.normalized(t.centroid))
                if best == nil || s > best!.score { best = (t.id, s) }
            }
            if let best, best.score >= assignThreshold { assigned[n.id] = best.id } else { unmatched.append((n.id, n.vector)) }
        }
        // Single-link grouping of the unmatched at the seed threshold.
        var groupOf = Array(unmatched.indices)
        func root(_ i: Int) -> Int { var i = i; while groupOf[i] != i { i = groupOf[i] }; return i }
        for i in unmatched.indices { for j in unmatched.indices where j > i {
            guard unmatched[i].vector.count == unmatched[j].vector.count else { continue }
            if VectorMath.dot(VectorMath.normalized(unmatched[i].vector), VectorMath.normalized(unmatched[j].vector)) >= seedThreshold { groupOf[root(j)] = root(i) }
        } }
        var groups: [Int: [Int]] = [:]
        for i in unmatched.indices { groups[root(i), default: []].append(i) }
        var newTopics: [(members: [String], centroid: [Float])] = []
        for (_, members) in groups.sorted(by: { $0.value.count > $1.value.count || ($0.value.count == $1.value.count && $0.key < $1.key) }) where members.count >= minNewTopicSize {
            let vectors = members.map { unmatched[$0].vector }
            newTopics.append((members.map { unmatched[$0].id }, mean(vectors)))
        }
        return Assignment(assigned: assigned, newTopics: newTopics)
    }

    public static func mean(_ vectors: [[Float]]) -> [Float] {
        guard let dims = vectors.first?.count, dims > 0 else { return [] }
        var out = [Float](repeating: 0, count: dims)
        var n: Float = 0
        for v in vectors where v.count == dims { for i in 0..<dims { out[i] += v[i] }; n += 1 }
        return n > 0 ? out.map { $0 / n } : []
    }

    // MARK: the map's arrangement (the old relayout: cards in a grid, threads in their card, orphans and sources aside)

    public static let cardPadding = 24.0
    public static let chipWidth = 220.0
    public static let chipHeight = 96.0
    public static let chipGap = 16.0

    /// Lays the whole map out again: every topic card a block with its threads inside in rows of three, cards flowing
    /// in rows across; orphan questions in a column to the right, artifacts next, sources at the bottom. The cards'
    /// sizes come out of this too.
    public static func arrange(_ layout: CanvasLayout) -> CanvasLayout {
        var out = layout
        let cards = layout.topicCards.sorted { ($0.createdAt ?? "") < ($1.createdAt ?? "") }
        var cursorX = 0.0, cursorY = 0.0, rowBottom = 0.0
        let maxRowWidth = 3 * (3 * chipWidth + 2 * chipGap + 2 * cardPadding) + 2 * 80
        var placed = Set<String>()
        func place(_ id: String, _ x: Double, _ y: Double, width: Double? = nil, height: Double? = nil) {
            guard let i = out.nodes.firstIndex(where: { $0.id == id }) else { return }
            out.nodes[i].x = x; out.nodes[i].y = y
            if let width { out.nodes[i].width = width }; if let height { out.nodes[i].height = height }
            placed.insert(id)
        }
        for card in cards {
            let members = layout.members(of: card.id).sorted { ($0.createdAt ?? "") < ($1.createdAt ?? "") }
            let cols = min(3, max(1, members.count))
            let rows = max(1, (members.count + cols - 1) / cols)
            let w = Double(cols) * chipWidth + Double(cols - 1) * chipGap + 2 * cardPadding
            let h = Double(rows) * chipHeight + Double(rows - 1) * chipGap + 2 * cardPadding + 36
            if cursorX > 0, cursorX + w > maxRowWidth { cursorX = 0; cursorY = rowBottom + 80 }
            place(card.id, cursorX, cursorY, width: w, height: h)
            for (i, m) in members.enumerated() {
                let c = i % cols, r = i / cols
                place(m.id, cursorX + cardPadding + Double(c) * (chipWidth + chipGap), cursorY + 36 + cardPadding + Double(r) * (chipHeight + chipGap))
            }
            rowBottom = max(rowBottom, cursorY + h)
            cursorX += w + 80
        }
        var y = cards.isEmpty ? 0 : rowBottom + 120
        let orphans = layout.nodes.filter { !$0.isTopic && $0.topicId == nil && $0.kind != CanvasNode.sourceKind && $0.kind != CanvasNode.artifactKind }
            .sorted { ($0.createdAt ?? "") < ($1.createdAt ?? "") }
        let artifacts = layout.nodes.filter { $0.kind == CanvasNode.artifactKind && $0.topicId == nil }.sorted { ($0.createdAt ?? "") < ($1.createdAt ?? "") }
        let sources = layout.nodes.filter { $0.kind == CanvasNode.sourceKind }.sorted { $0.title < $1.title }
        for (label, group) in [("orphans", orphans), ("artifacts", artifacts), ("sources", sources)] where !group.isEmpty {
            _ = label
            let cols = 4
            for (i, n) in group.enumerated() where !placed.contains(n.id) {
                place(n.id, Double(i % cols) * (chipWidth + chipGap), y + Double(i / cols) * (chipHeight + chipGap))
            }
            y += Double((group.count + cols - 1) / cols) * (chipHeight + chipGap) + 80
        }
        out.nodes = out.nodes.map { var n = $0; n.updatedAt = CanvasStore.stamp(); return n }
        return out
    }
}
