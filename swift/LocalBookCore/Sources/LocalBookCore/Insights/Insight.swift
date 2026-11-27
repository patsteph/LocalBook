import Foundation

/// One insight made from a notebook: a timeline, a knowledge graph, the contradictions found, a comparison of two
/// sources, the perspectives on a topic, or the notebook's topics. Kept as JSON files under `Insights/`, like Studio
/// items, with the typed body encoded in `body`.
public struct Insight: Codable, Identifiable, Sendable, Hashable {
    public enum Kind: String, Codable, CaseIterable, Sendable, Identifiable {
        case timeline, graph, contradictions, comparison, perspectives, topics
        public var id: String { rawValue }
        public var label: String {
            switch self {
            case .timeline: "Timeline"; case .graph: "Knowledge graph"; case .contradictions: "Contradictions"
            case .comparison: "Comparison"; case .perspectives: "Perspectives"; case .topics: "Topics"
            }
        }
        public var symbol: String {
            switch self {
            case .timeline: "calendar.day.timeline.left"; case .graph: "point.3.connected.trianglepath.dotted"; case .contradictions: "exclamationmark.triangle"
            case .comparison: "rectangle.split.2x1"; case .perspectives: "person.2.wave.2"; case .topics: "tag"
            }
        }
        public var blurb: String {
            switch self {
            case .timeline: "Dates and events, in order"
            case .graph: "People, places and things, and how they connect"
            case .contradictions: "Where the sources disagree"
            case .comparison: "Two sources side by side"
            case .perspectives: "What each source says about a topic"
            case .topics: "The themes the notebook falls into"
            }
        }
    }

    public var id: String
    public var notebookId: String
    public var kind: Kind
    public var title: String
    public var created: Date
    /// The JSON of the kind's body type below.
    public var body: String

    public init(id: String = UUID().uuidString, notebookId: String, kind: Kind, title: String, created: Date = Date(), body: String) {
        self.id = id; self.notebookId = notebookId; self.kind = kind; self.title = title; self.created = created; self.body = body
    }

    public func decode<T: Decodable>(_ type: T.Type) -> T? { try? JSONDecoder().decode(type, from: Data(body.utf8)) }
    public static func encode<T: Encodable>(_ value: T) -> String { String(decoding: (try? JSONEncoder().encode(value)) ?? Data("{}".utf8), as: UTF8.self) }
}

public struct TimelineInsight: Codable, Sendable, Hashable {
    public struct Event: Codable, Sendable, Hashable, Identifiable {
        public var id: String
        public var date: String
        public var title: String
        public var description: String
        /// low, medium or high.
        public var importance: String
        /// Sortable: the year with a fraction for the month and day; nil when the date could not be read.
        public var sortKey: Double?
        public var sourceId: String?
        public var filename: String?
        public init(id: String = UUID().uuidString, date: String, title: String, description: String, importance: String, sortKey: Double?, sourceId: String? = nil, filename: String? = nil) {
            self.id = id; self.date = date; self.title = title; self.description = description; self.importance = importance; self.sortKey = sortKey; self.sourceId = sourceId; self.filename = filename
        }
    }
    public var events: [Event]
    public var timeSpan: String
    public var context: String
    public init(events: [Event], timeSpan: String, context: String) { self.events = events; self.timeSpan = timeSpan; self.context = context }
}

public struct GraphInsight: Codable, Sendable, Hashable {
    public struct Node: Codable, Sendable, Hashable, Identifiable {
        public var id: String
        public var name: String
        /// person, company, location, product, date, metric, concept.
        public var type: String
        public var mentions: Int
        public var context: String
        public var sourceIds: [String]
        public init(id: String, name: String, type: String, mentions: Int, context: String, sourceIds: [String]) {
            self.id = id; self.name = name; self.type = type; self.mentions = mentions; self.context = context; self.sourceIds = sourceIds
        }
    }
    public struct Edge: Codable, Sendable, Hashable, Identifiable {
        public var a: String
        public var b: String
        public var relation: String
        public var weight: Int
        public var id: String { a + "→" + b }
        public init(a: String, b: String, relation: String, weight: Int) { self.a = a; self.b = b; self.relation = relation; self.weight = weight }
    }
    public var nodes: [Node]
    public var edges: [Edge]
    public init(nodes: [Node], edges: [Edge]) { self.nodes = nodes; self.edges = edges }
}

public struct ContradictionsInsight: Codable, Sendable, Hashable {
    public struct Item: Codable, Sendable, Hashable, Identifiable {
        public var id: String
        public var claimA: String
        public var sourceA: String
        public var claimB: String
        public var sourceB: String
        /// factual, statistical, temporal, methodological, interpretive.
        public var type: String
        /// low, medium, high.
        public var severity: String
        public var explanation: String
        public var resolutionHint: String?
        public var dismissed: Bool
        public init(id: String = UUID().uuidString, claimA: String, sourceA: String, claimB: String, sourceB: String, type: String, severity: String, explanation: String, resolutionHint: String?, dismissed: Bool = false) {
            self.id = id; self.claimA = claimA; self.sourceA = sourceA; self.claimB = claimB; self.sourceB = sourceB; self.type = type; self.severity = severity; self.explanation = explanation; self.resolutionHint = resolutionHint; self.dismissed = dismissed
        }
    }
    public var items: [Item]
    public var claimsChecked: Int
    public var pairsChecked: Int
    public init(items: [Item], claimsChecked: Int, pairsChecked: Int) { self.items = items; self.claimsChecked = claimsChecked; self.pairsChecked = pairsChecked }
}

public struct ComparisonInsight: Codable, Sendable, Hashable {
    public var sourceA: String, titleA: String
    public var sourceB: String, titleB: String
    public var focus: String?
    public var similarities: [String]
    public var differences: [String]
    public var uniqueToA: [String]
    public var uniqueToB: [String]
    public var synthesis: String
    public init(sourceA: String, titleA: String, sourceB: String, titleB: String, focus: String?, similarities: [String], differences: [String], uniqueToA: [String], uniqueToB: [String], synthesis: String) {
        self.sourceA = sourceA; self.titleA = titleA; self.sourceB = sourceB; self.titleB = titleB; self.focus = focus
        self.similarities = similarities; self.differences = differences; self.uniqueToA = uniqueToA; self.uniqueToB = uniqueToB; self.synthesis = synthesis
    }
}

public struct PerspectivesInsight: Codable, Sendable, Hashable {
    public struct Perspective: Codable, Sendable, Hashable, Identifiable {
        public var sourceId: String
        public var filename: String
        public var take: String
        public var claims: [String]
        public var id: String { sourceId }
        public init(sourceId: String, filename: String, take: String, claims: [String]) { self.sourceId = sourceId; self.filename = filename; self.take = take; self.claims = claims }
    }
    public var topic: String
    public var perspectives: [Perspective]
    public var consensus: [String]
    public var contested: [String]
    public var synthesis: String
    public init(topic: String, perspectives: [Perspective], consensus: [String], contested: [String], synthesis: String) {
        self.topic = topic; self.perspectives = perspectives; self.consensus = consensus; self.contested = contested; self.synthesis = synthesis
    }
}

public struct TopicsInsight: Codable, Sendable, Hashable {
    public struct Topic: Codable, Sendable, Hashable, Identifiable {
        public var id: String
        public var name: String
        public var keywords: [String]
        public var chunkCount: Int
        public var sources: [String]
        public var sample: String
        public init(id: String = UUID().uuidString, name: String, keywords: [String], chunkCount: Int, sources: [String], sample: String) {
            self.id = id; self.name = name; self.keywords = keywords; self.chunkCount = chunkCount; self.sources = sources; self.sample = sample
        }
    }
    public var topics: [Topic]
    public var chunksClustered: Int
    public init(topics: [Topic], chunksClustered: Int) { self.topics = topics; self.chunksClustered = chunksClustered }
}

/// Insights as JSON files under `Insights/`, one per item.
public enum InsightStore {
    public static var folder: URL {
        let dir = AppFolders.base.appendingPathComponent("Insights", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
    private static func url(_ id: String) -> URL { folder.appendingPathComponent(id + ".json") }

    public static func all(notebook: String?) -> [Insight] {
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        let decoder = JSONDecoder()
        return files.filter { $0.pathExtension == "json" }.compactMap { try? decoder.decode(Insight.self, from: Data(contentsOf: $0)) }
            .filter { notebook == nil || $0.notebookId == notebook }.sorted { $0.created > $1.created }
    }
    public static func save(_ item: Insight) {
        if let data = try? JSONEncoder().encode(item) { try? data.write(to: url(item.id), options: .atomic) }
    }
    public static func delete(_ item: Insight) { try? FileManager.default.removeItem(at: url(item.id)) }
}

/// A finding: something worth keeping from a notebook (an answer, a passage, a source, a note or an insight), with
/// tags and a star, as the old app's findings were. JSON files under `Findings/`.
public struct Finding: Codable, Identifiable, Sendable, Hashable {
    public enum Kind: String, Codable, CaseIterable, Sendable { case answer, highlight, source, note, insight, visual }
    public var id: String
    public var notebookId: String
    public var kind: Kind
    public var title: String
    public var text: String
    /// Where it came from: source ids for an answer or highlight, the insight id for an insight.
    public var refs: [String]
    public var tags: [String]
    public var starred: Bool
    public var created: Date
    public var updated: Date
    public init(id: String = UUID().uuidString, notebookId: String, kind: Kind, title: String, text: String, refs: [String] = [], tags: [String] = [], starred: Bool = false, created: Date = Date(), updated: Date = Date()) {
        self.id = id; self.notebookId = notebookId; self.kind = kind; self.title = title; self.text = text; self.refs = refs; self.tags = tags; self.starred = starred; self.created = created; self.updated = updated
    }
}

public enum FindingsStore {
    public static var folder: URL {
        let dir = AppFolders.base.appendingPathComponent("Findings", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
    private static func url(_ id: String) -> URL { folder.appendingPathComponent(id + ".json") }

    public static func all(notebook: String?) -> [Finding] {
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        let decoder = JSONDecoder()
        return files.filter { $0.pathExtension == "json" }.compactMap { try? decoder.decode(Finding.self, from: Data(contentsOf: $0)) }
            .filter { notebook == nil || $0.notebookId == notebook }
            .sorted { ($0.starred ? 1 : 0, $0.created) > ($1.starred ? 1 : 0, $1.created) }
    }
    public static func save(_ finding: Finding) {
        var f = finding; f.updated = Date()
        if let data = try? JSONEncoder().encode(f) { try? data.write(to: url(f.id), options: .atomic) }
    }
    public static func delete(_ finding: Finding) { try? FileManager.default.removeItem(at: url(finding.id)) }
    /// Every tag in use, most used first.
    public static func tags(notebook: String?) -> [String] {
        var counts: [String: Int] = [:]
        for f in all(notebook: notebook) { for t in f.tags { counts[t, default: 0] += 1 } }
        return counts.sorted { $0.value > $1.value || ($0.value == $1.value && $0.key < $1.key) }.map(\.key)
    }
}
