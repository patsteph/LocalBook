import Foundation
import LocalBookCore
import Observation

/// Runs each notebook's Collector: reads its feeds and watched pages, searches its keywords, scores what it finds
/// against what the notebook is for (the search model's vectors), and either adds a source, queues it for approval or
/// skips it as the approval mode says. Approving a queued item adds it the way a dropped link is added. A timer runs
/// the due notebooks while the app is open; nothing runs when the Mac is offline, it simply tries again later.
@MainActor @Observable
public final class CollectorEngine {
    public private(set) var states: [String: CollectorState] = [:]
    public private(set) var running: Set<String> = []
    public private(set) var status: [String: String] = [:]
    public private(set) var error: String?
    private var timer: Timer?
    private weak var library: LibraryModel?

    public init() {}

    // MARK: state

    public func state(_ notebook: String) -> CollectorState {
        if let s = states[notebook] { return s }
        let s = CollectorStore.load(notebook: notebook)
        states[notebook] = s
        return s
    }
    public func update(_ notebook: String, _ change: (inout CollectorState) -> Void) {
        var s = state(notebook)
        change(&s)
        s.config.updatedAt = Date()
        states[notebook] = s
        CollectorStore.save(s, notebook: notebook)
    }
    public func pendingCount(_ notebook: String) -> Int { state(notebook).pending.count }
    public var pendingTotal: Int { CollectorStore.notebooks().reduce(0) { $0 + state($1).pending.count } }

    // MARK: schedule

    /// Starts the clock: every ten minutes the due notebooks run, in turn.
    public func startSchedule(library: LibraryModel) {
        self.library = library
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 600, repeats: true) { [weak self] _ in Task { @MainActor in await self?.runDue() } }
        Task { try? await Task.sleep(for: .seconds(90)); await runDue() }
    }

    public func runDue() async {
        guard let library else { return }
        for id in CollectorStore.notebooks() where state(id).isDue() && !running.contains(id) {
            guard let nb = library.notebooks.first(where: { $0.id == id }) else { continue }
            await collect(notebook: nb, library: library, trigger: "schedule")
        }
    }

    // MARK: a run

    public func collect(notebook: LibraryNotebookInfo, library: LibraryModel, trigger: String = "manual") async {
        guard !running.contains(notebook.id) else { return }
        running.insert(notebook.id); defer { running.remove(notebook.id); status[notebook.id] = nil }
        error = nil
        let started = Date()
        var s = state(notebook.id)
        s.expire()
        let config = s.config
        var examined = 0, stored = 0, queued = 0, skipped = 0
        var problems: [String] = []
        let intentVector: [Float]? = config.intentText.isEmpty ? nil : try? await library.documentVector(config.intentText)
        let existing = await Self.existingURLs(notebook: notebook.id, library: library)
        var seen = Set(s.seen)
        var found: [(CollectedItem, userAdded: Bool)] = []

        for feed in config.rssFeeds.prefix(20) {
            status[notebook.id] = "Reading \(URL(string: feed)?.host() ?? feed)…"
            guard let url = URL(string: feed) else { problems.append("\(feed) is not an address"); continue }
            var health = s.health(of: feed)
            do {
                let (data, _) = try await AgentText.fetch(url)
                guard let parsed = FeedParser.parse(data) else { throw LibraryError.badResponse("\(url.host() ?? feed) is not a feed") }
                var fresh = 0
                for item in parsed.items.prefix(20) {
                    examined += 1
                    let link = item.link.isEmpty ? nil : item.link
                    let key = CollectorScoring.hash(link ?? "", item.title, item.summary)
                    guard !seen.contains(key), link.map({ !seen.contains($0) && !existing.contains(Self.normalized($0)) }) ?? true else { continue }
                    seen.insert(key); if let link { seen.insert(link) }
                    guard CollectorScoring.passesFilters(title: item.title, summary: item.summary, keywords: feed.contains("youtube.com/feeds/") ? [] : config.keywords, excluded: config.excludedTopics) else { skipped += 1; continue }
                    var c = CollectedItem(title: item.title.isEmpty ? (link ?? "Untitled") : item.title, url: link, content: item.summary, sourceName: parsed.title.isEmpty ? (url.host() ?? feed) : parsed.title,
                                          sourceType: "rss", sourceURL: feed, published: item.published)
                    c.expiresAt = Date().addingTimeInterval(Double(CollectorScoring.expiryDays) * 86_400)
                    found.append((c, true)); fresh += 1
                }
                health.succeeded(items: fresh)
            } catch { health.failed(error.localizedDescription); problems.append("\(url.host() ?? feed): \(error.localizedDescription)") }
            s.setHealth(health)
        }

        for page in config.webPages.prefix(10) {
            status[notebook.id] = "Looking at \(URL(string: page)?.host() ?? page)…"
            guard let url = URL(string: page) else { continue }
            var health = s.health(of: page)
            do {
                let fetched = try await WebExtract.fetch(url)
                guard case .text(let title, let text, _, _, let date) = fetched else { health.succeeded(items: 0); s.setHealth(health); continue }
                let hash = CollectorScoring.hash(text)
                examined += 1
                if s.pageHashes[page] != hash {
                    let first = s.pageHashes[page] == nil
                    s.pageHashes[page] = hash
                    if !existing.contains(Self.normalized(page)) || !first {
                        var c = CollectedItem(title: first ? title : "\(title) (changed)", url: page, content: String(text.prefix(4_000)), sourceName: url.host() ?? page, sourceType: "web", sourceURL: page, published: date.flatMap(FeedParser.date))
                        c.expiresAt = Date().addingTimeInterval(Double(CollectorScoring.expiryDays) * 86_400)
                        if CollectorScoring.passesFilters(title: title, summary: text, keywords: [], excluded: config.excludedTopics) { found.append((c, true)) } else { skipped += 1 }
                    }
                }
                health.succeeded(items: 1)
            } catch { health.failed(error.localizedDescription); problems.append("\(url.host() ?? page): \(error.localizedDescription)") }
            s.setHealth(health)
        }

        if !config.newsKeywords.isEmpty {
            status[notebook.id] = "Searching for \(config.newsKeywords.prefix(2).joined(separator: ", "))…"
            let query = (config.subject.isEmpty ? [] : [config.subject]) + config.newsKeywords
            let (batches, p) = await SiteSearchClient.searchAll([.hackerNews, .reddit, .wikipedia], query: query.prefix(3).joined(separator: " "), range: .month, max: 8)
            problems.append(contentsOf: p)
            for r in batches.flatMap({ $0 }) {
                examined += 1
                guard !seen.contains(r.url), !existing.contains(Self.normalized(r.url)) else { continue }
                seen.insert(r.url)
                guard CollectorScoring.passesFilters(title: r.title, summary: r.snippet, keywords: [], excluded: config.excludedTopics) else { skipped += 1; continue }
                var c = CollectedItem(title: r.title, url: r.url, content: r.snippet, sourceName: r.site.label, sourceType: "news", sourceURL: r.site.domain, published: r.published.flatMap(FeedParser.date))
                c.expiresAt = Date().addingTimeInterval(Double(CollectorScoring.expiryDays) * 86_400)
                found.append((c, false))
            }
        }

        // Score, decide, act.
        status[notebook.id] = "Weighing \(found.count) item\(found.count == 1 ? "" : "s")…"
        var keep = found
        if keep.count > config.maxItemsPerRun * 3 { keep = Array(keep.prefix(config.maxItemsPerRun * 3)) }
        var acted = 0
        for pair in keep {
            var item = pair.0
            let userAdded = pair.userAdded
            var cosine: Double?
            if let intentVector, let v = try? await library.documentVector(item.title + "\n" + item.content) { cosine = VectorMath.cosine(intentVector, v) }
            CollectorScoring.score(&item, cosine: cosine, health: s.health(of: item.sourceURL), userAdded: userAdded, learnedTopics: s.learnedTopics,
                                   rejectedBefore: s.rejectedHashes.contains(item.contentHash), maxAgeDays: config.maxAgeDays)
            let decision = CollectorScoring.decision(for: item, mode: config.approvalMode, minRelevance: intentVector == nil ? 0 : config.minRelevance)
            switch decision {
            case .skip: skipped += 1
            case .queue:
                guard acted < config.maxItemsPerRun else { skipped += 1; continue }
                s.items.append(item); queued += 1; acted += 1
                EventLog.record("collector_found", notebook: notebook.id, title: item.title, detail: item.url ?? "")
            case .store:
                guard acted < config.maxItemsPerRun else { skipped += 1; continue }
                item.status = "approved"
                if let id = await store(item, notebook: notebook.id, library: library) { item.storedSourceId = id; stored += 1; EventLog.record("collector_stored", notebook: notebook.id, title: item.title, detail: item.url ?? "") }
                else { item.status = "rejected"; item.rejectionReason = "could not be read"; skipped += 1 }
                s.items.append(item); acted += 1
            }
        }
        s.seen = Array(seen.suffix(5_000))
        s.items = Array(s.items.suffix(500))
        s.lastRun = Date()
        s.runs.append(CollectorRun(startedAt: started, finishedAt: Date(), examined: examined, stored: stored, queued: queued, skipped: skipped, problems: problems, trigger: trigger))
        s.runs = Array(s.runs.suffix(50))
        states[notebook.id] = s
        CollectorStore.save(s, notebook: notebook.id)
        if !problems.isEmpty, stored + queued == 0 { error = problems.first }
    }

    /// Adds the item as a source, the way a dropped link is added; the item's own text when there is no address.
    private func store(_ item: CollectedItem, notebook: String, library: LibraryModel) async -> String? {
        status[notebook] = "Adding \(item.title)…"
        if let u = item.url.flatMap(URL.init(string:)) {
            if let report = try? await library.ingestLink(u, notebook: notebook) { return report.sourceId }
            // The page would not read: keep what the feed gave.
        }
        guard !item.content.isEmpty else { return nil }
        return (try? await library.ingestText(item.content, title: item.title, format: "web", notebook: notebook, url: item.url))?.sourceId
    }

    // MARK: the queue

    public func approve(_ itemId: String, notebook: LibraryNotebookInfo, library: LibraryModel) async {
        guard let i = state(notebook.id).items.firstIndex(where: { $0.id == itemId }) else { return }
        var item = state(notebook.id).items[i]
        running.insert(notebook.id); defer { running.remove(notebook.id); status[notebook.id] = nil }
        if let id = await store(item, notebook: notebook.id, library: library) {
            item.status = "approved"; item.storedSourceId = id
            EventLog.record("source_added", notebook: notebook.id, title: item.title, detail: "approved from the Collector")
        } else { item.status = "rejected"; item.rejectionReason = "could not be read" }
        update(notebook.id) { s in
            if let j = s.items.firstIndex(where: { $0.id == itemId }) { s.items[j] = item }
            // What the user approves teaches the Collector what the notebook likes.
            for w in TextClusters.keywords(for: [item.title + " " + item.content], count: 3) where !s.learnedTopics.contains(w) { s.learnedTopics.append(w) }
            s.learnedTopics = Array(s.learnedTopics.suffix(40))
        }
    }

    public func approveAll(notebook: LibraryNotebookInfo, library: LibraryModel) async {
        for item in state(notebook.id).pending { await approve(item.id, notebook: notebook, library: library) }
    }

    public func reject(_ itemId: String, notebook: String, reason: String) {
        update(notebook) { s in
            guard let j = s.items.firstIndex(where: { $0.id == itemId }) else { return }
            s.items[j].status = "rejected"; s.items[j].rejectionReason = reason.isEmpty ? nil : reason
            s.rejectedHashes.append(s.items[j].contentHash); s.rejectedHashes = Array(s.rejectedHashes.suffix(500))
        }
    }

    /// Research results join the queue as items of their own kind.
    public func enqueue(_ results: [ResearchJob.Result], topic: String, notebook: String) -> Int {
        var added = 0
        update(notebook) { s in
            let seen = Set(s.items.compactMap(\.url))
            for r in results where !seen.contains(r.url) {
                var c = CollectedItem(title: r.title, url: r.url, content: r.summary.isEmpty ? r.snippet : r.summary, sourceName: r.site, sourceType: "research", sourceURL: "research: \(topic)")
                c.relevanceScore = max(0.5, r.quality); c.sourceTrust = 0.7; c.freshnessScore = 0.8
                c.overallConfidence = min(1, 0.5 * c.relevanceScore + 0.3 * c.sourceTrust + 0.2 * c.freshnessScore)
                c.confidenceReasons = ["Found by research on “\(topic)”", r.quality > 0 ? "Quality \(Int(r.quality * 100)) %" : "Not read in depth"]
                c.expiresAt = Date().addingTimeInterval(Double(CollectorScoring.expiryDays) * 86_400)
                s.items.append(c); added += 1
                EventLog.record("collector_found", notebook: notebook, title: c.title, detail: r.url)
            }
        }
        return added
    }

    /// Adds a feed or a page; a page address that advertises a feed becomes that feed.
    public func addSource(_ address: String, notebook: String) async -> String? {
        let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed.hasPrefix("http") ? trimmed : "https://" + trimmed) else { return "That is not a web address." }
        var kind = "page"
        var final = url.absoluteString
        if let (data, mime) = try? await AgentText.fetch(url) {
            if FeedParser.parse(data) != nil { kind = "feed" }
            else if (mime ?? "").contains("html"), let html = String(data: data, encoding: .utf8), let feed = FeedParser.feedLinks(inHTML: html, base: url).first,
                    let (fd, _) = try? await AgentText.fetch(feed), FeedParser.parse(fd) != nil { kind = "feed"; final = feed.absoluteString }
        }
        update(notebook) { s in
            if kind == "feed" { if !s.config.rssFeeds.contains(final) { s.config.rssFeeds.append(final) } }
            else if !s.config.webPages.contains(final) { s.config.webPages.append(final) }
        }
        return nil
    }

    public func removeSource(_ address: String, notebook: String) {
        update(notebook) { s in s.config.rssFeeds.removeAll { $0 == address }; s.config.webPages.removeAll { $0 == address }; s.health.removeAll { $0.url == address }; s.pageHashes[address] = nil }
    }

    // MARK: helpers

    static func normalized(_ url: String) -> String {
        var s = url.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        for p in ["https://", "http://"] where s.hasPrefix(p) { s.removeFirst(p.count) }
        if s.hasPrefix("www.") { s.removeFirst(4) }
        while s.hasSuffix("/") { s.removeLast() }
        if let q = s.firstIndex(of: "?") , s[q...].contains("utm_") { s = String(s[..<q]) }
        return s
    }
    static func existingURLs(notebook: String, library: LibraryModel) async -> Set<String> {
        var out = Set<String>()
        for s in await library.sources(in: notebook) { if let u = await library.sourceURL(notebook: notebook, sourceId: s.id), !u.isEmpty { out.insert(normalized(u)) } }
        return out
    }
}
