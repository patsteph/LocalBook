import Foundation

/// The Canvas: a notebook's learning journey as a map. The document keeps the same shape as the old app's
/// `GET /canvas/layout/{notebook}` body (`nodes`, `edges`, `viewport`, snake_case keys), so a canvas exported from the
/// old app opens here and this app's canvases read the same way.
public struct CanvasNode: Codable, Identifiable, Sendable, Hashable {
    /// What kind of thing the node is: `chat_turn` (a question and its answer), `source`, `artifact` (a Studio item or an
    /// insight), `topic` (a sub-topic card that threads sit in), `note`.
    public var id: String
    public var kind: String
    public var refType: String?
    public var refId: String?
    public var title: String
    /// A frozen, render-ready snapshot of the thing: `payload` is Markdown, `metadata` carries the chip's depth facts
    /// (sources, answered, preview, topics), as the old Artifact envelope did.
    public var snapshot: Snapshot
    public var x: Double
    public var y: Double
    public var z: Int
    public var width: Double?
    public var height: Double?
    /// The sub-topic card this thread belongs to (nil = an orphan: the map's invitation to say what it was about).
    public var topicId: String?
    public var parentId: String?
    /// The user's answer to "what were you exploring here?" on an orphan.
    public var intent: String?
    public var createdAt: String?
    public var updatedAt: String?

    public struct Snapshot: Codable, Sendable, Hashable {
        public var type: String
        public var payload: String
        public var metadata: Meta?
        public init(payload: String, metadata: Meta? = nil) { type = "markdown"; self.payload = payload; self.metadata = metadata }
    }
    public struct Meta: Codable, Sendable, Hashable {
        public var sources: Int?
        public var answered: Bool?
        public var preview: String?
        public var topics: [String]?
        public var sourceIds: [String]?
        public var studioKind: String?
        public var insightKind: String?
        public var gap: String?            // why the notebook answered weakly, when it did
        public init(sources: Int? = nil, answered: Bool? = nil, preview: String? = nil, topics: [String]? = nil, sourceIds: [String]? = nil, studioKind: String? = nil, insightKind: String? = nil, gap: String? = nil) {
            self.sources = sources; self.answered = answered; self.preview = preview; self.topics = topics; self.sourceIds = sourceIds; self.studioKind = studioKind; self.insightKind = insightKind; self.gap = gap
        }
        enum CodingKeys: String, CodingKey { case sources, answered, preview, topics, sourceIds = "source_ids", studioKind = "studio_kind", insightKind = "insight_kind", gap }
    }

    enum CodingKeys: String, CodingKey {
        case id, kind, refType = "ref_type", refId = "ref_id", title, snapshot, x, y, z, width, height, topicId = "topic_id", parentId = "parent_id", intent, createdAt = "created_at", updatedAt = "updated_at"
    }

    public init(id: String, kind: String, refType: String? = nil, refId: String? = nil, title: String, snapshot: Snapshot, x: Double = 0, y: Double = 0, z: Int = 0, width: Double? = nil, height: Double? = nil, topicId: String? = nil, parentId: String? = nil, intent: String? = nil, createdAt: String? = nil, updatedAt: String? = nil) {
        self.id = id; self.kind = kind; self.refType = refType; self.refId = refId; self.title = title; self.snapshot = snapshot
        self.x = x; self.y = y; self.z = z; self.width = width; self.height = height; self.topicId = topicId; self.parentId = parentId; self.intent = intent; self.createdAt = createdAt; self.updatedAt = updatedAt
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? "chat_turn"
        refType = try c.decodeIfPresent(String.self, forKey: .refType)
        refId = try c.decodeIfPresent(String.self, forKey: .refId)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        // The old app stored any JSON here; a snapshot without a Markdown payload becomes the title.
        if let s = try? c.decodeIfPresent(Snapshot.self, forKey: .snapshot) { snapshot = s }
        else if let s = try? c.decodeIfPresent(String.self, forKey: .snapshot) { snapshot = Snapshot(payload: s) }
        else { snapshot = Snapshot(payload: title) }
        x = try c.decodeIfPresent(Double.self, forKey: .x) ?? 0
        y = try c.decodeIfPresent(Double.self, forKey: .y) ?? 0
        z = try c.decodeIfPresent(Int.self, forKey: .z) ?? 0
        width = try c.decodeIfPresent(Double.self, forKey: .width)
        height = try c.decodeIfPresent(Double.self, forKey: .height)
        topicId = try c.decodeIfPresent(String.self, forKey: .topicId)
        parentId = try c.decodeIfPresent(String.self, forKey: .parentId)
        intent = try c.decodeIfPresent(String.self, forKey: .intent)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt)
        updatedAt = try c.decodeIfPresent(String.self, forKey: .updatedAt)
    }

    public static let chatKind = "chat_turn"
    public static let sourceKind = "source"
    public static let artifactKind = "artifact"
    public static let topicKind = "topic"
    public static let noteKind = "note"

    public var isTopic: Bool { kind == Self.topicKind }
    /// A stable id for a derived node: the same question or source lands on the same node every populate.
    public static func derivedId(refType: String, refId: String) -> String { refType + ":" + refId }
    /// The source ids a node stands on: a source node's own id, else what its metadata says it used.
    public var sourceIds: [String] {
        if kind == Self.sourceKind, let refId { return [refId] }
        return snapshot.metadata?.sourceIds ?? []
    }
}

/// An edge between two nodes. `state` is one of the old app's six: `candidate` (a suggestion, dotted), `provenance`
/// (made from), `user` (drawn by hand), `curator` (auto-connected), `researched`, `tension` (two sources disagree).
public struct CanvasEdge: Codable, Identifiable, Sendable, Hashable {
    public var id: String
    public var source: String
    public var target: String
    public var state: String
    public var label: String
    public var meta: [String: String]
    public var createdAt: String?

    public static let states = ["candidate", "provenance", "user", "curator", "researched", "tension"]

    enum CodingKeys: String, CodingKey { case id, source, target, state, label, meta, createdAt = "created_at" }

    public init(id: String? = nil, source: String, target: String, state: String, label: String = "", meta: [String: String] = [:], createdAt: String? = nil) {
        self.id = id ?? Self.pairId(source, target, state)
        self.source = source; self.target = target; self.state = state; self.label = label; self.meta = meta; self.createdAt = createdAt
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = try c.decode(String.self, forKey: .source)
        target = try c.decode(String.self, forKey: .target)
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? "user"
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        // The old app's meta held any JSON; keep what reads as text.
        if let m = try? c.decodeIfPresent([String: String].self, forKey: .meta) { meta = m }
        else if let any = try? c.decodeIfPresent([String: AnyText].self, forKey: .meta) { meta = any.mapValues(\.text) }
        else { meta = [:] }
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? Self.pairId(source, target, state)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt)
    }

    /// One id for a pair whichever way round it is given, per state.
    public static func pairId(_ a: String, _ b: String, _ state: String) -> String {
        let pair = [a, b].sorted().joined(separator: "|")
        return state + ":" + pair
    }
    public func touches(_ nodeId: String) -> Bool { source == nodeId || target == nodeId }
    public func other(than nodeId: String) -> String { source == nodeId ? target : source }
}

/// Any JSON scalar read as text (for the old app's free-form edge meta).
struct AnyText: Decodable {
    let text: String
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let s = try? c.decode(String.self) { text = s }
        else if let d = try? c.decode(Double.self) { text = d == d.rounded() ? String(Int(d)) : String(d) }
        else if let b = try? c.decode(Bool.self) { text = b ? "true" : "false" }
        else { text = "" }
    }
}

public struct CanvasViewport: Codable, Sendable, Hashable {
    public var x: Double
    public var y: Double
    public var zoom: Double
    public init(x: Double = 0, y: Double = 0, zoom: Double = 1) { self.x = x; self.y = y; self.zoom = zoom }
}

/// One notebook's canvas, as the old app's layout body.
public struct CanvasLayout: Codable, Sendable, Hashable {
    public var nodes: [CanvasNode]
    public var edges: [CanvasEdge]
    public var viewport: CanvasViewport
    /// Sub-topic cards' centroids, so the next populate assigns to the same cards (the old `canvas_topics` store).
    public var topics: [CanvasTopic]
    /// Spaced-repetition state per node (the old `canvas_recall` store).
    public var recall: [String: RecallState]

    enum CodingKeys: String, CodingKey { case nodes, edges, viewport, topics, recall }

    public init(nodes: [CanvasNode] = [], edges: [CanvasEdge] = [], viewport: CanvasViewport = CanvasViewport(), topics: [CanvasTopic] = [], recall: [String: RecallState] = [:]) {
        self.nodes = nodes; self.edges = edges; self.viewport = viewport; self.topics = topics; self.recall = recall
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        nodes = try c.decodeIfPresent([CanvasNode].self, forKey: .nodes) ?? []
        edges = try c.decodeIfPresent([CanvasEdge].self, forKey: .edges) ?? []
        viewport = try c.decodeIfPresent(CanvasViewport.self, forKey: .viewport) ?? CanvasViewport()
        topics = try c.decodeIfPresent([CanvasTopic].self, forKey: .topics) ?? []
        recall = try c.decodeIfPresent([String: RecallState].self, forKey: .recall) ?? [:]
    }

    public func node(_ id: String) -> CanvasNode? { nodes.first { $0.id == id } }
    public var threads: [CanvasNode] { nodes.filter { !$0.isTopic } }
    public var topicCards: [CanvasNode] { nodes.filter(\.isTopic) }
    public func members(of topicId: String) -> [CanvasNode] { nodes.filter { $0.topicId == topicId && !$0.isTopic } }
    public var orphans: [CanvasNode] { nodes.filter { $0.kind == CanvasNode.chatKind && $0.topicId == nil } }

    public mutating func upsert(_ edge: CanvasEdge) {
        if let i = edges.firstIndex(where: { $0.id == edge.id }) { edges[i] = edge } else { edges.append(edge) }
    }
    public mutating func remove(node id: String) {
        nodes.removeAll { $0.id == id }
        edges.removeAll { $0.touches(id) }
        recall[id] = nil
    }
    public mutating func move(_ id: String, x: Double, y: Double) {
        guard let i = nodes.firstIndex(where: { $0.id == id }) else { return }
        nodes[i].x = x; nodes[i].y = y; nodes[i].updatedAt = CanvasStore.stamp()
    }
}

/// A sub-topic card with its centroid: identity that survives from one populate to the next.
public struct CanvasTopic: Codable, Identifiable, Sendable, Hashable {
    public var id: String
    public var title: String
    public var summary: String
    public var centroid: [Float]
    public var members: Int
    public var createdAt: String?
    enum CodingKeys: String, CodingKey { case id, title, summary, centroid, members, createdAt = "created_at" }
    public init(id: String = UUID().uuidString, title: String, summary: String = "", centroid: [Float], members: Int, createdAt: String? = nil) {
        self.id = id; self.title = title; self.summary = summary; self.centroid = centroid; self.members = members; self.createdAt = createdAt
    }
}

/// SM-2 state for one node, as the old recall store kept it.
public struct RecallState: Codable, Sendable, Hashable {
    public var interval: Int           // days
    public var ease: Double
    public var repetitions: Int
    public var nextDue: Date
    public var reviews: Int
    enum CodingKeys: String, CodingKey { case interval, ease, repetitions, nextDue = "next_due", reviews }
    public init(interval: Int = 0, ease: Double = 2.5, repetitions: Int = 0, nextDue: Date, reviews: Int = 0) {
        self.interval = interval; self.ease = ease; self.repetitions = repetitions; self.nextDue = nextDue; self.reviews = reviews
    }
}

/// Canvases as JSON files under `Canvas/`, one per notebook.
public enum CanvasStore {
    public static var folder: URL {
        let dir = AppFolders.base.appendingPathComponent("Canvas", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
    public static func url(notebook: String) -> URL { folder.appendingPathComponent(safe(notebook) + ".json") }

    public static func load(notebook: String) -> CanvasLayout {
        guard let data = try? Data(contentsOf: url(notebook: notebook)) else { return CanvasLayout() }
        return decode(data) ?? CanvasLayout()
    }
    public static func decode(_ data: Data) -> CanvasLayout? {
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601
        return try? d.decode(CanvasLayout.self, from: data)
    }
    public static func encode(_ layout: CanvasLayout) -> Data {
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601; e.outputFormatting = [.sortedKeys]
        return (try? e.encode(layout)) ?? Data()
    }
    public static func save(_ layout: CanvasLayout, notebook: String) {
        try? encode(layout).write(to: url(notebook: notebook), options: .atomic)
    }
    public static func delete(notebook: String) { try? FileManager.default.removeItem(at: url(notebook: notebook)) }

    public static func safe(_ s: String) -> String { String(s.unicodeScalars.map { CharacterSet.alphanumerics.contains($0) || $0 == "-" || $0 == "_" ? Character($0) : "_" }) }
    public static func stamp(_ date: Date = Date()) -> String { ISO8601DateFormatter().string(from: date) }
}

/// The learning journey behind the canvas: every question asked in a notebook, with its answer's first lines and the
/// sources it used (the old `exploration_store`). One JSON-lines file per notebook under `Journey/`, appended as turns
/// finish, so the canvas can be built from a notebook's whole history and not only from the chat on screen.
public struct JourneyTurn: Codable, Identifiable, Sendable, Hashable {
    public var id: String
    public var notebookId: String
    public var question: String
    public var answerPreview: String
    public var sourcesUsed: [String]
    public var filenames: [String]
    public var timestamp: Date
    /// The old app's binary: 0.3 when the answer read as a miss, 0.7 otherwise.
    public var confidence: Double
    /// Where it was asked: `ask`, `curator` (across notebooks), `canvas` (chat with a selection), `agent`.
    public var origin: String

    enum CodingKeys: String, CodingKey { case id, notebookId = "notebook_id", question = "query", answerPreview = "answer_preview", sourcesUsed = "sources_used", filenames = "topics", timestamp, confidence, origin }

    public init(id: String = UUID().uuidString, notebookId: String, question: String, answer: String, sourcesUsed: [String], filenames: [String], timestamp: Date = Date(), origin: String = "ask") {
        self.id = id; self.notebookId = notebookId; self.question = question
        self.answerPreview = JourneyTurn.preview(answer)
        self.sourcesUsed = sourcesUsed; self.filenames = filenames; self.timestamp = timestamp; self.origin = origin
        self.confidence = CanvasGaps.readsAsMiss(answer) ? 0.3 : 0.7
    }

    public static func preview(_ answer: String, limit: Int = 400) -> String {
        let flat = answer.replacingOccurrences(of: "\n", with: " ").trimmingCharacters(in: .whitespacesAndNewlines)
        return flat.count <= limit ? flat : String(flat.prefix(limit - 1)).trimmingCharacters(in: .whitespaces) + "…"
    }
}

public enum JourneyStore {
    public static var folder: URL {
        let dir = AppFolders.base.appendingPathComponent("Journey", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
    static func url(notebook: String) -> URL { folder.appendingPathComponent(CanvasStore.safe(notebook) + ".jsonl") }

    public static func append(_ turn: JourneyTurn) {
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601
        guard var line = try? e.encode(turn) else { return }
        line.append(0x0A)
        let u = url(notebook: turn.notebookId)
        if let h = try? FileHandle(forWritingTo: u) { defer { try? h.close() }; _ = try? h.seekToEnd(); try? h.write(contentsOf: line) }
        else { try? line.write(to: u) }
    }

    public static func turns(notebook: String) -> [JourneyTurn] {
        guard let data = try? Data(contentsOf: url(notebook: notebook)), let text = String(data: data, encoding: .utf8) else { return [] }
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601
        return text.split(separator: "\n").compactMap { try? d.decode(JourneyTurn.self, from: Data($0.utf8)) }
    }

    public static func clear(notebook: String) { try? FileManager.default.removeItem(at: url(notebook: notebook)) }
}
