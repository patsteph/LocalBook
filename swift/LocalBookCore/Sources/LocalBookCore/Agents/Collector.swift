import Foundation

/// The Collector: one per notebook, as the old app had it. It knows what the notebook is for (intent, subject, focus,
/// exclusions), where to look (feeds, pages, keywords), how to behave (collection mode, approval mode, schedule,
/// filters), and keeps what it found in an approval queue with a confidence and its reasons. Field names follow the
/// old `CollectorConfig` / `CollectedItem` so an exported profile reads here.
public struct CollectorConfig: Codable, Sendable, Hashable {
    public enum CollectionMode: String, Codable, Sendable, CaseIterable { case manual, automatic, hybrid }
    public enum ApprovalMode: String, Codable, Sendable, CaseIterable {
        case trustMe = "trust_me", showMe = "show_me", mixed
        public var label: String { switch self { case .trustMe: "Trust me (add everything)"; case .showMe: "Show me (I approve each)"; case .mixed: "Mixed (sure ones in, the rest asks)" } }
    }
    public enum Frequency: String, Codable, Sendable, CaseIterable {
        case manual, hourly, daily, weekly
        public var seconds: TimeInterval? { switch self { case .manual: nil; case .hourly: 3_600; case .daily: 86_400; case .weekly: 7 * 86_400 } }
    }
    public var name: String
    public var intent: String
    public var subject: String
    public var focusAreas: [String]
    public var excludedTopics: [String]
    public var collectionMode: CollectionMode
    public var approvalMode: ApprovalMode
    public var rssFeeds: [String]
    public var webPages: [String]
    public var newsKeywords: [String]
    public var frequency: Frequency
    public var maxItemsPerRun: Int
    public var maxAgeDays: Int
    public var minRelevance: Double
    public var createdAt: Date
    public var updatedAt: Date

    public init(name: String = "Scout", intent: String = "", subject: String = "", focusAreas: [String] = [], excludedTopics: [String] = [],
                collectionMode: CollectionMode = .hybrid, approvalMode: ApprovalMode = .mixed, rssFeeds: [String] = [], webPages: [String] = [],
                newsKeywords: [String] = [], frequency: Frequency = .daily, maxItemsPerRun: Int = 10, maxAgeDays: Int = 30, minRelevance: Double = 0.5) {
        self.name = name; self.intent = intent; self.subject = subject; self.focusAreas = focusAreas; self.excludedTopics = excludedTopics
        self.collectionMode = collectionMode; self.approvalMode = approvalMode; self.rssFeeds = rssFeeds; self.webPages = webPages; self.newsKeywords = newsKeywords
        self.frequency = frequency; self.maxItemsPerRun = maxItemsPerRun; self.maxAgeDays = maxAgeDays; self.minRelevance = minRelevance
        createdAt = Date(); updatedAt = Date()
    }

    enum CodingKeys: String, CodingKey {
        case name, intent, subject, focusAreas = "focus_areas", excludedTopics = "excluded_topics", collectionMode = "collection_mode", approvalMode = "approval_mode"
        case sources, schedule, filters, createdAt = "created_at", updatedAt = "updated_at"
    }
    private struct Sources: Codable { var rss_feeds: [String]; var web_pages: [String]; var news_keywords: [String] }
    private struct Schedule: Codable { var frequency: String; var max_items_per_run: Int }
    private struct Filters: Codable { var max_age_days: Int; var min_relevance: Double; var language: String? }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? "Scout"
        intent = try c.decodeIfPresent(String.self, forKey: .intent) ?? ""
        subject = try c.decodeIfPresent(String.self, forKey: .subject) ?? ""
        focusAreas = try c.decodeIfPresent([String].self, forKey: .focusAreas) ?? []
        excludedTopics = try c.decodeIfPresent([String].self, forKey: .excludedTopics) ?? []
        collectionMode = CollectionMode(rawValue: try c.decodeIfPresent(String.self, forKey: .collectionMode) ?? "") ?? .hybrid
        approvalMode = ApprovalMode(rawValue: try c.decodeIfPresent(String.self, forKey: .approvalMode) ?? "") ?? .mixed
        let s = try c.decodeIfPresent(Sources.self, forKey: .sources)
        rssFeeds = s?.rss_feeds ?? []; webPages = s?.web_pages ?? []; newsKeywords = s?.news_keywords ?? []
        let sch = try c.decodeIfPresent(Schedule.self, forKey: .schedule)
        frequency = Frequency(rawValue: sch?.frequency ?? "") ?? .daily
        maxItemsPerRun = sch?.max_items_per_run ?? 10
        let f = try c.decodeIfPresent(Filters.self, forKey: .filters)
        maxAgeDays = f?.max_age_days ?? 30; minRelevance = f?.min_relevance ?? 0.5
        createdAt = try c.decodeIfPresent(Date.self, forKey: .createdAt) ?? Date()
        updatedAt = try c.decodeIfPresent(Date.self, forKey: .updatedAt) ?? Date()
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(name, forKey: .name); try c.encode(intent, forKey: .intent); try c.encode(subject, forKey: .subject)
        try c.encode(focusAreas, forKey: .focusAreas); try c.encode(excludedTopics, forKey: .excludedTopics)
        try c.encode(collectionMode.rawValue, forKey: .collectionMode); try c.encode(approvalMode.rawValue, forKey: .approvalMode)
        try c.encode(Sources(rss_feeds: rssFeeds, web_pages: webPages, news_keywords: newsKeywords), forKey: .sources)
        try c.encode(Schedule(frequency: frequency.rawValue, max_items_per_run: maxItemsPerRun), forKey: .schedule)
        try c.encode(Filters(max_age_days: maxAgeDays, min_relevance: minRelevance, language: "en"), forKey: .filters)
        try c.encode(createdAt, forKey: .createdAt); try c.encode(updatedAt, forKey: .updatedAt)
    }

    /// What the notebook is about, in one line, for scoring and searching.
    public var intentText: String { [subject, intent, focusAreas.joined(separator: ", ")].filter { !$0.isEmpty }.joined(separator: ". ") }
    public var keywords: [String] { (focusAreas + newsKeywords + (subject.isEmpty ? [] : [subject])).map { $0.lowercased() } }
    public var isConfigured: Bool { !rssFeeds.isEmpty || !webPages.isEmpty || !newsKeywords.isEmpty }
}

/// Something the Collector found. `status`: pending, approved, rejected, expired.
public struct CollectedItem: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var title: String
    public var url: String?
    public var content: String
    public var preview: String
    public var sourceName: String
    public var sourceType: String          // rss, web, news, research, manual
    public var sourceURL: String
    public var collectedAt: Date
    public var published: Date?
    public var relevanceScore: Double
    public var sourceTrust: Double
    public var freshnessScore: Double
    public var overallConfidence: Double
    public var confidenceReasons: [String]
    public var contentHash: String
    public var status: String
    public var rejectionReason: String?
    public var expiresAt: Date?
    public var storedSourceId: String?

    enum CodingKeys: String, CodingKey {
        case id, title, url, content, preview, sourceName = "source_name", sourceType = "source_type", sourceURL = "source_url", collectedAt = "collected_at", published
        case relevanceScore = "relevance_score", sourceTrust = "source_trust", freshnessScore = "freshness_score", overallConfidence = "overall_confidence"
        case confidenceReasons = "confidence_reasons", contentHash = "content_hash", status, rejectionReason = "rejection_reason", expiresAt = "expires_at", storedSourceId = "stored_source_id"
    }

    public init(id: String = UUID().uuidString, title: String, url: String?, content: String, sourceName: String, sourceType: String, sourceURL: String, collectedAt: Date = Date(), published: Date? = nil) {
        self.id = id; self.title = title; self.url = url; self.content = content
        self.preview = String(content.prefix(280))
        self.sourceName = sourceName; self.sourceType = sourceType; self.sourceURL = sourceURL; self.collectedAt = collectedAt; self.published = published
        relevanceScore = 0.5; sourceTrust = 0.5; freshnessScore = 1; overallConfidence = 0.5; confidenceReasons = []
        contentHash = CollectorScoring.hash(url ?? "", title, content)
        status = "pending"
    }
}

/// How a feed or page has been behaving.
public struct SourceHealthRecord: Codable, Sendable, Hashable, Identifiable {
    public enum Health: String, Codable, Sendable { case healthy, degraded, failing, dead }
    public var id: String { url }
    public var url: String
    public var health: Health
    public var lastSuccess: Date?
    public var lastFailure: Date?
    public var consecutiveFailures: Int
    public var itemsCollected: Int
    public var lastError: String?
    enum CodingKeys: String, CodingKey { case url, health, lastSuccess = "last_success", lastFailure = "last_failure", consecutiveFailures = "consecutive_failures", itemsCollected = "items_collected", lastError = "last_error" }
    public init(url: String) { self.url = url; health = .healthy; consecutiveFailures = 0; itemsCollected = 0 }

    public mutating func succeeded(items: Int, at now: Date = Date()) { lastSuccess = now; consecutiveFailures = 0; itemsCollected += items; lastError = nil; health = .healthy }
    public mutating func failed(_ error: String, at now: Date = Date()) {
        lastFailure = now; consecutiveFailures += 1; lastError = error
        health = consecutiveFailures >= 6 ? .dead : (consecutiveFailures >= 3 ? .failing : .degraded)
    }
}

/// One run of the Collector.
public struct CollectorRun: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var startedAt: Date
    public var finishedAt: Date
    public var examined: Int
    public var stored: Int
    public var queued: Int
    public var skipped: Int
    public var problems: [String]
    public var trigger: String        // manual, schedule, research
    enum CodingKeys: String, CodingKey { case id, startedAt = "started_at", finishedAt = "finished_at", examined, stored, queued, skipped, problems, trigger }
    public init(id: String = UUID().uuidString, startedAt: Date, finishedAt: Date, examined: Int, stored: Int, queued: Int, skipped: Int, problems: [String], trigger: String) {
        self.id = id; self.startedAt = startedAt; self.finishedAt = finishedAt; self.examined = examined; self.stored = stored; self.queued = queued; self.skipped = skipped; self.problems = problems; self.trigger = trigger
    }
}

/// Everything the Collector keeps for one notebook, one JSON file under `Collector/`.
public struct CollectorState: Codable, Sendable, Hashable {
    public var config: CollectorConfig
    public var items: [CollectedItem]
    public var health: [SourceHealthRecord]
    public var runs: [CollectorRun]
    public var seen: [String]                  // content hashes and urls already examined
    public var pageHashes: [String: String]    // watched page → hash of its text at the last look
    public var lastRun: Date?
    public var learnedTopics: [String]         // from approvals
    public var rejectedHashes: [String]
    enum CodingKeys: String, CodingKey { case config, items, health, runs, seen, pageHashes = "page_hashes", lastRun = "last_run", learnedTopics = "learned_topics", rejectedHashes = "rejected_hashes" }
    public init(config: CollectorConfig = CollectorConfig()) {
        self.config = config; items = []; health = []; runs = []; seen = []; pageHashes = [:]; learnedTopics = []; rejectedHashes = []
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        config = try c.decodeIfPresent(CollectorConfig.self, forKey: .config) ?? CollectorConfig()
        items = try c.decodeIfPresent([CollectedItem].self, forKey: .items) ?? []
        health = try c.decodeIfPresent([SourceHealthRecord].self, forKey: .health) ?? []
        runs = try c.decodeIfPresent([CollectorRun].self, forKey: .runs) ?? []
        seen = try c.decodeIfPresent([String].self, forKey: .seen) ?? []
        pageHashes = try c.decodeIfPresent([String: String].self, forKey: .pageHashes) ?? [:]
        lastRun = try c.decodeIfPresent(Date.self, forKey: .lastRun)
        learnedTopics = try c.decodeIfPresent([String].self, forKey: .learnedTopics) ?? []
        rejectedHashes = try c.decodeIfPresent([String].self, forKey: .rejectedHashes) ?? []
    }

    public var pending: [CollectedItem] { items.filter { $0.status == "pending" }.sorted { $0.overallConfidence > $1.overallConfidence } }
    public func health(of url: String) -> SourceHealthRecord { health.first { $0.url == url } ?? SourceHealthRecord(url: url) }
    public mutating func setHealth(_ h: SourceHealthRecord) { if let i = health.firstIndex(where: { $0.url == h.url }) { health[i] = h } else { health.append(h) } }
    /// Items past their expiry go to `expired`; the queue never piles up.
    public mutating func expire(now: Date = Date()) {
        for i in items.indices where items[i].status == "pending" { if let e = items[i].expiresAt, e < now { items[i].status = "expired" } }
    }
    /// The next time the schedule wants a run, nil for manual.
    public func nextRun(now: Date = Date()) -> Date? {
        guard let every = config.frequency.seconds, config.isConfigured else { return nil }
        guard let last = lastRun else { return now }
        return last.addingTimeInterval(every)
    }
    public func isDue(now: Date = Date()) -> Bool { nextRun(now: now).map { $0 <= now } ?? false }
}

public enum CollectorStore {
    public static var folder: URL {
        let dir = AppFolders.base.appendingPathComponent("Collector", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
    static func url(notebook: String) -> URL { folder.appendingPathComponent(CanvasStore.safe(notebook) + ".json") }
    static var decoder: JSONDecoder { let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601; return d }
    static var encoder: JSONEncoder { let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601; e.outputFormatting = [.sortedKeys]; return e }

    public static func load(notebook: String) -> CollectorState {
        guard let data = try? Data(contentsOf: url(notebook: notebook)), let s = try? decoder.decode(CollectorState.self, from: data) else { return CollectorState() }
        return s
    }
    public static func save(_ state: CollectorState, notebook: String) { if let data = try? encoder.encode(state) { try? data.write(to: url(notebook: notebook), options: .atomic) } }
    public static func notebooks() -> [String] {
        ((try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []).filter { $0.pathExtension == "json" }.map { $0.deletingPathExtension().lastPathComponent }
    }
}

/// The old Collector's confidence: relevance (0.5) + source trust (0.3) + freshness (0.2), with learned bonuses, and
/// the approval decision each mode makes from it.
public enum CollectorScoring {
    public static let autoApprove = 0.85
    public static let expiryDays = 7

    public static func hash(_ parts: String...) -> String {
        var h: UInt64 = 0xcbf29ce484222325
        for byte in parts.joined(separator: "\u{1}").lowercased().utf8 { h ^= UInt64(byte); h = h &* 0x100000001b3 }
        return String(h, radix: 16)
    }

    /// Cosine 0.3…0.9 mapped onto 0…1, as the old app did.
    public static func relevance(cosine: Double?) -> (score: Double, reason: String) {
        guard let cosine else { return (0.5, "No intent set, so relevance is unknown") }
        let s = max(0, min(1, (cosine - 0.3) / 0.6))
        if s >= 0.7 { return (s, "Strongly matches what this notebook is for") }
        if s >= 0.4 { return (s, "Related to what this notebook is for") }
        return (s, "Only loosely related to this notebook")
    }

    public static func trust(_ health: SourceHealthRecord, userAdded: Bool) -> (score: Double, reason: String) {
        if userAdded, health.itemsCollected == 0 { return (0.95, "A source you added") }
        switch health.health {
        case .healthy where health.itemsCollected >= 10: return (0.9, "Trusted source (\(health.itemsCollected) items so far)")
        case .healthy: return (userAdded ? 0.85 : 0.5, userAdded ? "A source you added" : "New source, no history yet")
        case .degraded: return (0.6, "The source has been slow lately")
        case .failing, .dead: return (0.3, "The source has been failing")
        }
    }

    public static func freshness(published: Date?, now: Date = Date(), maxAgeDays: Int) -> (score: Double, reason: String) {
        guard let published else { return (0.7, "No date on the item") }
        let hours = now.timeIntervalSince(published) / 3_600
        let maxHours = Double(maxAgeDays) * 24
        if hours <= 24 { return (1, "Published today") }
        if hours <= 72 { return (0.8, "Published this week") }
        if hours <= 168 { return (0.6, "Published within 7 days") }
        if hours <= maxHours { return (max(0.3, 1 - hours / maxHours), "Published \(Int(hours / 24)) days ago") }
        return (0, "Older than \(maxAgeDays) days")
    }

    /// Fills the item's scores and reasons. `learned`: +0.1 per preferred topic hit, −0.2 when it looks like something rejected before.
    public static func score(_ item: inout CollectedItem, cosine: Double?, health: SourceHealthRecord, userAdded: Bool, learnedTopics: [String], rejectedBefore: Bool, maxAgeDays: Int, now: Date = Date()) {
        var reasons: [String] = []
        let r = relevance(cosine: cosine); item.relevanceScore = r.score; reasons.append(r.reason)
        let t = trust(health, userAdded: userAdded); item.sourceTrust = t.score; reasons.append(t.reason)
        let f = freshness(published: item.published, now: now, maxAgeDays: maxAgeDays); item.freshnessScore = f.score; reasons.append(f.reason)
        var bonus = 0.0
        let text = (item.title + " " + item.content).lowercased()
        for topic in learnedTopics where text.contains(topic.lowercased()) { bonus += 0.1; reasons.append("Matches a topic you approved before: \(topic)") }
        if rejectedBefore { bonus -= 0.2; reasons.append("Like something you rejected before") }
        var base = item.relevanceScore * 0.5 + item.sourceTrust * 0.3 + item.freshnessScore * 0.2
        if item.freshnessScore == 0 { base = min(base, 0.35); reasons.append("Capped: too old") }
        item.overallConfidence = max(0, min(1, base + bonus))
        item.confidenceReasons = reasons
    }

    public enum Decision: String, Sendable { case store, queue, skip }

    /// What the approval mode does with a scored item; items under the relevance floor are skipped first.
    public static func decision(for item: CollectedItem, mode: CollectorConfig.ApprovalMode, minRelevance: Double) -> Decision {
        if item.relevanceScore < minRelevance, item.relevanceScore > 0 || minRelevance > 0 { return .skip }
        switch mode {
        case .trustMe: return .store
        case .mixed: return item.overallConfidence >= autoApprove ? .store : .queue
        case .showMe: return .queue
        }
    }

    /// True when the item matches a keyword (or there are none to match) and touches no excluded topic.
    public static func passesFilters(title: String, summary: String, keywords: [String], excluded: [String]) -> Bool {
        let text = (title + " " + summary).lowercased()
        if excluded.contains(where: { !$0.isEmpty && text.contains($0.lowercased()) }) { return false }
        let keys = keywords.filter { !$0.isEmpty }
        return keys.isEmpty || keys.contains { text.contains($0) }
    }
}
