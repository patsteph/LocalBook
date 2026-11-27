import Foundation

/// A research job (`@research`): web search, site search or a deep dive that reads the pages, scores their quality
/// and keeps the best. Results go to the notebook's Collector queue for approval, so one place decides what enters.
public struct ResearchJob: Codable, Sendable, Hashable, Identifiable {
    public enum Mode: String, Codable, Sendable, CaseIterable, Identifiable {
        case web = "web_search", site = "site_search", deep = "deep_dive"
        public var id: String { rawValue }
        public var label: String { switch self { case .web: "Web search"; case .site: "Site search"; case .deep: "Deep dive" } }
    }
    public enum Status: String, Codable, Sendable { case queued, running, done, failed }
    public struct Result: Codable, Sendable, Hashable, Identifiable {
        public var id: String { url }
        public var title: String
        public var url: String
        public var snippet: String
        public var site: String
        public var summary: String
        public var wordCount: Int
        public var quality: Double
        public var published: String?
        enum CodingKeys: String, CodingKey { case title, url, snippet, site, summary, wordCount = "word_count", quality, published }
        public init(title: String, url: String, snippet: String, site: String, summary: String = "", wordCount: Int = 0, quality: Double = 0, published: String? = nil) {
            self.title = title; self.url = url; self.snippet = snippet; self.site = site; self.summary = summary; self.wordCount = wordCount; self.quality = quality; self.published = published
        }
    }
    public var id: String
    public var topic: String
    public var notebookId: String
    public var mode: Mode
    public var site: String?
    public var status: Status
    public var progress: String
    public var results: [Result]
    public var problems: [String]
    public var created: Date
    public var finished: Date?
    public var queuedToCollector: Int
    enum CodingKeys: String, CodingKey { case id, topic, notebookId = "notebook_id", mode, site, status, progress, results, problems, created, finished, queuedToCollector = "queued_to_collector" }
    public init(id: String = UUID().uuidString.lowercased(), topic: String, notebookId: String, mode: Mode, site: String? = nil) {
        self.id = id; self.topic = topic; self.notebookId = notebookId; self.mode = mode; self.site = site
        status = .queued; progress = "Queued"; results = []; problems = []; created = Date(); queuedToCollector = 0
    }
}

public enum ResearchStore {
    public static var folder: URL {
        let dir = AppFolders.base.appendingPathComponent("Research", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
    static var url: URL { folder.appendingPathComponent("jobs.json") }
    public static func load() -> [ResearchJob] {
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601
        guard let data = try? Data(contentsOf: url) else { return [] }
        return (try? d.decode([ResearchJob].self, from: data)) ?? []
    }
    public static func save(_ jobs: [ResearchJob]) {
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601
        if let data = try? e.encode(jobs.suffix(200)) { try? data.write(to: url, options: .atomic) }
    }
}

/// The deep dive's guardrails and the quality score, as the old `DeepDiveFilters` had them.
public enum ResearchQuality {
    public static let recencyDays = 30, minWordCount = 500, minOutboundLinks = 3, minQuality = 0.6, maxResults = 5

    /// 0…1 from length, outbound links, recency and how well the text matches the topic's words.
    public static func score(wordCount: Int, outboundLinks: Int, ageDays: Int?, topicHits: Int, topicWords: Int) -> Double {
        let length = min(1, Double(wordCount) / 1_500)
        let links = min(1, Double(outboundLinks) / 10)
        let recency: Double = ageDays.map { $0 <= recencyDays ? 1 : max(0, 1 - Double($0 - recencyDays) / 365) } ?? 0.6
        let topical = topicWords > 0 ? Double(topicHits) / Double(topicWords) : 0.5
        return (0.3 * length + 0.15 * links + 0.2 * recency + 0.35 * topical).rounded(toPlaces: 3)
    }

    public static func wordCount(_ text: String) -> Int { text.split(whereSeparator: \.isWhitespace).count }
    public static func topicWords(_ topic: String) -> [String] {
        topic.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init).filter { $0.count > 2 && !TextClusters.stopwords.contains($0) }
    }
    public static func hits(_ text: String, words: [String]) -> Int { let t = text.lowercased(); return words.filter { t.contains($0) }.count }

    public static func summaryPrompt(topic: String, title: String, text: String) -> String {
        "Summarize this page for someone researching \"\(topic)\": 2-3 sentences on what it says that bears on the topic, then one line \"Why it matters: …\". Use only the page.\n\nTitle: \(title)\n\n\(String(text.prefix(6_000)))"
    }
}

extension Double {
    func rounded(toPlaces p: Int) -> Double { let m = pow(10.0, Double(p)); return (self * m).rounded() / m }
}
